"""Gate 2b live Alembic-to-Chapter-4 schema, RLS, partition, view and grant reconciler.

Reconciles a real PostgreSQL schema migrated via ``alembic upgrade head`` against the
Chapter 4 specification (``docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql``)
and ``tests/security/grants_manifest.csv``. Supports progressive wave rollouts by
auditing all V11.3 relations already present while automatically enforcing complete
123-table Gate 2b parity once every Chapter 4 table has been migrated.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg

from app.ops.ddl_rls_audit import (
    ANSWER_TABLES,
    GRADING_POLICIES,
    MANIFEST_DB_ROLE_NAMES,
)
from app.platform.rls import LEGACY_TABLES
from app.platform.tenancy import DbRole

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHAPTER4_DDL_PATH = _REPO_ROOT / "docs" / "architecture" / "chapters" / "04_postgresql_ddl_rls_grants.sql"
DEFAULT_GRANTS_MANIFEST_PATH = _REPO_ROOT / "tests" / "security" / "grants_manifest.csv"

EXPECTED_CHAPTER4_LOGICAL_TABLE_COUNT = 123
EXPECTED_CHAPTER4_SECURITY_VIEWS: frozenset[str] = frozenset({"v_users_status", "v_public_question_versions"})
EXPECTED_PARTITIONED_PARENTS: tuple[str, ...] = ("audit_events", "response_revisions")

RUNTIME_ROLE_NAMES: tuple[str, ...] = tuple(role.value for role in DbRole)
MANIFEST_ROLE_NAMES: tuple[str, ...] = tuple(role for role in RUNTIME_ROLE_NAMES if role != DbRole.APP_RW.value)
ALL_MANAGED_ROLE_NAMES: tuple[str, ...] = tuple(sorted(MANIFEST_DB_ROLE_NAMES))

# `questions` exists pre-TASK-17 (legacy serial PK without tenant_id) and post-TASK-17 (V11.3 UUID with tenant_id).
NON_COLLIDING_LEGACY_TABLES: frozenset[str] = LEGACY_TABLES - {"questions"}

# Decision #7 (IMPLEMENTATION_PLAN_V11_3.md): ulp_migrator owns F04 SECURITY DEFINER helpers and carries an
# explicit owner-scoped policy on these three tables so FORCE ROW LEVEL SECURITY permits internal helper calls.
OWNER_POLICY_TABLES: frozenset[str] = frozenset({"users", "user_pii_encryption_keys", "memberships"})
DECISION7_RLS_TENANTLESS_TABLES: frozenset[str] = frozenset({"users", "user_pii_encryption_keys"})
# Decision #7 allows `notification_deliveries` to add `tenant_id` in Alembic even though Chapter 4 DDL omitted it.
TENANT_ENABLED_DECISION7_TABLES: frozenset[str] = frozenset({"notification_deliveries"})

SCHEDULER_POLICIES: dict[str, str] = {
    "outbox_events": "sys_outbox_relay",
    "attempts": "sys_attempt_expiry_scan",
    "analytics_events": "sys_rollup_read",
    "daily_metric_rollups": "sys_rollup_write",
}

ANSWER_DENIED_ROLES: tuple[str, ...] = (
    "ulp_exam_session_rw",
    "ulp_worker_ingestion",
    "ulp_system_scheduler",
    "ulp_analytics_ro",
    "PUBLIC",
)
APPEND_ONLY_TABLES: tuple[str, ...] = ("audit_events", "response_revisions")
APP_RW_READONLY_TABLES: tuple[str, ...] = ("fts_dictionary_versions", "daily_metric_rollups", "inbox_events")

# M0.2 foundation migration (c1d2e3f4a5b6) granted read-only SELECT on `tenants` to all runtime roles and on
# `fts_dictionary_versions` to `ulp_analytics_ro`, whereas Chapter 4 section 5c-5f REVOKE ALL omitted them.
# Keeping this exact 6-tuple set explicit surfaces the foundation delta without masking any unexpected grant.
FOUNDATION_MANIFEST_GRANT_DELTAS: frozenset[tuple[str, str, str, str]] = frozenset(
    {
        *(
            (role, "tenants", "SELECT", "")
            for role in MANIFEST_ROLE_NAMES
        ),
        ("ulp_analytics_ro", "fts_dictionary_versions", "SELECT", ""),
    }
)

_INITPLAN_GUARDED_FUNCTIONS: tuple[str, ...] = (
    "current_app_tenant_id",
    "app_system_job",
    "current_app_user_id",
)

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+(?:public\.)?([a-z_][a-z0-9_]*)\s*(?:\(|PARTITION\s+OF\b)",
    re.IGNORECASE,
)
_PARTITION_TABLE_RE = re.compile(
    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+(?:public\.)?([a-z_][a-z0-9_]*)\s+PARTITION\s+OF\b",
    re.IGNORECASE,
)
_CREATE_VIEW_RE = re.compile(
    r"CREATE(?:\s+OR\s+REPLACE)?\s+VIEW\s+(?:public\.)?([a-z_][a-z0-9_]*)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Chapter4Spec:
    """Canonical structural and privilege expectations parsed from Chapter 4 DDL and grants_manifest.csv."""

    logical_tables: frozenset[str]
    strict_tenant_tables: frozenset[str]
    hybrid_public_tables: frozenset[str]
    tenantless_tables: frozenset[str]
    security_views: frozenset[str]
    ddl_partitions: frozenset[str]
    manifest_grants: frozenset[tuple[str, str, str, str]]
    manifest_objects: frozenset[str]


@dataclass(frozen=True)
class LiveRelation:
    """Snapshot of a relation (`pg_class`) in the `public` schema."""

    name: str
    relkind: str
    is_partition: bool
    parent_name: str | None
    rls_enabled: bool
    rls_forced: bool
    reloptions: tuple[str, ...]
    has_tenant_id: bool


@dataclass(frozen=True)
class LivePolicy:
    """Snapshot of a PostgreSQL RLS policy (`pg_policies`) in `public`."""

    table_name: str
    policy_name: str
    roles: tuple[str, ...]
    cmd: str
    qual: str
    with_check: str


@dataclass(frozen=True)
class LiveRole:
    """Snapshot of a PostgreSQL role and its `public` schema privileges."""

    name: str
    superuser: bool
    bypassrls: bool
    createrole: bool
    createdb: bool
    canlogin: bool
    schema_usage: bool
    schema_create: bool


@dataclass(frozen=True)
class LiveSecurityDefinerFn:
    """Snapshot of a non-extension `SECURITY DEFINER` function in `public`."""

    name: str
    owner: str
    config: tuple[str, ...]
    public_execute: bool


@dataclass(frozen=True)
class LiveSchemaSnapshot:
    """Complete read-only security snapshot of a live database schema."""

    relations: tuple[LiveRelation, ...]
    policies: tuple[LivePolicy, ...]
    roles: tuple[LiveRole, ...]
    unexpected_privileged_ulp_roles: tuple[str, ...]
    public_schema_create: bool
    manifest_table_grants: frozenset[tuple[str, str, str]]
    manifest_column_grants: frozenset[tuple[str, str, str, str]]
    all_table_grants: frozenset[tuple[str, str, str]]
    public_column_grants: frozenset[tuple[str, str, str]]
    partition_effective_privileges: tuple[tuple[str, str, str], ...]
    sensitive_effective_privileges: tuple[tuple[str, str, str], ...]
    security_definer_functions: tuple[LiveSecurityDefinerFn, ...]


@dataclass(frozen=True)
class AlembicSchemaAuditReport:
    """Reconciliation result comparing live Alembic database state against Chapter 4."""

    passed: bool
    full_schema_enforced: bool
    is_schema_complete: bool
    expected_logical_table_count: int
    migrated_v113_tables: tuple[str, ...]
    migrated_chapter4_tables: tuple[str, ...]
    missing_chapter4_tables: tuple[str, ...]
    unexpected_v113_tables: tuple[str, ...]
    legacy_tables_present: tuple[str, ...]
    partitions_present: tuple[str, ...]
    views_present: tuple[str, ...]
    missing_rls_enable: tuple[str, ...]
    missing_rls_force: tuple[str, ...]
    missing_rls_policy: tuple[str, ...]
    policy_shape_violations: tuple[str, ...]
    initplan_violations: tuple[str, ...]
    permissive_policy_violations: tuple[str, ...]
    role_violations: tuple[str, ...]
    schema_privilege_violations: tuple[str, ...]
    partition_violations: tuple[str, ...]
    view_violations: tuple[str, ...]
    missing_manifest_grants: tuple[str, ...]
    extra_manifest_grants: tuple[str, ...]
    column_grant_sync_violations: tuple[str, ...]
    foundation_manifest_deltas: tuple[str, ...]
    sensitive_grant_violations: tuple[str, ...]
    security_definer_violations: tuple[str, ...]

    def failure_summary(self) -> str:
        """Render non-empty violation buckets into a concise failure message."""
        buckets = (
            (
                "missing_chapter4_tables (full schema required)",
                self.missing_chapter4_tables if self.full_schema_enforced else (),
            ),
            ("missing_rls_enable", self.missing_rls_enable),
            ("missing_rls_force", self.missing_rls_force),
            ("missing_rls_policy", self.missing_rls_policy),
            ("policy_shape_violations", self.policy_shape_violations),
            ("initplan_violations", self.initplan_violations),
            ("permissive_policy_violations", self.permissive_policy_violations),
            ("role_violations", self.role_violations),
            ("schema_privilege_violations", self.schema_privilege_violations),
            ("partition_violations", self.partition_violations),
            ("view_violations", self.view_violations),
            ("missing_manifest_grants", self.missing_manifest_grants),
            ("extra_manifest_grants", self.extra_manifest_grants),
            ("column_grant_sync_violations", self.column_grant_sync_violations),
            ("sensitive_grant_violations", self.sensitive_grant_violations),
            ("security_definer_violations", self.security_definer_violations),
        )
        lines = [f"{label}: {list(items)}" for label, items in buckets if items]
        return "; ".join(lines) if lines else "no violations"


def _extract_pg_array_literals(sql: str, variable_name: str) -> frozenset[str]:
    match = re.search(rf"{re.escape(variable_name)}\s+text\[\]\s*:=\s*ARRAY\[(.*?)\];", sql, re.DOTALL)
    if match is None:
        raise ValueError(f"Could not find `{variable_name}` array in Chapter 4 DDL")
    return frozenset(re.findall(r"'([a-z_][a-z0-9_]*)'", match.group(1)))


def load_grants_manifest(manifest_path: Path = DEFAULT_GRANTS_MANIFEST_PATH) -> frozenset[tuple[str, str, str, str]]:
    """Load `(role, object, privilege, column)` tuples from `tests/security/grants_manifest.csv`."""
    with manifest_path.open(encoding="utf-8", newline="") as handle:
        return frozenset(
            (row["role"].strip(), row["object"].strip(), row["privilege"].strip(), row["column"].strip())
            for row in csv.DictReader(handle)
        )


def load_chapter4_spec(
    ddl_path: Path = DEFAULT_CHAPTER4_DDL_PATH,
    manifest_path: Path = DEFAULT_GRANTS_MANIFEST_PATH,
) -> Chapter4Spec:
    """Parse canonical logical tables, RLS sets, views, partitions and manifest grants from Chapter 4."""
    sql = ddl_path.read_text(encoding="utf-8")
    all_created = frozenset(_CREATE_TABLE_RE.findall(sql))
    ddl_partitions = frozenset(_PARTITION_TABLE_RE.findall(sql))
    logical_tables = all_created - ddl_partitions
    strict_tables = _extract_pg_array_literals(sql, "strict_tenant_tables")
    hybrid_tables = _extract_pg_array_literals(sql, "hybrid_public_tables")
    tenantless_tables = logical_tables - strict_tables - hybrid_tables
    security_views = frozenset(_CREATE_VIEW_RE.findall(sql))
    manifest_grants = load_grants_manifest(manifest_path)
    manifest_objects = frozenset(obj for _, obj, _, _ in manifest_grants)
    return Chapter4Spec(
        logical_tables=logical_tables,
        strict_tenant_tables=strict_tables,
        hybrid_public_tables=hybrid_tables,
        tenantless_tables=tenantless_tables,
        security_views=security_views,
        ddl_partitions=ddl_partitions,
        manifest_grants=manifest_grants,
        manifest_objects=manifest_objects,
    )


def _decode_relkind(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("ascii")
    return str(value)


def _format_grant_tuple(item: tuple[str, ...]) -> str:
    return ":".join(item)


def _is_legacy_relation(rel: LiveRelation) -> bool:
    if rel.name.startswith("legacy_") or rel.name in NON_COLLIDING_LEGACY_TABLES:
        return True
    # Pre-TASK-17 legacy `questions` uses integer PK without `tenant_id`; V11.3 `questions` always has `tenant_id`.
    if rel.name == "questions" and not rel.has_tenant_id:
        return True
    return False


async def inspect_live_schema(conn: asyncpg.Connection) -> LiveSchemaSnapshot:
    """Capture a complete catalog and privilege snapshot from a live PostgreSQL database."""
    rel_rows = await conn.fetch(
        """
        SELECT
            c.relname AS name,
            c.relkind::text AS relkind,
            c.relispartition AS is_partition,
            parent.relname AS parent_name,
            c.relrowsecurity AS rls_enabled,
            c.relforcerowsecurity AS rls_forced,
            coalesce(c.reloptions, ARRAY[]::text[]) AS reloptions,
            EXISTS(
                SELECT 1
                FROM pg_attribute a
                WHERE a.attrelid = c.oid
                  AND a.attname = 'tenant_id'
                  AND NOT a.attisdropped
            ) AS has_tenant_id
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_inherits i ON i.inhrelid = c.oid
        LEFT JOIN pg_class parent ON parent.oid = i.inhparent
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'v', 'm')
        ORDER BY c.relname
        """
    )
    relations = tuple(
        LiveRelation(
            name=row["name"],
            relkind=_decode_relkind(row["relkind"]),
            is_partition=bool(row["is_partition"]),
            parent_name=row["parent_name"],
            rls_enabled=bool(row["rls_enabled"]),
            rls_forced=bool(row["rls_forced"]),
            reloptions=tuple(row["reloptions"] or ()),
            has_tenant_id=bool(row["has_tenant_id"]),
        )
        for row in rel_rows
    )

    policy_rows = await conn.fetch(
        """
        SELECT
            tablename,
            policyname,
            roles::text[] AS roles,
            cmd,
            coalesce(qual, '') AS qual,
            coalesce(with_check, '') AS with_check
        FROM pg_policies
        WHERE schemaname = 'public'
        ORDER BY tablename, policyname
        """
    )
    policies = tuple(
        LivePolicy(
            table_name=row["tablename"],
            policy_name=row["policyname"],
            roles=tuple(sorted(row["roles"] or ())),
            cmd=row["cmd"],
            qual=row["qual"],
            with_check=row["with_check"],
        )
        for row in policy_rows
    )

    role_rows = await conn.fetch(
        """
        SELECT
            rolname,
            rolsuper,
            rolbypassrls,
            rolcreaterole,
            rolcreatedb,
            rolcanlogin,
            has_schema_privilege(rolname, 'public', 'USAGE') AS schema_usage,
            has_schema_privilege(rolname, 'public', 'CREATE') AS schema_create
        FROM pg_roles
        WHERE rolname = ANY($1::text[])
        ORDER BY rolname
        """,
        list(ALL_MANAGED_ROLE_NAMES),
    )
    roles = tuple(
        LiveRole(
            name=row["rolname"],
            superuser=bool(row["rolsuper"]),
            bypassrls=bool(row["rolbypassrls"]),
            createrole=bool(row["rolcreaterole"]),
            createdb=bool(row["rolcreatedb"]),
            canlogin=bool(row["rolcanlogin"]),
            schema_usage=bool(row["schema_usage"]),
            schema_create=bool(row["schema_create"]),
        )
        for row in role_rows
    )

    unexpected_ulp_rows = await conn.fetch(
        """
        SELECT rolname
        FROM pg_roles
        WHERE starts_with(rolname, 'ulp_')
          AND (rolbypassrls OR rolsuper OR rolcreaterole OR rolcreatedb)
        ORDER BY rolname
        """
    )
    unexpected_privileged_ulp_roles = tuple(row["rolname"] for row in unexpected_ulp_rows)
    public_schema_create = bool(
        await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_namespace n,
                     LATERAL aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) a
                WHERE n.nspname = 'public'
                  AND a.grantee = 0
                  AND a.privilege_type = 'CREATE'
            )
            """
        )
    )

    all_table_grant_rows = await conn.fetch(
        """
        SELECT grantee, table_name, privilege_type
        FROM information_schema.role_table_grants
        WHERE table_schema = 'public'
          AND grantee = ANY($1::text[])
        """,
        [*RUNTIME_ROLE_NAMES, "PUBLIC"],
    )
    all_table_grants = frozenset(
        (row["grantee"], row["table_name"], row["privilege_type"]) for row in all_table_grant_rows
    )
    manifest_role_set = set(MANIFEST_ROLE_NAMES)
    manifest_table_grants = frozenset(
        grant for grant in all_table_grants if grant[0] in manifest_role_set
    )

    manifest_col_grant_rows = await conn.fetch(
        """
        SELECT grantee, table_name, privilege_type, column_name
        FROM information_schema.role_column_grants
        WHERE table_schema = 'public'
          AND grantee = ANY($1::text[])
        """,
        list(MANIFEST_ROLE_NAMES),
    )
    manifest_column_grants = frozenset(
        (row["grantee"], row["table_name"], row["privilege_type"], row["column_name"])
        for row in manifest_col_grant_rows
    )

    public_col_rows = await conn.fetch(
        """
        SELECT table_name, privilege_type, column_name
        FROM information_schema.column_privileges
        WHERE table_schema = 'public'
          AND grantee = 'PUBLIC'
        """
    )
    public_column_grants = frozenset(
        (row["table_name"], row["privilege_type"], row["column_name"]) for row in public_col_rows
    )

    partition_names = [rel.name for rel in relations if rel.is_partition]
    partition_effective_privileges: tuple[tuple[str, str, str], ...] = ()
    if partition_names:
        part_priv_rows = await conn.fetch(
            """
            SELECT r AS grantee, t AS table_name, p AS privilege
            FROM unnest($1::text[]) AS r
            CROSS JOIN unnest($2::text[]) AS t
            CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) AS p
            WHERE has_table_privilege(r, format('public.%I', t), p)
            ORDER BY r, t, p
            """,
            list(RUNTIME_ROLE_NAMES),
            partition_names,
        )
        partition_effective_privileges = tuple(
            (row["grantee"], row["table_name"], row["privilege"]) for row in part_priv_rows
        )

    present_names = {rel.name for rel in relations}
    sensitive_candidates = [
        name
        for name in (
            "user_pii_encryption_keys",
            *ANSWER_TABLES,
            *APPEND_ONLY_TABLES,
            *APP_RW_READONLY_TABLES,
        )
        if name in present_names
    ]
    sensitive_effective_privileges: tuple[tuple[str, str, str], ...] = ()
    if sensitive_candidates:
        sens_priv_rows = await conn.fetch(
            """
            SELECT r AS grantee, t AS table_name, p AS privilege
            FROM unnest($1::text[]) AS r
            CROSS JOIN unnest($2::text[]) AS t
            CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) AS p
            WHERE has_table_privilege(r, format('public.%I', t), p)
            ORDER BY r, t, p
            """,
            list(RUNTIME_ROLE_NAMES),
            sensitive_candidates,
        )
        sensitive_effective_privileges = tuple(
            (row["grantee"], row["table_name"], row["privilege"]) for row in sens_priv_rows
        )

    definer_rows = await conn.fetch(
        """
        SELECT
            p.proname AS name,
            pg_get_userbyid(p.proowner) AS owner,
            coalesce(p.proconfig, ARRAY[]::text[]) AS config,
            EXISTS (
                SELECT 1
                FROM aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
                WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE'
            ) AS public_execute
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.prosecdef IS TRUE
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend d
              WHERE d.objid = p.oid AND d.deptype = 'e'
          )
        ORDER BY p.proname
        """
    )
    security_definer_functions = tuple(
        LiveSecurityDefinerFn(
            name=row["name"],
            owner=row["owner"],
            config=tuple(row["config"] or ()),
            public_execute=bool(row["public_execute"]),
        )
        for row in definer_rows
    )

    return LiveSchemaSnapshot(
        relations=relations,
        policies=policies,
        roles=roles,
        unexpected_privileged_ulp_roles=unexpected_privileged_ulp_roles,
        public_schema_create=public_schema_create,
        manifest_table_grants=manifest_table_grants,
        manifest_column_grants=manifest_column_grants,
        all_table_grants=all_table_grants,
        public_column_grants=public_column_grants,
        partition_effective_privileges=partition_effective_privileges,
        sensitive_effective_privileges=sensitive_effective_privileges,
        security_definer_functions=security_definer_functions,
    )


def _classify_relations(
    relations: tuple[LiveRelation, ...],
) -> tuple[dict[str, LiveRelation], dict[str, LiveRelation], dict[str, LiveRelation], dict[str, LiveRelation]]:
    v113_tables: dict[str, LiveRelation] = {}
    legacy_tables: dict[str, LiveRelation] = {}
    partitions: dict[str, LiveRelation] = {}
    views: dict[str, LiveRelation] = {}

    for rel in relations:
        if rel.name == "alembic_version":
            continue
        if rel.relkind == "v":
            views[rel.name] = rel
        elif rel.is_partition:
            partitions[rel.name] = rel
        elif rel.relkind in ("r", "p"):
            if _is_legacy_relation(rel):
                legacy_tables[rel.name] = rel
            else:
                v113_tables[rel.name] = rel
    return v113_tables, legacy_tables, partitions, views


def _audit_roles_and_schema(snapshot: LiveSchemaSnapshot) -> tuple[list[str], list[str]]:
    role_violations: list[str] = []
    schema_violations: list[str] = []

    by_name = {role.name: role for role in snapshot.roles}
    for expected in ALL_MANAGED_ROLE_NAMES:
        role = by_name.get(expected)
        if role is None:
            role_violations.append(f"{expected}: missing from pg_roles")
            continue
        if role.bypassrls:
            role_violations.append(f"{expected}: rolbypassrls=true")
        if role.superuser:
            role_violations.append(f"{expected}: rolsuper=true")
        if role.createrole or role.createdb:
            role_violations.append(f"{expected}: elevated role creation flags")
        if role.canlogin:
            role_violations.append(f"{expected}: group role must have NOLOGIN")
        if not role.schema_usage:
            schema_violations.append(f"{expected}: missing USAGE on schema public")
        if expected in RUNTIME_ROLE_NAMES and role.schema_create:
            schema_violations.append(f"{expected}: has CREATE on schema public")

    for rogue in snapshot.unexpected_privileged_ulp_roles:
        if rogue not in by_name:
            role_violations.append(f"{rogue}: unexpected privileged ulp_* role")

    if snapshot.public_schema_create:
        schema_violations.append("PUBLIC: has CREATE on schema public")

    return sorted(role_violations), sorted(schema_violations)


def _find_bare_guc_calls(policy: LivePolicy) -> list[str]:
    expr = f"{policy.qual}\n{policy.with_check}"
    found: list[str] = []
    for fn_name in _INITPLAN_GUARDED_FUNCTIONS:
        stripped = re.sub(rf"\bSELECT\s+{fn_name}\(\)", "", expr, flags=re.IGNORECASE)
        if re.search(rf"\b{fn_name}\(\)", stripped):
            found.append(f"{policy.table_name}.{policy.policy_name}: bare {fn_name}() without SELECT InitPlan")
    return found


def _is_trivial_true(expr: str) -> bool:
    normalized = expr.strip().strip("()").lower()
    return normalized == "true"


def _audit_rls_and_policies(
    v113_tables: dict[str, LiveRelation],
    policies: tuple[LivePolicy, ...],
    spec: Chapter4Spec,
) -> tuple[list[str], list[str], list[str], list[str], list[str], list[str]]:
    missing_enable: list[str] = []
    missing_force: list[str] = []
    missing_policy: list[str] = []
    shape_violations: list[str] = []
    initplan_violations: list[str] = []
    permissive_violations: list[str] = []

    policies_by_table: dict[str, list[LivePolicy]] = {}
    for pol in policies:
        policies_by_table.setdefault(pol.table_name, []).append(pol)
        initplan_violations.extend(_find_bare_guc_calls(pol))
        is_allowed_owner = (
            pol.policy_name == "f04_owner_policy"
            and pol.table_name in OWNER_POLICY_TABLES
            and pol.roles == ("ulp_migrator",)
        )
        if not is_allowed_owner and (_is_trivial_true(pol.qual) or _is_trivial_true(pol.with_check)):
            permissive_violations.append(f"{pol.table_name}.{pol.policy_name}: permissive true clause for {pol.roles}")

    for name, rel in sorted(v113_tables.items()):
        requires_rls = rel.has_tenant_id or name in DECISION7_RLS_TENANTLESS_TABLES
        if not requires_rls:
            if name in spec.strict_tenant_tables or name in spec.hybrid_public_tables:
                shape_violations.append(f"{name}: expected tenant_id column per Chapter 4")
            continue

        if not rel.rls_enabled:
            missing_enable.append(name)
        if not rel.rls_forced:
            missing_force.append(name)

        table_policies = policies_by_table.get(name, [])
        if not table_policies:
            missing_policy.append(name)
            continue

        by_name = {pol.policy_name: pol for pol in table_policies}
        if name in spec.hybrid_public_tables:
            for required in ("hybrid_read_policy", "hybrid_write_policy"):
                if required not in by_name:
                    shape_violations.append(f"{name}: missing {required}")
            if "hybrid_write_policy" in by_name:
                write_pol = by_name["hybrid_write_policy"]
                if "current_app_tenant_id" not in write_pol.qual or "current_app_tenant_id" not in write_pol.with_check:
                    shape_violations.append(f"{name}.hybrid_write_policy: missing current_app_tenant_id guard")
        elif name in spec.strict_tenant_tables or (rel.has_tenant_id and name in TENANT_ENABLED_DECISION7_TABLES):
            shape_violations.extend(_check_tenant_isolation_policy(name, by_name))
            if "hybrid_read_policy" in by_name or "hybrid_write_policy" in by_name:
                shape_violations.append(f"{name}: strict tenant table must not carry hybrid_* policies")
        elif name == "users":
            for required in ("users_member_read_policy", "users_member_update_policy"):
                if required not in by_name:
                    shape_violations.append(f"users: missing {required}")
        elif name == "user_pii_encryption_keys":
            for pol in table_policies:
                if pol.roles != ("ulp_migrator",):
                    shape_violations.append(
                        f"user_pii_encryption_keys.{pol.policy_name}: owner-only policy expected, got {pol.roles}"
                    )

        if name in SCHEDULER_POLICIES:
            sched_policy = SCHEDULER_POLICIES[name]
            if sched_policy not in by_name:
                shape_violations.append(f"{name}: missing scheduler policy {sched_policy}")
            elif "ulp_system_scheduler" not in by_name[sched_policy].roles:
                shape_violations.append(f"{name}.{sched_policy}: must target ulp_system_scheduler")

        if name in GRADING_POLICIES:
            grading_policy = GRADING_POLICIES[name]
            if grading_policy not in by_name:
                shape_violations.append(f"{name}: missing grading policy {grading_policy}")
            elif "ulp_worker_grading" not in by_name[grading_policy].roles:
                shape_violations.append(f"{name}.{grading_policy}: must target ulp_worker_grading")

    return (
        sorted(missing_enable),
        sorted(missing_force),
        sorted(missing_policy),
        sorted(shape_violations),
        sorted(set(initplan_violations)),
        sorted(permissive_violations),
    )


def _check_tenant_isolation_policy(table_name: str, by_name: dict[str, LivePolicy], *, label: str = "") -> list[str]:
    prefix = f"{label} " if label else ""
    iso = by_name.get("tenant_isolation_policy")
    if iso is None:
        return [f"{table_name}: {prefix}missing tenant_isolation_policy"]
    if "current_app_tenant_id" not in iso.qual or "current_app_tenant_id" not in iso.with_check:
        return [
            f"{table_name}.tenant_isolation_policy: {prefix}must check current_app_tenant_id() on USING and WITH CHECK"
        ]
    return []


def _audit_partitions(
    v113_tables: dict[str, LiveRelation],
    partitions: dict[str, LiveRelation],
    policies: tuple[LivePolicy, ...],
    snapshot: LiveSchemaSnapshot,
) -> list[str]:
    violations: list[str] = []
    policies_by_table: dict[str, dict[str, LivePolicy]] = {}
    for pol in policies:
        policies_by_table.setdefault(pol.table_name, {})[pol.policy_name] = pol

    for parent in EXPECTED_PARTITIONED_PARENTS:
        if parent in v113_tables:
            rel = v113_tables[parent]
            if rel.relkind != "p":
                violations.append(f"{parent}: expected partitioned table relkind='p', got {rel.relkind!r}")
            children = [p for p in partitions.values() if p.parent_name == parent]
            if not children:
                violations.append(f"{parent}: partitioned table has 0 attached child partitions")

    for name, rel in sorted(partitions.items()):
        if not (rel.rls_enabled and rel.rls_forced):
            violations.append(f"{name}: partition missing ENABLE+FORCE ROW LEVEL SECURITY")
        violations.extend(_check_tenant_isolation_policy(name, policies_by_table.get(name, {}), label="partition"))

    partition_set = set(partitions)
    for role, table_name, priv in snapshot.all_table_grants:
        if table_name in partition_set:
            violations.append(f"{name_and_priv(role, table_name, priv)}: direct role_table_grants on partition")
    for role, table_name, priv, col in snapshot.manifest_column_grants:
        if table_name in partition_set:
            violations.append(f"{role}:{table_name}.{col}:{priv}: direct role_column_grants on partition")
    for table_name, priv, col in snapshot.public_column_grants:
        if table_name in partition_set:
            violations.append(f"PUBLIC:{table_name}.{col}:{priv}: direct column grant on partition")
    for role, table_name, priv in snapshot.partition_effective_privileges:
        violations.append(f"{role}:{table_name}:{priv}: runtime role holds effective table privilege on partition")

    return sorted(set(violations))


def name_and_priv(role: str, table: str, priv: str) -> str:
    return f"{role}:{table}:{priv}"


def _audit_views(views: dict[str, LiveRelation], spec: Chapter4Spec, full_schema_enforced: bool) -> list[str]:
    violations: list[str] = []
    for name, rel in sorted(views.items()):
        if "security_invoker=true" not in rel.reloptions:
            violations.append(f"{name}: missing security_invoker=true in reloptions ({list(rel.reloptions)})")
    if full_schema_enforced:
        for missing_view in sorted(spec.security_views - set(views)):
            violations.append(f"{missing_view}: expected Chapter 4 security view missing")
    return sorted(violations)


def _audit_grants(
    v113_tables: dict[str, LiveRelation],
    views: dict[str, LiveRelation],
    partitions: dict[str, LiveRelation],
    snapshot: LiveSchemaSnapshot,
    spec: Chapter4Spec,
) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    migrated_objects = set(v113_tables) | set(views)
    expected_manifest = {entry for entry in spec.manifest_grants if entry[1] in migrated_objects}

    # Combine table-level grants and column-only grants on migrated objects (excluding partitions, audited in F-01).
    actual_table = {
        (role, obj, priv, "")
        for (role, obj, priv) in snapshot.manifest_table_grants
        if obj in migrated_objects and obj not in partitions
    }
    actual_table_keys = {(role, obj, priv) for (role, obj, priv, _) in actual_table}
    actual_col_only = {
        (role, obj, priv, col)
        for (role, obj, priv, col) in snapshot.manifest_column_grants
        if obj in migrated_objects and obj not in partitions and (role, obj, priv) not in actual_table_keys
    }
    actual_manifest = actual_table | actual_col_only

    missing = [_format_grant_tuple(item) for item in sorted(expected_manifest - actual_manifest)]
    raw_extra = actual_manifest - expected_manifest
    foundation_deltas = [_format_grant_tuple(item) for item in sorted(raw_extra & FOUNDATION_MANIFEST_GRANT_DELTAS)]
    extra = [_format_grant_tuple(item) for item in sorted(raw_extra - FOUNDATION_MANIFEST_GRANT_DELTAS)]

    # Explicitly verify column-level grants from `role_column_grants` haven't widened or gone missing.
    col_sync_violations: list[str] = []
    for role, obj, priv, col in sorted(expected_manifest):
        if col:
            if (role, obj, priv, col) not in snapshot.manifest_column_grants:
                col_sync_violations.append(
                    f"{role}:{obj}.{col}:{priv}: absent from information_schema.role_column_grants"
                )
            if (role, obj, priv) in snapshot.manifest_table_grants:
                col_sync_violations.append(f"{role}:{obj}:{priv}: widened to whole table instead of column ({col})")

    sensitive_violations: list[str] = []
    for role, obj, priv in snapshot.all_table_grants:
        if role == "PUBLIC" and (obj in migrated_objects or obj in partitions):
            sensitive_violations.append(f"PUBLIC:{obj}:{priv}: PUBLIC must hold zero grants on V11.3 relations")
        if priv == "TRUNCATE" and obj in migrated_objects:
            sensitive_violations.append(f"{role}:{obj}:TRUNCATE: runtime roles must not hold TRUNCATE")
    for obj, priv, col in snapshot.public_column_grants:
        if obj in migrated_objects or obj in partitions:
            sensitive_violations.append(f"PUBLIC:{obj}.{col}:{priv}: PUBLIC column grant forbidden")

    for role, table_name, priv in snapshot.sensitive_effective_privileges:
        if table_name == "user_pii_encryption_keys":
            sensitive_violations.append(f"{role}:{table_name}:{priv}: key vault table closed to all runtime roles")
        elif table_name in ANSWER_TABLES and role in ANSWER_DENIED_ROLES:
            sensitive_violations.append(f"{role}:{table_name}:{priv}: answer table closed to {role}")
        elif table_name in APPEND_ONLY_TABLES and priv in ("UPDATE", "DELETE", "TRUNCATE"):
            sensitive_violations.append(f"{role}:{table_name}:{priv}: append-only table must deny {priv}")
        elif (
            table_name in APP_RW_READONLY_TABLES
            and role == DbRole.APP_RW.value
            and priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE")
        ):
            sensitive_violations.append(f"{role}:{table_name}:{priv}: ulp_app_rw must remain read-only")

    return (
        missing,
        extra,
        sorted(set(col_sync_violations)),
        foundation_deltas,
        sorted(set(sensitive_violations)),
    )


def _audit_security_definers(snapshot: LiveSchemaSnapshot) -> list[str]:
    violations: list[str] = []
    for fn in snapshot.security_definer_functions:
        if fn.owner != "ulp_migrator":
            violations.append(f"{fn.name}: SECURITY DEFINER owner must be ulp_migrator, got {fn.owner!r}")
        if not any(cfg.startswith("search_path=") for cfg in fn.config):
            violations.append(f"{fn.name}: SECURITY DEFINER function missing pinned SET search_path")
        if fn.public_execute:
            violations.append(f"{fn.name}: SECURITY DEFINER function grants EXECUTE to PUBLIC")
    return sorted(violations)


def reconcile_live_schema(
    snapshot: LiveSchemaSnapshot,
    spec: Chapter4Spec,
    *,
    require_full_schema: bool | None = None,
) -> AlembicSchemaAuditReport:
    """Evaluate a live database snapshot against Chapter 4 invariants and `grants_manifest.csv`."""
    v113_tables, legacy_tables, partitions, views = _classify_relations(snapshot.relations)
    migrated_c4 = sorted(set(v113_tables) & spec.logical_tables)
    missing_c4 = sorted(spec.logical_tables - set(v113_tables))
    unexpected_v113 = sorted(set(v113_tables) - spec.logical_tables)
    is_schema_complete = len(missing_c4) == 0
    full_enforced = is_schema_complete if require_full_schema is None else require_full_schema

    role_violations, schema_violations = _audit_roles_and_schema(snapshot)
    (
        missing_enable,
        missing_force,
        missing_policy,
        shape_violations,
        initplan_violations,
        permissive_violations,
    ) = _audit_rls_and_policies(v113_tables, snapshot.policies, spec)
    partition_violations = _audit_partitions(v113_tables, partitions, snapshot.policies, snapshot)
    view_violations = _audit_views(views, spec, full_enforced)
    missing_grants, extra_grants, col_sync, foundation_deltas, sensitive_violations = _audit_grants(
        v113_tables, views, partitions, snapshot, spec
    )
    definer_violations = _audit_security_definers(snapshot)

    passed = not any(
        [
            missing_c4 if full_enforced else [],
            missing_enable,
            missing_force,
            missing_policy,
            shape_violations,
            initplan_violations,
            permissive_violations,
            role_violations,
            schema_violations,
            partition_violations,
            view_violations,
            missing_grants,
            extra_grants,
            col_sync,
            sensitive_violations,
            definer_violations,
        ]
    )

    return AlembicSchemaAuditReport(
        passed=passed,
        full_schema_enforced=full_enforced,
        is_schema_complete=is_schema_complete,
        expected_logical_table_count=len(spec.logical_tables),
        migrated_v113_tables=tuple(sorted(v113_tables)),
        migrated_chapter4_tables=tuple(migrated_c4),
        missing_chapter4_tables=tuple(missing_c4),
        unexpected_v113_tables=tuple(unexpected_v113),
        legacy_tables_present=tuple(sorted(legacy_tables)),
        partitions_present=tuple(sorted(partitions)),
        views_present=tuple(sorted(views)),
        missing_rls_enable=tuple(missing_enable),
        missing_rls_force=tuple(missing_force),
        missing_rls_policy=tuple(missing_policy),
        policy_shape_violations=tuple(shape_violations),
        initplan_violations=tuple(initplan_violations),
        permissive_policy_violations=tuple(permissive_violations),
        role_violations=tuple(role_violations),
        schema_privilege_violations=tuple(schema_violations),
        partition_violations=tuple(partition_violations),
        view_violations=tuple(view_violations),
        missing_manifest_grants=tuple(missing_grants),
        extra_manifest_grants=tuple(extra_grants),
        column_grant_sync_violations=tuple(col_sync),
        foundation_manifest_deltas=tuple(foundation_deltas),
        sensitive_grant_violations=tuple(sensitive_violations),
        security_definer_violations=tuple(definer_violations),
    )


async def audit_live_alembic_schema(
    conn: asyncpg.Connection,
    *,
    require_full_schema: bool | None = None,
    ddl_path: Path = DEFAULT_CHAPTER4_DDL_PATH,
    manifest_path: Path = DEFAULT_GRANTS_MANIFEST_PATH,
) -> AlembicSchemaAuditReport:
    """Inspect a live PostgreSQL database connection and reconcile it against Chapter 4."""
    spec = load_chapter4_spec(ddl_path=ddl_path, manifest_path=manifest_path)
    snapshot = await inspect_live_schema(conn)
    return reconcile_live_schema(snapshot, spec, require_full_schema=require_full_schema)
