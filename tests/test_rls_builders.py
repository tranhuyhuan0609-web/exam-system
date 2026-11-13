"""W0.2 (H-01, H-03): the policy builders of app.platform.rls, exercised on probe tables.

Module migrations build their row-level security only with these builders, so what holds for the
probe tables here holds for every shared-material, answer, job and partitioned table to come.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import SessionLocal, engine
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID, Tenant
from app.platform.rls import (
    FROZEN_VERSION,
    grading_read,
    hybrid_read_child,
    hybrid_read_root,
    hybrid_write_rls,
    invoker_view,
    partition_lockdown,
    strict_tenant_rls,
    system_job_policy,
)
from app.platform.tenancy import DbRole, SystemJob, system_job_transaction, tenant_transaction

SYSTEM = str(SYSTEM_PUBLIC_TENANT_ID)
PROBE_TABLES = ("pub_probe_options", "pub_probe_versions", "pub_probe_items", "key_probe", "job_probe", "part_probe")

PROBE_DDL = [
    "DROP VIEW IF EXISTS v_pub_probe_versions",
    *[f"DROP TABLE IF EXISTS {t} CASCADE" for t in PROBE_TABLES],
    """CREATE TABLE pub_probe_items (
           id UUID PRIMARY KEY DEFAULT uuid_generate_v7(), tenant_id UUID NOT NULL REFERENCES tenants(id),
           code TEXT NOT NULL, visibility_scope TEXT NOT NULL DEFAULT 'TENANT_PRIVATE',
           publication_status TEXT NOT NULL DEFAULT 'DRAFT', rights_status TEXT NOT NULL DEFAULT 'PENDING_REVIEW')""",
    """CREATE TABLE pub_probe_versions (
           id UUID PRIMARY KEY DEFAULT uuid_generate_v7(), tenant_id UUID NOT NULL REFERENCES tenants(id),
           item_id UUID NOT NULL REFERENCES pub_probe_items(id), body TEXT NOT NULL,
           is_immutable BOOLEAN NOT NULL DEFAULT FALSE)""",
    """CREATE TABLE pub_probe_options (
           id UUID PRIMARY KEY DEFAULT uuid_generate_v7(), tenant_id UUID NOT NULL REFERENCES tenants(id),
           version_id UUID NOT NULL REFERENCES pub_probe_versions(id), label TEXT NOT NULL)""",
    """CREATE TABLE key_probe (
           id UUID PRIMARY KEY DEFAULT uuid_generate_v7(), tenant_id UUID NOT NULL REFERENCES tenants(id),
           answer TEXT NOT NULL)""",
    """CREATE TABLE job_probe (
           id UUID PRIMARY KEY DEFAULT uuid_generate_v7(), tenant_id UUID NOT NULL REFERENCES tenants(id),
           status TEXT NOT NULL DEFAULT 'PENDING')""",
    """CREATE TABLE part_probe (
           id UUID NOT NULL DEFAULT uuid_generate_v7(), tenant_id UUID NOT NULL REFERENCES tenants(id),
           at TIMESTAMPTZ NOT NULL, PRIMARY KEY (id, at)) PARTITION BY RANGE (at)""",
    "CREATE TABLE part_probe_2026 PARTITION OF part_probe FOR VALUES FROM ('2026-01-01') TO ('2027-01-01')",
    *hybrid_write_rls("pub_probe_items"),
    *hybrid_read_root("pub_probe_items"),
    *hybrid_write_rls("pub_probe_versions"),
    *hybrid_read_child("pub_probe_versions", "pub_probe_items", "item_id", FROZEN_VERSION),
    *hybrid_write_rls("pub_probe_options"),
    *hybrid_read_child("pub_probe_options", "pub_probe_versions", "version_id"),
    *strict_tenant_rls("key_probe"),
    *grading_read("key_probe", "grading_read_key_probe"),
    *strict_tenant_rls("job_probe"),
    *system_job_policy("job_probe", "sys_job_probe", "outbox_relay", "SELECT", "status = 'PENDING'"),
    *strict_tenant_rls("part_probe"),
    "GRANT SELECT, INSERT, UPDATE, DELETE ON pub_probe_items, pub_probe_versions, pub_probe_options TO ulp_app_rw",
    "GRANT SELECT, INSERT ON key_probe, job_probe, part_probe, part_probe_2026 TO ulp_app_rw",
    "GRANT SELECT ON key_probe TO ulp_worker_grading",
    "GRANT SELECT ON job_probe TO ulp_system_scheduler",
    *partition_lockdown("part_probe_2026"),  # after the grant above, which it has to undo
    invoker_view("v_pub_probe_versions", "SELECT id, tenant_id, item_id, body FROM pub_probe_versions"),
    "GRANT SELECT ON v_pub_probe_versions TO ulp_app_rw",
]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def probes():
    """Probe tables built only with the builders, the way module migrations will build real ones."""

    async def create():
        async with engine.begin() as conn:
            for statement in PROBE_DDL:
                await conn.execute(text(statement))

    async def drop():
        async with engine.begin() as conn:
            await conn.execute(text("DROP VIEW IF EXISTS v_pub_probe_versions"))
            for table in PROBE_TABLES:
                await conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))

    run(create())
    yield
    run(drop())


async def _two_tenants() -> tuple[uuid.UUID, uuid.UUID]:
    async with SessionLocal() as session, session.begin():
        a, b = Tenant(slug="tenant-a", name="A"), Tenant(slug="tenant-b", name="B")
        session.add_all([a, b])
        await session.flush()
        return a.id, b.id


SEED_ITEMS = (
    # code, owner, visibility_scope, publication_status, rights_status
    ("published", "system", "SYSTEM_PUBLIC", "PUBLISHED", "OWNED"),
    ("draft", "system", "SYSTEM_PUBLIC", "DRAFT", "OWNED"),
    ("taken-down", "system", "SYSTEM_PUBLIC", "TAKEN_DOWN", "TAKEDOWN"),
    ("pending-rights", "system", "SYSTEM_PUBLIC", "PUBLISHED", "PENDING_REVIEW"),
    ("private-a", "a", "TENANT_PRIVATE", "DRAFT", "OWNED"),
)


async def _seed(tenant_a: uuid.UUID) -> None:
    """Each item gets a frozen version and an option, so only the item's own state can hide them."""
    owners = {"system": SYSTEM, "a": str(tenant_a)}
    async with SessionLocal() as session, session.begin():
        for code, owner, scope, publication, rights in SEED_ITEMS:
            tenant = owners[owner]
            item = await session.scalar(
                text(
                    "INSERT INTO pub_probe_items (tenant_id, code, visibility_scope, publication_status, rights_status)"
                    " VALUES (:t, :c, :s, :p, :r) RETURNING id"
                ),
                {"t": tenant, "c": code, "s": scope, "p": publication, "r": rights},
            )
            version = await session.scalar(
                text(
                    "INSERT INTO pub_probe_versions (tenant_id, item_id, body, is_immutable)"
                    " VALUES (:t, :i, :b, TRUE) RETURNING id"
                ),
                {"t": tenant, "i": item, "b": code},
            )
            await session.execute(
                text("INSERT INTO pub_probe_options (tenant_id, version_id, label) VALUES (:t, :v, :l)"),
                {"t": tenant, "v": version, "l": code},
            )
        # The published item's working copy, not frozen yet.
        await session.execute(
            text(
                "INSERT INTO pub_probe_versions (tenant_id, item_id, body, is_immutable)"
                " SELECT tenant_id, id, 'published-unfrozen', FALSE FROM pub_probe_items WHERE code = 'published'"
            )
        )
        for tenant, answer in ((SYSTEM, "system key"), (str(tenant_a), "key of A")):
            await session.execute(
                text("INSERT INTO key_probe (tenant_id, answer) VALUES (:t, :a)"), {"t": tenant, "a": answer}
            )


QUERIES = (
    "SELECT code FROM pub_probe_items",
    "SELECT body FROM pub_probe_versions",
    "SELECT label FROM pub_probe_options",
    "SELECT body FROM v_pub_probe_versions",
)


def test_public_material_shows_only_published_cleared_rows_and_their_children(probes):
    async def scenario():
        tenant_a, tenant_b = await _two_tenants()
        await _seed(tenant_a)
        seen = {}
        for name, tenant in (("a", tenant_a), ("b", tenant_b)):
            async with tenant_transaction(tenant, role=DbRole.APP_RW) as session:
                seen[name] = [set((await session.execute(text(sql))).scalars().all()) for sql in QUERIES]
        return seen

    seen = run(scenario())
    # Not the draft, the taken-down one, the one whose rights are pending, the unfrozen copy, nor A's.
    assert seen["b"] == [{"published"}] * 4
    assert seen["a"] == [{"published", "private-a"}] * 4


def test_a_tenant_cannot_write_shared_material_into_the_system_tenant(probes):
    async def scenario():
        tenant_a, _ = await _two_tenants()
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            await session.execute(
                text("INSERT INTO pub_probe_items (tenant_id, code) VALUES (:t, 'forged')"), {"t": SYSTEM}
            )

    with pytest.raises(DBAPIError, match="row-level security"):
        run(scenario())


def test_only_the_grading_worker_reads_system_answers(probes):
    async def scenario():
        tenant_a, tenant_b = await _two_tenants()
        await _seed(tenant_a)
        seen = {}
        for name, role in (("api", DbRole.APP_RW), ("grading", DbRole.WORKER_GRADING)):
            async with tenant_transaction(tenant_b, role=role) as session:
                seen[name] = set((await session.execute(text("SELECT answer FROM key_probe"))).scalars().all())
        return seen

    seen = run(scenario())
    assert seen["api"] == set()
    assert seen["grading"] == {"system key"}  # never tenant A's


def test_a_system_job_policy_opens_a_table_to_that_job_only(probes):
    async def scenario():
        tenant_a, tenant_b = await _two_tenants()
        async with SessionLocal() as session, session.begin():
            for tenant, status in ((tenant_a, "PENDING"), (tenant_b, "PENDING"), (tenant_b, "DONE")):
                await session.execute(
                    text("INSERT INTO job_probe (tenant_id, status) VALUES (:t, :s)"), {"t": tenant, "s": status}
                )
        counts = {}
        for job in (SystemJob.OUTBOX_RELAY, SystemJob.METRIC_ROLLUP):
            async with system_job_transaction(job) as session:
                counts[job] = (await session.execute(text("SELECT count(*) FROM job_probe"))).scalar()
        return counts

    counts = run(scenario())
    assert counts[SystemJob.OUTBOX_RELAY] == 2  # both tenants, pending rows only
    assert counts[SystemJob.METRIC_ROLLUP] == 0


def test_system_job_policy_rejects_names_that_are_not_identifiers():
    with pytest.raises(ValueError):
        system_job_policy("t", "p", "x' OR '1'='1")
    with pytest.raises(ValueError):
        system_job_policy("t", "p", "outbox_relay", "TRUNCATE")


def test_rows_reach_a_locked_down_partition_through_the_parent(probes):
    async def scenario():
        tenant_a, tenant_b = await _two_tenants()
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            await session.execute(
                text("INSERT INTO part_probe (tenant_id, at) VALUES (:t, '2026-06-01')"), {"t": tenant_a}
            )
        counts = []
        for tenant in (tenant_a, tenant_b):
            async with tenant_transaction(tenant, role=DbRole.APP_RW) as session:
                counts.append((await session.execute(text("SELECT count(*) FROM part_probe"))).scalar())
        return counts

    assert run(scenario()) == [1, 0]


def test_a_locked_down_partition_cannot_be_queried_directly(probes):
    async def scenario():
        _, tenant_b = await _two_tenants()
        async with tenant_transaction(tenant_b, role=DbRole.APP_RW) as session:
            await session.execute(text("SELECT count(*) FROM part_probe_2026"))

    with pytest.raises(DBAPIError, match="permission denied"):
        run(scenario())


def test_every_builder_reads_the_tenant_through_an_initplan():
    statements = [
        *strict_tenant_rls("t"),
        *hybrid_write_rls("t"),
        *hybrid_read_root("t"),
        *hybrid_read_child("t", "p", "p_id", FROZEN_VERSION),
        *grading_read("t", "g"),
        *partition_lockdown("t_2026"),
    ]
    for statement in statements:
        bare = statement.replace("(SELECT current_app_tenant_id())", "")
        assert "current_app_tenant_id()" not in bare, statement
