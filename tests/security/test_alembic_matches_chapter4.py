"""Gate 2b security audit: verify the live Alembic schema matches Chapter 4 & grants_manifest.csv.

Unlike `test_ddl_dry_run.py` (which applies the raw Chapter 4 SQL specification directly), this
suite creates an isolated scratch PostgreSQL database, runs `alembic upgrade head`, and audits
the resulting live catalog, RLS policies, roles, partitions (F-01/F-05), views (F-02), triggers
(F-03), SECURITY DEFINER helpers, and `information_schema.role_table_grants` /
`role_column_grants` against `docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql`
and `tests/security/grants_manifest.csv`.

As Domain/Migration waves finish bringing all 123 logical tables into Alembic, full-schema Gate 2b
parity and `tests/security/verify_rls_hardening.sql` behavioral verification engage automatically.
Set `ULP_REQUIRE_FULL_CHAPTER4=1` to fail strictly if any of the 123 logical tables is still missing.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from app.ops.alembic_schema_audit import (
    ALL_MANAGED_ROLE_NAMES,
    ANSWER_DENIED_ROLES,
    ANSWER_TABLES,
    EXPECTED_CHAPTER4_LOGICAL_TABLE_COUNT,
    EXPECTED_CHAPTER4_SECURITY_VIEWS,
    FOUNDATION_MANIFEST_GRANT_DELTAS,
    MANIFEST_ROLE_NAMES,
    RUNTIME_ROLE_NAMES,
    Chapter4Spec,
    LivePolicy,
    LiveRelation,
    LiveSchemaSnapshot,
    audit_live_alembic_schema,
    inspect_live_schema,
    load_chapter4_spec,
    reconcile_live_schema,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SEED_FIXTURE = HERE / "seed_rls_fixture.sql"
VERIFY_HARDENING = HERE / "verify_rls_hardening.sql"

ALEMBIC_AUDIT_DB = "ulp_alembic_gate2b"

SYSTEM_TENANT = "00000000-0000-0000-0000-000000000000"
TENANT_A = "aaaaaaaa-0000-0000-0000-000000000001"
TENANT_B = "bbbbbbbb-0000-0000-0000-000000000002"
USER_A = "10000000-0000-0000-0000-000000000001"
USER_B = "20000000-0000-0000-0000-000000000002"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _base_db_url() -> Any:
    raw = os.environ.get("DATABASE_URL") or os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://exam:exam@127.0.0.1:5432/exam_test?ssl=disable",
    )
    return make_url(raw)


async def _connect(database: str) -> asyncpg.Connection:
    url = _base_db_url()
    ssl_opt: Any = False if url.query.get("ssl") == "disable" else None
    return await asyncpg.connect(
        user=url.username,
        password=url.password,
        host=url.host or "127.0.0.1",
        port=url.port or 5432,
        database=database,
        ssl=ssl_opt,
    )


async def _prepare_clean_audit_db() -> None:
    admin = await _connect("postgres")
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{ALEMBIC_AUDIT_DB}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{ALEMBIC_AUDIT_DB}"')
    finally:
        await admin.close()

    conn = await _connect(ALEMBIC_AUDIT_DB)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
    finally:
        await conn.close()


async def _drop_audit_db() -> None:
    admin = await _connect("postgres")
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{ALEMBIC_AUDIT_DB}" WITH (FORCE)')
    finally:
        await admin.close()


def _run_alembic_upgrade_head() -> None:
    audit_url = _base_db_url().set(database=ALEMBIC_AUDIT_DB).render_as_string(hide_password=False)
    env = {**os.environ, "DATABASE_URL": audit_url}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"alembic upgrade head failed on isolated DB `{ALEMBIC_AUDIT_DB}`:\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )


@pytest.fixture(scope="module")
def migrated_audit_state() -> Any:
    """Build an isolated database via `alembic upgrade head` once for the module and drop on teardown."""
    _run(_prepare_clean_audit_db())
    try:
        _run_alembic_upgrade_head()

        async def _capture() -> tuple[Chapter4Spec, LiveSchemaSnapshot]:
            conn = await _connect(ALEMBIC_AUDIT_DB)
            try:
                return load_chapter4_spec(), await inspect_live_schema(conn)
            finally:
                await conn.close()

        spec, snapshot = _run(_capture())
        yield spec, snapshot
    finally:
        _run(_drop_audit_db())


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Override session-wide exam_test truncation; this suite isolates itself in ALEMBIC_AUDIT_DB."""
    return


def _require_full_flag() -> bool | None:
    raw = os.environ.get("ULP_REQUIRE_FULL_CHAPTER4")
    if raw == "1":
        return True
    if raw == "0":
        return False
    return None


async def _run_in_savepoint_as_role(
    conn: asyncpg.Connection,
    role: str,
    sql: str,
    *args: Any,
    tenant: str | None = None,
    user_id: str | None = None,
    job: str | None = None,
) -> list[asyncpg.Record]:
    async with conn.transaction():
        await conn.execute(f"SET LOCAL ROLE {role}")
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant or "")
        await conn.execute("SELECT set_config('app.current_user_id', $1, true)", user_id or "")
        await conn.execute("SELECT set_config('app.system_job', $1, true)", job or "")
        return await conn.fetch(sql, *args)


# --- 1. Reconciler helper unit checks --------------------------------------------------------


def test_chapter4_spec_extracts_exact_123_tables_and_views(migrated_audit_state: Any) -> None:
    spec, _ = migrated_audit_state
    assert len(spec.logical_tables) == EXPECTED_CHAPTER4_LOGICAL_TABLE_COUNT
    assert len(spec.strict_tenant_tables) == 82
    assert len(spec.hybrid_public_tables) == 21
    assert len(spec.tenantless_tables) == 20
    assert spec.security_views == EXPECTED_CHAPTER4_SECURITY_VIEWS
    assert spec.ddl_partitions == {"audit_events_2026_11", "audit_events_default", "response_revisions_default"}
    assert len(spec.manifest_objects) == 53
    assert len(spec.manifest_grants) == 154


def test_reconciler_catches_rls_initplan_role_partition_view_and_grant_regressions(migrated_audit_state: Any) -> None:
    spec, base = migrated_audit_state

    # (a) Dropping FORCE RLS on a tenant table
    mutated_relations = tuple(
        dataclasses.replace(r, rls_forced=False) if r.name == "outbox_events" else r for r in base.relations
    )
    rls_report = reconcile_live_schema(dataclasses.replace(base, relations=mutated_relations), spec)
    assert not rls_report.passed
    assert "outbox_events" in rls_report.missing_rls_force

    # (b) Bare current_app_tenant_id() call without InitPlan (SELECT ...)
    mutated_policies = tuple(
        dataclasses.replace(p, qual="(tenant_id = current_app_tenant_id())")
        if p.table_name == "roles" and p.policy_name == "tenant_isolation_policy"
        else p
        for p in base.policies
    )
    initplan_report = reconcile_live_schema(dataclasses.replace(base, policies=mutated_policies), spec)
    assert not initplan_report.passed
    assert any("roles.tenant_isolation_policy" in msg for msg in initplan_report.initplan_violations)

    # (c) Permissive USING (true) policy on runtime roles
    bad_policy = LivePolicy(
        table_name="roles",
        policy_name="oops_open",
        roles=("public",),
        cmd="SELECT",
        qual="true",
        with_check="",
    )
    true_report = reconcile_live_schema(dataclasses.replace(base, policies=(*base.policies, bad_policy)), spec)
    assert not true_report.passed
    assert any("roles.oops_open" in msg for msg in true_report.permissive_policy_violations)

    # (d) Role with rolbypassrls=true or CREATE on schema public
    bad_roles = tuple(
        dataclasses.replace(r, bypassrls=True, schema_create=True) if r.name == "ulp_app_rw" else r
        for r in base.roles
    )
    role_report = reconcile_live_schema(dataclasses.replace(base, roles=bad_roles), spec)
    assert not role_report.passed
    assert "ulp_app_rw: rolbypassrls=true" in role_report.role_violations
    assert "ulp_app_rw: has CREATE on schema public" in role_report.schema_privilege_violations

    # (e) F-01 direct grant on a partition
    part_name = next(r.name for r in base.relations if r.is_partition)
    part_report = reconcile_live_schema(
        dataclasses.replace(
            base,
            partition_effective_privileges=(*base.partition_effective_privileges, ("ulp_app_rw", part_name, "SELECT")),
        ),
        spec,
    )
    assert not part_report.passed
    assert any(part_name in msg for msg in part_report.partition_violations)

    # (f) F-02 view missing security_invoker=true
    bad_views = tuple(
        dataclasses.replace(r, reloptions=()) if r.relkind == "v" else r for r in base.relations
    )
    view_report = reconcile_live_schema(dataclasses.replace(base, relations=bad_views), spec)
    assert not view_report.passed
    assert any("v_users_status" in msg for msg in view_report.view_violations)

    # (g) Unexpected extra grant vs grants_manifest.csv
    extra_grant_report = reconcile_live_schema(
        dataclasses.replace(
            base,
            manifest_table_grants=base.manifest_table_grants | {("ulp_analytics_ro", "roles", "SELECT")},
        ),
        spec,
    )
    assert not extra_grant_report.passed
    assert "ulp_analytics_ro:roles:SELECT:" in extra_grant_report.extra_manifest_grants

    # (h) Column-level grant widened to whole table
    widened_report = reconcile_live_schema(
        dataclasses.replace(
            base,
            manifest_table_grants=base.manifest_table_grants | {("ulp_system_scheduler", "outbox_events", "UPDATE")},
        ),
        spec,
    )
    assert not widened_report.passed
    assert any("outbox_events" in msg and "UPDATE" in msg for msg in widened_report.column_grant_sync_violations)


def test_reconciler_auto_enforces_123_tables_when_complete(migrated_audit_state: Any) -> None:
    spec, base = migrated_audit_state

    # Require-full mode rejects incomplete schemas explicitly.
    strict_incomplete = reconcile_live_schema(base, spec, require_full_schema=True)
    if strict_incomplete.is_schema_complete:
        assert strict_incomplete.passed is True
        assert strict_incomplete.full_schema_enforced is True
    else:
        assert strict_incomplete.passed is False
        assert len(strict_incomplete.missing_chapter4_tables) > 0

    # Synthetic 123-table completion triggers full_schema_enforced automatically with require_full_schema=None.
    base_report = reconcile_live_schema(base, spec, require_full_schema=False)
    present_names = set(base_report.migrated_chapter4_tables)
    missing_tables = spec.logical_tables - present_names
    synthetic_rels = [
        r for r in base.relations if r.name not in missing_tables and r.name != "v_public_question_versions"
    ]
    synthetic_policies = list(base.policies)
    for table in sorted(missing_tables):
        is_tenant = table in spec.strict_tenant_tables or table in spec.hybrid_public_tables
        synthetic_rels.append(
            LiveRelation(
                name=table,
                relkind="p" if table == "response_revisions" else "r",
                is_partition=False,
                parent_name=None,
                rls_enabled=is_tenant,
                rls_forced=is_tenant,
                reloptions=(),
                has_tenant_id=is_tenant,
            )
        )
        if table in spec.strict_tenant_tables:
            synthetic_policies.append(
                LivePolicy(
                    table_name=table,
                    policy_name="tenant_isolation_policy",
                    roles=("public",),
                    cmd="ALL",
                    qual="(tenant_id = ( SELECT current_app_tenant_id()))",
                    with_check="(tenant_id = ( SELECT current_app_tenant_id()))",
                )
            )
        elif table in spec.hybrid_public_tables:
            synthetic_policies.extend(
                [
                    LivePolicy(
                        table_name=table,
                        policy_name="hybrid_read_policy",
                        roles=("public",),
                        cmd="SELECT",
                        qual=(
                            "((tenant_id = ( SELECT current_app_tenant_id())) "
                            "OR (tenant_id = system_public_tenant_id()))"
                        ),
                        with_check="",
                    ),
                    LivePolicy(
                        table_name=table,
                        policy_name="hybrid_write_policy",
                        roles=("public",),
                        cmd="ALL",
                        qual="(tenant_id = ( SELECT current_app_tenant_id()))",
                        with_check="(tenant_id = ( SELECT current_app_tenant_id()))",
                    ),
                ]
            )
    full_snapshot = dataclasses.replace(
        base,
        relations=tuple(synthetic_rels),
        policies=tuple(synthetic_policies),
    )
    full_report = reconcile_live_schema(full_snapshot, spec, require_full_schema=None)
    assert full_report.is_schema_complete is True
    assert full_report.full_schema_enforced is True
    assert len(full_report.migrated_chapter4_tables) == EXPECTED_CHAPTER4_LOGICAL_TABLE_COUNT
    assert full_report.missing_chapter4_tables == ()
    # Auto-enforced full mode also detects missing `v_public_question_versions` and `response_revisions` partitions.
    assert full_report.passed is False
    assert any("v_public_question_versions" in msg for msg in full_report.view_violations)
    assert any("response_revisions" in msg for msg in full_report.partition_violations)


# --- 2. Gate 2b Structural Audits on Clean `alembic upgrade head` Database ---------------------


def test_alembic_upgrade_head_passes_gate2b_reconciliation(migrated_audit_state: Any) -> None:
    spec, snapshot = migrated_audit_state
    report = reconcile_live_schema(snapshot, spec, require_full_schema=_require_full_flag())
    assert report.passed, report.failure_summary()
    assert len(report.migrated_chapter4_tables) >= 42
    if report.is_schema_complete:
        assert len(report.migrated_chapter4_tables) == EXPECTED_CHAPTER4_LOGICAL_TABLE_COUNT
        assert set(spec.hybrid_public_tables) <= set(report.migrated_v113_tables)
        assert EXPECTED_CHAPTER4_SECURITY_VIEWS <= set(report.views_present)

    async def _live_call() -> Any:
        conn = await _connect(ALEMBIC_AUDIT_DB)
        try:
            return await audit_live_alembic_schema(conn, require_full_schema=_require_full_flag())
        finally:
            await conn.close()

    live_report = _run(_live_call())
    assert live_report == report


def test_every_migrated_v113_table_with_tenant_id_forces_rls_and_initplan(migrated_audit_state: Any) -> None:
    spec, snapshot = migrated_audit_state
    report = reconcile_live_schema(snapshot, spec, require_full_schema=False)
    assert report.missing_rls_enable == ()
    assert report.missing_rls_force == ()
    assert report.missing_rls_policy == ()
    assert report.policy_shape_violations == ()
    assert report.initplan_violations == ()
    assert report.permissive_policy_violations == ()

    # Double-check via direct PostgreSQL query that no non-legacy table with tenant_id lacks InitPlan RLS.
    async def _direct_pg_check() -> tuple[set[str], set[str], set[str]]:
        conn = await _connect(ALEMBIC_AUDIT_DB)
        try:
            unforced = await conn.fetch(
                """
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relkind IN ('r', 'p')
                  AND EXISTS (
                      SELECT 1 FROM pg_attribute a
                      WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
                  )
                  AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
                """
            )
            no_policy = await conn.fetch(
                """
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relrowsecurity
                  AND NOT EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid)
                """
            )
            bare_calls = await conn.fetch(
                r"""
                SELECT tablename || '.' || policyname AS name
                FROM pg_policies
                WHERE schemaname = 'public'
                  AND regexp_replace(coalesce(qual, '') || coalesce(with_check, ''),
                                     'SELECT\s+current_app_tenant_id\(\)', '', 'gi') ~ 'current_app_tenant_id\(\)'
                """
            )
            return (
                {r["relname"] for r in unforced},
                {r["relname"] for r in no_policy},
                {r["name"] for r in bare_calls},
            )
        finally:
            await conn.close()

    unforced, no_policy, bare_calls = _run(_direct_pg_check())
    assert unforced == set()
    assert no_policy == set()
    assert bare_calls == set()


def test_zero_runtime_roles_bypass_rls_or_create_in_public(migrated_audit_state: Any) -> None:
    spec, snapshot = migrated_audit_state
    report = reconcile_live_schema(snapshot, spec, require_full_schema=False)
    assert report.role_violations == ()
    assert report.schema_privilege_violations == ()

    by_name = {r.name: r for r in snapshot.roles}
    assert set(by_name) == set(ALL_MANAGED_ROLE_NAMES)
    for role in snapshot.roles:
        assert role.bypassrls is False
        assert role.superuser is False
    for runtime_role in RUNTIME_ROLE_NAMES:
        assert by_name[runtime_role].schema_create is False
        assert by_name[runtime_role].schema_usage is True


def test_all_partitions_force_rls_deny_grants_and_future_partitions_lock_down(migrated_audit_state: Any) -> None:
    spec, snapshot = migrated_audit_state
    report = reconcile_live_schema(snapshot, spec, require_full_schema=False)
    assert report.partition_violations == ()
    assert len(report.partitions_present) >= 7, "audit_events must attach monthly + default partitions"

    # Also prove `ensure_monthly_partitions()` locks down newly minted partitions and enforces F-05 UTC bounds.
    async def _probe_future_partition() -> tuple[bool, bool, bool, list[str]]:
        conn = await _connect(ALEMBIC_AUDIT_DB)
        try:
            async with conn.transaction():
                await conn.execute("SELECT ensure_monthly_partitions('audit_events', DATE '2031-05-01', 1)")
                row = await conn.fetchrow(
                    r"""
                    SELECT
                        c.relrowsecurity AND c.relforcerowsecurity AS forced,
                        EXISTS(
                            SELECT 1 FROM pg_policies p
                            WHERE p.schemaname = 'public'
                              AND p.tablename = 'audit_events_2031_05'
                              AND p.policyname = 'tenant_isolation_policy'
                              AND p.qual ~ 'SELECT\s+current_app_tenant_id\(\)'
                        ) AS has_initplan_policy,
                        pg_get_expr(c.relpartbound, c.oid) AS bound_expr
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relname = 'audit_events_2031_05'
                    """
                )
                assert row is not None
                granted_roles = [
                    r["grantee"]
                    for r in await conn.fetch(
                        """
                        SELECT r AS grantee
                        FROM unnest($1::text[]) AS r
                        CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) AS priv
                        WHERE has_table_privilege(r, 'public.audit_events_2031_05', priv)
                        UNION ALL
                        SELECT 'PUBLIC' AS grantee
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        CROSS JOIN LATERAL aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
                        WHERE n.nspname = 'public'
                          AND c.relname = 'audit_events_2031_05'
                          AND a.grantee = 0
                        """,
                        list(RUNTIME_ROLE_NAMES),
                    )
                ]
                assert "2031-05-01 00:00:00+00" in row["bound_expr"]
                assert "2031-06-01 00:00:00+00" in row["bound_expr"]
                raise _RollbackProbe(
                    (bool(row["forced"]), bool(row["has_initplan_policy"]), granted_roles == [], granted_roles)
                )
        except _RollbackProbe as res:
            return res.payload
        finally:
            await conn.close()

    forced, has_policy, no_grants, offenders = _run(_probe_future_partition())
    assert forced is True
    assert has_policy is True
    assert no_grants is True, f"unexpected runtime privileges on dynamic partition: {offenders}"


class _RollbackProbe(Exception):
    def __init__(self, payload: Any) -> None:
        self.payload = payload


def test_all_security_views_enforce_security_invoker(migrated_audit_state: Any) -> None:
    spec, snapshot = migrated_audit_state
    report = reconcile_live_schema(snapshot, spec, require_full_schema=False)
    assert report.view_violations == ()
    assert "v_users_status" in report.views_present


def test_role_table_and_column_grants_match_grants_manifest(migrated_audit_state: Any) -> None:
    spec, snapshot = migrated_audit_state
    report = reconcile_live_schema(snapshot, spec, require_full_schema=False)
    assert report.missing_manifest_grants == ()
    assert report.extra_manifest_grants == ()
    assert report.column_grant_sync_violations == ()
    assert report.sensitive_grant_violations == ()

    # Transparently assert the exact known M0.2 foundation delta (no silent suppression).
    expected_foundation_delta = {":".join(item) for item in FOUNDATION_MANIFEST_GRANT_DELTAS}
    assert set(report.foundation_manifest_deltas) <= expected_foundation_delta

    # Verify column-level grants from `information_schema.role_column_grants` explicitly.
    for col in ("status", "published_at", "attempt_count", "next_attempt_at"):
        assert ("ulp_system_scheduler", "outbox_events", "UPDATE", col) in snapshot.manifest_column_grants
    assert ("ulp_system_scheduler", "outbox_events", "UPDATE") not in snapshot.manifest_table_grants

    if report.is_schema_complete:
        for role in MANIFEST_ROLE_NAMES:
            expected_count = sum(1 for r, *_ in spec.manifest_grants if r == role)
            assert expected_count > 0


def test_security_definer_functions_pin_search_path_and_owner(migrated_audit_state: Any) -> None:
    spec, snapshot = migrated_audit_state
    report = reconcile_live_schema(snapshot, spec, require_full_schema=False)
    assert report.security_definer_violations == ()
    names = {fn.name for fn in snapshot.security_definer_functions}
    expected_f04 = {
        "f04_register_user",
        "f04_find_user_id",
        "f04_store_user_key",
        "f04_load_user_key",
        "f04_shred_user_key",
    }
    assert expected_f04 <= names


# --- 3. Live Behavioral Security Tests against Migrated Tables -------------------------------


def test_live_rls_hybrid_visibility_and_scheduler_guc_isolation(migrated_audit_state: Any) -> None:
    async def _scenario() -> None:
        conn = await _connect(ALEMBIC_AUDIT_DB)
        try:
            async with conn.transaction():
                # Seed tenants + system/tenant hybrid content + cross-tenant scheduler rows inside savepoint-rolled TX.
                await conn.execute(
                    """
                    INSERT INTO tenants (id, slug, name) VALUES
                        ($1, 'gate2b-a', 'Gate 2b Tenant A'),
                        ($2, 'gate2b-b', 'Gate 2b Tenant B')
                    ON CONFLICT (id) DO NOTHING
                    """,
                    TENANT_A,
                    TENANT_B,
                )
                sys_pub = "c0000000-0000-0000-0000-000000000101"
                sys_draft = "c0000000-0000-0000-0000-000000000102"
                sys_takedown = "c0000000-0000-0000-0000-000000000103"
                tenant_a_item = "c0000000-0000-0000-0000-000000000104"

                ver_pub = "d0000000-0000-0000-0000-000000000101"
                ver_draft = "d0000000-0000-0000-0000-000000000102"
                ver_takedown = "d0000000-0000-0000-0000-000000000103"

                await conn.execute(
                    """
                    INSERT INTO content_items (
                        id, tenant_id, visibility_scope, content_type, slug, rights_status, publication_status
                    ) VALUES
                        ($1, $5, 'SYSTEM_PUBLIC', 'ARTICLE', 'sys-pub', 'OWNED', 'DRAFT'),
                        ($2, $5, 'SYSTEM_PUBLIC', 'ARTICLE', 'sys-draft', 'OWNED', 'DRAFT'),
                        ($3, $5, 'SYSTEM_PUBLIC', 'ARTICLE', 'sys-takedown', 'OWNED', 'DRAFT'),
                        ($4, $6, 'TENANT_PRIVATE', 'ARTICLE', 'tenant-a-private', 'OWNED', 'DRAFT')
                    """,
                    sys_pub,
                    sys_draft,
                    sys_takedown,
                    tenant_a_item,
                    SYSTEM_TENANT,
                    TENANT_A,
                )
                await conn.execute(
                    """
                    INSERT INTO content_versions (id, tenant_id, content_item_id, version_no, title, status) VALUES
                        ($1, $4, $7, 1, 'Published Sys', 'PUBLISHED'),
                        ($2, $5, $8, 1, 'Draft Sys', 'DRAFT'),
                        ($3, $6, $9, 1, 'Takedown Sys', 'PUBLISHED')
                    """,
                    ver_pub,
                    ver_draft,
                    ver_takedown,
                    SYSTEM_TENANT,
                    SYSTEM_TENANT,
                    SYSTEM_TENANT,
                    sys_pub,
                    sys_draft,
                    sys_takedown,
                )
                await conn.execute(
                    """
                    UPDATE content_items
                       SET verification_status = 'VERIFIED', publication_status = 'PUBLISHED', current_version_id = $2
                     WHERE id = $1
                    """,
                    sys_pub,
                    ver_pub,
                )
                await conn.execute(
                    """
                    UPDATE content_items
                       SET verification_status = 'VERIFIED', publication_status = 'PUBLISHED', current_version_id = $2
                     WHERE id = $1
                    """,
                    sys_takedown,
                    ver_takedown,
                )
                await conn.execute(
                    """
                    UPDATE content_items
                       SET rights_status = 'TAKEDOWN', publication_status = 'TAKEN_DOWN'
                     WHERE id = $1
                    """,
                    sys_takedown,
                )
                await conn.execute(
                    """
                    INSERT INTO outbox_events (tenant_id, event_type, aggregate_type, aggregate_id, payload_json) VALUES
                        ($1, 'gate2b.test', 'probe', gen_random_uuid(), '{}'),
                        ($2, 'gate2b.test', 'probe', gen_random_uuid(), '{}')
                    """,
                    TENANT_A,
                    TENANT_B,
                )

                # Tenant B sees ONLY SYSTEM_PUBLIC + PUBLISHED content_items (never DRAFT/TAKEN_DOWN/Tenant A)
                seen_b = await _run_in_savepoint_as_role(
                    conn, "ulp_app_rw", "SELECT slug FROM content_items", tenant=TENANT_B
                )
                assert {r["slug"] for r in seen_b} == {"sys-pub"}

                # Child hybrid_read_policy hides content_versions whose parent content_item is not PUBLISHED
                seen_vers_b = await _run_in_savepoint_as_role(
                    conn, "ulp_app_rw", "SELECT title FROM content_versions", tenant=TENANT_B
                )
                assert {r["title"] for r in seen_vers_b} == {"Published Sys"}

                # Tenant A sees both SYSTEM_PUBLIC PUBLISHED and its own private item
                seen_a = await _run_in_savepoint_as_role(
                    conn, "ulp_app_rw", "SELECT slug FROM content_items", tenant=TENANT_A
                )
                assert {r["slug"] for r in seen_a} == {"sys-pub", "tenant-a-private"}

                # Anonymous (no tenant GUC) sees ONLY published system items and 0 strict outbox rows
                anon_items = await _run_in_savepoint_as_role(conn, "ulp_app_rw", "SELECT slug FROM content_items")
                assert {r["slug"] for r in anon_items} == {"sys-pub"}
                anon_outbox = await _run_in_savepoint_as_role(conn, "ulp_app_rw", "SELECT count(*) FROM outbox_events")
                assert anon_outbox[0][0] == 0

                # Tenant B cannot smuggle a row under Tenant A or System Tenant
                for owner_id in (SYSTEM_TENANT, TENANT_A):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await _run_in_savepoint_as_role(
                            conn,
                            "ulp_app_rw",
                            "INSERT INTO content_items (tenant_id, visibility_scope, content_type, slug) "
                            "VALUES ($1, 'TENANT_PRIVATE', 'ARTICLE', 'smuggled')",
                            owner_id,
                            tenant=TENANT_B,
                        )

                # Scheduler fail-closed without app.system_job, fails on wrong job, sees both tenants on outbox_relay
                assert (
                    await _run_in_savepoint_as_role(conn, "ulp_system_scheduler", "SELECT count(*) FROM outbox_events")
                )[0][0] == 0
                assert (
                    await _run_in_savepoint_as_role(
                        conn, "ulp_system_scheduler", "SELECT count(*) FROM outbox_events", job="metric_rollup"
                    )
                )[0][0] == 0
                assert (
                    await _run_in_savepoint_as_role(
                        conn, "ulp_system_scheduler", "SELECT count(*) FROM outbox_events", job="outbox_relay"
                    )
                )[0][0] >= 2

                raise _RollbackProbe(True)
        except _RollbackProbe:
            pass
        finally:
            await conn.close()

    _run(_scenario())


def test_live_partition_lockdown_and_parent_isolation(migrated_audit_state: Any) -> None:
    async def _scenario() -> None:
        conn = await _connect(ALEMBIC_AUDIT_DB)
        try:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO tenants (id, slug, name) VALUES
                        ($1, 'gate2b-p-a', 'P A'),
                        ($2, 'gate2b-p-b', 'P B')
                    ON CONFLICT (id) DO NOTHING
                    """,
                    TENANT_A,
                    TENANT_B,
                )
                # F-01: direct partition queries fail with 42501 for all runtime roles
                for role in RUNTIME_ROLE_NAMES:
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await _run_in_savepoint_as_role(
                            conn, role, "SELECT count(*) FROM audit_events_2026_11", tenant=TENANT_A
                        )

                # Writing through the partitioned parent routes into audit_events_2026_11 & isolates tenants
                await _run_in_savepoint_as_role(
                    conn,
                    "ulp_app_rw",
                    "INSERT INTO audit_events (tenant_id, action, target_type, target_id, occurred_at) "
                    "VALUES ($1, 'GATE2B', 'probe', gen_random_uuid(), '2026-11-20T00:00:00Z')",
                    TENANT_A,
                    tenant=TENANT_A,
                )
                count_a = await _run_in_savepoint_as_role(
                    conn, "ulp_app_rw", "SELECT count(*) FROM audit_events", tenant=TENANT_A
                )
                count_b = await _run_in_savepoint_as_role(
                    conn, "ulp_app_rw", "SELECT count(*) FROM audit_events", tenant=TENANT_B
                )
                assert count_a[0][0] == 1
                assert count_b[0][0] == 0

                raise _RollbackProbe(True)
        except _RollbackProbe:
            pass
        finally:
            await conn.close()

    _run(_scenario())


def test_live_security_invoker_view_and_key_vault_lockdown(migrated_audit_state: Any) -> None:
    async def _scenario() -> None:
        conn = await _connect(ALEMBIC_AUDIT_DB)
        try:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO tenants (id, slug, name) VALUES
                        ($1, 'gate2b-v-a', 'V A'),
                        ($2, 'gate2b-v-b', 'V B')
                    ON CONFLICT (id) DO NOTHING
                    """,
                    TENANT_A,
                    TENANT_B,
                )
                await conn.execute(
                    """
                    INSERT INTO users (id, email_blind_index, pseudonym_handle, birth_year) VALUES
                        ($1, repeat('a', 64), 'Alice', 2005),
                        ($2, repeat('b', 64), 'Bob', 2012)
                    """,
                    USER_A,
                    USER_B,
                )
                await conn.execute(
                    "INSERT INTO memberships (tenant_id, user_id) VALUES ($1, $2), ($3, $4)",
                    TENANT_A,
                    USER_A,
                    TENANT_B,
                    USER_B,
                )

                # Without caller context, `security_invoker` view `v_users_status` enforces users RLS (0 rows).
                anon_rows = await _run_in_savepoint_as_role(
                    conn, "ulp_app_rw", "SELECT pseudonym_handle FROM v_users_status"
                )
                assert anon_rows == []

                # With Tenant A context, caller sees only Tenant A member Alice (and NOT Tenant B member Bob).
                a_rows = await _run_in_savepoint_as_role(
                    conn, "ulp_app_rw", "SELECT pseudonym_handle FROM v_users_status", tenant=TENANT_A
                )
                assert [r["pseudonym_handle"] for r in a_rows] == ["Alice"]

                # Decision #7: key vault table is completely closed to runtime roles directly.
                for role in RUNTIME_ROLE_NAMES:
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await _run_in_savepoint_as_role(
                            conn, role, "SELECT count(*) FROM user_pii_encryption_keys", tenant=TENANT_A
                        )

                raise _RollbackProbe(True)
        except _RollbackProbe:
            pass
        finally:
            await conn.close()

    _run(_scenario())


def test_live_append_only_and_content_immutability_triggers(migrated_audit_state: Any) -> None:
    async def _scenario() -> None:
        conn = await _connect(ALEMBIC_AUDIT_DB)
        try:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO tenants (id, slug, name) VALUES ($1, 'gate2b-t', 'Trigger T')
                    ON CONFLICT (id) DO NOTHING
                    """,
                    TENANT_A,
                )
                await conn.execute(
                    """
                    INSERT INTO audit_events (tenant_id, action, target_type, target_id, occurred_at)
                    VALUES ($1, 'PROBE', 'probe', gen_random_uuid(), '2026-11-20T00:00:00Z')
                    """,
                    TENANT_A,
                )
                with pytest.raises(asyncpg.RaiseError, match="APPEND_ONLY_VIOLATION"):
                    async with conn.transaction():
                        await conn.execute("UPDATE audit_events SET action = 'MUTATED'")
                with pytest.raises(asyncpg.RaiseError, match="APPEND_ONLY_VIOLATION"):
                    async with conn.transaction():
                        await conn.execute("DELETE FROM audit_events")
                with pytest.raises(asyncpg.RaiseError, match="RAW_PII_IN_AUDIT_FORBIDDEN"):
                    async with conn.transaction():
                        await conn.execute(
                            """
                            INSERT INTO audit_events (
                                tenant_id, action, target_type, target_id, after_json, occurred_at
                            ) VALUES (
                                $1, 'LEAK', 'user', gen_random_uuid(),
                                '{"raw_email":"x@example.com"}'::jsonb, '2026-11-20T00:00:00Z'
                            )
                            """,
                            TENANT_A,
                        )

                # Publish gate & F-03 immutability trigger on content_versions / content_blocks
                item_id = "c0000000-0000-0000-0000-000000000201"
                ver_id = "d0000000-0000-0000-0000-000000000201"
                with pytest.raises(asyncpg.CheckViolationError):
                    async with conn.transaction():
                        await conn.execute(
                            """
                            INSERT INTO content_items (
                                id, tenant_id, visibility_scope, content_type, slug, rights_status, publication_status
                            ) VALUES ($1, $2, 'TENANT_PRIVATE', 'ARTICLE', 'bad-publish', 'PENDING_REVIEW', 'PUBLISHED')
                            """,
                            item_id,
                            TENANT_A,
                        )

                await conn.execute(
                    """
                    INSERT INTO content_items (
                        id, tenant_id, visibility_scope, content_type, slug, verification_status, rights_status
                    ) VALUES ($1, $2, 'TENANT_PRIVATE', 'ARTICLE', 'freeze-me', 'VERIFIED', 'OWNED')
                    """,
                    item_id,
                    TENANT_A,
                )
                await conn.execute(
                    """
                    INSERT INTO content_versions (id, tenant_id, content_item_id, version_no, title, status)
                    VALUES ($1, $2, $3, 1, 'Frozen Title', 'DRAFT')
                    """,
                    ver_id,
                    TENANT_A,
                    item_id,
                )
                await conn.execute(
                    """
                    INSERT INTO content_blocks (tenant_id, content_version_id, block_order, block_type, payload_json)
                    VALUES ($1, $2, 1, 'THEORY_SUMMARY', '{"text":"locked"}'::jsonb)
                    """,
                    TENANT_A,
                    ver_id,
                )
                await conn.execute("UPDATE content_versions SET status = 'PUBLISHED' WHERE id = $1", ver_id)

                with pytest.raises(asyncpg.RaiseError, match="IMMUTABLE_VIOLATION"):
                    async with conn.transaction():
                        await conn.execute("UPDATE content_versions SET title = 'Tampered' WHERE id = $1", ver_id)
                with pytest.raises(asyncpg.RaiseError):
                    async with conn.transaction():
                        await conn.execute(
                            "UPDATE content_blocks SET block_order = 2 WHERE content_version_id = $1", ver_id
                        )
                # Legal takedown transition remains permitted on frozen content_versions (F-03)
                await conn.execute("UPDATE content_versions SET status = 'TAKEN_DOWN' WHERE id = $1", ver_id)

                raise _RollbackProbe(True)
        except _RollbackProbe:
            pass
        finally:
            await conn.close()

    _run(_scenario())


def test_full_chapter4_123_tables_behavioral_gate(migrated_audit_state: Any) -> None:
    """Automatically executes the complete 123-table Gate 2b behavioral suite once all migrations land."""
    spec, snapshot = migrated_audit_state
    report = reconcile_live_schema(snapshot, spec, require_full_schema=_require_full_flag())
    assert report.passed, report.failure_summary()

    if not report.is_schema_complete:
        # Progressive wave check: ensure legacy `questions` is not conflated with unmigrated V11.3 tables,
        # and verify require_full_schema=True accurately surfaces every missing Chapter 4 table.
        assert "questions" not in report.migrated_v113_tables or (
            any(r.name == "questions" and r.has_tenant_id for r in snapshot.relations)
        )
        strict_report = reconcile_live_schema(snapshot, spec, require_full_schema=True)
        assert strict_report.passed is False
        assert set(strict_report.missing_chapter4_tables) == set(spec.logical_tables) - set(report.migrated_v113_tables)
        return

    # Full 123-table schema is present: run complete handover verification & answer/view/partition gates.
    async def _full_behavioral_verify() -> None:
        conn = await _connect(ALEMBIC_AUDIT_DB)
        try:
            async with conn.transaction():
                await conn.execute(SEED_FIXTURE.read_text(encoding="utf-8"))
                verify_body = "\n".join(
                    line
                    for line in VERIFY_HARDENING.read_text(encoding="utf-8").splitlines()
                    if line.strip().upper() not in ("BEGIN;", "ROLLBACK;")
                )
                async with conn.transaction():
                    await conn.execute(verify_body)

                # Answer tables must stay closed (42501) to non-grading manifest roles and PUBLIC.
                for role in [r for r in ANSWER_DENIED_ROLES if r != "PUBLIC"]:
                    for table in ANSWER_TABLES:
                        with pytest.raises(asyncpg.InsufficientPrivilegeError):
                            await _run_in_savepoint_as_role(
                                conn, role, f"SELECT 1 FROM {table} LIMIT 1", tenant=TENANT_A
                            )

                # Grading worker reads SYSTEM_PUBLIC answer keys across tenants.
                keys = await _run_in_savepoint_as_role(
                    conn,
                    "ulp_worker_grading",
                    "SELECT tenant_id::text AS t FROM assessment_answer_keys",
                    tenant=TENANT_B,
                )
                assert {r["t"] for r in keys} == {SYSTEM_TENANT}

                # F-02: v_public_question_versions applies caller RLS across all 3 consumer roles.
                for role in ("ulp_app_rw", "ulp_exam_session_rw", "ulp_analytics_ro"):
                    seen = await _run_in_savepoint_as_role(
                        conn, role, "SELECT stem_json->>'t' AS t FROM v_public_question_versions", tenant=TENANT_B
                    )
                    assert {r["t"] for r in seen} == {"published"}

                # response_revisions F-01 + append-only trigger check.
                for role in RUNTIME_ROLE_NAMES:
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await _run_in_savepoint_as_role(
                            conn, role, "SELECT count(*) FROM response_revisions_default", tenant=TENANT_A
                        )

                raise _RollbackProbe(True)
        except _RollbackProbe:
            pass
        finally:
            await conn.close()

    _run(_full_behavioral_verify())
