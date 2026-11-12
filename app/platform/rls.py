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
