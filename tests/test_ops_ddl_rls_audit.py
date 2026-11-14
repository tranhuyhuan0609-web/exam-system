"""Tests for app.ops.ddl_rls_audit static PostgreSQL DDL & RLS verifier (EV-V11-03)."""

from __future__ import annotations

from app.ops.ddl_rls_audit import audit_ddl_rls_hardening


def test_audit_passes_hardened_rls_ddl_and_respects_exempt_global_tables() -> None:
    sql = """
    CREATE ROLE ulp_app_rw NOLOGIN NOBYPASSRLS;
    CREATE TABLE tenants (id uuid PRIMARY KEY);
    CREATE TABLE question_banks (id uuid PRIMARY KEY, tenant_id uuid NOT NULL);
    ALTER TABLE question_banks ENABLE ROW LEVEL SECURITY;
    ALTER TABLE question_banks FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation_policy ON question_banks
        USING (tenant_id = (SELECT current_app_tenant_id()));
    CREATE FUNCTION safe_helper() RETURNS void AS $$
    BEGIN NULL; END;
    $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp;
    """
    report = audit_ddl_rls_hardening(sql)
    assert report.passed is True
    assert report.discovered_tables == ("question_banks", "tenants")


def test_audit_detects_missing_force_rls_bypassrls_and_unsafe_security_definer() -> None:
    bad_sql = """
    CREATE ROLE ulp_worker_grading NOLOGIN BYPASSRLS;
    CREATE TABLE leaked_scores (id uuid PRIMARY KEY, tenant_id uuid NOT NULL);
    ALTER TABLE leaked_scores ENABLE ROW LEVEL SECURITY;
    CREATE POLICY partial_policy ON leaked_scores USING (true);
    CREATE FUNCTION insecure_definer() RETURNS void AS $$
    BEGIN NULL; END;
    $$ LANGUAGE plpgsql SECURITY DEFINER;
    """
    report = audit_ddl_rls_hardening(bad_sql)
    assert report.passed is False
    assert report.missing_rls_enable == ()
    assert report.missing_rls_force == ("leaked_scores",)
    assert report.privileged_role_violations == ("ulp_worker_grading: granted BYPASSRLS",)
    assert report.unsafe_security_definer_fns == ("insecure_definer",)


# --- regression + boundary tests --------------------------------------------------------------

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from app.ops.ddl_rls_audit import MANIFEST_DB_ROLE_NAMES  # noqa: E402

_SPEC_SQL = (
    Path(__file__).resolve().parents[1] / "docs" / "architecture" / "chapters" / "04_postgresql_ddl_rls_grants.sql"
)
_SEVEN_ROLES = {
    "ulp_migrator",
    "ulp_app_rw",
    "ulp_exam_session_rw",
    "ulp_worker_ingestion",
    "ulp_worker_grading",
    "ulp_system_scheduler",
    "ulp_analytics_ro",
}


@pytest.fixture(scope="module")
def spec_sql() -> str:
    return _SPEC_SQL.read_text(encoding="utf-8")


def _audit_spec_variant(spec_sql: str, old: str, new: str, **kw):
    assert spec_sql.count(old) >= 1, f"fixture text not found: {old[:60]!r}"
    return audit_ddl_rls_hardening(spec_sql.replace(old, new, 1), require_all_roles=True, **kw)


def test_manifest_role_set_is_the_seven_manifest_roles() -> None:
    assert MANIFEST_DB_ROLE_NAMES == _SEVEN_ROLES


def test_real_spec_ddl_has_zero_findings(spec_sql: str) -> None:
    report = audit_ddl_rls_hardening(
        spec_sql,
        exempt_tables=frozenset({"tenants", "fts_dictionary_versions"}),
        require_all_roles=True,
    )
    assert report.passed is True
    assert report.missing_rls_enable == ()
    assert report.missing_rls_force == ()
    assert report.missing_rls_policy == ()
    assert report.privileged_role_violations == ()
    assert report.unsafe_security_definer_fns == ()
    assert report.policy_violations == ()
    assert report.grant_violations == ()
    assert len(report.discovered_tables) > 100
    # global catalogs are surfaced for review instead of being silently skipped
    assert "permissions" in report.tenantless_tables and "ai_models" in report.tenantless_tables
    # partitions are covered through secure_partition(), not through the parent
    assert {"audit_events_2026_11", "audit_events_default", "response_revisions_default"} <= set(
        report.discovered_tables
    )
    assert not {"audit_events_2026_11", "audit_events_default"} & set(report.missing_rls_enable)
    # the spec creates ulp_migrator without writing NOBYPASSRLS (PostgreSQL default); advisory only
    assert report.roles_without_explicit_nobypassrls == ("ulp_migrator",)


def test_spec_without_the_role_block_fails_when_all_roles_are_required(spec_sql: str) -> None:
    stripped = spec_sql.replace("CREATE ROLE ulp_analytics_ro NOLOGIN NOBYPASSRLS;", "NULL;")
    report = audit_ddl_rls_hardening(stripped, require_all_roles=True)
    assert report.passed is False
    assert "ulp_analytics_ro: manifest role is never created" in report.privileged_role_violations
    assert audit_ddl_rls_hardening(stripped).passed is True  # opt-in strictness


def test_broken_copy_table_dropped_from_the_strict_array(spec_sql: str) -> None:
    report = _audit_spec_variant(
        spec_sql, "'assessment_assignments', 'attempts', 'responses',", "'assessment_assignments', 'responses',"
    )
    assert report.passed is False
    assert "attempts" in report.missing_rls_enable
    assert "attempts" in report.missing_rls_force
    assert "attempts" in report.missing_rls_policy


def test_broken_copy_force_rls_removed_from_the_strict_loop(spec_sql: str) -> None:
    old = "EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', t);"
    report = _audit_spec_variant(spec_sql, old, "NULL;")
    assert report.passed is False
    assert "memberships" in report.missing_rls_force
    assert "memberships" not in report.missing_rls_enable


def test_broken_copy_policy_creation_removed_from_the_loop(spec_sql: str) -> None:
    old = (
        "EXECUTE format('DROP POLICY IF EXISTS tenant_isolation_policy ON %I;', t);\n"
        "        EXECUTE format(\n"
        "            'CREATE POLICY tenant_isolation_policy ON %I"
    )
    new = (
        "EXECUTE format('DROP POLICY IF EXISTS tenant_isolation_policy ON %I;', t);\n"
        "        EXECUTE format(\n"
        "            'SELECT 1 -- %I"
    )
    report = _audit_spec_variant(spec_sql, old, new)
    assert report.passed is False
    assert "memberships" in report.missing_rls_policy


def test_broken_copy_migrator_granted_bypassrls(spec_sql: str) -> None:
    report = audit_ddl_rls_hardening(spec_sql + "\nALTER ROLE ulp_migrator BYPASSRLS;\n", require_all_roles=True)
    assert report.passed is False
    assert report.privileged_role_violations == ("ulp_migrator: granted BYPASSRLS",)


def test_broken_copy_runtime_role_created_with_bypassrls(spec_sql: str) -> None:
    report = _audit_spec_variant(
        spec_sql, "CREATE ROLE ulp_app_rw NOLOGIN NOBYPASSRLS;", "CREATE ROLE ulp_app_rw NOLOGIN BYPASSRLS;"
    )
    assert report.privileged_role_violations == ("ulp_app_rw: granted BYPASSRLS",)


def test_broken_copy_bare_tenant_function_in_a_hybrid_policy(spec_sql: str) -> None:
    old = (
        "CREATE POLICY hybrid_read_policy ON content_items FOR SELECT USING (\n"
        "    tenant_id = (SELECT current_app_tenant_id())"
    )
    new = (
        "CREATE POLICY hybrid_read_policy ON content_items FOR SELECT USING (\n    tenant_id = current_app_tenant_id()"
    )
    report = _audit_spec_variant(spec_sql, old, new)
    assert report.passed is False
    assert any("content_items" in v and "InitPlan" in v for v in report.policy_violations)


def test_broken_copy_hybrid_parent_without_published_gate(spec_sql: str) -> None:
    old = (
        "CREATE POLICY hybrid_read_policy ON questions FOR SELECT USING (\n"
        "    tenant_id = (SELECT current_app_tenant_id())\n"
        "    OR (tenant_id = system_public_tenant_id()\n"
        "        AND visibility_scope = 'SYSTEM_PUBLIC'\n"
        "        AND publication_status = 'PUBLISHED'\n"
    )
    new = (
        "CREATE POLICY hybrid_read_policy ON questions FOR SELECT USING (\n"
        "    tenant_id = (SELECT current_app_tenant_id())\n"
        "    OR (tenant_id = system_public_tenant_id()\n"
        "        AND visibility_scope = 'SYSTEM_PUBLIC'\n"
    )
    report = _audit_spec_variant(spec_sql, old, new)
    assert any("questions" in v and "PUBLISHED" in v for v in report.policy_violations)


def test_broken_copy_knowledge_edges_checks_only_one_endpoint(spec_sql: str) -> None:
    old = (
        "AND EXISTS (SELECT 1 FROM taxonomy_nodes t "
        "WHERE t.id = knowledge_edges.target_node_id AND t.is_active IS TRUE))"
    )
    report = _audit_spec_variant(spec_sql, old, ")")
    assert any("knowledge_edges" in v and "target_node_id" in v for v in report.policy_violations)



def test_broken_copy_hybrid_read_policy_removed(spec_sql: str) -> None:
    old = "CREATE POLICY hybrid_read_policy ON search_documents FOR SELECT USING ("
    report = _audit_spec_variant(
        spec_sql, old, "CREATE POLICY hybrid_read_policy_x ON search_documents FOR SELECT USING ("
    )
    assert "hybrid_read_policy missing on search_documents" in report.policy_violations


def test_broken_copy_partition_sweep_removed(spec_sql: str) -> None:
    report = _audit_spec_variant(spec_sql, "PERFORM secure_partition(p::text);", "NULL;")
    assert report.passed is False
    assert {"audit_events_default", "audit_events_2026_11", "response_revisions_default"} <= set(
        report.missing_rls_enable
    )


def test_broken_copy_secure_partition_without_force(spec_sql: str) -> None:
    old = "EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', p_partition);"
    report = _audit_spec_variant(spec_sql, old, "NULL;")
    assert report.passed is False
    assert any("secure_partition" in v for v in report.policy_violations)


def test_broken_copy_answer_keys_regranted_to_exam_session_role(spec_sql: str) -> None:
    extra = "\nGRANT SELECT ON ALL TABLES IN SCHEMA public TO ulp_exam_session_rw;\n"
    report = audit_ddl_rls_hardening(spec_sql + extra, require_all_roles=True)
    assert report.passed is False
    assert any("ulp_exam_session_rw" in v and "question_version_answers" in v for v in report.grant_violations)


def test_broken_copy_answer_key_revoke_removed(spec_sql: str) -> None:
    old = (
        "REVOKE ALL ON question_version_answers, assessment_answer_keys "
        "FROM PUBLIC, ulp_exam_session_rw, ulp_worker_ingestion, ulp_system_scheduler, ulp_analytics_ro;"
    )
    report = _audit_spec_variant(spec_sql, old, "")
    assert any(
        "ulp_worker_ingestion: no explicit REVOKE ALL on question_version_answers" == v for v in report.grant_violations
    )


def test_broken_copy_grading_policy_removed(spec_sql: str) -> None:
    report = _audit_spec_variant(spec_sql, "CREATE POLICY grading_read_answer_keys", "CREATE POLICY something_else")
    assert "missing grading policy grading_read_answer_keys" in report.grant_violations


def test_broken_copy_system_job_policy_without_initplan(spec_sql: str) -> None:
    old = (
        "USING ((SELECT app_system_job()) = 'outbox_relay')\n"
        "    WITH CHECK ((SELECT app_system_job()) = 'outbox_relay');"
    )
    report = _audit_spec_variant(spec_sql, old, "USING (true)\n    WITH CHECK (true);")
    assert any("sys_outbox_relay" in v for v in report.policy_violations)


# --- synthetic cases -------------------------------------------------------------------------


def test_array_without_a_covering_loop_does_not_cover_its_tables() -> None:
    """Old behaviour: any array plus any ENABLE/FORCE/POLICY text anywhere covered every listed table."""
    sql = """
    CREATE TABLE a_tbl (id uuid, tenant_id uuid);
    CREATE TABLE b_tbl (id uuid, tenant_id uuid);
    DO $$
    DECLARE strict_tenant_tables text[] := ARRAY['a_tbl'];
    BEGIN NULL; END $$;
    ALTER TABLE b_tbl ENABLE ROW LEVEL SECURITY;
    ALTER TABLE b_tbl FORCE ROW LEVEL SECURITY;
    CREATE POLICY p ON b_tbl USING (tenant_id = (SELECT current_app_tenant_id()));
    """
    report = audit_ddl_rls_hardening(sql)
    assert report.missing_rls_enable == ("a_tbl",)
    assert report.missing_rls_force == ("a_tbl",)
    assert report.missing_rls_policy == ("a_tbl",)


def test_loop_over_a_different_array_does_not_cover_the_other_array() -> None:
    sql = """
    CREATE TABLE a_tbl (id uuid, tenant_id uuid);
    CREATE TABLE b_tbl (id uuid, tenant_id uuid);
    DO $$
    DECLARE
        first_tables text[] := ARRAY['a_tbl'];
        second_tables text[] := ARRAY['b_tbl'];
        t text;
    BEGIN
        FOREACH t IN ARRAY first_tables LOOP
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', t);
            EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', t);
            EXECUTE format(
                'CREATE POLICY tenant_isolation_policy ON %I USING (tenant_id = (SELECT current_app_tenant_id()));', t
            );
        END LOOP;
    END $$;
    """
    report = audit_ddl_rls_hardening(sql)
    assert report.missing_rls_enable == ("b_tbl",)



def test_loop_policy_without_initplan_is_flagged() -> None:
    sql = """
    CREATE TABLE a_tbl (id uuid, tenant_id uuid);
    DO $$
    DECLARE strict_tenant_tables text[] := ARRAY['a_tbl']; t text;
    BEGIN
        FOREACH t IN ARRAY strict_tenant_tables LOOP
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', t);
            EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', t);
            EXECUTE format('CREATE POLICY tenant_isolation_policy ON %I USING (true);', t);
        END LOOP;
    END $$;
    """
    report = audit_ddl_rls_hardening(sql)
    assert report.passed is False and report.policy_violations


def test_commented_out_statements_do_not_count() -> None:
    sql = """
    CREATE TABLE a_tbl (id uuid, tenant_id uuid);
    -- ALTER TABLE a_tbl ENABLE ROW LEVEL SECURITY;
    /* ALTER TABLE a_tbl FORCE ROW LEVEL SECURITY;
       CREATE POLICY p ON a_tbl USING (tenant_id = (SELECT current_app_tenant_id())); */
    """
    report = audit_ddl_rls_hardening(sql)
    assert report.missing_rls_enable == report.missing_rls_force == report.missing_rls_policy == ("a_tbl",)


def test_double_dash_inside_a_string_is_not_a_comment() -> None:
    sql = """
    CREATE TABLE a_tbl (id uuid, tenant_id uuid, note text DEFAULT '--keep');
    ALTER TABLE a_tbl ENABLE ROW LEVEL SECURITY;
    ALTER TABLE a_tbl FORCE ROW LEVEL SECURITY;
    CREATE POLICY p ON a_tbl USING (tenant_id = (SELECT current_app_tenant_id()));
    """
    assert audit_ddl_rls_hardening(sql).passed is True


def test_partition_without_own_security_is_flagged_and_secured_one_is_not() -> None:
    sql = """
    CREATE TABLE ev (id uuid, tenant_id uuid, at timestamptz) PARTITION BY RANGE (at);
    ALTER TABLE ev ENABLE ROW LEVEL SECURITY;
    ALTER TABLE ev FORCE ROW LEVEL SECURITY;
    CREATE POLICY p ON ev USING (tenant_id = (SELECT current_app_tenant_id()));
    CREATE TABLE ev_default PARTITION OF ev DEFAULT;
    """
    report = audit_ddl_rls_hardening(sql)
    assert report.missing_rls_enable == ("ev_default",)
    assert report.passed is False

    secured = (
        sql
        + """
    ALTER TABLE ev_default ENABLE ROW LEVEL SECURITY;
    ALTER TABLE ev_default FORCE ROW LEVEL SECURITY;
    CREATE POLICY p ON ev_default USING (tenant_id = (SELECT current_app_tenant_id()));
    """
    )
    assert audit_ddl_rls_hardening(secured).passed is True


def test_tenantless_global_table_is_reported_not_failed_and_tenant_table_still_fails() -> None:
    sql = """
    CREATE TABLE catalog (id uuid PRIMARY KEY, label text);
    CREATE TABLE tenant_data (id uuid PRIMARY KEY, tenant_id uuid NOT NULL);
    """
    report = audit_ddl_rls_hardening(sql)
    assert report.tenantless_tables == ("catalog",)
    assert report.missing_rls_enable == ("tenant_data",)


def test_exempt_tables_parameter_is_honoured() -> None:
    sql = "CREATE TABLE t_exempt (id uuid, tenant_id uuid);"
    assert audit_ddl_rls_hardening(sql).passed is False
    assert audit_ddl_rls_hardening(sql, exempt_tables=frozenset({"T_EXEMPT"})).passed is True


def test_empty_and_whitespace_sql_pass_vacuously() -> None:
    for text in ("", "   \n", "-- only a comment"):
        report = audit_ddl_rls_hardening(text)
        assert report.passed is True and report.discovered_tables == ()


@pytest.mark.parametrize(
    "statement,expected",
    [
        ("ALTER ROLE ulp_migrator BYPASSRLS;", "ulp_migrator: granted BYPASSRLS"),
        ("alter role ulp_app_rw with bypassrls;", "ulp_app_rw: granted BYPASSRLS"),
        ('ALTER ROLE "ulp_worker_grading" BYPASSRLS;', "ulp_worker_grading: granted BYPASSRLS"),
        ("ALTER USER ulp_analytics_ro SUPERUSER;", "ulp_analytics_ro: granted SUPERUSER"),
        ("CREATE ROLE ulp_system_scheduler LOGIN SUPERUSER NOBYPASSRLS;", "ulp_system_scheduler: granted SUPERUSER"),
        ("ALTER ROLE ulp_api BYPASSRLS;", "ulp_api: granted BYPASSRLS"),
    ],
)
def test_role_violations_in_all_spellings(statement: str, expected: str) -> None:
    assert audit_ddl_rls_hardening(statement).privileged_role_violations == (expected,)


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER ROLE ulp_migrator NOBYPASSRLS;",
        "CREATE ROLE ulp_app_rw NOLOGIN NOBYPASSRLS NOSUPERUSER;",
        "ALTER ROLE some_other_role BYPASSRLS;",
    ],
)
def test_safe_or_unrelated_role_statements_pass(statement: str) -> None:
    assert audit_ddl_rls_hardening(statement).privileged_role_violations == ()


def test_role_statement_inside_a_do_block_is_checked() -> None:
    sql = "DO $$ BEGIN CREATE ROLE ulp_app_rw NOLOGIN BYPASSRLS; END $$;"
    assert audit_ddl_rls_hardening(sql).privileged_role_violations == ("ulp_app_rw: granted BYPASSRLS",)


def test_security_definer_search_path_rules() -> None:
    unsafe = "CREATE FUNCTION f() RETURNS void AS $$ BEGIN NULL; END; $$ LANGUAGE plpgsql SECURITY DEFINER;"
    safe = (
        "CREATE FUNCTION g() RETURNS void AS $$ BEGIN NULL; END; $$ LANGUAGE plpgsql "
        "SECURITY DEFINER SET search_path = public, pg_temp;"
    )
    attrs_before_body = (
        "CREATE FUNCTION h(a int DEFAULT (1)) RETURNS void LANGUAGE plpgsql SECURITY DEFINER AS $$ BEGIN NULL; END; $$;"
    )
    invoker = "CREATE FUNCTION i() RETURNS void AS $$ BEGIN NULL; END; $$ LANGUAGE plpgsql;"
    assert audit_ddl_rls_hardening(unsafe).unsafe_security_definer_fns == ("f",)
    assert audit_ddl_rls_hardening(safe).unsafe_security_definer_fns == ()
    assert audit_ddl_rls_hardening(attrs_before_body).unsafe_security_definer_fns == ("h",)
    assert audit_ddl_rls_hardening(invoker).unsafe_security_definer_fns == ()


def test_answer_table_grants_and_revokes_in_order() -> None:
    base = """
    CREATE TABLE question_version_answers (id uuid, tenant_id uuid);
    ALTER TABLE question_version_answers ENABLE ROW LEVEL SECURITY;
    ALTER TABLE question_version_answers FORCE ROW LEVEL SECURITY;
    CREATE POLICY p ON question_version_answers USING (tenant_id = (SELECT current_app_tenant_id()));
    CREATE POLICY grading_read_question_answers ON question_version_answers
        FOR SELECT TO ulp_worker_grading USING (tenant_id = (SELECT current_app_tenant_id()));
    """
    revoke = "REVOKE ALL ON question_version_answers FROM PUBLIC, ulp_exam_session_rw, ulp_worker_ingestion;\n"
    assert audit_ddl_rls_hardening(base + revoke).grant_violations == ()
    regrant = "GRANT SELECT ON question_version_answers TO ulp_worker_ingestion;\n"
    assert audit_ddl_rls_hardening(base + revoke + regrant).grant_violations == (
        "ulp_worker_ingestion: privileges on question_version_answers are (re)granted after the REVOKE",
    )
    # a grant that is revoked afterwards is fine
    assert audit_ddl_rls_hardening(base + regrant + revoke).grant_violations == ()
    # revoke only one of the two roles
    partial = "REVOKE ALL ON question_version_answers FROM ulp_exam_session_rw;\n"
    assert audit_ddl_rls_hardening(base + partial).grant_violations == (
        "ulp_worker_ingestion: no explicit REVOKE ALL on question_version_answers",
    )


def test_audit_report_is_deterministic() -> None:
    sql = "CREATE TABLE z (tenant_id uuid); CREATE TABLE a (tenant_id uuid);"
    assert audit_ddl_rls_hardening(sql) == audit_ddl_rls_hardening(sql)
    assert audit_ddl_rls_hardening(sql).discovered_tables == ("a", "z")
