"""W0.1 (H-01, H-03): the V11.3 schema of chapter 4, applied whole to a scratch database.

The application database receives its tables module by module. This suite proves, long before the
last module lands, that the specified DDL runs on a real PostgreSQL and that its row-level
security, grants and immutability triggers behave as the handover says.

Defects of the specification itself are recorded as strict xfails: the suite stays green, and the
day the DDL is corrected the xfail flips and has to be removed.
"""

import asyncio
import csv
import os
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.engine import make_url

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DDL = ROOT / "docs" / "architecture" / "chapters" / "04_postgresql_ddl_rls_grants.sql"
SEED = HERE / "seed_rls_fixture.sql"
VERIFY = HERE / "verify_rls_hardening.sql"
GRANTS_MANIFEST = HERE / "grants_manifest.csv"

DRY_RUN_DB = "ulp_ddl_dry_run"

TENANT_A = "aaaaaaaa-0000-0000-0000-000000000001"
TENANT_B = "bbbbbbbb-0000-0000-0000-000000000002"
SYSTEM = "00000000-0000-0000-0000-000000000000"

RUNTIME_ROLES = [
    "ulp_app_rw", "ulp_exam_session_rw", "ulp_worker_ingestion",
    "ulp_worker_grading", "ulp_system_scheduler", "ulp_analytics_ro",
]  # fmt: skip
MANIFEST_ROLES = [r for r in RUNTIME_ROLES if r != "ulp_app_rw"]  # ulp_app_rw is granted ALL TABLES
ANSWER_TABLES = ["question_version_answers", "assessment_answer_keys"]

HYBRID_PUBLIC_TABLES = {
    "taxonomy_nodes", "taxonomy_node_versions", "taxonomy_aliases", "knowledge_edges",
    "curriculum_versions", "curriculum_nodes", "taxonomy_mappings",
    "assets", "content_items", "content_versions", "content_blocks", "content_assets",
    "questions", "question_stimuli", "question_versions", "question_options", "question_assets",
    "assessments", "assessment_versions", "assessment_items", "search_documents",
}  # fmt: skip

# Tables the specification leaves without a tenant column, hence without row-level security.
# A snapshot: a table joining or leaving this set is a design decision someone must make on purpose.
TABLES_WITHOUT_TENANT = {
    "ai_models", "ai_providers", "assessment_frameworks", "feature_flags", "framework_versions",
    "fts_dictionary_versions", "locale_bundles", "notification_deliveries", "parser_definitions",
    "payment_webhooks", "permissions", "platform_settings", "prompt_versions",
    "question_irt_parameters", "scoring_scheme_versions", "subscription_plans", "tenants",
    "user_pii_encryption_keys", "users", "vertical_packs",
}  # fmt: skip


def run(coro):
    return asyncio.run(coro)


async def _connect(database: str) -> asyncpg.Connection:
    url = make_url(os.environ["DATABASE_URL"])
    return await asyncpg.connect(
        user=url.username, password=url.password, host=url.host, port=url.port or 5432, database=database
    )


async def _build() -> None:
    admin = await _connect("postgres")
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{DRY_RUN_DB}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{DRY_RUN_DB}"')
    finally:
        await admin.close()
    conn = await _connect(DRY_RUN_DB)
    try:
        await conn.execute(DDL.read_text(encoding="utf-8"))
        async with conn.transaction():
            await conn.execute(SEED.read_text(encoding="utf-8"))
    finally:
        await conn.close()


async def _drop() -> None:
    admin = await _connect("postgres")
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{DRY_RUN_DB}" WITH (FORCE)')
    finally:
        await admin.close()


@pytest.fixture(scope="module", autouse=True)
def dry_run_database():
    run(_build())
    yield
    run(_drop())


async def _owner(sql: str, *args):
    conn = await _connect(DRY_RUN_DB)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


async def _as(role: str, sql: str, *, tenant: str | None = None, job: str | None = None):
    """Run one statement as a runtime role inside a transaction that is always rolled back."""
    conn = await _connect(DRY_RUN_DB)
    try:
        transaction = conn.transaction()
        await transaction.start()
        try:
            assert role in RUNTIME_ROLES
            await conn.execute(f"SET LOCAL ROLE {role}")
            if tenant is not None:
                await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant)
            if job is not None:
                await conn.execute("SELECT set_config('app.system_job', $1, true)", job)
            return await conn.fetch(sql)
        finally:
            await transaction.rollback()
    finally:
        await conn.close()


def owner(sql: str, *args) -> list[asyncpg.Record]:
    return run(_owner(sql, *args))


def as_role(role: str, sql: str, **context) -> list[asyncpg.Record]:
    return run(_as(role, sql, **context))


def column(rows: list[asyncpg.Record]) -> set:
    return {row[0] for row in rows}


TENANT_TABLES_SQL = """
    SELECT c.relname FROM pg_class c
     WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p') AND {partition}
       AND {negate} EXISTS (SELECT 1 FROM pg_attribute a
                            WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped)
"""


# --- the DDL as a whole ---------------------------------------------------------------------


def test_the_specified_ddl_applies_cleanly_and_creates_the_schema():
    tables = column(
        owner("SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r', 'p')")
    )
    assert {"tenants", "questions", "attempts", "responses", "outbox_events", "audit_events"} <= tables
    assert HYBRID_PUBLIC_TABLES <= tables


def test_the_handover_verification_script_passes():
    async def _verify():
        conn = await _connect(DRY_RUN_DB)
        try:
            await conn.execute(VERIFY.read_text(encoding="utf-8"))
        finally:
            await conn.close()

    run(_verify())  # the script raises on any violation and rolls itself back


# --- H-03: roles and grants -----------------------------------------------------------------


def test_no_runtime_role_can_bypass_rls_or_is_superuser():
    rows = owner(
        "SELECT rolname FROM pg_roles WHERE rolname = ANY($1::text[]) AND (rolbypassrls OR rolsuper)",
        [*RUNTIME_ROLES, "ulp_migrator"],
    )
    assert column(rows) == set()


def test_runtime_roles_cannot_create_objects_in_the_schema():
    for role in RUNTIME_ROLES:
        assert owner("SELECT has_schema_privilege($1, 'public', 'CREATE')", role)[0][0] is False, role


def test_worker_role_grants_match_the_manifest_exactly():
    with GRANTS_MANIFEST.open(encoding="utf-8", newline="") as handle:
        expected = {(r["role"], r["object"], r["privilege"], r["column"]) for r in csv.DictReader(handle)}
    rows = owner(
        """
        SELECT grantee, table_name, privilege_type, '' FROM information_schema.role_table_grants
         WHERE table_schema = 'public' AND grantee = ANY($1::text[])
        UNION ALL
        SELECT cp.grantee, cp.table_name, cp.privilege_type, cp.column_name
          FROM information_schema.column_privileges cp
         WHERE cp.table_schema = 'public' AND cp.grantee = ANY($1::text[])
           AND NOT EXISTS (SELECT 1 FROM information_schema.role_table_grants t
                            WHERE t.table_schema = 'public' AND t.grantee = cp.grantee
                              AND t.table_name = cp.table_name AND t.privilege_type = cp.privilege_type)
        """,
        MANIFEST_ROLES,
    )
    actual = {tuple(row) for row in rows}
    assert actual - expected == set(), "granted in the database but not in grants_manifest.csv"
    assert expected - actual == set(), "in grants_manifest.csv but not granted"


@pytest.mark.parametrize(
    "role", ["ulp_exam_session_rw", "ulp_worker_ingestion", "ulp_system_scheduler", "ulp_analytics_ro"]
)
@pytest.mark.parametrize("table", ANSWER_TABLES)
def test_answer_tables_are_closed_to_every_role_but_api_and_grading(role, table):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        as_role(role, f"SELECT 1 FROM {table} LIMIT 1", tenant=TENANT_A)


@pytest.mark.parametrize("table", ["fts_dictionary_versions", "daily_metric_rollups", "inbox_events"])
def test_the_api_role_cannot_write_worker_and_migrator_tables(table):
    for privilege in ("INSERT", "UPDATE", "DELETE"):
        assert owner("SELECT has_table_privilege('ulp_app_rw', $1, $2)", table, privilege)[0][0] is False


@pytest.mark.parametrize("table", ["audit_events", "response_revisions"])
def test_append_only_tables_grant_no_update_or_delete(table):
    for role in RUNTIME_ROLES:
        for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
            assert owner("SELECT has_table_privilege($1, $2, $3)", role, table, privilege)[0][0] is False, (
                role,
                privilege,
            )


# --- H-01: row-level security, structure ------------------------------------------------------


def test_every_table_with_a_tenant_column_forces_rls():
    rows = owner(
        "SELECT c.relname FROM ("
        + TENANT_TABLES_SQL.format(partition="NOT c.relispartition", negate="")
        + ") t JOIN pg_class c ON c.relname = t.relname AND c.relnamespace = 'public'::regnamespace"
        " WHERE NOT (c.relrowsecurity AND c.relforcerowsecurity)"
    )
    assert column(rows) == set()


def test_tables_without_a_tenant_column_are_exactly_the_known_set():
    rows = owner(TENANT_TABLES_SQL.format(partition="NOT c.relispartition", negate="NOT"))
    assert column(rows) == TABLES_WITHOUT_TENANT


def test_every_table_under_rls_has_at_least_one_policy():
    rows = owner(
        """
        SELECT c.relname FROM pg_class c
         WHERE c.relnamespace = 'public'::regnamespace AND c.relrowsecurity
           AND NOT EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid)
        """
    )
    assert column(rows) == set()


def test_every_policy_reads_the_tenant_through_an_initplan():
    """`(SELECT current_app_tenant_id())` is evaluated once per statement; the bare call once per row."""
    rows = owner(
        r"""
        SELECT tablename || '.' || policyname FROM pg_policies
         WHERE schemaname = 'public'
           AND regexp_replace(coalesce(qual, '') || coalesce(with_check, ''),
                              'SELECT current_app_tenant_id\(\)', '', 'g') ~ 'current_app_tenant_id\(\)'
        """
    )
    assert column(rows) == set()


def test_hybrid_public_tables_have_a_read_and_a_write_policy():
    rows = owner(
        "SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'public' AND policyname LIKE 'hybrid_%'"
    )
    by_policy: dict[str, set[str]] = {"hybrid_read_policy": set(), "hybrid_write_policy": set()}
    for table, policy in rows:
        by_policy[policy].add(table)
    assert by_policy["hybrid_read_policy"] == HYBRID_PUBLIC_TABLES
    assert by_policy["hybrid_write_policy"] == HYBRID_PUBLIC_TABLES


# --- H-01: row-level security, behaviour ------------------------------------------------------


def test_without_a_tenant_context_only_published_system_material_is_visible():
    assert column(as_role("ulp_app_rw", "SELECT stable_code FROM questions")) == {"SYS-PUBLISHED"}
    for strict_table in ("outbox_events", "analytics_events", "audit_events", "assessment_answer_keys"):
        assert as_role("ulp_app_rw", f"SELECT count(*) FROM {strict_table}")[0][0] == 0, strict_table


def test_a_tenant_sees_its_own_rows_and_only_published_system_material():
    seen_by_b = column(as_role("ulp_app_rw", "SELECT stable_code FROM questions", tenant=TENANT_B))
    assert seen_by_b == {"SYS-PUBLISHED"}  # not the draft, not the taken-down one, not tenant A's
    seen_by_a = column(as_role("ulp_app_rw", "SELECT stable_code FROM questions", tenant=TENANT_A))
    assert seen_by_a == {"SYS-PUBLISHED", "A-PRIVATE"}


def test_child_rows_are_hidden_when_the_parent_is_not_published():
    """The taken-down question's version is still immutable and SYSTEM_PUBLIC: only the parent hides it."""
    versions = column(as_role("ulp_app_rw", "SELECT stem_json->>'t' FROM question_versions", tenant=TENANT_B))
    assert versions == {"published"}
    options = as_role(
        "ulp_app_rw",
        "SELECT question_version_id::text FROM question_options",
        tenant=TENANT_B,
    )
    assert column(options) == {"d0000000-0000-0000-0000-000000000001"}


def test_knowledge_edges_need_both_endpoints_active():
    edges = column(as_role("ulp_app_rw", "SELECT id::text FROM knowledge_edges", tenant=TENANT_B))
    assert edges == {"a2000000-0000-0000-0000-000000000001"}


def test_a_tenant_cannot_write_into_the_system_tenant_or_another_tenant():
    for owner_tenant in (SYSTEM, TENANT_A):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):  # 42501: new row violates the RLS policy
            as_role(
                "ulp_app_rw",
                f"INSERT INTO questions (tenant_id, stable_code, item_type) VALUES ('{owner_tenant}', 'X', 'ESSAY')",
                tenant=TENANT_B,
            )


def test_the_api_role_reads_no_answers_of_the_system_tenant():
    for table in ANSWER_TABLES:
        assert as_role("ulp_app_rw", f"SELECT count(*) FROM {table}", tenant=TENANT_B)[0][0] == 0


def test_the_grading_worker_reads_system_answer_keys_from_any_tenant():
    keys = column(as_role("ulp_worker_grading", "SELECT tenant_id::text FROM assessment_answer_keys", tenant=TENANT_B))
    assert keys == {SYSTEM}  # the system paper's key, and not tenant A's
    answers = column(
        as_role("ulp_worker_grading", "SELECT tenant_id::text FROM question_version_answers", tenant=TENANT_B)
    )
    assert answers == {SYSTEM}


def test_the_scheduler_sees_rows_only_for_the_job_it_names():
    count = "SELECT count(*) FROM outbox_events"
    assert as_role("ulp_system_scheduler", count)[0][0] == 0
    assert as_role("ulp_system_scheduler", count, job="metric_rollup")[0][0] == 0
    assert as_role("ulp_system_scheduler", count, job="outbox_relay")[0][0] == 2  # both tenants
    assert as_role("ulp_system_scheduler", "SELECT count(*) FROM analytics_events", job="metric_rollup")[0][0] == 2
    assert as_role("ulp_system_scheduler", "SELECT count(*) FROM analytics_events", job="outbox_relay")[0][0] == 0


# --- immutability and the publish gate --------------------------------------------------------


FROZEN_VERSION_MUTATIONS = [
    "UPDATE question_versions SET stem_json = '{}' WHERE id = 'd0000000-0000-0000-0000-000000000001'",
    "DELETE FROM question_versions WHERE id = 'd0000000-0000-0000-0000-000000000001'",
    "UPDATE question_version_answers SET answer_schema = '{}'"
    " WHERE question_version_id = 'd0000000-0000-0000-0000-000000000001'",
    "UPDATE assessment_versions SET duration_seconds = 1 WHERE id = 'f1000000-0000-0000-0000-000000000001'",
]


@pytest.mark.parametrize("statement", FROZEN_VERSION_MUTATIONS)
def test_a_frozen_version_cannot_be_changed_or_deleted(statement):
    with pytest.raises(asyncpg.PostgresError):
        owner(statement)


def test_audit_events_are_append_only():
    with pytest.raises(asyncpg.RaiseError, match="APPEND_ONLY_VIOLATION"):
        owner("DELETE FROM audit_events")
    with pytest.raises(asyncpg.RaiseError, match="APPEND_ONLY_VIOLATION"):
        owner("UPDATE audit_events SET action = 'X'")


@pytest.mark.parametrize("rights", ["PENDING_REVIEW", "RESTRICTED", "TAKEDOWN"])
def test_publishing_without_cleared_rights_is_refused_by_the_database(rights):
    with pytest.raises(asyncpg.CheckViolationError):
        owner(
            "UPDATE questions SET verification_status = 'VERIFIED', rights_status = $1,"
            " publication_status = 'PUBLISHED',"
            " current_version_id = 'd0000000-0000-0000-0000-000000000002'"
            " WHERE id = 'c0000000-0000-0000-0000-000000000002'",
            rights,
        )


def test_publishing_an_unverified_question_is_refused_by_the_database():
    with pytest.raises(asyncpg.CheckViolationError):
        owner(
            "UPDATE questions SET rights_status = 'OWNED', publication_status = 'PUBLISHED',"
            " current_version_id = 'd0000000-0000-0000-0000-000000000002'"
            " WHERE id = 'c0000000-0000-0000-0000-000000000002'"
        )


# --- defects of the specified DDL (see docs/architecture/DDL_DRY_RUN_FINDINGS.md) --------------


@pytest.mark.xfail(strict=True, reason="F-01: partitions carry no RLS of their own and ulp_app_rw is granted on them")
def test_a_partition_cannot_be_read_around_the_parent_tables_rls():
    leaked = as_role("ulp_app_rw", "SELECT count(*) FROM audit_events_2026_11", tenant=TENANT_B)[0][0]
    assert leaked == 0  # tenant A's audit row


@pytest.mark.xfail(
    strict=True, reason="F-02: v_public_question_versions is not security_invoker, so it reads as its owner"
)
@pytest.mark.parametrize("role", ["ulp_app_rw", "ulp_exam_session_rw"])
def test_the_public_question_view_applies_the_callers_rls(role):
    seen = column(as_role(role, "SELECT stem_json->>'t' FROM v_public_question_versions", tenant=TENANT_B))
    assert seen == {"published"}


@pytest.mark.xfail(
    strict=True, reason="F-03: the immutability trigger reads NEW.status, a column only content_versions has"
)
@pytest.mark.parametrize("statement", FROZEN_VERSION_MUTATIONS)
def test_a_frozen_version_is_refused_with_the_immutability_error(statement):
    with pytest.raises(asyncpg.RaiseError, match="IMMUTABLE_VIOLATION"):
        owner(statement)
