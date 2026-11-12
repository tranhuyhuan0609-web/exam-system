"""Row-level security building blocks for module migrations, and the list of tables exempt from RLS.

The SQL builders are used by Alembic revisions. Treat them as append-only: changing what an
existing builder returns would change what already-released migrations do on a fresh database.
"""

GLOBAL_TABLES = frozenset({"alembic_version", "tenants", "fts_dictionary_versions"})
"""Platform tables with no tenant of their own."""

LEGACY_TABLES = frozenset(
    {
        "ai_cache", "ai_usage", "answer_keys", "categories", "crawl_category_map", "crawl_runs",
        "crawled_questions", "crawler_sites", "exam_attempts", "exam_enrollments", "exam_questions",
        "exam_targets", "exams", "import_jobs", "knowledge_articles", "knowledge_units",
        "library_documents", "library_views", "question_categories", "question_target_tags",
        "questions", "target_curriculum_mappings", "users",
    }
)  # fmt: skip
"""Pre-V11.3 tables, single-tenant by design. Each one leaves this list when its module replaces it;
nothing may be added."""

RLS_EXEMPT_TABLES = GLOBAL_TABLES | LEGACY_TABLES


def strict_tenant_rls(table: str) -> list[str]:
    """Statements that confine every read and write on `table` to the current tenant.

    `(SELECT current_app_tenant_id())` is evaluated once per statement (InitPlan), not once per row.
    """
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table}",
        f"""CREATE POLICY tenant_isolation_policy ON {table}
            USING (tenant_id = (SELECT current_app_tenant_id()))
            WITH CHECK (tenant_id = (SELECT current_app_tenant_id()))""",
    ]


def hybrid_write_rls(table: str) -> list[str]:
    """Statements for a table that also holds SYSTEM_PUBLIC material: writes stay inside the tenant.

    A tenant reads and writes its own rows through this policy. Reading the system tenant's
    published rows needs a `hybrid_read_policy`, which depends on the table's own status columns:
    each module's migration writes it explicitly, and until it does public rows stay hidden.
    """
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS hybrid_read_policy ON {table}",
        f"DROP POLICY IF EXISTS hybrid_write_policy ON {table}",
        f"""CREATE POLICY hybrid_write_policy ON {table} FOR ALL
            USING (tenant_id = (SELECT current_app_tenant_id()))
            WITH CHECK (tenant_id = (SELECT current_app_tenant_id()))""",
    ]


# --- shared, public material (H-01) -----------------------------------------------------------

PUBLISHED_AND_CLEARED = (
    "visibility_scope = 'SYSTEM_PUBLIC' AND publication_status = 'PUBLISHED' "
    "AND rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE')"
)
"""Condition under which a root row of the system tenant (content item, question, assessment) is public."""

FROZEN_VERSION = "is_immutable IS TRUE"
"""Extra condition for a version row: only frozen versions of public material are public."""

_OWN_TENANT = "tenant_id = (SELECT current_app_tenant_id())"


def hybrid_read_root(table: str, public_condition: str = PUBLISHED_AND_CLEARED) -> list[str]:
    """Read policy for a root table of shared material; use after `hybrid_write_rls(table)`.

    A tenant reads its own rows, plus the system tenant's rows that satisfy `public_condition`.
    Drafts, taken-down and rights-pending rows of the system tenant stay hidden.
    """
    return [
        f"DROP POLICY IF EXISTS hybrid_read_policy ON {table}",
        f"""CREATE POLICY hybrid_read_policy ON {table} FOR SELECT USING (
            {_OWN_TENANT}
            OR (tenant_id = system_public_tenant_id() AND {public_condition})
        )""",
    ]


def hybrid_read_child(table: str, parent: str, foreign_key: str, own_condition: str | None = None) -> list[str]:
    """Read policy for a child table of shared material; use after `hybrid_write_rls(table)`.

    A system row is visible only while its parent row is: the EXISTS subquery reads `parent` under
    the caller's own row-level security, so a child can never be read around a hidden parent.
    `own_condition` adds a condition on the child itself (FROZEN_VERSION for version tables).
    """
    own = f"{own_condition} AND " if own_condition else ""
    return [
        f"DROP POLICY IF EXISTS hybrid_read_policy ON {table}",
        f"""CREATE POLICY hybrid_read_policy ON {table} FOR SELECT USING (
            {_OWN_TENANT}
            OR (tenant_id = system_public_tenant_id() AND {own}EXISTS (
                SELECT 1 FROM {parent} p WHERE p.id = {table}.{foreign_key} AND p.tenant_id = {table}.tenant_id
            ))
        )""",
    ]


def grading_read(table: str, policy: str) -> list[str]:
    """The grading worker reads answers of its tenant and of the system tenant (system papers)."""
    return [
        f"DROP POLICY IF EXISTS {policy} ON {table}",
        f"""CREATE POLICY {policy} ON {table} FOR SELECT TO ulp_worker_grading
            USING ({_OWN_TENANT} OR tenant_id = system_public_tenant_id())""",
    ]


def system_job_policy(
    table: str, policy: str, job: str, command: str = "ALL", row_condition: str | None = None
) -> list[str]:
    """Narrow cross-tenant access for one named background job running as ulp_system_scheduler.

    `job` is a value of app.platform.tenancy.SystemJob; `row_condition` narrows the rows further.
    """
    if command not in {"ALL", "SELECT", "INSERT", "UPDATE", "DELETE"}:
        raise ValueError(f"unknown policy command: {command}")
    if not job.replace("_", "").isalnum():
        raise ValueError(f"job name must be an identifier: {job}")
    names_job = f"(SELECT app_system_job()) = '{job}'"
    using = f"{names_job} AND {row_condition}" if row_condition else names_job
    clauses = {
        "SELECT": f"USING ({using})",
        "DELETE": f"USING ({using})",
        "INSERT": f"WITH CHECK ({using})",
    }.get(command, f"USING ({using}) WITH CHECK ({using})")
    return [
        f"DROP POLICY IF EXISTS {policy} ON {table}",
        f"CREATE POLICY {policy} ON {table} FOR {command} TO ulp_system_scheduler {clauses}",
    ]


RUNTIME_ROLES_SQL = (
    "ulp_app_rw, ulp_exam_session_rw, ulp_worker_ingestion, ulp_worker_grading, ulp_system_scheduler, ulp_analytics_ro"
)


def partition_lockdown(partition: str) -> list[str]:
    """Statements every partition needs: the parent's row-level security does not cover a query that
    names the partition itself. Rows still reach it through the parent, which needs no grant here."""
    return [f"REVOKE ALL ON {partition} FROM PUBLIC, {RUNTIME_ROLES_SQL}", *strict_tenant_rls(partition)]


def invoker_view(name: str, select: str) -> str:
    """A view that reads with the caller's privileges and row-level security, never the owner's."""
    return f"CREATE OR REPLACE VIEW {name} WITH (security_invoker = true) AS {select}"
