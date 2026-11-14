"""G02 audit: takedown_cases, partitioned append-only audit_events (F-01), content_blocks freeze

As in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql:
* takedown_cases; audit_events PARTITION BY RANGE (occurred_at) with audit_events_2026_11 and
  audit_events_default, and ix_audit_events_tenant_target.
* secure_partition() (F-01): every partition gets its own ENABLE + FORCE RLS and tenant policy and no
  privilege for any runtime role; rows reach partitions through the parent only.
* ensure_monthly_partitions(): creates and secures the next months. Fix over the spec text: the spec
  computes the bounds with `date_trunc('month', <date>) AT TIME ZONE 'UTC'` assigned to a TIMESTAMPTZ,
  which depends on the session time zone; under Asia/Ho_Chi_Minh December 2026 came out as
  `2026-11-30 10:00+00` and was named audit_events_2026_11 (already existing, so December never got a
  partition). The bounds are built with make_timestamptz(..., 'UTC') instead; same intent, any time zone.
* prevent_append_only_mutation() + trg_immutable_audit_events, enforce_audit_no_raw_pii() + trg_audit_no_raw_pii;
  row triggers on a partitioned table are cloned onto every partition, present and future.
* strict tenant RLS on takedown_cases and audit_events; section 5 privileges (audit_events: no UPDATE,
  DELETE or TRUNCATE for anyone; INSERT for the API, exam-room and worker roles).
* Proposal accepted for TASK-14: content_blocks of a frozen content version can no longer be inserted,
  changed or deleted (trg_freeze_content_blocks), mirroring trg_immutable_content_versions.

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-11-14
"""

from alembic import op

from app.platform.rls import strict_tenant_rls

revision = "e8f9a0b1c2d3"
down_revision = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None

FUTURE_MONTHS = 6  # as run_daily_partition_and_registry_maintenance() in the spec


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE takedown_cases (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
            complainant_name VARCHAR(255) NOT NULL,
            target_entity_type VARCHAR(40) NOT NULL,
            target_entity_id UUID NOT NULL,
            legal_reason TEXT NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'OPEN',
            executed_by UUID REFERENCES users(id),
            executed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_takedown_cases_target_entity_type
                CHECK (target_entity_type IN ('SOURCE_DOCUMENT', 'CONTENT_ITEM', 'QUESTION')),
            CONSTRAINT ck_takedown_cases_status CHECK (status IN ('OPEN', 'EXECUTED', 'REJECTED'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE audit_events (
            id UUID DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            actor_id UUID,
            action VARCHAR(100) NOT NULL,
            target_type VARCHAR(60) NOT NULL,
            target_id UUID NOT NULL,
            reason TEXT,
            before_json JSONB,
            after_json JSONB,
            correlation_id VARCHAR(100),
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (id, occurred_at)
        ) PARTITION BY RANGE (occurred_at)
        """
    )
    op.execute(
        """
        CREATE TABLE audit_events_2026_11 PARTITION OF audit_events
            FOR VALUES FROM ('2026-11-01 00:00:00+00') TO ('2026-12-01 00:00:00+00')
        """
    )
    op.execute("CREATE TABLE audit_events_default PARTITION OF audit_events DEFAULT")
    op.execute(
        "CREATE INDEX ix_audit_events_tenant_target ON audit_events (tenant_id, target_type, target_id, occurred_at DESC)"
    )

    # --- F-01: partitions are locked down on their own --------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION secure_partition(p_partition TEXT) RETURNS void AS $$
        BEGIN
            EXECUTE format(
                'REVOKE ALL ON %I FROM PUBLIC, ulp_app_rw, ulp_exam_session_rw, ulp_worker_ingestion, '
                'ulp_worker_grading, ulp_system_scheduler, ulp_analytics_ro;', p_partition);
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', p_partition);
            EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', p_partition);
            EXECUTE format('DROP POLICY IF EXISTS tenant_isolation_policy ON %I;', p_partition);
            EXECUTE format(
                'CREATE POLICY tenant_isolation_policy ON %I
                 USING (tenant_id = (SELECT current_app_tenant_id()))
                 WITH CHECK (tenant_id = (SELECT current_app_tenant_id()));', p_partition);
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION ensure_monthly_partitions(p_table_name TEXT, p_start_date DATE, p_months INT)
        RETURNS void AS $$
        DECLARE
            i INT;
            v_month DATE;
            v_start TIMESTAMPTZ;
            v_end TIMESTAMPTZ;
            v_part_name TEXT;
        BEGIN
            FOR i IN 0..(p_months - 1) LOOP
                -- Month bounds in UTC whatever the session time zone (see the revision docstring).
                v_month := (date_trunc('month', p_start_date::timestamp) + make_interval(months => i))::date;
                v_start := make_timestamptz(EXTRACT(YEAR FROM v_month)::int, EXTRACT(MONTH FROM v_month)::int, 1, 0, 0, 0, 'UTC');
                v_end := make_timestamptz(
                    EXTRACT(YEAR FROM (v_month + interval '1 month'))::int,
                    EXTRACT(MONTH FROM (v_month + interval '1 month'))::int, 1, 0, 0, 0, 'UTC');
                v_part_name := format('%s_%s', p_table_name, to_char(v_month, 'YYYY_MM'));
                IF to_regclass(v_part_name) IS NULL THEN
                    EXECUTE format(
                        'CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L);',
                        v_part_name, p_table_name, v_start, v_end
                    );
                    PERFORM secure_partition(v_part_name);
                END IF;
            END LOOP;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute("SELECT secure_partition('audit_events_2026_11')")
    op.execute("SELECT secure_partition('audit_events_default')")
    op.execute(f"SELECT ensure_monthly_partitions('audit_events', CURRENT_DATE, {FUTURE_MONTHS})")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
                EXECUTE $cron$SELECT cron.schedule('ulp_audit_partitions', '15 2 * * *',
                    'SELECT ensure_monthly_partitions(''audit_events'', CURRENT_DATE, 6)')$cron$;
            END IF;
        END $$
        """
    )

    # --- append-only and no raw PII (spec section 4) ---------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_append_only_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'APPEND_ONLY_VIOLATION: Bảng % (thuộc %) là Append-Only, cấm tuyệt đối UPDATE/DELETE.',
                TG_TABLE_NAME, TG_ARGV[0];
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_immutable_audit_events
            BEFORE UPDATE OR DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION prevent_append_only_mutation('audit_events')
        """
    )
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION enforce_audit_no_raw_pii() RETURNS trigger AS $$
        DECLARE
            v_combined TEXT;
        BEGIN
            v_combined := coalesce(NEW.before_json::text, '') || coalesce(NEW.after_json::text, '');
            IF v_combined ~* '"(raw_email|plaintext_email|guardian_email|password|phone_number|cccd)"\s*:' THEN
                RAISE EXCEPTION 'RAW_PII_IN_AUDIT_FORBIDDEN: Không được phép ghi PII dạng rõ vào audit_events; phải dùng blind_index hoặc pii_ciphertext.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_no_raw_pii
            BEFORE INSERT ON audit_events
            FOR EACH ROW EXECUTE FUNCTION enforce_audit_no_raw_pii()
        """
    )

    # --- content_blocks of a frozen version are frozen too -----------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_frozen_content_block_mutation() RETURNS trigger AS $$
        DECLARE
            v_version uuid := COALESCE(NEW.content_version_id, OLD.content_version_id);
        BEGIN
            IF TG_OP = 'UPDATE' AND NEW.content_version_id IS DISTINCT FROM OLD.content_version_id THEN
                IF EXISTS (SELECT 1 FROM content_versions WHERE id = OLD.content_version_id AND is_immutable IS TRUE) THEN
                    RAISE EXCEPTION 'IMMUTABLE_VIOLATION: content_blocks của phiên bản đã đóng băng không được thay đổi.';
                END IF;
            END IF;
            IF EXISTS (SELECT 1 FROM content_versions WHERE id = v_version AND is_immutable IS TRUE) THEN
                RAISE EXCEPTION 'IMMUTABLE_VIOLATION: content_blocks của phiên bản đã đóng băng không được thay đổi, phải tạo version_no + 1.';
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_freeze_content_blocks
            BEFORE INSERT OR UPDATE OR DELETE ON content_blocks
            FOR EACH ROW EXECUTE FUNCTION prevent_frozen_content_block_mutation()
        """
    )

    # --- row-level security and privileges ----------------------------------------------------------
    for table in ("takedown_cases", "audit_events"):
        for stmt in strict_tenant_rls(table):
            op.execute(stmt)
    runtime = (
        "PUBLIC, ulp_app_rw, ulp_exam_session_rw, ulp_worker_ingestion, ulp_worker_grading, "
        "ulp_system_scheduler, ulp_analytics_ro"
    )
    op.execute(f"REVOKE ALL ON takedown_cases, audit_events FROM {runtime}")
    op.execute("GRANT SELECT, INSERT, UPDATE ON takedown_cases TO ulp_app_rw")  # a legal record is never deleted
    op.execute("GRANT SELECT, INSERT ON audit_events TO ulp_app_rw, ulp_exam_session_rw")
    op.execute("GRANT INSERT ON audit_events TO ulp_worker_grading, ulp_worker_ingestion")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_freeze_content_blocks ON content_blocks")
    op.execute("DROP FUNCTION IF EXISTS prevent_frozen_content_block_mutation()")
    op.execute("DROP TABLE IF EXISTS audit_events")  # partitions go with it
    op.execute("DROP TABLE IF EXISTS takedown_cases")
    op.execute("DROP FUNCTION IF EXISTS enforce_audit_no_raw_pii()")
    op.execute("DROP FUNCTION IF EXISTS prevent_append_only_mutation()")
    op.execute("DROP FUNCTION IF EXISTS ensure_monthly_partitions(TEXT, DATE, INT)")
    op.execute("DROP FUNCTION IF EXISTS secure_partition(TEXT)")
