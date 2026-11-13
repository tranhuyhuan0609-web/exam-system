"""G03: transactional outbox and idempotent consumer inbox

Tables, row-level security and grants of outbox_events and inbox_events as in
docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql (DDL, section 2 and 2c, section 5).

Revision ID: a7b8c9d0e1f2
Revises: e3f4a5b6c7d8
Create Date: 2026-11-13
"""
from alembic import op
from app.platform.rls import strict_tenant_rls, system_job_policy

revision = "a7b8c9d0e1f2"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE outbox_events (
            event_id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            event_type VARCHAR(100) NOT NULL,
            schema_version INT NOT NULL DEFAULT 1,
            aggregate_type VARCHAR(60) NOT NULL,
            aggregate_id UUID NOT NULL,
            correlation_id VARCHAR(100),
            payload_json JSONB NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING', 'PUBLISHED', 'FAILED')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            published_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX ix_outbox_events_pending ON outbox_events (status, created_at) WHERE status = 'PENDING'")
    op.execute(
        """
        CREATE TABLE inbox_events (
            consumer_name VARCHAR(80) NOT NULL,
            event_id UUID NOT NULL,
            tenant_id UUID NOT NULL,
            processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (consumer_name, event_id)
        )
        """
    )

    for statement in (
        *strict_tenant_rls("outbox_events"),
        *strict_tenant_rls("inbox_events"),
        # The relay reads every tenant's outbox, and only under its own job name.
        *system_job_policy("outbox_events", "sys_outbox_relay", "outbox_relay", "ALL"),
    ):
        op.execute(statement)

    # API: writes its own outbox; reads, never writes, the workers' inbox.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON outbox_events TO ulp_app_rw")
    op.execute("GRANT SELECT ON inbox_events TO ulp_app_rw")
    # Exam room: publishes attempt events, cannot change them afterwards.
    op.execute("GRANT SELECT, INSERT ON outbox_events TO ulp_exam_session_rw")
    # Workers: publish follow-up events and record what they consumed.
    op.execute("GRANT INSERT ON outbox_events TO ulp_worker_grading, ulp_worker_ingestion")
    op.execute("GRANT SELECT, INSERT ON inbox_events TO ulp_worker_grading, ulp_worker_ingestion")
    # Relay: reads the outbox and may change nothing but the delivery status.
    op.execute("GRANT SELECT, UPDATE (status, published_at) ON outbox_events TO ulp_system_scheduler")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS inbox_events")
    op.execute("DROP TABLE IF EXISTS outbox_events")
