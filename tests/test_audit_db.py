"""G02 audit (TASK-14): append-only partitioned audit_events (F-01), legal takedown, and the /api/v1 routes.

DoD: UPDATE and DELETE on audit_events are refused by the database; neither the parent table nor a partition
named directly lets one tenant read another's rows (F-01); executing a takedown writes the audit event and the
`rights.takedown.executed` outbox event in the same transaction as the takedown itself.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.modules.audit.public import AuditDiffError, record_audit_event
from app.platform.auth_context import mint_hs256_token
from app.platform.models import Tenant
from app.platform.tenancy import DbRole, tenant_transaction

RUNTIME_ROLES = (
    "ulp_app_rw", "ulp_exam_session_rw", "ulp_worker_ingestion",
    "ulp_worker_grading", "ulp_system_scheduler", "ulp_analytics_ro",
)  # fmt: skip
REASON = "Khiếu nại bản quyền của nhà xuất bản, có văn bản đính kèm"
NOV_2026 = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)  # lands in audit_events_2026_11
OLD = datetime(2020, 1, 1, tzinfo=UTC)  # no monthly partition: lands in audit_events_default


def run(coro):
    return asyncio.run(coro)


async def _scalar(sql: str, **params):
    async with SessionLocal() as session:
        return (await session.execute(text(sql), params)).scalar()


async def _all(sql: str, **params):
    async with SessionLocal() as session:
        return (await session.execute(text(sql), params)).all()


async def _execute(sql: str, **params) -> None:
    async with SessionLocal() as session, session.begin():
        await session.execute(text(sql), params)


@pytest.fixture
def world() -> dict[str, uuid.UUID]:
    async def create():
        suffix = uuid.uuid4().hex[:8]
        async with SessionLocal() as session, session.begin():
            a, b = Tenant(slug=f"aud-a-{suffix}", name="A"), Tenant(slug=f"aud-b-{suffix}", name="B")
            session.add_all([a, b])
            await session.flush()
            users = {}
            for key in ("admin_a", "admin_b"):
                users[key] = (
                    await session.execute(
                        text("INSERT INTO users (email_blind_index) VALUES (:b) RETURNING id"),
                        {"b": uuid.uuid4().hex + uuid.uuid4().hex},
                    )
                ).scalar_one()
            return {"a": a.id, "b": b.id, **users}

    return run(create())


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _headers(tenant: uuid.UUID, user: uuid.UUID, roles: tuple[str, ...] = ("TENANT_ADMIN",)) -> dict[str, str]:
    token = mint_hs256_token(
        secret=get_settings().secret_key,
        user_id=user,
        tenant_id=tenant,
        membership_id=uuid.uuid4(),
        roles=roles,
        issued_at=int(time.time()),
    )
    return {"Authorization": f"Bearer {token}"}


async def _audit(tenant, actor, *, occurred_at=None, action="CHANGE_RIGHTS_STATUS", before=None, after=None):
    async with tenant_transaction(tenant, role=DbRole.APP_RW) as session:
        return await record_audit_event(
            session,
            tenant_id=tenant,
            actor_id=actor,
            action=action,
            target_type="content_items",
            target_id=uuid.uuid4(),
            reason=REASON,
            before=before or {"rights_status": "PENDING_REVIEW"},
            after=after or {"rights_status": "OWNED"},
            correlation_id="corr-1",
            occurred_at=occurred_at,
        )


def _content_item(world, who="a", publish=True) -> tuple[uuid.UUID, uuid.UUID]:
    """A content item of the tenant with a published (frozen) version 1."""

    async def create():
        async with tenant_transaction(world[who], role=DbRole.APP_RW) as session:
            item_id, version_id = uuid.uuid4(), uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO content_items (id, tenant_id, content_type, slug, verification_status, rights_status, "
                    "publication_status, current_version_id) "
                    "VALUES (:i, :t, 'LESSON', :s, 'VERIFIED', 'OWNED', :p, :cv)"
                ),
                {
                    "i": item_id, "t": world[who], "s": f"bai-{item_id.hex[:8]}",
                    "p": "PUBLISHED" if publish else "DRAFT", "cv": version_id if publish else None,
                },
            )  # fmt: skip
            await session.execute(
                text(
                    "INSERT INTO content_versions (id, tenant_id, content_item_id, version_no, title, status) "
                    "VALUES (:v, :t, :i, 1, 'Bài', :st)"
                ),
                {"v": version_id, "t": world[who], "i": item_id, "st": "PUBLISHED" if publish else "DRAFT"},
            )
            return item_id, version_id

    return run(create())


# --- schema: partitions, F-01, grants ---------------------------------------------------------------


def _partitions() -> list[str]:
    rows = run(_all("SELECT inhrelid::regclass::text FROM pg_inherits WHERE inhparent = 'audit_events'::regclass"))
    return sorted(r[0] for r in rows)


def test_audit_events_is_partitioned_by_month_with_a_default_partition():
    assert run(_scalar("SELECT relkind::text FROM pg_class WHERE relname = 'audit_events'")) == "p"
    parts = _partitions()
    assert {"audit_events_2026_11", "audit_events_default"} <= set(parts)
    assert len([p for p in parts if p != "audit_events_default"]) >= 6  # current month + the next five
    bound = run(_scalar("SELECT pg_get_expr(relpartbound, oid) FROM pg_class WHERE relname = 'audit_events_2026_11'"))
    assert "2026-11-01 00:00:00+00" in bound and "2026-12-01 00:00:00+00" in bound


def test_every_partition_is_locked_down_on_its_own():
    """F-01: each partition forces RLS with the tenant policy and grants nothing to any runtime role."""
    for part in _partitions():
        forced = run(
            _scalar(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid = CAST(:p AS regclass)", p=part
            )
        )
        assert forced is True, part
        qual = run(
            _scalar(
                "SELECT qual FROM pg_policies WHERE tablename = :p AND policyname = 'tenant_isolation_policy'", p=part
            )
        )
        assert "( SELECT current_app_tenant_id() AS current_app_tenant_id)" in qual, part
        for role in RUNTIME_ROLES:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert not run(_scalar("SELECT has_table_privilege(:r, :p, :x)", r=role, p=part, x=privilege)), (
                    part, role, privilege,
                )  # fmt: skip


def test_parent_privileges_are_insert_and_select_only():
    def has(role, privilege, table="audit_events"):
        return run(_scalar("SELECT has_table_privilege(:r, :t, :p)", r=role, t=table, p=privilege))

    assert has("ulp_app_rw", "SELECT") and has("ulp_app_rw", "INSERT")
    assert (
        has("ulp_exam_session_rw", "INSERT")
        and has("ulp_worker_grading", "INSERT")
        and has("ulp_worker_ingestion", "INSERT")
    )
    for role in RUNTIME_ROLES:
        for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
            assert not has(role, privilege), (role, privilege)
    assert not has("ulp_analytics_ro", "SELECT") and not has("ulp_system_scheduler", "SELECT")
    assert not has("ulp_app_rw", "DELETE", "takedown_cases")  # a legal record is never deleted


def test_a_partition_created_later_is_locked_down_and_inherits_the_triggers():
    run(_execute("SELECT ensure_monthly_partitions('audit_events', DATE '2031-03-10', 1)"))
    assert "audit_events_2031_03" in _partitions()
    assert run(_scalar("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'audit_events_2031_03'"))
    assert not run(_scalar("SELECT has_table_privilege('ulp_app_rw', 'audit_events_2031_03', 'SELECT')"))
    triggers = {
        r[0] for r in run(_all("SELECT tgname FROM pg_trigger WHERE tgrelid = 'audit_events_2031_03'::regclass"))
    }
    assert {"trg_immutable_audit_events", "trg_audit_no_raw_pii"} <= triggers


@pytest.mark.parametrize("tz", ["UTC", "Asia/Ho_Chi_Minh", "America/Los_Angeles"])
def test_monthly_partition_bounds_do_not_depend_on_the_session_time_zone(tz):
    """The spec's version named December 2026 'audit_events_2026_11' under Asia/Ho_Chi_Minh (and skipped it)."""

    async def create():
        async with SessionLocal() as session, session.begin():
            await session.execute(text(f"SET LOCAL TIME ZONE '{tz}'"))
            await session.execute(text("SELECT ensure_monthly_partitions('audit_events', DATE '2032-12-15', 1)"))

    run(create())
    bound = run(_scalar("SELECT pg_get_expr(relpartbound, oid) FROM pg_class WHERE relname = 'audit_events_2032_12'"))
    assert bound is not None

    async def utc_bound():
        async with SessionLocal() as session:
            await session.execute(text("SET TIME ZONE 'UTC'"))
            return (
                await session.execute(
                    text("SELECT pg_get_expr(relpartbound, oid) FROM pg_class WHERE relname = 'audit_events_2032_12'")
                )
            ).scalar()

    assert "2032-12-01 00:00:00+00" in run(utc_bound()) and "2033-01-01 00:00:00+00" in run(utc_bound())
    run(_execute("DROP TABLE audit_events_2032_12"))


# --- append-only (DoD) --------------------------------------------------------------------------


def test_the_api_role_can_neither_update_nor_delete_audit_events(world):
    run(_audit(world["a"], world["admin_a"]))

    async def attempt(sql):
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            await session.execute(text(sql))

    for sql in ("UPDATE audit_events SET reason = 'sửa'", "DELETE FROM audit_events", "TRUNCATE audit_events"):
        with pytest.raises(DBAPIError, match="permission denied"):
            run(attempt(sql))
    assert run(_scalar("SELECT count(*) FROM audit_events")) == 1


@pytest.mark.parametrize("table", ["audit_events", "audit_events_2026_11", "audit_events_default"])
def test_even_the_owner_cannot_update_or_delete_an_audit_event(world, table):
    """The append-only trigger holds on the parent and on every partition, whoever runs the statement."""
    run(_audit(world["a"], world["admin_a"], occurred_at=NOV_2026))
    run(_audit(world["a"], world["admin_a"], occurred_at=OLD))
    for sql in (f"UPDATE {table} SET reason = 'sửa'", f"DELETE FROM {table}"):
        with pytest.raises(DBAPIError, match="APPEND_ONLY_VIOLATION"):
            run(_execute(sql))
    assert run(_scalar("SELECT count(*) FROM audit_events")) == 2


def test_rows_land_in_their_monthly_partition_through_the_parent(world):
    run(_audit(world["a"], world["admin_a"], occurred_at=NOV_2026))
    run(_audit(world["a"], world["admin_a"], occurred_at=OLD))
    assert run(_scalar("SELECT count(*) FROM audit_events_2026_11")) == 1
    assert run(_scalar("SELECT count(*) FROM audit_events_default")) == 1


def test_raw_pii_in_a_snapshot_is_refused_by_the_database(world):
    async def insert_raw():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            await session.execute(
                text(
                    "INSERT INTO audit_events (tenant_id, action, target_type, target_id, after_json) "
                    "VALUES (:t, 'X', 'users', :i, CAST(:j AS jsonb))"
                ),
                {"t": world["a"], "i": uuid.uuid4(), "j": '{"guardian_email": "me@example.com"}'},
            )

    with pytest.raises(DBAPIError, match="RAW_PII_IN_AUDIT_FORBIDDEN"):
        run(insert_raw())
    # the service redacts such keys before they reach the database
    event = run(_audit(world["a"], world["admin_a"], before={"email": "a@x.vn"}, after={"email": "b@x.vn"}))
    assert event.before_json == {"email": "[REDACTED]"} and event.after_json == {"email": "[REDACTED]"}


def test_sensitive_actions_need_a_real_reason(world):
    async def short():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            await record_audit_event(
                session, tenant_id=world["a"], actor_id=world["admin_a"], action="EXECUTE_TAKEDOWN",
                target_type="content_items", target_id=uuid.uuid4(), reason="vì", before=None, after=None,
                correlation_id="c",
            )  # fmt: skip

    with pytest.raises(AuditDiffError, match="reason"):
        run(short())


# --- tenant isolation, also through the partitions (F-01, DoD) ------------------------------------------


@pytest.mark.parametrize("table", ["audit_events", "audit_events_2026_11", "audit_events_default"])
def test_tenant_b_cannot_read_tenant_a_audit_rows(world, table):
    run(_audit(world["a"], world["admin_a"], occurred_at=NOV_2026))
    run(_audit(world["a"], world["admin_a"], occurred_at=OLD))

    async def as_b():
        async with tenant_transaction(world["b"], role=DbRole.APP_RW) as session:
            return (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar()

    if table == "audit_events":
        assert run(as_b()) == 0  # through the parent: the tenant policy
    else:
        with pytest.raises(DBAPIError, match="permission denied"):
            run(as_b())  # a partition named directly: no runtime role holds any privilege on it

    async def as_a():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            return (await session.execute(text("SELECT count(*) FROM audit_events"))).scalar()

    assert run(as_a()) == 2


def test_a_partition_is_isolated_by_its_own_policy_even_with_a_grant(world):
    """Defence in depth: if someone granted SELECT on a partition, its own RLS policy still isolates tenants."""
    run(_audit(world["a"], world["admin_a"], occurred_at=NOV_2026))

    async def scenario():
        async with SessionLocal() as session, session.begin():
            await session.execute(text("GRANT SELECT ON audit_events_2026_11 TO ulp_app_rw"))
            await session.execute(text("SET LOCAL ROLE ulp_app_rw"))
            await session.execute(text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(world["b"])})
            seen_by_b = (await session.execute(text("SELECT count(*) FROM audit_events_2026_11"))).scalar()
            await session.rollback()  # the grant goes away with the transaction
            return seen_by_b

    assert run(scenario()) == 0
    assert not run(_scalar("SELECT has_table_privilege('ulp_app_rw', 'audit_events_2026_11', 'SELECT')"))


def test_a_tenant_cannot_write_an_audit_event_for_another_tenant(world):
    async def forge():
        async with tenant_transaction(world["b"], role=DbRole.APP_RW) as session:
            await session.execute(
                text("INSERT INTO audit_events (tenant_id, action, target_type, target_id) VALUES (:t, 'X', 'x', :i)"),
                {"t": world["a"], "i": uuid.uuid4()},
            )

    with pytest.raises(DBAPIError, match="row-level security"):
        run(forge())


# --- GET /api/v1/audit-logs --------------------------------------------------------------------------


def test_audit_logs_are_tenant_scoped_newest_first_and_paginated(client, world):
    for _ in range(5):
        run(_audit(world["a"], world["admin_a"]))
    run(_audit(world["b"], world["admin_b"]))
    headers = _headers(world["a"], world["admin_a"])
    first = client.get("/api/v1/audit-logs?limit=3", headers=headers)
    assert first.status_code == 200, first.text
    page = first.json()
    assert len(page["items"]) == 3 and page["has_more"] is True
    assert all(item["tenant_id"] == str(world["a"]) for item in page["items"])
    times = [item["occurred_at"] for item in page["items"]]
    assert times == sorted(times, reverse=True)
    rest = client.get(f"/api/v1/audit-logs?limit=3&cursor={page['next_cursor']}", headers=headers).json()
    assert len(rest["items"]) == 2 and rest["has_more"] is False
    assert {i["id"] for i in page["items"]}.isdisjoint({i["id"] for i in rest["items"]})
    # a cursor minted for tenant A is refused in tenant B
    other = client.get(
        f"/api/v1/audit-logs?cursor={page['next_cursor']}", headers=_headers(world["b"], world["admin_b"])
    )
    assert other.status_code == 422


def test_audit_logs_filters_and_authorization(client, world):
    event = run(_audit(world["a"], world["admin_a"]))
    run(_audit(world["a"], world["admin_a"], action="OVERRIDE_GRADE"))
    headers = _headers(world["a"], world["admin_a"])
    by_target = client.get(f"/api/v1/audit-logs?target_id={event.target_id}", headers=headers).json()
    assert [i["id"] for i in by_target["items"]] == [str(event.id)]
    by_action = client.get("/api/v1/audit-logs?action=override_grade", headers=headers).json()
    assert [i["action"] for i in by_action["items"]] == ["OVERRIDE_GRADE"]
    assert (
        client.get("/api/v1/audit-logs", headers=_headers(world["a"], world["admin_a"], ("TEACHER",))).status_code
        == 403
    )
    assert client.get("/api/v1/audit-logs").status_code == 401


# --- takedown ------------------------------------------------------------------------------------------


def _open_case(client, world, target_id, target_type="CONTENT_ITEM", who="a"):
    return client.post(
        "/api/v1/takedown-cases",
        headers=_headers(world[who], world[f"admin_{who}"]),
        json={
            "complainant_name": "NXB Giáo dục",
            "target_entity_type": target_type,
            "target_entity_id": str(target_id),
            "legal_reason": REASON,
        },
    )


def test_opening_a_case_is_audited(client, world):
    item_id, _ = _content_item(world)
    response = _open_case(client, world, item_id)
    assert response.status_code == 201, response.text
    case = response.json()
    assert case["status"] == "OPEN" and case["tenant_id"] == str(world["a"]) and case["executed_by"] is None
    (row,) = run(_all("SELECT action, target_type, target_id, actor_id FROM audit_events"))
    assert (row.action, row.target_type, str(row.target_id), row.actor_id) == (
        "OPEN_TAKEDOWN_CASE", "takedown_cases", case["id"], world["admin_a"],
    )  # fmt: skip


def test_a_case_cannot_target_another_tenants_item_and_needs_a_reason(client, world):
    item_b, _ = _content_item(world, who="b")
    assert _open_case(client, world, item_b).status_code == 404
    assert _open_case(client, world, uuid.uuid4()).status_code == 404
    short = client.post(
        "/api/v1/takedown-cases",
        headers=_headers(world["a"], world["admin_a"]),
        json={
            "complainant_name": "x",
            "target_entity_type": "CONTENT_ITEM",
            "target_entity_id": str(item_b),
            "legal_reason": "ngắn",
        },
    )
    assert short.status_code == 422
    assert run(_scalar("SELECT count(*) FROM takedown_cases")) == 0


def test_executing_a_takedown_does_everything_in_one_transaction(client, world):
    item_id, version_id = _content_item(world)
    case = _open_case(client, world, item_id).json()
    response = client.post(
        f"/api/v1/takedown-cases/{case['id']}/execute", headers=_headers(world["a"], world["admin_a"])
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["case"]["status"] == "EXECUTED" and body["case"]["executed_by"] == str(world["admin_a"])
    assert body["case"]["executed_at"] is not None

    item = run(_all("SELECT rights_status, publication_status FROM content_items WHERE id = :i", i=item_id))
    assert [tuple(r) for r in item] == [("TAKEDOWN", "TAKEN_DOWN")]
    assert run(_scalar("SELECT status FROM content_versions WHERE id = :v", v=version_id)) == "TAKEN_DOWN"

    (audit,) = run(_all("SELECT * FROM audit_events WHERE action = 'EXECUTE_TAKEDOWN'"))
    assert str(audit.id) == body["audit_event_id"] and audit.target_id == item_id and audit.actor_id == world["admin_a"]
    assert audit.before_json["rights_status"] == "OWNED" and audit.before_json["publication_status"] == "PUBLISHED"
    assert audit.after_json["rights_status"] == "TAKEDOWN" and audit.after_json["takedown_case_id"] == case["id"]
    assert audit.reason == REASON and audit.correlation_id

    (event,) = run(_all("SELECT * FROM outbox_events WHERE event_type = 'rights.takedown.executed'"))
    assert str(event.event_id) == body["event_id"] and event.tenant_id == world["a"]
    assert event.payload_json["takedown_case_id"] == case["id"] and event.payload_json["target_entity_id"] == str(
        item_id
    )
    assert event.payload_json["target_entity_type"] == "CONTENT_ITEM" and event.payload_json["executed_at"]
    assert event.correlation_id == audit.correlation_id  # one request, one correlation id
    # same transaction: same commit instant for the case, the audit row and the event
    executed_at = run(_scalar("SELECT executed_at FROM takedown_cases WHERE id = :c", c=case["id"]))
    assert executed_at == audit.occurred_at


def test_a_failure_in_the_last_step_leaves_no_trace_at_all(client, world, monkeypatch):
    item_id, version_id = _content_item(world)
    case = _open_case(client, world, item_id).json()

    from app.modules.audit.internal import service as audit_service

    async def broken_enqueue(session, envelope):
        raise RuntimeError("broker down")

    monkeypatch.setattr(audit_service, "enqueue", broken_enqueue)
    with pytest.raises(RuntimeError, match="broker down"):
        client.post(f"/api/v1/takedown-cases/{case['id']}/execute", headers=_headers(world["a"], world["admin_a"]))
    assert run(_scalar("SELECT status FROM takedown_cases WHERE id = :c", c=case["id"])) == "OPEN"
    assert run(_scalar("SELECT publication_status FROM content_items WHERE id = :i", i=item_id)) == "PUBLISHED"
    assert run(_scalar("SELECT status FROM content_versions WHERE id = :v", v=version_id)) == "PUBLISHED"
    assert run(_scalar("SELECT count(*) FROM audit_events WHERE action = 'EXECUTE_TAKEDOWN'")) == 0
    assert run(_scalar("SELECT count(*) FROM outbox_events")) == 0


def test_a_case_executes_once(client, world):
    item_id, _ = _content_item(world)
    case = _open_case(client, world, item_id).json()
    url = f"/api/v1/takedown-cases/{case['id']}/execute"
    assert client.post(url, headers=_headers(world["a"], world["admin_a"])).status_code == 200
    again = client.post(url, headers=_headers(world["a"], world["admin_a"]))
    assert again.status_code == 409 and "OPEN" in again.json()["detail"]
    assert run(_scalar("SELECT count(*) FROM outbox_events")) == 1


def test_a_source_document_takedown(client, world):
    async def seed():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            source_id = (
                await session.execute(
                    text(
                        "INSERT INTO sources (tenant_id, source_type, name) "
                        "VALUES (:t, 'MANUAL_UPLOAD', 's') RETURNING id"
                    ),
                    {"t": world["a"]},
                )
            ).scalar_one()
            return (
                await session.execute(
                    text(
                        "INSERT INTO source_documents (tenant_id, source_id, external_id, mime_type, rights_status) "
                        "VALUES (:t, :s, 'x', 'application/pdf', 'LICENSED') RETURNING id"
                    ),
                    {"t": world["a"], "s": source_id},
                )
            ).scalar_one()

    doc_id = run(seed())
    case = _open_case(client, world, doc_id, target_type="SOURCE_DOCUMENT").json()
    response = client.post(
        f"/api/v1/takedown-cases/{case['id']}/execute", headers=_headers(world["a"], world["admin_a"])
    )
    assert response.status_code == 200, response.text
    assert run(_scalar("SELECT rights_status FROM source_documents WHERE id = :d", d=doc_id)) == "TAKEDOWN"
    audit = run(_all("SELECT before_json, after_json FROM audit_events WHERE action = 'EXECUTE_TAKEDOWN'"))[0]
    assert "publication_status" not in audit.before_json and audit.before_json["rights_status"] == "LICENSED"
    event = run(_all("SELECT payload_json FROM outbox_events"))[0]
    assert event.payload_json["publication_status"] is None


def test_question_cases_wait_for_the_questions_module(client, world):
    case = _open_case(client, world, uuid.uuid4(), target_type="QUESTION")
    assert case.status_code == 201  # the legal record is kept
    execute = client.post(
        f"/api/v1/takedown-cases/{case.json()['id']}/execute", headers=_headers(world["a"], world["admin_a"])
    )
    assert execute.status_code == 409 and "C01" in execute.json()["detail"]
    assert run(_scalar("SELECT status FROM takedown_cases")) == "OPEN"


def test_execution_rules_for_people_and_tenants(client, world):
    item_id, _ = _content_item(world)
    case = _open_case(client, world, item_id).json()
    url = f"/api/v1/takedown-cases/{case['id']}/execute"
    assert client.post(url, headers=_headers(world["a"], world["admin_a"], ("TEACHER",))).status_code == 403
    assert client.post(url, headers=_headers(world["b"], world["admin_b"])).status_code == 404  # another tenant's case
    ghost = client.post(url, headers=_headers(world["a"], uuid.uuid4()))  # an admin token for no registered user
    assert ghost.status_code == 403
    assert client.post(url).status_code == 401
    assert (
        client.post(
            f"/api/v1/takedown-cases/{uuid.uuid4()}/execute", headers=_headers(world["a"], world["admin_a"])
        ).status_code
        == 404
    )
    assert run(_scalar("SELECT status FROM takedown_cases")) == "OPEN"


def test_tenant_b_sees_no_takedown_case_of_tenant_a(client, world):
    item_id, _ = _content_item(world)
    _open_case(client, world, item_id)

    async def as_b():
        async with tenant_transaction(world["b"], role=DbRole.APP_RW) as session:
            seen = (await session.execute(text("SELECT count(*) FROM takedown_cases"))).scalar()
            changed = (await session.execute(text("UPDATE takedown_cases SET status = 'REJECTED'"))).rowcount
            return seen, changed

    assert run(as_b()) == (0, 0)


# --- frozen content blocks (accepted proposal) -----------------------------------------------------------


def test_blocks_of_a_frozen_version_cannot_be_inserted_changed_or_deleted(world):
    _, frozen = _content_item(world)
    _, draft = _content_item(world, publish=False)

    async def run_sql(sql, version):
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            await session.execute(text(sql), {"v": version, "t": world["a"]})

    insert = (
        "INSERT INTO content_blocks (tenant_id, content_version_id, block_order, block_type, payload_json) "
        "VALUES (:t, :v, 0, 'THEORY_SUMMARY', '{}')"
    )
    with pytest.raises(DBAPIError, match="IMMUTABLE_VIOLATION"):
        run(run_sql(insert, frozen))
    run(run_sql(insert, draft))  # a draft version's blocks are editable
    run(run_sql("UPDATE content_blocks SET block_order = 1 WHERE content_version_id = :v", draft))
    run(_execute("UPDATE content_versions SET status = 'PUBLISHED' WHERE id = :v", v=draft))  # now frozen
    for sql in (
        "UPDATE content_blocks SET payload_json = '{\"x\": 1}' WHERE content_version_id = :v",
        "DELETE FROM content_blocks WHERE content_version_id = :v",
    ):
        with pytest.raises(DBAPIError, match="IMMUTABLE_VIOLATION"):
            run(run_sql(sql, draft))
    assert run(_scalar("SELECT count(*) FROM content_blocks WHERE content_version_id = :v", v=draft)) == 1
