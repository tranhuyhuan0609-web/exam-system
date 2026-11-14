"""Database service operations for G03 transactional outbox, idempotent inbox, and DLQ replay."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.events import EventEnvelopeContract
from app.modules.outbox.internal.models import FAILED, PENDING, OutboxEvent
from app.modules.outbox.internal.relay import (
    ConsumerRegistry,
    Handler,
    RelayStats,
    RetryPolicy,
    relay_once,
)
from app.modules.outbox.internal.store import OutboxStats, consume_once, enqueue, outbox_stats
from app.platform.pagination import (
    CursorPage,
    build_cursor_page,
    clamp_limit,
    decode_cursor,
)

MIN_REPLAY_REASON_LENGTH = 15
_DLQ_SORT_FIELD = "created_at"
_DLQ_ALLOWED_SORT_FIELDS = frozenset({_DLQ_SORT_FIELD})
_DLQ_ROUTE = "/api/v1/admin/dlq"
_INVISIBLE_CHARS_RE = re.compile(r"[\u200B-\u200F\u2028-\u202F\u2060-\u206F\uFEFF\u00AD\u180E\u034F]")


class DlqInvalidStateError(ValueError):
    """Raised when attempting to replay an outbox event that is not in FAILED status."""


class DlqReasonValidationError(ValueError):
    """Raised when a DLQ replay reason is missing or shorter than MIN_REPLAY_REASON_LENGTH."""


@dataclass(frozen=True, slots=True)
class DeadLetterRecord:
    """Snapshot of a FAILED outbox event surfaced in the admin DLQ."""

    event_id: uuid.UUID
    tenant_id: uuid.UUID
    event_type: str
    schema_version: int
    aggregate_type: str
    aggregate_id: uuid.UUID
    correlation_id: str | None
    causation_id: uuid.UUID | None
    payload_json: dict[str, Any]
    status: str
    attempt_count: int
    next_attempt_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DlqReplayResult:
    """Result of re-queueing a FAILED outbox event back to PENDING."""

    event_id: uuid.UUID
    tenant_id: uuid.UUID
    previous_status: str
    status: str
    previous_attempt_count: int
    attempt_count: int
    reason: str
    replayed_by: uuid.UUID | None
    correlation_id: str | None


def normalize_replay_reason(raw_reason: str) -> str:
    """Strip invisible characters, collapse whitespace, and enforce minimum admin reason length."""
    if not isinstance(raw_reason, str):
        raise DlqReasonValidationError("reason must be a string")
    cleaned = _INVISIBLE_CHARS_RE.sub("", raw_reason).strip()
    normalized = " ".join(cleaned.split())
    if not normalized:
        raise DlqReasonValidationError("reason must not be empty")
    if len(normalized) < MIN_REPLAY_REASON_LENGTH:
        raise DlqReasonValidationError(
            f"replay reason must be at least {MIN_REPLAY_REASON_LENGTH} characters"
        )
    return normalized


def _to_dlq_record(row: OutboxEvent) -> DeadLetterRecord:
    return DeadLetterRecord(
        event_id=row.event_id,
        tenant_id=row.tenant_id,
        event_type=row.event_type,
        schema_version=row.schema_version,
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        payload_json=dict(row.payload_json),
        status=row.status,
        attempt_count=row.attempt_count,
        next_attempt_at=row.next_attempt_at,
        created_at=row.created_at,
    )


async def enqueue_event(session: AsyncSession, envelope: EventEnvelopeContract) -> uuid.UUID:
    """Persist an outbox event inside the caller's active business transaction."""
    await enqueue(session, envelope)
    return envelope.event_id


async def consume_event_once(
    session: AsyncSession,
    *,
    consumer_name: str,
    envelope: EventEnvelopeContract,
    handler: Handler,
) -> bool:
    """Execute `handler` exactly once per `(consumer_name, event_id)` via `inbox_events`."""
    first = await consume_once(session, consumer_name, envelope.event_id, envelope.tenant_id)
    if first:
        await handler(session, envelope)
    return first


async def run_outbox_relay(
    batch_size: int = 100,
    *,
    registry: ConsumerRegistry | None = None,
    retry: RetryPolicy | None = None,
) -> RelayStats:
    """Run one relay pass under `system_job_transaction(SystemJob.OUTBOX_RELAY)` (`ulp_system_scheduler`)."""
    return await relay_once(batch_size, registry=registry, retry=retry)


async def get_outbox_stats(session: AsyncSession) -> OutboxStats:
    """Return pending/published/failed counts and oldest pending age within the session's RLS scope."""
    return await outbox_stats(session)


async def list_dlq_events(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    cursor_secret: str,
    limit: int | None = None,
    cursor: str | None = None,
    event_type: str | None = None,
) -> CursorPage[DeadLetterRecord]:
    """List FAILED outbox events (DLQ) in descending `(created_at, event_id)` order with signed keyset cursor."""
    resolved_limit = clamp_limit(limit)
    stmt = select(OutboxEvent).where(OutboxEvent.status == FAILED)
    if event_type is not None and event_type.strip():
        stmt = stmt.where(OutboxEvent.event_type == event_type.strip())

    if cursor is not None:
        decoded = decode_cursor(
            cursor,
            secret=cursor_secret,
            allowed_sort_fields=_DLQ_ALLOWED_SORT_FIELDS,
            expected_sort_field=_DLQ_SORT_FIELD,
            expected_direction="desc",
            tenant_id=str(tenant_id),
            route=_DLQ_ROUTE,
        )
        cursor_created_at = datetime.fromisoformat(str(decoded.sort_value))
        cursor_event_id = uuid.UUID(decoded.tie_id)
        stmt = stmt.where(
            tuple_(OutboxEvent.created_at, OutboxEvent.event_id) < (cursor_created_at, cursor_event_id)
        )

    stmt = stmt.order_by(OutboxEvent.created_at.desc(), OutboxEvent.event_id.desc()).limit(resolved_limit + 1)
    rows = (await session.scalars(stmt)).all()
    records = [_to_dlq_record(r) for r in rows]
    return build_cursor_page(
        records,
        limit=resolved_limit,
        sort_field=_DLQ_SORT_FIELD,
        direction="desc",
        key_extractor=lambda rec: (rec.created_at.isoformat(), str(rec.event_id)),
        secret=cursor_secret,
        tenant_id=str(tenant_id),
        route=_DLQ_ROUTE,
    )


async def count_dlq_events(session: AsyncSession) -> int:
    """Return total FAILED outbox events visible in the caller's RLS scope."""
    count = await session.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.status == FAILED))
    return int(count or 0)


async def replay_dlq_event(
    session: AsyncSession,
    *,
    event_id: uuid.UUID,
    reason: str,
    actor_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> DlqReplayResult:
    """Reset a FAILED outbox event to PENDING so the relay picks it up on its next cycle."""
    normalized_reason = normalize_replay_reason(reason)
    row = await session.scalar(
        select(OutboxEvent).where(OutboxEvent.event_id == event_id).with_for_update()
    )
    if row is None:
        raise LookupError(f"dlq event {event_id} not found")
    if row.status != FAILED:
        raise DlqInvalidStateError(f"outbox event {event_id} has status {row.status}, expected {FAILED}")

    previous_status = row.status
    previous_attempt_count = row.attempt_count
    row.status = PENDING
    row.attempt_count = 0
    row.next_attempt_at = None
    row.published_at = None
    await session.flush()

    return DlqReplayResult(
        event_id=row.event_id,
        tenant_id=row.tenant_id,
        previous_status=previous_status,
        status=row.status,
        previous_attempt_count=previous_attempt_count,
        attempt_count=row.attempt_count,
        reason=normalized_reason,
        replayed_by=actor_id,
        correlation_id=correlation_id,
    )
