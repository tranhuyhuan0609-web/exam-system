"""G01 config (TASK-05): platform_settings / feature_flags on the database, the admin API and its audit trail.

Also checks the Sprint 1 tables this migration creates for B03 assets and A01 ingestion: forced RLS and
the section 5 privileges of the runtime roles.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.modules.config.public import (
    FLAG_UPDATED_EVENT,
    SETTING_UPDATED_EVENT,
    evaluate_flag,
    update_feature_flag,
)
from app.modules.outbox.internal.models import OutboxEvent
from app.platform.auth_context import mint_hs256_token
from app.platform.models import Tenant
from app.platform.tenancy import DbRole, tenant_transaction


def run(coro):
    return asyncio.run(coro)


async def _scalar(sql: str, **params):
    async with SessionLocal() as session:
        return (await session.execute(text(sql), params)).scalar()


@pytest.fixture
def tenants() -> tuple[uuid.UUID, uuid.UUID]:
    async def create():
        suffix = uuid.uuid4().hex[:8]
        async with SessionLocal() as session, session.begin():
            a, b = Tenant(slug=f"cfg-a-{suffix}", name="A"), Tenant(slug=f"cfg-b-{suffix}", name="B")
            session.add_all([a, b])
            await session.flush()
            return a.id, b.id

    return run(create())


def _headers(tenant_id: uuid.UUID, roles: list[str], **extra: str) -> dict[str, str]:
    token = mint_hs256_token(
        secret=get_settings().secret_key,
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        membership_id=uuid.uuid4(),
        roles=roles,
        issued_at=int(time.time()),
    )
    return {"Authorization": f"Bearer {token}", **extra}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


async def _audit_events(event_type: str) -> list[OutboxEvent]:
    async with SessionLocal() as session:
        rows = await session.scalars(
            select(OutboxEvent).where(OutboxEvent.event_type == event_type).order_by(OutboxEvent.created_at)
        )
        return list(rows)


# --- schema, RLS and privileges -----------------------------------------------------------------


def test_config_tables_are_global_and_only_the_api_role_writes_them():
    for table in ("platform_settings", "feature_flags"):
        assert run(_scalar("SELECT relrowsecurity FROM pg_class WHERE relname = :t", t=table)) is False
        for privilege in ("SELECT", "INSERT", "UPDATE"):
            assert run(_scalar("SELECT has_table_privilege('ulp_app_rw', :t, :p)", t=table, p=privilege))
        assert not run(_scalar("SELECT has_table_privilege('ulp_app_rw', :t, 'DELETE')", t=table))
        for role in ("ulp_exam_session_rw", "ulp_worker_ingestion", "ulp_worker_grading", "ulp_analytics_ro"):
            assert not run(_scalar("SELECT has_table_privilege(:r, :t, 'SELECT')", r=role, t=table)), role


@pytest.mark.parametrize(
    "table",
    ["assets", "sources", "source_documents", "document_versions", "acquisition_jobs", "pipeline_stage_runs",
     "dead_letter_items"],
)  # fmt: skip
def test_sprint1_tables_force_rls_with_the_initplan_tenant_check(table):
    forced = run(_scalar("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = :t", t=table))
    assert forced is True
    policies = run(_scalar("SELECT string_agg(qual, ' ') FROM pg_policies WHERE tablename = :t", t=table))
    assert "( SELECT current_app_tenant_id() AS current_app_tenant_id)" in policies


def test_sprint1_privileges_follow_section_5():
    def has(role, table, privilege):
        return run(_scalar("SELECT has_table_privilege(:r, :t, :p)", r=role, t=table, p=privilege))

    assert has("ulp_worker_ingestion", "acquisition_jobs", "UPDATE")
    assert not has("ulp_worker_ingestion", "acquisition_jobs", "DELETE")
    assert has("ulp_worker_ingestion", "assets", "INSERT") and not has("ulp_worker_ingestion", "assets", "UPDATE")
    assert has("ulp_worker_ingestion", "dead_letter_items", "INSERT")
    assert not has("ulp_worker_ingestion", "dead_letter_items", "UPDATE")
    assert has("ulp_exam_session_rw", "assets", "SELECT") and not has("ulp_exam_session_rw", "sources", "SELECT")
    assert not has("ulp_worker_grading", "sources", "SELECT")
    assert has("ulp_app_rw", "dead_letter_items", "DELETE")


def test_system_raw_assets_stay_hidden_from_other_tenants(tenants):
    tenant_a, _ = tenants
    system = uuid.UUID(int=0)

    async def scenario():
        async with tenant_transaction(system) as session:
            for role, key in (("RAW_SOURCE", "raw"), ("PREVIEW_PDF", "preview")):
                await session.execute(
                    text(
                        "INSERT INTO assets (tenant_id, asset_role, object_key, mime_type, sha256, size_bytes) "
                        "VALUES (:t, :r, :k, 'application/pdf', :s, 1)"
                    ),
                    {"t": system, "r": role, "k": f"sys/{key}-{uuid.uuid4()}", "s": "0" * 64},
                )
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            return sorted((await session.execute(text("SELECT asset_role FROM assets"))).scalars())

    assert run(scenario()) == ["PREVIEW_PDF"]


# --- feature flags ---------------------------------------------------------------------------


def test_patch_creates_a_flag_audits_it_and_takes_effect_at_once(client, tenants):
    tenant_a, tenant_b = tenants
    admin = _headers(tenant_a, ["PLATFORM_ADMIN"])

    async def evaluate(tenant):
        async with tenant_transaction(tenant, role=DbRole.APP_RW) as session:
            return await evaluate_flag(session, "new_exam_room", tenant_id=tenant)

    assert run(evaluate(tenant_b)).enabled is False  # unknown flag: off

    created = client.patch(
        "/api/v1/admin/feature-flags/new_exam_room",
        headers=admin,
        json={"is_enabled": False, "tenant_whitelist": [str(tenant_b)], "config_json": {"note": "canary"}},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["created"] is True and body["flag"]["tenant_whitelist"] == [str(tenant_b)]
    assert body["flag"]["config_json"] == {"note": "canary"} and body["flag"]["rollout_percent"] == 100

    result = run(evaluate(tenant_b))
    assert result.enabled is True and result.reason == "TENANT_WHITELIST"
    assert run(evaluate(tenant_a)).enabled is False

    changed = client.patch(
        "/api/v1/admin/feature-flags/new_exam_room", headers=admin, json={"is_enabled": True, "rollout_percent": 0}
    )
    assert changed.status_code == 200 and changed.json()["created"] is False
    assert run(evaluate(tenant_a)).reason == "DISABLED"  # 0 % rollout, no restart, no cache

    on = client.patch("/api/v1/admin/feature-flags/new_exam_room", headers=admin, json={"rollout_percent": 100})
    assert on.status_code == 200
    assert run(evaluate(tenant_a)).reason == "GLOBAL_ENABLED"

    events = run(_audit_events(FLAG_UPDATED_EVENT))
    assert [e.payload_json["created"] for e in events] == [True, False, False]
    assert all(e.tenant_id == tenant_a and e.aggregate_type == "feature_flag" for e in events)
    assert events[0].payload_json["before_json"] is None
    second = events[1].payload_json
    assert second["before_json"]["is_enabled"] is False and second["after_json"]["is_enabled"] is True
    assert second["after_json"]["rollout_percent"] == 0
    assert {str(e.event_id) for e in events} >= {body["audit_event_id"]}


def test_list_flags_for_admins_only(client, tenants):
    tenant_a, _ = tenants
    client.patch("/api/v1/admin/feature-flags/b_flag", headers=_headers(tenant_a, ["SUPER_ADMIN"]), json={})
    client.patch("/api/v1/admin/feature-flags/a_flag", headers=_headers(tenant_a, ["SUPER_ADMIN"]), json={})
    listed = client.get("/api/v1/admin/feature-flags", headers=_headers(tenant_a, ["TENANT_ADMIN"]))
    assert listed.status_code == 200
    assert [f["key"] for f in listed.json()["items"]] == ["a_flag", "b_flag"]
    assert client.get("/api/v1/admin/feature-flags", headers=_headers(tenant_a, ["STUDENT"])).status_code == 403
    assert client.get("/api/v1/admin/feature-flags").status_code == 401


def test_tenant_admins_cannot_change_platform_flags_or_settings(client, tenants):
    tenant_a, _ = tenants
    tenant_admin = _headers(tenant_a, ["TENANT_ADMIN", "ADMIN"])
    flag = client.patch("/api/v1/admin/feature-flags/x", headers=tenant_admin, json={"is_enabled": True})
    setting = client.patch("/api/v1/admin/config/x", headers=tenant_admin, json={"value_json": 1})
    assert flag.status_code == setting.status_code == 403
    assert flag.headers["content-type"].startswith("application/problem+json")
    assert run(_scalar("SELECT count(*) FROM feature_flags")) == 0


def test_spoofed_tenant_header_is_rejected(client, tenants):
    tenant_a, tenant_b = tenants
    headers = _headers(tenant_a, ["PLATFORM_ADMIN"], **{"X-Tenant-Id": str(tenant_b)})
    assert client.patch("/api/v1/admin/feature-flags/x", headers=headers, json={}).status_code == 403


@pytest.mark.parametrize(
    ("key", "body"),
    [
        ("ok_key", {"rollout_percent": 101}),
        ("ok_key", {"unknown_field": True}),
        ("ok_key", {"config_json": {"rollout_percent": 5}}),  # rule fields have their own keys
        ("Bad Key", {}),
    ],
)
def test_invalid_flag_patches_are_rejected(client, tenants, key, body):
    tenant_a, _ = tenants
    response = client.patch(
        f"/api/v1/admin/feature-flags/{key}", headers=_headers(tenant_a, ["PLATFORM_ADMIN"]), json=body
    )
    assert response.status_code == 422
    assert run(_scalar("SELECT count(*) FROM feature_flags")) == 0


def test_pins_can_be_set_and_cleared(client, tenants):
    tenant_a, _ = tenants
    admin = _headers(tenant_a, ["PLATFORM_ADMIN"])
    url = "/api/v1/admin/feature-flags/parser_pin"
    pinned = client.patch(url, headers=admin, json={"is_enabled": True, "pinned_parser_version": "1.4.2"})
    assert pinned.json()["flag"]["pinned_parser_version"] == "1.4.2"
    kept = client.patch(url, headers=admin, json={"pinned_ai_model": "model-x"})
    assert kept.json()["flag"]["pinned_parser_version"] == "1.4.2"  # left out: unchanged
    cleared = client.patch(url, headers=admin, json={"pinned_parser_version": None})
    assert cleared.json()["flag"]["pinned_parser_version"] is None
    assert cleared.json()["flag"]["pinned_ai_model"] == "model-x"


def test_a_rolled_back_change_leaves_no_flag_and_no_audit_event(tenants):
    tenant_a, _ = tenants

    async def scenario():
        with pytest.raises(RuntimeError):
            async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
                await update_feature_flag(session, "ghost", actor_tenant_id=tenant_a, is_enabled=True)
                raise RuntimeError("business step failed")

    run(scenario())
    assert run(_scalar("SELECT count(*) FROM feature_flags")) == 0
    assert run(_audit_events(FLAG_UPDATED_EVENT)) == []


# --- platform settings -----------------------------------------------------------------------


def test_patch_config_upserts_the_setting_and_audits_before_and_after(client, tenants):
    tenant_a, _ = tenants
    admin = _headers(tenant_a, ["PLATFORM_ADMIN"])
    first = client.patch("/api/v1/admin/config/max_upload_mb", headers=admin, json={"value_json": {"pdf": 50}})
    assert first.status_code == 200 and first.json()["created"] is True
    second = client.patch("/api/v1/admin/config/max_upload_mb", headers=admin, json={"value_json": {"pdf": 80}})
    assert second.json() | {"updated_at": None, "audit_event_id": None} == {
        "key": "max_upload_mb",
        "value_json": {"pdf": 80},
        "updated_at": None,
        "created": False,
        "audit_event_id": None,
    }
    assert run(_scalar("SELECT value_json FROM platform_settings WHERE key = 'max_upload_mb'")) == {"pdf": 80}
    events = run(_audit_events(SETTING_UPDATED_EVENT))
    assert [(e.payload_json["before_json"], e.payload_json["after_json"]) for e in events] == [
        (None, {"pdf": 50}),
        ({"pdf": 50}, {"pdf": 80}),
    ]


def test_patch_config_requires_a_value(client, tenants):
    tenant_a, _ = tenants
    response = client.patch("/api/v1/admin/config/k", headers=_headers(tenant_a, ["PLATFORM_ADMIN"]), json={})
    assert response.status_code == 422
