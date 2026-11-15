"""WAVE-8 hardening B2/B3/B5: registry cleanup and attempt expiry without superuser, narrower attempt UPDATE

B2  autosave_idempotency_registry is FORCE row-level security, so the `DELETE` in
    run_daily_partition_and_registry_maintenance() only works for a superuser (pg_cron). The cleanup now also runs as
    `ulp_system_scheduler` under the new job `idempotency_cleanup`: SELECT and DELETE policies that name the job, and
    only rows older than the 30-day retention.
B5  Nothing moved an IN_PROGRESS attempt past `deadline_at`. The job `attempt_expiry` (app/ops/attempt_expiry.py) now may
    UPDATE (status, submitted_at) of an overdue IN_PROGRESS attempt, only to AUTO_SUBMITTED, and INSERT the
    `attempt.expired` outbox event of that attempt, nothing else.
B3  `ulp_exam_session_rw` had a table-wide UPDATE on attempts, so it could move `deadline_at`. It keeps UPDATE only on
    the columns the exam room closes an attempt with: status, submitted_at, submit_idempotency_key.

NOTE: tests/security/grants_manifest.csv and Chapter 4 section 5 still list the old attempts grant; the proposal to
change them is in the WAVE-8 report and is made in its own commit.

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-11-15
"""

from alembic import op

from app.platform.rls import system_job_policy

revision = "c4d5e6f7a8b9"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None

RETENTION = "recorded_at < NOW() - INTERVAL '30 days'"


def upgrade() -> None:
    # --- B2: registry cleanup as the scheduler ------------------------------------------------
    for stmt in (
        *system_job_policy(
            "autosave_idempotency_registry", "sys_registry_cleanup_scan", "idempotency_cleanup", "SELECT",
            row_condition=RETENTION,
        ),
        *system_job_policy(
            "autosave_idempotency_registry", "sys_registry_cleanup", "idempotency_cleanup", "DELETE",
            row_condition=RETENTION,
        ),
    ):
        op.execute(stmt)
    op.execute("GRANT SELECT (recorded_at), DELETE ON autosave_idempotency_registry TO ulp_system_scheduler")

    # --- B5: attempt expiry --------------------------------------------------------------------
    op.execute("DROP POLICY IF EXISTS sys_attempt_expiry_close ON attempts")
    op.execute(
        """
        CREATE POLICY sys_attempt_expiry_close ON attempts FOR UPDATE TO ulp_system_scheduler
            USING ((SELECT app_system_job()) = 'attempt_expiry' AND status = 'IN_PROGRESS' AND deadline_at < NOW())
            WITH CHECK ((SELECT app_system_job()) = 'attempt_expiry' AND status = 'AUTO_SUBMITTED')
        """
    )
    # The UPDATE reads the row (WHERE) so PostgreSQL also checks the new row against a SELECT policy: the job may
    # see the rows it has just closed, nothing else.
    op.execute("DROP POLICY IF EXISTS sys_attempt_expiry_closed ON attempts")
    op.execute(
        """
        CREATE POLICY sys_attempt_expiry_closed ON attempts FOR SELECT TO ulp_system_scheduler
            USING ((SELECT app_system_job()) = 'attempt_expiry' AND status = 'AUTO_SUBMITTED')
        """
    )
    op.execute("GRANT UPDATE (status, submitted_at) ON attempts TO ulp_system_scheduler")
    for stmt in system_job_policy(
        "outbox_events", "sys_attempt_expiry_event", "attempt_expiry", "INSERT",
        row_condition="event_type = 'attempt.expired'",
    ):
        op.execute(stmt)
    op.execute("GRANT INSERT ON outbox_events TO ulp_system_scheduler")

    # --- B3: the exam room cannot move the deadline --------------------------------------------
    op.execute("REVOKE UPDATE ON attempts FROM ulp_exam_session_rw")
    op.execute("GRANT UPDATE (status, submitted_at, submit_idempotency_key) ON attempts TO ulp_exam_session_rw")


def downgrade() -> None:
    op.execute("REVOKE UPDATE ON attempts FROM ulp_exam_session_rw")
    op.execute("GRANT UPDATE ON attempts TO ulp_exam_session_rw")
    op.execute("REVOKE INSERT ON outbox_events FROM ulp_system_scheduler")
    op.execute("DROP POLICY IF EXISTS sys_attempt_expiry_event ON outbox_events")
    op.execute("REVOKE UPDATE (status, submitted_at) ON attempts FROM ulp_system_scheduler")
    op.execute("DROP POLICY IF EXISTS sys_attempt_expiry_closed ON attempts")
    op.execute("DROP POLICY IF EXISTS sys_attempt_expiry_close ON attempts")
    op.execute("REVOKE SELECT (recorded_at), DELETE ON autosave_idempotency_registry FROM ulp_system_scheduler")
    op.execute("DROP POLICY IF EXISTS sys_registry_cleanup ON autosave_idempotency_registry")
    op.execute("DROP POLICY IF EXISTS sys_registry_cleanup_scan ON autosave_idempotency_registry")
