"""M0.2: the database foundation every V11.3 module builds on (roles, UUIDv7, tenant context, RLS)."""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import SessionLocal, engine
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID, Tenant
from app.platform.rls import GLOBAL_TABLES, LEGACY_TABLES, RLS_EXEMPT_TABLES, strict_tenant_rls
from app.platform.tenancy import (
    DbRole,
    SystemJob,
    set_system_job,
    set_tenant,
    system_job_transaction,
    tenant_transaction,
)

RUNTIME_ROLES = [role.value for role in DbRole]
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def run(coro):
    return asyncio.run(coro)


async def _scalar(sql: str, **params):
    async with SessionLocal() as session:
        return (await session.execute(text(sql), params)).scalar()


async def _rows(sql: str, **params):
    async with SessionLocal() as session:
        return (await session.execute(text(sql), params)).all()


# --- roles ----------------------------------------------------------------------------------


def test_seven_roles_exist_and_none_can_bypass_rls():
    rows = run(
        _rows(
            "SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
            "FROM pg_roles WHERE starts_with(rolname, 'ulp_')"
        )
    )
    by_name = {r.rolname: r for r in rows}
    assert set(by_name) == {*RUNTIME_ROLES, "ulp_migrator"}
    for name in RUNTIME_ROLES:
        role = by_name[name]
        assert not role.rolsuper and not role.rolbypassrls, f"{name} must never bypass RLS"
        assert not role.rolcreaterole and not role.rolcreatedb


def test_runtime_roles_cannot_create_objects_in_the_schema():
    for name in RUNTIME_ROLES:
        assert run(_scalar("SELECT has_schema_privilege(:r, 'public', 'USAGE')", r=name)) is True
        assert run(_scalar("SELECT has_schema_privilege(:r, 'public', 'CREATE')", r=name)) is False


def test_only_the_api_role_writes_tenants():
    for name in RUNTIME_ROLES:
        can_insert = run(_scalar("SELECT has_table_privilege(:r, 'tenants', 'INSERT')", r=name))
        assert can_insert is (name == DbRole.APP_RW.value)
        assert run(_scalar("SELECT has_table_privilege(:r, 'tenants', 'DELETE')", r=name)) is False
    assert run(_scalar("SELECT has_table_privilege('ulp_app_rw', 'fts_dictionary_versions', 'UPDATE')")) is False


# --- UUIDv7 ---------------------------------------------------------------------------------


def test_uuid_v7_is_version_7_and_sorts_by_creation_time():
    async def generate():
        async with SessionLocal() as session:
            first = (await session.execute(text("SELECT uuid_generate_v7()"))).scalar()
            await session.execute(text("SELECT pg_sleep(0.005)"))
            second = (await session.execute(text("SELECT uuid_generate_v7()"))).scalar()
            return first, second

    first, second = run(generate())
    assert first.version == 7 and second.version == 7
    assert first.variant == uuid.RFC_4122
    assert first < second, "ids created later sort later (B-tree friendly)"


def test_tenant_ids_default_to_uuid_v7():
    async def create():
        async with SessionLocal() as session, session.begin():
            tenant = Tenant(slug="truong-thpt-a", name="Trường THPT A")
            session.add(tenant)
            await session.flush()
            await session.refresh(tenant)
            return tenant

    tenant = run(create())
    assert tenant.id.version == 7
    assert tenant.status == "ACTIVE" and tenant.settings_json == {}


def test_tenant_status_is_checked_by_the_database():
    async def create():
        async with SessionLocal() as session, session.begin():
            session.add(Tenant(slug="bad", name="Bad", status="DELETED"))

    with pytest.raises(DBAPIError, match="tenants_status_check"):
        run(create())


def test_the_system_tenant_is_seeded():
    assert run(_scalar("SELECT slug FROM tenants WHERE id = system_public_tenant_id()")) == "system-public"
    assert run(_scalar("SELECT system_public_tenant_id()")) == SYSTEM_PUBLIC_TENANT_ID


# --- tenant context -------------------------------------------------------------------------


def test_tenant_context_is_null_until_set_and_ends_with_the_transaction():
    tenant_id = uuid.uuid4()

    async def scenario():
        async with engine.connect() as conn:  # one physical connection, as a pooler would reuse it
            before = (await conn.execute(text("SELECT current_app_tenant_id()"))).scalar()
            await conn.rollback()
            async with conn.begin():
                await conn.execute(
                    text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(tenant_id)}
                )
                inside = (await conn.execute(text("SELECT current_app_tenant_id()"))).scalar()
            after = (await conn.execute(text("SELECT current_app_tenant_id()"))).scalar()
            return before, inside, after

    before, inside, after = run(scenario())
    assert before is None
    assert inside == tenant_id
    assert after is None, "the next transaction on the same connection must not inherit the tenant"


def test_tenant_transaction_sets_tenant_and_role():
    tenant_id = uuid.uuid4()

    async def scenario():
        async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
            return (await session.execute(text("SELECT current_app_tenant_id(), current_user, app_system_job()"))).one()

    current_tenant, current_user, job = run(scenario())
    assert current_tenant == tenant_id
    assert current_user == "ulp_app_rw"
    assert job is None


def test_system_job_transaction_names_the_job_and_no_tenant():
    async def scenario():
        async with system_job_transaction(SystemJob.OUTBOX_RELAY) as session:
            return (await session.execute(text("SELECT app_system_job(), current_user, current_app_tenant_id()"))).one()

    assert tuple(run(scenario())) == ("outbox_relay", "ulp_system_scheduler", None)


def test_set_tenant_and_set_system_job_are_transaction_local():
    async def scenario():
        async with SessionLocal() as session:
            async with session.begin():
                await set_tenant(session, SYSTEM_PUBLIC_TENANT_ID)
                await set_system_job(session, SystemJob.METRIC_ROLLUP)
            return (await session.execute(text("SELECT current_app_tenant_id(), app_system_job()"))).one()

    assert tuple(run(scenario())) == (None, None)


# --- row-level security ---------------------------------------------------------------------


@pytest.fixture
def probe_table():
    """A throwaway tenant table with the standard strict policy, as a module migration would create it."""

    async def create():
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS rls_probe"))
            await conn.execute(
                text(
                    "CREATE TABLE rls_probe (id UUID PRIMARY KEY DEFAULT uuid_generate_v7(), "
                    "tenant_id UUID NOT NULL REFERENCES tenants(id), note TEXT NOT NULL)"
                )
            )
            for statement in strict_tenant_rls("rls_probe"):
                await conn.execute(text(statement))
            await conn.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON rls_probe TO ulp_app_rw"))
            await conn.execute(text("GRANT SELECT ON rls_probe TO ulp_system_scheduler"))

    async def drop():
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS rls_probe"))

    run(create())
    yield
    run(drop())


async def _two_tenants() -> tuple[uuid.UUID, uuid.UUID]:
    async with SessionLocal() as session, session.begin():
        a, b = Tenant(slug="tenant-a", name="A"), Tenant(slug="tenant-b", name="B")
        session.add_all([a, b])
        await session.flush()
        return a.id, b.id


def test_a_tenant_sees_and_changes_only_its_own_rows(probe_table):
    async def scenario():
        tenant_a, tenant_b = await _two_tenants()
        for tenant_id, note in ((tenant_a, "of A"), (tenant_b, "of B")):
            async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
                await session.execute(
                    text("INSERT INTO rls_probe (tenant_id, note) VALUES (:t, :n)"), {"t": tenant_id, "n": note}
                )
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            seen = (await session.execute(text("SELECT note FROM rls_probe"))).scalars().all()
            updated = (await session.execute(text("UPDATE rls_probe SET note = 'hacked' WHERE note = 'of B'"))).rowcount
            deleted = (await session.execute(text("DELETE FROM rls_probe WHERE note = 'of B'"))).rowcount
        async with tenant_transaction(tenant_b, role=DbRole.APP_RW) as session:
            still_there = (await session.execute(text("SELECT note FROM rls_probe"))).scalars().all()
        return seen, updated, deleted, still_there

    seen, updated, deleted, still_there = run(scenario())
    assert seen == ["of A"]
    assert (updated, deleted) == (0, 0)
    assert still_there == ["of B"]


def test_a_tenant_cannot_write_a_row_for_another_tenant(probe_table):
    async def scenario():
        tenant_a, tenant_b = await _two_tenants()
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            await session.execute(
                text("INSERT INTO rls_probe (tenant_id, note) VALUES (:t, 'smuggled')"), {"t": tenant_b}
            )

    with pytest.raises(DBAPIError, match="row-level security"):
        run(scenario())


def test_without_a_tenant_context_nothing_is_visible(probe_table):
    async def scenario():
        tenant_a, _ = await _two_tenants()
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            await session.execute(text("INSERT INTO rls_probe (tenant_id, note) VALUES (:t, 'x')"), {"t": tenant_a})
        async with SessionLocal() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE ulp_app_rw"))
            no_context = (await session.execute(text("SELECT count(*) FROM rls_probe"))).scalar()
        async with system_job_transaction(SystemJob.OUTBOX_RELAY) as session:
            scheduler = (await session.execute(text("SELECT count(*) FROM rls_probe"))).scalar()
        return no_context, scheduler

    no_context, scheduler = run(scenario())
    assert no_context == 0, "fail closed: no tenant, no rows"
    assert scheduler == 0, "a system job sees a table only through a policy written for that job"


def test_every_table_is_under_forced_rls_or_explicitly_exempt(probe_table):
    """GATE-01: a new table without FORCE ROW LEVEL SECURITY fails here unless it is a known global table."""
    rows = run(
        _rows(
            "SELECT c.relname, c.relrowsecurity AND c.relforcerowsecurity AS forced "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')"
        )
    )
    tables = {r.relname: r.forced for r in rows}
    assert tables["rls_probe"] is True
    unprotected = sorted(name for name, forced in tables.items() if not forced and name not in RLS_EXEMPT_TABLES)
    assert not unprotected, f"enable and force RLS (app.platform.rls) on: {unprotected}"
    stale = sorted(RLS_EXEMPT_TABLES - set(tables))
    assert not stale, f"remove from the exemption lists, the tables no longer exist: {stale}"
    assert not (GLOBAL_TABLES & LEGACY_TABLES)


# --- full-text search dictionary --------------------------------------------------------------


def test_f_unaccent_folds_vietnamese():
    assert run(_scalar("SELECT f_unaccent('Công thức Bayes, dao động tắt dần, Đường tròn')")) == (
        "Cong thuc Bayes, dao dong tat dan, Duong tron"
    )


def test_the_pinned_dictionary_fingerprint_matches_the_server():
    pinned = run(_scalar("SELECT rules_sha256 FROM fts_dictionary_versions WHERE dict_name = 'public.unaccent'"))
    assert pinned == run(_scalar("SELECT fts_unaccent_fingerprint()"))
    assert pinned != EMPTY_SHA256, "the pin must describe the dictionary, not be the hash of nothing"


# --- legacy schema drift ----------------------------------------------------------------------


def test_enum_check_constraints_allow_exactly_what_the_models_declare():
    """Alembic does not compare CHECK constraints; a value added to an enum needs a migration too."""
    import re

    import sqlalchemy as sa

    from app.db import Base

    rows = run(
        _rows(
            "SELECT conrelid::regclass::text AS tbl, conname, pg_get_constraintdef(oid) AS definition "
            "FROM pg_constraint WHERE contype = 'c' AND connamespace = 'public'::regnamespace"
        )
    )
    in_db = {(r.tbl, r.conname): set(re.findall(r"'([^']*)'", r.definition)) for r in rows}
    problems = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            kind = column.type
            if isinstance(kind, sa.Enum) and kind.create_constraint:
                allowed = in_db.get((table.name, kind.name))
                if allowed != set(kind.enums):
                    problems.append(f"{table.name}.{column.name}: database allows {allowed}, model {set(kind.enums)}")
    assert not problems, problems
