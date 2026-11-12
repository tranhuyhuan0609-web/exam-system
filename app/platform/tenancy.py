"""Tenant context for row-level security.

Every transaction that touches V11.3 tables states who it runs for with transaction-local
settings, read back in SQL by current_app_tenant_id() and app_system_job(). Transaction-local
means the setting dies at COMMIT/ROLLBACK, so it is safe behind PgBouncer transaction pooling.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import StrEnum

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal


class DbRole(StrEnum):
    """The runtime roles of the V11.3 least-privilege model. None of them may bypass RLS."""

    APP_RW = "ulp_app_rw"
    EXAM_SESSION_RW = "ulp_exam_session_rw"
    WORKER_INGESTION = "ulp_worker_ingestion"
    WORKER_GRADING = "ulp_worker_grading"
    SYSTEM_SCHEDULER = "ulp_system_scheduler"
    ANALYTICS_RO = "ulp_analytics_ro"


class SystemJob(StrEnum):
    """Cross-tenant background jobs, each matched by its own narrow RLS policies."""

    OUTBOX_RELAY = "outbox_relay"
    ATTEMPT_EXPIRY = "attempt_expiry"
    METRIC_ROLLUP = "metric_rollup"


async def set_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Bind the current transaction to one tenant."""
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_id)}
    )


async def set_system_job(session: AsyncSession, job: SystemJob) -> None:
    """Mark the current transaction as a cross-tenant system job."""
    await session.execute(text("SELECT set_config('app.system_job', :job, true)"), {"job": job.value})


async def set_role(session: AsyncSession, role: DbRole) -> None:
    """Drop to a runtime role for the rest of the transaction."""
    # The role name comes from the enum, never from input: SET ROLE takes no bind parameter.
    await session.execute(text(f"SET LOCAL ROLE {DbRole(role).value}"))


@asynccontextmanager
async def tenant_transaction(tenant_id: uuid.UUID, role: DbRole | None = None) -> AsyncIterator[AsyncSession]:
    """One transaction bound to `tenant_id`; commits on exit, rolls back on error."""
    async with SessionLocal() as session, session.begin():
        if role is not None:
            await set_role(session, role)
        await set_tenant(session, tenant_id)
        yield session


@asynccontextmanager
async def system_job_transaction(
    job: SystemJob, role: DbRole | None = DbRole.SYSTEM_SCHEDULER
) -> AsyncIterator[AsyncSession]:
    """One transaction for a cross-tenant job. It sees only what that job's policies allow."""
    async with SessionLocal() as session, session.begin():
        if role is not None:
            await set_role(session, role)
        await set_system_job(session, job)
        yield session
