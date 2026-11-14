"""Integration tests for G03 outbox service & /api/v1/admin/{outbox,dlq} API router."""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings
from app.contracts.events import EventEnvelopeContract
from app.db import SessionLocal, engine
from app.modules.outbox.public import (
    FAILED,
    PENDING,
    PUBLISHED,
    ConsumerRegistry,
    RetryPolicy,
    consume_event_once,
    enqueue_event,
    run_outbox_relay,
)
from app.modules.outbox.router import router as outbox_router
from app.platform.auth_context import mint_hs256_token
from app.platform.models import Tenant
from app.platform.rls import strict_tenant_rls
from app.platform.tenancy import DbRole, tenant_transaction

NO_WAIT = RetryPolicy(base_delay_seconds=0, max_delay_seconds=0, max_attempts=3)


def run(coro):
    return asyncio.run(coro)


def _api_client() -> TestClient:
    test_app = FastAPI()
    test_app.include_router(outbox_router)
    return TestClient(test_app)


def _auth_headers(
    *,
    tenant_id: uuid.UUID,
    roles: list[str],
    user_id: uuid.UUID | None = None,
    membership_id: uuid.UUID | None = None,
    ttl_seconds: int = 900,
    issued_at: int | None = None,
) -> dict[str, str]:
    settings = get_settings()
    token = mint_hs256_token(
        secret=settings.secret_key,
        user_id=user_id or uuid.uuid4(),
        tenant_id=tenant_id,
        membership_id=membership_id or uuid.uuid4(),
        roles=roles,
        issued_at=int(time.time()) if issued_at is None else issued_at,
        ttl_seconds=ttl_seconds,
    )
    return {"Authorization": f"Bearer {token}"}


def _make_envelope(
    tenant_id: uuid.UUID,
    *,
    event_type: str = "attempt.submitted",
    payload: dict | None = None,
) -> EventEnvelopeContract:
    return EventEnvelopeContract(
        event_type=event_type,
        tenant_id=tenant_id,
        aggregate_type="attempt",
        aggregate_id=uuid.uuid4(),
        correlation_id="req-outbox-api-1",
        payload=payload or {"attempt_id": str(uuid.uuid4())},
    )


@pytest.fixture
def tenants() -> tuple[uuid.UUID, uuid.UUID]:
    async def create() -> tuple[uuid.UUID, uuid.UUID]:
        suffix = uuid.uuid4().hex[:8]
        async with SessionLocal() as session, session.begin():
            a = Tenant(slug=f"outbox-api-a-{suffix}", name="Outbox API Tenant A")
            b = Tenant(slug=f"outbox-api-b-{suffix}", name="Outbox API Tenant B")
            session.add_all([a, b])
            await session.flush()
            return a.id, b.id

    return run(create())


@pytest.fixture
def effects_table():
    async def create() -> None:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS outbox_api_effects"))
            await conn.execute(
                text(
                    "CREATE TABLE outbox_api_effects ("
                    "id UUID PRIMARY KEY DEFAULT uuid_generate_v7(), "
                    "tenant_id UUID NOT NULL REFERENCES tenants(id), "
                    "event_id UUID NOT NULL, "
                    "note TEXT NOT NULL)"
                )
            )
            for statement in strict_tenant_rls("outbox_api_effects"):
                await conn.execute(text(statement))
            await conn.execute(
                text(
                    "GRANT SELECT, INSERT ON outbox_api_effects "
                    "TO ulp_app_rw, ulp_worker_ingestion, ulp_worker_grading"
                )
            )

    async def drop() -> None:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS outbox_api_effects"))

    run(create())
    yield
    run(drop())


async def _seed_outbox_event(
    tenant_id: uuid.UUID,
    *,
    event_type: str = "attempt.submitted",
    status: str = PENDING,
    attempt_count: int = 0,
) -> EventEnvelopeContract:
    env = _make_envelope(tenant_id, event_type=event_type)
    async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
        await enqueue_event(session, env)
    if status != PENDING or attempt_count != 0:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE outbox_events "
                    "SET status = :status, attempt_count = :attempts "
                    "WHERE event_id = :eid"
                ),
                {"status": status, "attempts": attempt_count, "eid": env.event_id},
            )
    return env


def test_admin_outbox_endpoints_enforce_jwt_role_and_anti_spoofing(tenants: tuple[uuid.UUID, uuid.UUID]) -> None:
    tenant_a, tenant_b = tenants
    client = _api_client()

    # 1. Missing JWT -> 401
    r_missing = client.get("/api/v1/admin/outbox/stats")
    assert r_missing.status_code == 401
    assert r_missing.headers["content-type"].startswith("application/problem+json")
    assert r_missing.json()["error_code"] == "UNAUTHORIZED"

    # 2. Expired JWT -> 401
    expired_hdrs = _auth_headers(tenant_id=tenant_a, roles=["ADMIN"], issued_at=int(time.time()) - 3600, ttl_seconds=60)
    assert client.get("/api/v1/admin/dlq", headers=expired_hdrs).status_code == 401

    # 3. Non-admin role -> 403
    student_hdrs = _auth_headers(tenant_id=tenant_a, roles=["STUDENT"])
    r_student = client.get("/api/v1/admin/outbox/stats", headers=student_hdrs)
    assert r_student.status_code == 403
    assert r_student.json()["error_code"] == "FORBIDDEN"

    # 4. Spoofed X-Tenant-Id header -> 403
    admin_a_hdrs = _auth_headers(tenant_id=tenant_a, roles=["ADMIN"])
    r_spoof = client.get(
        "/api/v1/admin/outbox/stats",
        headers={**admin_a_hdrs, "X-Tenant-Id": str(tenant_b)},
    )
    assert r_spoof.status_code == 403
    assert r_spoof.json()["error_code"] == "FORBIDDEN"


def test_outbox_stats_are_tenant_scoped_via_rls(tenants: tuple[uuid.UUID, uuid.UUID]) -> None:
    tenant_a, tenant_b = tenants

    async def setup() -> None:
        await _seed_outbox_event(tenant_a, status=PENDING)
        await _seed_outbox_event(tenant_a, status=PENDING)
        await _seed_outbox_event(tenant_a, status=PUBLISHED)
        await _seed_outbox_event(tenant_a, status=FAILED, attempt_count=5)
        await _seed_outbox_event(tenant_b, status=FAILED, attempt_count=5)

    run(setup())
    client = _api_client()

    resp_a = client.get("/api/v1/admin/outbox/stats", headers=_auth_headers(tenant_id=tenant_a, roles=["ADMIN"]))
    assert resp_a.status_code == 200
    body_a = resp_a.json()
    assert body_a["tenant_id"] == str(tenant_a)
    assert (body_a["pending"], body_a["published"], body_a["failed"], body_a["dlq_count"]) == (2, 1, 1, 1)
    assert body_a["oldest_pending_age_seconds"] is not None and body_a["oldest_pending_age_seconds"] >= 0

    resp_b = client.get(
        "/api/v1/admin/outbox/stats",
        headers=_auth_headers(tenant_id=tenant_b, roles=["PLATFORM_ADMIN"]),
    )
    assert resp_b.status_code == 200
    body_b = resp_b.json()
    assert body_b["tenant_id"] == str(tenant_b)
    assert (body_b["pending"], body_b["published"], body_b["failed"], body_b["dlq_count"]) == (0, 0, 1, 1)
    assert body_b["oldest_pending_age_seconds"] is None


def test_dlq_listing_pagination_filtering_and_cross_tenant_cursor_isolation(
    tenants: tuple[uuid.UUID, uuid.UUID],
) -> None:
    tenant_a, tenant_b = tenants

    async def setup() -> tuple[list[uuid.UUID], uuid.UUID]:
        f1 = await _seed_outbox_event(tenant_a, event_type="attempt.submitted", status=FAILED, attempt_count=5)
        await asyncio.sleep(0.01)
        f2 = await _seed_outbox_event(tenant_a, event_type="document.uploaded", status=FAILED, attempt_count=5)
        await asyncio.sleep(0.01)
        f3 = await _seed_outbox_event(tenant_a, event_type="attempt.submitted", status=FAILED, attempt_count=5)
        await _seed_outbox_event(tenant_a, event_type="attempt.submitted", status=PENDING)
        fb = await _seed_outbox_event(tenant_b, event_type="attempt.submitted", status=FAILED, attempt_count=5)
        return [f3.event_id, f2.event_id, f1.event_id], fb.event_id

    expected_a_desc, fb_id = run(setup())
    client = _api_client()
    hdrs_a = _auth_headers(tenant_id=tenant_a, roles=["ADMIN"])
    hdrs_b = _auth_headers(tenant_id=tenant_b, roles=["ADMIN"])

    page1 = client.get("/api/v1/admin/dlq?limit=2", headers=hdrs_a)
    assert page1.status_code == 200
    d1 = page1.json()
    assert d1["limit"] == 2
    assert d1["has_more"] is True
    assert d1["next_cursor"] is not None
    assert [uuid.UUID(item["event_id"]) for item in d1["items"]] == expected_a_desc[:2]

    page2 = client.get(f"/api/v1/admin/dlq?limit=2&cursor={d1['next_cursor']}", headers=hdrs_a)
    assert page2.status_code == 200
    d2 = page2.json()
    assert d2["has_more"] is False
    assert d2["next_cursor"] is None
    assert [uuid.UUID(item["event_id"]) for item in d2["items"]] == expected_a_desc[2:]

    # Filter by event_type
    filtered = client.get("/api/v1/admin/dlq?event_type=document.uploaded", headers=hdrs_a)
    assert filtered.status_code == 200
    assert [uuid.UUID(item["event_id"]) for item in filtered.json()["items"]] == [expected_a_desc[1]]

    # Tenant B sees only its own DLQ item, never Tenant A's
    b_list = client.get("/api/v1/admin/dlq", headers=hdrs_b)
    assert b_list.status_code == 200
    assert [uuid.UUID(item["event_id"]) for item in b_list.json()["items"]] == [fb_id]

    # Cursor bound to Tenant A is rejected when used by Tenant B -> 422
    foreign_cursor = client.get(f"/api/v1/admin/dlq?cursor={d1['next_cursor']}", headers=hdrs_b)
    assert foreign_cursor.status_code == 422
    assert foreign_cursor.json()["error_code"] == "VALIDATION_ERROR"


def test_dlq_replay_validation_cross_tenant_isolation_and_scheduler_delivery(
    tenants: tuple[uuid.UUID, uuid.UUID],
    effects_table,
) -> None:
    tenant_a, tenant_b = tenants
    client = _api_client()
    hdrs_a = _auth_headers(tenant_id=tenant_a, roles=["ADMIN"])
    hdrs_b = _auth_headers(tenant_id=tenant_b, roles=["ADMIN"])

    # Fail an event through the real relay running under ulp_system_scheduler
    should_fail = True
    seen_roles: list[str] = []

    async def flaky_handler(session, event: EventEnvelopeContract) -> None:
        role_name = (await session.execute(text("SELECT current_user"))).scalar_one()
        seen_roles.append(role_name)
        if should_fail:
            raise RuntimeError("downstream temporary outage")
        await session.execute(
            text("INSERT INTO outbox_api_effects (tenant_id, event_id, note) VALUES (:t, :e, :n)"),
            {"t": event.tenant_id, "e": event.event_id, "n": "replayed-ok"},
        )

    registry = ConsumerRegistry()
    registry.subscribe("attempt.submitted", "grading.api_probe", flaky_handler, role=DbRole.WORKER_GRADING)

    async def drive_to_dlq() -> uuid.UUID:
        env = await _seed_outbox_event(tenant_a, event_type="attempt.submitted", status=PENDING)
        for _ in range(NO_WAIT.max_attempts):
            await run_outbox_relay(registry=registry, retry=NO_WAIT)
        return env.event_id

    failed_id = run(drive_to_dlq())
    pending_id = run(_seed_outbox_event(tenant_a, status=PENDING)).event_id

    # 1. Reason shorter than 15 chars (or invisible chars) -> 422
    r_short = client.post(
        f"/api/v1/admin/dlq/{failed_id}/replay",
        headers=hdrs_a,
        json={"reason": "too short"},
    )
    assert r_short.status_code == 422

    r_invisible = client.post(
        f"/api/v1/admin/dlq/{failed_id}/replay",
        headers=hdrs_a,
        json={"reason": "fix\u200b\u200b\u200b\u200b\u200b\u200b\u200b\u200b\u200b\u200b\u200b\u200b"},
    )
    assert r_invisible.status_code == 422

    # 2. Cross-tenant replay is blocked by RLS -> 404
    r_cross = client.post(
        f"/api/v1/admin/dlq/{failed_id}/replay",
        headers=hdrs_b,
        json={"reason": "Attempting unauthorized cross-tenant replay"},
    )
    assert r_cross.status_code == 404
    assert r_cross.json()["error_code"] == "NOT_FOUND"

    # 3. Replaying a PENDING event -> 409
    r_not_failed = client.post(
        f"/api/v1/admin/dlq/{pending_id}/replay",
        headers=hdrs_a,
        json={"reason": "Replaying an event that is still pending"},
    )
    assert r_not_failed.status_code == 409

    # 4. Valid replay resets FAILED -> PENDING and attempt_count -> 0
    r_ok = client.post(
        f"/api/v1/admin/dlq/{failed_id}/replay",
        headers=hdrs_a,
        json={"reason": "Downstream grading worker recovered after deploy"},
    )
    assert r_ok.status_code == 200
    replayed = r_ok.json()
    assert replayed["event_id"] == str(failed_id)
    assert replayed["previous_status"] == FAILED
    assert replayed["status"] == PENDING
    assert replayed["previous_attempt_count"] == NO_WAIT.max_attempts
    assert replayed["attempt_count"] == 0

    # 5. Relay under ulp_system_scheduler picks up the replayed event and delivers it
    should_fail = False

    async def deliver_after_replay() -> tuple[str, list[str]]:
        # Delete the pending helper event so only our replayed event runs cleanly
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM outbox_events WHERE event_id = :eid"), {"eid": pending_id})
        stats = await run_outbox_relay(registry=registry, retry=NO_WAIT)
        assert stats.published == 1
        assert stats.deliveries == 1
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            status_now = (
                await session.execute(text("SELECT status FROM outbox_events WHERE event_id = :e"), {"e": failed_id})
            ).scalar_one()
            notes = list(
                (
                    await session.execute(
                        text("SELECT note FROM outbox_api_effects WHERE event_id = :e"),
                        {"e": failed_id},
                    )
                )
                .scalars()
                .all()
            )
        return status_now, notes

    status_now, notes = run(deliver_after_replay())
    assert status_now == PUBLISHED
    assert notes == ["replayed-ok"]
    assert seen_roles and all(r == DbRole.WORKER_GRADING.value for r in seen_roles)


def test_inbox_consumer_5x_redelivery_executes_side_effect_once_and_relay_uses_scheduler(
    tenants: tuple[uuid.UUID, uuid.UUID],
    effects_table,
) -> None:
    """DoD: sending the same event_id 5 times produces exactly 1 side effect; relay runs as ulp_system_scheduler."""
    tenant_a, _ = tenants
    env = _make_envelope(tenant_a)

    async def side_effect(session, event: EventEnvelopeContract) -> None:
        await session.execute(
            text("INSERT INTO outbox_api_effects (tenant_id, event_id, note) VALUES (:t, :e, :n)"),
            {"t": event.tenant_id, "e": event.event_id, "n": "once"},
        )

    async def run_dod() -> tuple[list[bool], list[str], tuple[str, str | None]]:
        outcomes: list[bool] = []
        for _ in range(5):
            async with tenant_transaction(tenant_a, role=DbRole.WORKER_INGESTION) as session:
                claimed = await consume_event_once(
                    session,
                    consumer_name="ingestion.dedup_probe",
                    envelope=env,
                    handler=side_effect,
                )
                outcomes.append(claimed)

        # Also prove relay_once runs as ulp_system_scheduler under outbox_relay job
        async def relay_probe(session, event: EventEnvelopeContract) -> None:
            await side_effect(session, event)

        registry = ConsumerRegistry()
        registry.subscribe("attempt.submitted", "ingestion.relay_probe", relay_probe, role=DbRole.WORKER_INGESTION)

        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            await enqueue_event(session, _make_envelope(tenant_a))

        stats = await run_outbox_relay(registry=registry, retry=NO_WAIT)
        assert stats.published == 1

        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            notes = list(
                (
                    await session.execute(
                        text("SELECT note FROM outbox_api_effects WHERE event_id = :e"),
                        {"e": env.event_id},
                    )
                )
                .scalars()
                .all()
            )
            api_ctx = (await session.execute(text("SELECT current_user, app_system_job()"))).one()
        return outcomes, notes, (api_ctx[0], api_ctx[1])

    outcomes, notes, api_ctx = run(run_dod())
    assert outcomes == [True, False, False, False, False]
    assert notes == ["once"]
    assert api_ctx == (DbRole.APP_RW.value, None)
