"""Consumer registry and the outbox relay (ADR-001, ADR-012).

The relay runs as ulp_system_scheduler under the outbox_relay job: the only role and job allowed to
read every tenant's outbox, and only allowed to change the delivery bookkeeping (`status`, `published_at`, `attempt_count`,
`next_attempt_at`). Each handler runs
in its own transaction, bound to the event's tenant, under a worker role that may write the inbox.
"""

import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta

from pydantic import ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.events import EventEnvelopeContract
from app.modules.outbox.internal.models import FAILED, PENDING, PUBLISHED, OutboxEvent
from app.modules.outbox.internal.store import consume_once
from app.platform.tenancy import DbRole, SystemJob, system_job_transaction, tenant_transaction

logger = logging.getLogger(__name__)

Handler = Callable[[AsyncSession, EventEnvelopeContract], Awaitable[None]]
"""A consumer's side effect. It receives the open tenant transaction and must not commit it."""

INBOX_WRITER_ROLES = frozenset({DbRole.WORKER_INGESTION, DbRole.WORKER_GRADING})
"""Roles granted INSERT on inbox_events (spec chapter 4, section 5): the only roles a handler can run as."""

MAX_CONSUMER_NAME_LENGTH = 80  # inbox_events.consumer_name


@dataclass(frozen=True)
class Subscriber:
    event_type: str
    consumer_name: str
    handler: Handler
    role: DbRole


class ConsumerRegistry:
    """Which consumers run for which event type."""

    def __init__(self) -> None:
        self._by_type: dict[str, list[Subscriber]] = {}

    def subscribe(
        self, event_type: str, consumer_name: str, handler: Handler, *, role: DbRole = DbRole.WORKER_INGESTION
    ) -> None:
        """Register `handler` as `consumer_name` for `event_type`, run as `role`.

        The default role is the worker role with no access to answers or grades; a grading consumer
        passes DbRole.WORKER_GRADING.
        """
        if not consumer_name or len(consumer_name) > MAX_CONSUMER_NAME_LENGTH:
            raise ValueError(f"consumer_name must have 1 to {MAX_CONSUMER_NAME_LENGTH} characters")
        if role not in INBOX_WRITER_ROLES:
            raise ValueError(f"{role} may not write inbox_events; use one of {sorted(INBOX_WRITER_ROLES)}")
        subscribers = self._by_type.setdefault(event_type, [])
        if any(s.consumer_name == consumer_name for s in subscribers):
            raise ValueError(f"{consumer_name} already consumes {event_type}")
        subscribers.append(Subscriber(event_type, consumer_name, handler, role))

    def subscribers(self, event_type: str) -> tuple[Subscriber, ...]:
        return tuple(self._by_type.get(event_type, ()))


default_registry = ConsumerRegistry()


def subscribe(event_type: str, consumer_name: str, handler: Handler, *, role: DbRole = DbRole.WORKER_INGESTION) -> None:
    """Register a consumer on the process-wide registry that `relay_once` uses by default."""
    default_registry.subscribe(event_type, consumer_name, handler, role=role)


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with jitter, at most `max_attempts` delivery attempts per event (ADR-012)."""

    max_attempts: int = 5
    base_delay_seconds: float = 0.2
    max_delay_seconds: float = 5.0

    def delay(self, failed_attempts: int) -> float:
        """Pause before the next attempt: half the exponential step plus a random half of it."""
        step = min(self.max_delay_seconds, self.base_delay_seconds * 2 ** (failed_attempts - 1))
        return step / 2 + random.uniform(0, step / 2)


@dataclass
class RelayStats:
    claimed: int = 0
    published: int = 0
    rescheduled: int = 0
    """Events with a failed subscriber that stay PENDING with a later `next_attempt_at`."""
    failed: int = 0
    """Events that used up their attempts (or cannot be delivered at all) and are now FAILED."""
    deliveries: int = 0
    """Handler runs whose transaction committed."""
    duplicates: int = 0
    """Deliveries the inbox recognised as already processed (handler not run)."""
    handler_errors: int = 0
    """Handler runs that raised (and rolled back)."""
    failed_event_ids: list[uuid.UUID] = field(default_factory=list)
    rescheduled_event_ids: list[uuid.UUID] = field(default_factory=list)


async def _deliver(subscriber: Subscriber, envelope: EventEnvelopeContract, attempt: int, stats: RelayStats) -> bool:
    """One run of one consumer for one event, in its own tenant transaction; False if it raised."""
    try:
        async with tenant_transaction(envelope.tenant_id, role=subscriber.role) as session:
            first = await consume_once(session, subscriber.consumer_name, envelope.event_id, envelope.tenant_id)
            if first:
                await subscriber.handler(session, envelope)
    except Exception:
        stats.handler_errors += 1
        logger.warning(
            "outbox consumer %s failed on event %s (%s), attempt %d",
            subscriber.consumer_name, envelope.event_id, envelope.event_type, attempt,
            exc_info=True,
        )  # fmt: skip
        return False
    if first:
        stats.deliveries += 1
    else:
        stats.duplicates += 1
    return True


def _envelope(row: OutboxEvent) -> EventEnvelopeContract:
    return EventEnvelopeContract(
        event_id=row.event_id,
        event_type=row.event_type,
        schema_version=row.schema_version,
        tenant_id=row.tenant_id,
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        payload=row.payload_json,
        occurred_at=row.created_at,
    )


async def relay_once(
    batch_size: int = 100, *, registry: ConsumerRegistry | None = None, retry: RetryPolicy | None = None
) -> RelayStats:
    """Make one delivery attempt for up to `batch_size` due pending events, oldest first.

    Due means PENDING with `next_attempt_at` unset or passed. Every subscriber runs once per event;
    a subscriber that committed on an earlier attempt is recognised by the inbox and not run again.
    If all succeed the event becomes PUBLISHED. Otherwise `attempt_count` goes up by one and the
    event is FAILED once it reaches `retry.max_attempts`, else it stays PENDING with a
    `next_attempt_at` after the backoff, so a later `relay_once` (this process or another) retries it.
    Nothing sleeps here.

    Rows are claimed with FOR UPDATE SKIP LOCKED and stay locked until the batch is marked, so
    concurrent relays share the backlog without delivering an event twice. If the relay dies before
    marking, the lock is released, the events stay due and are delivered again; the inbox then
    keeps each consumer's side effect to one.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    registry = registry or default_registry
    retry = retry or RetryPolicy()
    stats = RelayStats()
    async with system_job_transaction(SystemJob.OUTBOX_RELAY) as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.status == PENDING,
                        or_(OutboxEvent.next_attempt_at.is_(None), OutboxEvent.next_attempt_at <= func.now()),
                    )
                    .order_by(OutboxEvent.created_at, OutboxEvent.event_id)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        stats.claimed = len(rows)
        for row in rows:
            attempt = row.attempt_count + 1
            try:
                envelope = _envelope(row)
            except ValidationError:
                logger.error("outbox event %s does not match EventEnvelopeContract", row.event_id, exc_info=True)
                delivered, retryable = False, False  # retrying cannot fix a malformed row
            else:
                # Every subscriber runs even when an earlier one failed: one failing consumer
                # must not keep the event from the others.
                results = [await _deliver(s, envelope, attempt, stats) for s in registry.subscribers(row.event_type)]
                delivered, retryable = all(results), True
            if delivered:
                values = {"status": PUBLISHED, "published_at": func.now()}
                stats.published += 1
            elif retryable and attempt < retry.max_attempts:
                delay = timedelta(seconds=retry.delay(attempt))
                values = {"attempt_count": attempt, "next_attempt_at": func.now() + delay}
                stats.rescheduled += 1
                stats.rescheduled_event_ids.append(row.event_id)
            else:
                values = {"status": FAILED, "attempt_count": attempt}
                stats.failed += 1
                stats.failed_event_ids.append(row.event_id)
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.event_id == row.event_id)
                .values(**values)
                .execution_options(synchronize_session=False)
            )
    return stats
