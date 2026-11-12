"""V11.3 foundation: extensions, 7 DB roles, UUIDv7, tenant context, FTS dictionary pin, tenants

Follows "MIGRATION 001" of docs/architecture/handover-v11.3/02-database-ddl-and-rls.md.
Business tables arrive with their modules; each one enables FORCE ROW LEVEL SECURITY and
grants its own privileges (see app/platform/rls.py).

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
Create Date: 2026-11-12
"""
from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "b0c1d2e3f4a5"
branch_labels = None
depends_on = None

RUNTIME_ROLES = (
    "ulp_app_rw, ulp_exam_session_rw, ulp_worker_ingestion, ulp_worker_grading, ulp_system_scheduler, ulp_analytics_ro"
)

# Every Vietnamese letter that carries a diacritic, plus Đ/đ: what f_unaccent must fold.
VIETNAMESE_ALPHABET = (
    "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"
    "ÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ"
)


def upgrade() -> None:
    for extension in ("pgcrypto", "pg_trgm", "unaccent", "ltree", "vector"):
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{extension}"')

    # 0. Seven least-privilege roles. Roles are cluster-wide, hence the existence checks.
    #    No runtime or worker role may bypass RLS.
    op.execute(
        """
        DO $$
        DECLARE
            r text;
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ulp_migrator') THEN
                CREATE ROLE ulp_migrator NOLOGIN;
            END IF;
            FOREACH r IN ARRAY ARRAY[
                'ulp_app_rw', 'ulp_exam_session_rw', 'ulp_worker_ingestion',
                'ulp_worker_grading', 'ulp_analytics_ro', 'ulp_system_scheduler'
            ] LOOP
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                    EXECUTE format('CREATE ROLE %I NOLOGIN NOBYPASSRLS', r);
                END IF;
            END LOOP;
        END $$;
        """
    )

    # Cross-tenant background jobs (outbox_relay, attempt_expiry, metric_rollup) identify themselves here;
    # narrow per-job policies read it instead of any role bypassing RLS.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_system_job() RETURNS text AS $$
            SELECT NULLIF(current_setting('app.system_job', true), '');
        $$ LANGUAGE sql STABLE;
        """
    )

    # 1. UUIDv7: native uuidv7() on PostgreSQL 18+, PL/pgSQL fallback on 16/17.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION uuid_generate_v7() RETURNS uuid AS $$
        DECLARE
            unix_ts_ms bytea;
            uuid_bytes bytea;
            v_native_uuid uuid;
        BEGIN
            IF to_regproc('pg_catalog.uuidv7') IS NOT NULL THEN
                EXECUTE 'SELECT pg_catalog.uuidv7()' INTO v_native_uuid;
                RETURN v_native_uuid;
            END IF;
            unix_ts_ms := substring(int8send(floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint) FROM 3 FOR 6);
            uuid_bytes := unix_ts_ms || gen_random_bytes(10);
            uuid_bytes := set_byte(uuid_bytes, 6, (b'0111' || get_byte(uuid_bytes, 6)::bit(4))::bit(8)::int);
            uuid_bytes := set_byte(uuid_bytes, 8, (b'10' || get_byte(uuid_bytes, 8)::bit(6))::bit(8)::int);
            RETURN encode(uuid_bytes, 'hex')::uuid;
        END;
        $$ LANGUAGE plpgsql VOLATILE;
        """
    )

    # 2. f_unaccent is IMMUTABLE only because the dictionary it wraps is pinned below.
    #    A PostgreSQL upgrade or an edit of unaccent.rules must bump func_version and reindex.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text AS $$
            SELECT public.unaccent('public.unaccent', $1);
        $$ LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT;
        """
    )
    # Fingerprint of what the dictionary does to Vietnamese text. Readable by any role, unlike the
    # rules file on the server's disk, so drift can be detected from CI and from the application.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION fts_unaccent_fingerprint() RETURNS char(64) AS $$
            SELECT encode(sha256(convert_to(public.unaccent('public.unaccent', '{VIETNAMESE_ALPHABET}'), 'UTF8')), 'hex')::char(64);
        $$ LANGUAGE sql STABLE;
        """
    )
    op.execute(
        """
        CREATE TABLE fts_dictionary_versions (
            dict_name VARCHAR(80) PRIMARY KEY,
            rules_sha256 CHAR(64) NOT NULL,
            func_version INT NOT NULL DEFAULT 1,
            last_reindexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        """
        INSERT INTO fts_dictionary_versions (dict_name, rules_sha256, func_version)
        VALUES ('public.unaccent', fts_unaccent_fingerprint(), 1);
        """
    )

    # 3. Tenant context, safe with PgBouncer transaction pooling (set with SET LOCAL per transaction).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION current_app_tenant_id() RETURNS uuid AS $$
            SELECT NULLIF(current_setting('app.current_tenant_id', true), '')::uuid;
        $$ LANGUAGE sql STABLE;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION system_public_tenant_id() RETURNS uuid AS $$
            SELECT '00000000-0000-0000-0000-000000000000'::uuid;
        $$ LANGUAGE sql IMMUTABLE;
        """
    )

    op.execute(
        """
        CREATE TABLE tenants (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            slug VARCHAR(80) UNIQUE NOT NULL,
            name VARCHAR(255) NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
                CHECK (status IN ('ACTIVE', 'SUSPENDED', 'ARCHIVED')),
            settings_json JSONB NOT NULL DEFAULT '{}',
            daily_ai_budget_usd NUMERIC(10,2) NOT NULL DEFAULT 10.00,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    # The system tenant owns the shared, public learning material.
    op.execute(
        """
        INSERT INTO tenants (id, slug, name, status)
        VALUES ('00000000-0000-0000-0000-000000000000', 'system-public', 'System Public Knowledge Commons', 'ACTIVE');
        """
    )

    # 5. Least privilege: schema usage only; table privileges are granted table by table.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {RUNTIME_ROLES}")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM PUBLIC, {RUNTIME_ROLES}")
    op.execute("ALTER DEFAULT PRIVILEGES FOR ROLE ulp_migrator IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC")
    op.execute(f"GRANT SELECT ON tenants TO {RUNTIME_ROLES}")
    op.execute("GRANT INSERT, UPDATE ON tenants TO ulp_app_rw")
    op.execute("GRANT SELECT ON fts_dictionary_versions TO ulp_app_rw, ulp_analytics_ro")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tenants")
    op.execute("DROP TABLE IF EXISTS fts_dictionary_versions")
    for function in (
        "fts_unaccent_fingerprint()",
        "f_unaccent(text)",
        "system_public_tenant_id()",
        "current_app_tenant_id()",
        "uuid_generate_v7()",
        "app_system_job()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {function}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {RUNTIME_ROLES}")
    # Roles are cluster-wide and may serve other databases; extensions may serve legacy tables. Both stay.
