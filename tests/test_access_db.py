"""F05 access (TASK-06): roles, permissions and role_permissions, the API, and cross-tenant IDOR.

DoD: 0 cases where a user of tenant A reads or changes a row of tenant B, through the API or directly in
the database as the runtime role.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.modules.access.public import create_role, list_roles, permissions_for_roles
from app.platform.auth_context import mint_hs256_token
from app.platform.models import Tenant
from app.platform.tenancy import DbRole, tenant_transaction

SEEDED = {"document:import", "exam:publish", "question:approve"}


def run(coro):
    return asyncio.run(coro)


async def _scalar(sql: str, **params):
    async with SessionLocal() as session:
        return (await session.execute(text(sql), params)).scalar()


@pytest.fixture(autouse=True)
def _permission_catalog():
    """The catalog is seeded by the migration; the suite-wide cleanup truncates it, so put it back."""

    async def seed():
        async with SessionLocal() as session, session.begin():
            for code in sorted(SEEDED):
                await session.execute(
                    text("INSERT INTO permissions (code, description) VALUES (:c, :c) ON CONFLICT DO NOTHING"),
                    {"c": code},
                )

    run(seed())


@pytest.fixture
def tenants() -> tuple[uuid.UUID, uuid.UUID]:
    async def create():
        suffix = uuid.uuid4().hex[:8]
        async with SessionLocal() as session, session.begin():
            a, b = Tenant(slug=f"acc-a-{suffix}", name="A"), Tenant(slug=f"acc-b-{suffix}", name="B")
            session.add_all([a, b])
            await session.flush()
            return a.id, b.id

    return run(create())


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _headers(tenant_id: uuid.UUID, roles: list[str], **extra: str) -> dict[str, str]:
    token = mint_hs256_token(
        secret=get_settings().secret_key,
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        membership_id=uuid.uuid4(),
        roles=roles,
        entitlement_tier="TEACHER_PRO",
        issued_at=int(time.time()),
    )
    return {"Authorization": f"Bearer {token}", **extra}


def _make_role(tenant_id: uuid.UUID, code: str, permissions: list[str]) -> uuid.UUID:
    async def create():
        async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
            return (
                await create_role(session, tenant_id=tenant_id, code=code, name=code, permission_codes=permissions)
            ).id

    return run(create())


# --- schema ------------------------------------------------------------------------------------


@pytest.mark.parametrize("table", ["roles", "role_permissions"])
def test_tenant_tables_force_rls_with_initplan(table):
    assert run(_scalar("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = :t", t=table))
    policy = run(
        _scalar("SELECT qual FROM pg_policies WHERE tablename = :t AND policyname = 'tenant_isolation_policy'", t=table)
    )
    assert "( SELECT current_app_tenant_id() AS current_app_tenant_id)" in policy


def test_permission_catalog_is_global_seeded_and_read_only_for_the_api():
    assert run(_scalar("SELECT relrowsecurity FROM pg_class WHERE relname = 'permissions'")) is False
    assert run(_scalar("SELECT has_table_privilege('ulp_app_rw', 'permissions', 'SELECT')"))
    for privilege in ("INSERT", "UPDATE", "DELETE"):
        assert not run(_scalar("SELECT has_table_privilege('ulp_app_rw', 'permissions', :p)", p=privilege))
    for role in ("ulp_exam_session_rw", "ulp_worker_ingestion", "ulp_worker_grading", "ulp_analytics_ro"):
        assert not run(_scalar("SELECT has_table_privilege(:r, 'roles', 'SELECT')", r=role)), role
    assert set(run(_scalar("SELECT array_agg(code) FROM permissions"))) >= SEEDED


def test_the_migration_seeds_exactly_the_codes_named_by_the_spec():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent / "alembic" / "versions" / "e2f3a4b5c6d7_f05_access_roles_permissions.py"
    )
    spec = importlib.util.spec_from_file_location("f05_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert {code for code, _ in migration.SEED_PERMISSIONS} == SEEDED


# --- POST /api/v1/roles --------------------------------------------------------------------------


def test_admin_creates_a_role_in_its_own_tenant(client, tenants):
    tenant_a, tenant_b = tenants
    response = client.post(
        "/api/v1/roles",
        headers=_headers(tenant_a, ["TENANT_ADMIN"]),
        json={"code": "head_teacher", "name": "Tổ trưởng", "permission_codes": ["exam:publish", "question:approve"]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["tenant_id"] == str(tenant_a) and body["code"] == "HEAD_TEACHER"
    assert body["permission_codes"] == ["exam:publish", "question:approve"]

    async def roles_of(tenant):
        async with tenant_transaction(tenant, role=DbRole.APP_RW) as session:
            return [r.code for r in await list_roles(session)]

    assert run(roles_of(tenant_a)) == ["HEAD_TEACHER"]
    assert run(roles_of(tenant_b)) == []


def test_the_body_cannot_choose_the_tenant(client, tenants):
    tenant_a, tenant_b = tenants
    response = client.post(
        "/api/v1/roles",
        headers=_headers(tenant_a, ["TENANT_ADMIN"]),
        json={"code": "EVIL", "name": "x", "tenant_id": str(tenant_b)},
    )
    assert response.status_code == 422
    assert run(_scalar("SELECT count(*) FROM roles")) == 0


def test_role_codes_are_unique_per_tenant_only(client, tenants):
    tenant_a, tenant_b = tenants
    payload = {"code": "REVIEWER", "name": "Reviewer"}
    assert client.post("/api/v1/roles", headers=_headers(tenant_a, ["ADMIN"]), json=payload).status_code == 201
    again = client.post("/api/v1/roles", headers=_headers(tenant_a, ["ADMIN"]), json=payload)
    assert again.status_code == 409 and again.headers["content-type"].startswith("application/problem+json")
    assert client.post("/api/v1/roles", headers=_headers(tenant_b, ["ADMIN"]), json=payload).status_code == 201


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "R1", "name": "x", "permission_codes": ["exam:publish", "root:everything"]},
        {"code": "bad code!", "name": "x"},
        {"code": "R2"},
    ],
)
def test_invalid_roles_are_rejected_and_nothing_is_written(client, tenants, payload):
    tenant_a, _ = tenants
    response = client.post("/api/v1/roles", headers=_headers(tenant_a, ["TENANT_ADMIN"]), json=payload)
    assert response.status_code == 422
    assert run(_scalar("SELECT count(*) FROM roles")) == 0
    assert run(_scalar("SELECT count(*) FROM role_permissions")) == 0


def test_only_administrators_create_roles(client, tenants):
    tenant_a, tenant_b = tenants
    payload = {"code": "X1", "name": "x"}
    assert client.post("/api/v1/roles", headers=_headers(tenant_a, ["TEACHER"]), json=payload).status_code == 403
    assert client.post("/api/v1/roles", json=payload).status_code == 401
    spoofed = _headers(tenant_a, ["TENANT_ADMIN"], **{"X-Tenant-Id": str(tenant_b)})
    assert client.post("/api/v1/roles", headers=spoofed, json=payload).status_code == 403
    assert run(_scalar("SELECT count(*) FROM roles")) == 0


# --- GET /api/v1/permissions/me --------------------------------------------------------------


def test_permissions_come_from_the_callers_tenant_roles_only(client, tenants):
    tenant_a, tenant_b = tenants
    _make_role(tenant_a, "TEACHER", ["exam:publish", "document:import"])
    _make_role(tenant_b, "TEACHER", ["question:approve"])
    _make_role(tenant_b, "ONLY_IN_B", ["exam:publish"])

    mine = client.get("/api/v1/permissions/me", headers=_headers(tenant_a, ["teacher", "ONLY_IN_B"]))
    assert mine.status_code == 200, mine.text
    body = mine.json()
    assert body["tenant_id"] == str(tenant_a) and body["roles"] == ["ONLY_IN_B", "TEACHER"]
    # ONLY_IN_B is a role of tenant B: carrying its code in tenant A grants nothing.
    assert body["permissions"] == ["document:import", "exam:publish"]
    assert body["entitlement_tier"] == "TEACHER_PRO"

    theirs = client.get("/api/v1/permissions/me", headers=_headers(tenant_b, ["TEACHER"])).json()
    assert theirs["permissions"] == ["question:approve"]


def test_permissions_me_requires_a_valid_token(client, tenants):
    tenant_a, _ = tenants
    assert client.get("/api/v1/permissions/me").status_code == 401
    assert client.get("/api/v1/permissions/me", headers={"Authorization": "Bearer abc.def.ghi"}).status_code == 401
    forged = _headers(tenant_a, ["TEACHER"])
    forged["Authorization"] = forged["Authorization"][:-4] + "AAAA"
    assert client.get("/api/v1/permissions/me", headers=forged).status_code == 401


# --- the database itself, as the runtime role ----------------------------------------------------


def test_tenant_b_cannot_read_or_change_tenant_a_roles_in_the_database(tenants):
    tenant_a, tenant_b = tenants
    role_a = _make_role(tenant_a, "SECRET_ROLE", ["exam:publish"])

    async def as_b():
        async with tenant_transaction(tenant_b, role=DbRole.APP_RW) as session:
            seen = (await session.execute(text("SELECT count(*) FROM roles WHERE id = :id"), {"id": role_a})).scalar()
            grants = (await session.execute(text("SELECT count(*) FROM role_permissions"))).scalar()
            updated = (
                await session.execute(text("UPDATE roles SET name = 'pwned' WHERE id = :id"), {"id": role_a})
            ).rowcount
            deleted = (
                await session.execute(text("DELETE FROM role_permissions WHERE role_id = :id"), {"id": role_a})
            ).rowcount
            permissions = await permissions_for_roles(session, ["SECRET_ROLE"])
            return seen, grants, updated, deleted, permissions

    assert run(as_b()) == (0, 0, 0, 0, frozenset())
    assert run(_scalar("SELECT name FROM roles WHERE id = :id", id=role_a)) == "SECRET_ROLE"
    assert run(_scalar("SELECT count(*) FROM role_permissions WHERE role_id = :id", id=role_a)) == 1


def test_tenant_b_cannot_write_rows_for_tenant_a(tenants):
    tenant_a, tenant_b = tenants
    role_a = _make_role(tenant_a, "A_ROLE", [])

    async def insert_role_for_a():
        async with tenant_transaction(tenant_b, role=DbRole.APP_RW) as session:
            await session.execute(
                text("INSERT INTO roles (tenant_id, code, name) VALUES (:t, 'X', 'x')"), {"t": tenant_a}
            )

    async def grant_on_a_role(claimed_tenant):
        async with tenant_transaction(tenant_b, role=DbRole.APP_RW) as session:
            await session.execute(
                text(
                    "INSERT INTO role_permissions (role_id, tenant_id, permission_code) VALUES (:r, :t, 'exam:publish')"
                ),
                {"r": role_a, "t": claimed_tenant},
            )

    with pytest.raises(DBAPIError, match="row-level security"):
        run(insert_role_for_a())
    with pytest.raises(DBAPIError, match="row-level security"):
        run(grant_on_a_role(tenant_a))
    with pytest.raises(IntegrityError):  # (role_a, tenant_b) is no role: the composite FK refuses it
        run(grant_on_a_role(tenant_b))
    assert run(_scalar("SELECT count(*) FROM role_permissions")) == 0


def test_without_a_tenant_the_runtime_role_sees_nothing(tenants):
    tenant_a, _ = tenants
    _make_role(tenant_a, "ANY", ["exam:publish"])

    async def no_context():
        async with SessionLocal() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE ulp_app_rw"))
            return (
                (await session.execute(text("SELECT count(*) FROM roles"))).scalar(),
                (await session.execute(text("SELECT count(*) FROM role_permissions"))).scalar(),
            )

    assert run(no_context()) == (0, 0)


def test_create_role_rolls_back_entirely_on_an_unknown_permission(tenants):
    tenant_a, _ = tenants

    async def scenario():
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            await create_role(session, tenant_id=tenant_a, code="BROKEN", name="x", permission_codes=["nope:nope"])

    with pytest.raises(ValueError, match="nope:nope"):
        run(scenario())
    assert run(_scalar("SELECT count(*) FROM roles")) == 0
