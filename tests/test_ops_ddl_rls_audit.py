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
