"""Background jobs of the exam room that run across tenants as ``ulp_system_scheduler`` (no BYPASSRLS).

* ``expire_overdue_attempts``: IN_PROGRESS attempts past ``deadline_at`` become AUTO_SUBMITTED (submitted_at = the
  deadline, as a late close in the delivery service) and announce ``attempt.expired`` for the grading worker.
* ``purge_idempotency_registry``: drops autosave idempotency keys older than 30 days (B2).
"""

import json

from sqlalchemy import text

from app.platform.tenancy import SystemJob, system_job_transaction

ATTEMPT_EXPIRED_EVENT = "attempt.expired"


async def expire_overdue_attempts(batch_size: int = 500) -> int:
    """Close up to ``batch_size`` overdue attempts in one transaction; returns how many were closed."""
    async with system_job_transaction(SystemJob.ATTEMPT_EXPIRY) as session:
        # No RETURNING on the UPDATE: PostgreSQL would check the new (AUTO_SUBMITTED) row against the SELECT
        # policy, which only shows IN_PROGRESS rows. So read first, then update the rows that were read.
        rows = (
            await session.execute(
                text(
                    "SELECT id, tenant_id, deadline_at FROM attempts "
                    "WHERE status = 'IN_PROGRESS' AND deadline_at < NOW() "
                    "ORDER BY deadline_at LIMIT :n FOR UPDATE SKIP LOCKED"
                ),
                {"n": batch_size},
            )
        ).all()
        if rows:
            await session.execute(
                text(
                    "UPDATE attempts SET status = 'AUTO_SUBMITTED', submitted_at = deadline_at "
                    "WHERE id = ANY(:ids) AND status = 'IN_PROGRESS'"
                ),
                {"ids": [r.id for r in rows]},
            )
        for attempt_id, tenant_id, deadline_at in rows:
            payload = {
                "tenant_id": str(tenant_id),
                "attempt_id": str(attempt_id),
                "deadline_at": deadline_at.isoformat(),
                "auto_submitted_at": deadline_at.isoformat(),
            }
            await session.execute(
                text(
                    "INSERT INTO outbox_events (tenant_id, event_type, aggregate_type, aggregate_id, payload_json) "
                    "VALUES (:tenant_id, :event_type, 'attempt', :aggregate_id, CAST(:payload AS jsonb))"
                ),
                {
                    "tenant_id": tenant_id,
                    "event_type": ATTEMPT_EXPIRED_EVENT,
                    "aggregate_id": attempt_id,
                    "payload": json.dumps(payload),
                },
            )
        return len(rows)


async def purge_idempotency_registry() -> int:
    """Delete autosave idempotency keys recorded more than 30 days ago; returns how many."""
    async with system_job_transaction(SystemJob.IDEMPOTENCY_CLEANUP) as session:
        result = await session.execute(
            text("DELETE FROM autosave_idempotency_registry WHERE recorded_at < NOW() - INTERVAL '30 days'")
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]
