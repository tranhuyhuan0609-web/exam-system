"""C06 attempts (H-04 Fast Autosave): responses, autosave_idempotency_registry, response_revisions (partitioned)

Tables as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql, all strict tenant tables:
* responses: the current answer of one question of one attempt, with `current_revision_no` (the version the client
  sends back as `expected_response_version`), `current_sequence` and the last Idempotency-Key.
* autosave_idempotency_registry: one row per Idempotency-Key of an attempt; checked FIRST on every save (replay or
  `PAYLOAD_HASH_MISMATCH`), independent of the revision partitions (P0-7). Kept 30 days.
* response_revisions: the append-only history of every save, PARTITION BY RANGE (recorded_at) with a DEFAULT partition
  and monthly ones. Every partition is secured on its own (`secure_partition`, F-01: RLS of the parent does not cover a
  query that names a partition; no runtime role holds a grant on one); `ensure_monthly_partitions` (UTC bounds, F-05)
  creates the next six months; `trg_immutable_response_revisions` refuses UPDATE and DELETE, even for the owner.
* run_daily_partition_and_registry_maintenance(): the spec's daily job (partitions of both partitioned tables, purge of
  registry keys older than 30 days), scheduled with pg_cron when the extension is present.

Privileges, section 5: the exam room (ulp_exam_session_rw) reads and writes responses and appends revisions and
registry rows (5b); the grading worker reads responses (5c); the API role reads all three (5a, teachers and appeals)
and writes none of them; UPDATE / DELETE / TRUNCATE on response_revisions are revoked from every runtime role (5g).

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-11-15
"""

from alembic import op
from app.platform.rls import RUNTIME_ROLES_SQL, strict_tenant_rls

revision = "f5a6b7c8d9e0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None

TABLES = ("responses", "autosave_idempotency_registry", "response_revisions")
FUTURE_MONTHS = 6
REGISTRY_RETENTION_DAYS = 30


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE responses (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            attempt_id UUID NOT NULL,
            question_version_id UUID NOT NULL REFERENCES question_versions(id) ON DELETE RESTRICT,
            current_sequence BIGINT NOT NULL DEFAULT 0,
            current_revision_no INT NOT NULL DEFAULT 0,
            last_idempotency_key VARCHAR(128),
            response_payload JSONB NOT NULL,
            is_flagged BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_responses_attempt_id_question_version_id UNIQUE (attempt_id, question_version_id),
            CONSTRAINT uq_responses_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT fk_responses_attempt_tenant
                FOREIGN KEY (attempt_id, tenant_id) REFERENCES attempts(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE autosave_idempotency_registry (
            tenant_id UUID NOT NULL,
            attempt_id UUID NOT NULL,
            idempotency_key VARCHAR(128) NOT NULL,
            response_id UUID NOT NULL,
            revision_no INT NOT NULL,
            client_sequence BIGINT NOT NULL,
            payload_sha256 CHAR(64) NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, attempt_id, idempotency_key),
            CONSTRAINT fk_autosave_idempotency_registry_attempt_tenant
                FOREIGN KEY (attempt_id, tenant_id) REFERENCES attempts(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_autosave_idem_registry_recorded_at ON autosave_idempotency_registry (recorded_at)")
    op.execute(
        """
        CREATE TABLE response_revisions (
            id UUID DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            response_id UUID NOT NULL,
            revision_no INT NOT NULL,
            client_sequence BIGINT NOT NULL,
            idempotency_key VARCHAR(128) NOT NULL,
            payload_sha256 CHAR(64) NOT NULL,
            payload_json JSONB NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (id, recorded_at),
            CONSTRAINT fk_response_revisions_response_tenant
                FOREIGN KEY (response_id, tenant_id) REFERENCES responses(id, tenant_id) ON DELETE CASCADE
        ) PARTITION BY RANGE (recorded_at)
        """
    )
    op.execute("CREATE TABLE response_revisions_default PARTITION OF response_revisions DEFAULT")
    op.execute("CREATE INDEX ix_response_revisions_lookup ON response_revisions (tenant_id, response_id, revision_no)")

    # --- append-only (spec section 4); prevent_append_only_mutation exists since G02 ----------------
    op.execute(
        """
        CREATE TRIGGER trg_immutable_response_revisions
            BEFORE UPDATE OR DELETE ON response_revisions
            FOR EACH ROW EXECUTE FUNCTION prevent_append_only_mutation('response_revisions')
        """
    )

    # --- row-level security; partitions are secured on their own (F-01) -------------------------
    for table in TABLES:
        for stmt in strict_tenant_rls(table):
            op.execute(stmt)
    op.execute("SELECT secure_partition('response_revisions_default')")
    op.execute(f"SELECT ensure_monthly_partitions('response_revisions', CURRENT_DATE, {FUTURE_MONTHS})")

    # --- the spec's daily maintenance ------------------------------------------------------------
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION run_daily_partition_and_registry_maintenance() RETURNS void AS $$
        BEGIN
            PERFORM ensure_monthly_partitions('response_revisions', CURRENT_DATE, {FUTURE_MONTHS});
            PERFORM ensure_monthly_partitions('audit_events', CURRENT_DATE, {FUTURE_MONTHS});
            -- Idempotency keys of exam sessions recorded more than {REGISTRY_RETENTION_DAYS} days ago
            DELETE FROM autosave_idempotency_registry
                WHERE recorded_at < NOW() - INTERVAL '{REGISTRY_RETENTION_DAYS} days';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute("REVOKE ALL ON FUNCTION run_daily_partition_and_registry_maintenance() FROM PUBLIC")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
                EXECUTE $cron$SELECT cron.schedule('ulp_daily_maintenance', '15 2 * * *',
                    'SELECT run_daily_partition_and_registry_maintenance()')$cron$;
            END IF;
        END $$
        """
    )

    # --- privileges (section 5) ----------------------------------------------------------------
    op.execute(f"REVOKE ALL ON {', '.join(TABLES)} FROM PUBLIC")
    op.execute("GRANT SELECT ON responses, autosave_idempotency_registry, response_revisions TO ulp_app_rw")  # 5a
    op.execute("GRANT SELECT, INSERT, UPDATE ON responses TO ulp_exam_session_rw")  # 5b
    op.execute("GRANT SELECT, INSERT ON autosave_idempotency_registry, response_revisions TO ulp_exam_session_rw")
    op.execute("GRANT SELECT ON responses TO ulp_worker_grading")  # 5c
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON response_revisions FROM PUBLIC, {RUNTIME_ROLES_SQL}")  # 5g


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
                PERFORM cron.unschedule('ulp_daily_maintenance');
            END IF;
        EXCEPTION WHEN OTHERS THEN NULL;
        END $$
        """
    )
    op.execute("DROP FUNCTION IF EXISTS run_daily_partition_and_registry_maintenance()")
    op.execute("DROP TABLE IF EXISTS response_revisions")  # partitions go with it
    op.execute("DROP TABLE IF EXISTS autosave_idempotency_registry")
    op.execute("DROP TABLE IF EXISTS responses")
