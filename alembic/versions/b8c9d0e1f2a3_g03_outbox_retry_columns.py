"""G03: persisted retry bookkeeping and causation_id on outbox_events

Deliberate deviation from the spec DDL (finding F-04 in docs/architecture/DDL_DRY_RUN_FINDINGS.md):
the relay persists its retries (`attempt_count`, `next_attempt_at`) instead of sleeping while it
holds row locks, and ADR-012's `causation_id` gets its column. The pending index now serves the
claim query (due rows, oldest first) and the scheduler may also update the two retry columns.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-11-13
"""
from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE outbox_events ADD COLUMN attempt_count INT NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE outbox_events ADD COLUMN next_attempt_at TIMESTAMPTZ")
    op.execute("ALTER TABLE outbox_events ADD COLUMN causation_id UUID")
    op.execute("DROP INDEX ix_outbox_events_pending")
    op.execute(
        "CREATE INDEX ix_outbox_events_pending ON outbox_events (created_at, event_id) WHERE status = 'PENDING'"
    )
    op.execute("REVOKE UPDATE ON outbox_events FROM ulp_system_scheduler")
    op.execute(
        "GRANT UPDATE (status, published_at, attempt_count, next_attempt_at) ON outbox_events TO ulp_system_scheduler"
    )


def downgrade() -> None:
    op.execute("REVOKE UPDATE ON outbox_events FROM ulp_system_scheduler")
    op.execute("GRANT UPDATE (status, published_at) ON outbox_events TO ulp_system_scheduler")
    op.execute("DROP INDEX ix_outbox_events_pending")
    op.execute("CREATE INDEX ix_outbox_events_pending ON outbox_events (status, created_at) WHERE status = 'PENDING'")
    op.execute("ALTER TABLE outbox_events DROP COLUMN causation_id")
    op.execute("ALTER TABLE outbox_events DROP COLUMN next_attempt_at")
    op.execute("ALTER TABLE outbox_events DROP COLUMN attempt_count")
