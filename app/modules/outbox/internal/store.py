"""Writing to the outbox and the inbox, always inside the caller's transaction (ADR-011)."""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.events import EventEnvelopeContract
from app.modules.outbox.internal.models import EVENT_STATUSES, PENDING, InboxEvent, OutboxEvent


async def enqueue(session: AsyncSession, envelope: EventEnvelopeContract) -> None:
    """Add `envelope` to the outbox in the caller's open transaction.

    Nothing is committed here: the event exists if and only if the caller's business change commits.
    The tenant policy rejects an envelope whose tenant is not the transaction's tenant. No RETURNING
    is used, so roles that may only INSERT into the outbox (the workers) can publish too.

    `causation_id` is not stored: outbox_events has no column for it (see the module report).
    """
    await session.execute(
        insert(OutboxEvent).values(
            event_id=envelope.event_id,
            tenant_id=envelope.tenant_id,
            event_type=envelope.event_type,
            schema_version=envelope.schema_version,
            aggregate_type=envelope.aggregate_type,
            aggregate_id=envelope.aggregate_id,
            correlation_id=envelope.correlation_id,
            payload_json=envelope.payload,
            created_at=envelope.occurred_at,
        )
    )


async def consume_once(session: AsyncSession, consumer_name: str, event_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
    """Claim `event_id` for `consumer_name` in the caller's transaction; True only for the first delivery.

    Run the side effect in the same transaction and only when this returns True. A concurrent
    delivery of the same event waits on the primary key until this transaction ends, then gets False
    if it committed, or claims the event itself if it rolled back.
    """
    result = await session.execute(
        pg_insert(InboxEvent)
        .values(consumer_name=consumer_name, event_id=event_id, tenant_id=tenant_id)
        .on_conflict_do_nothing(index_elements=["consumer_name", "event_id"])
    )
    return result.rowcount == 1


@dataclass(frozen=True)
class OutboxStats:
    """Outbox health as seen by the session's role and policies."""

    pending: int
    published: int
    failed: int
    oldest_pending_age_seconds: float | None


async def outbox_stats(session: AsyncSession) -> OutboxStats:
    """Counts by status and the age of the oldest pending event.

    Row-level security decides the scope: one tenant for a tenant transaction, every tenant for the
    outbox_relay system job.
    """
    counts = dict((await session.execute(select(OutboxEvent.status, func.count()).group_by(OutboxEvent.status))).all())
    oldest_age = (
        await session.execute(
            select(func.extract("epoch", func.now() - func.min(OutboxEvent.created_at))).where(
                OutboxEvent.status == PENDING
            )
        )
    ).scalar()
    pending, published, failed = (int(counts.get(status, 0)) for status in EVENT_STATUSES)
    return OutboxStats(
        pending=pending,
        published=published,
        failed=failed,
        oldest_pending_age_seconds=None if oldest_age is None else float(oldest_age),
    )
