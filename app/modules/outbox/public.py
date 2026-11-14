"""Public entry point of the outbox module (G03: transactional outbox, idempotent inbox, relay).

Producer: `await enqueue(session, EventEnvelopeContract(...))` inside the business transaction.
Consumer: `subscribe("attempt.submitted", "grading.grade_attempt", handler, role=DbRole.WORKER_GRADING)`.
Relay:    `await relay_once()` from a scheduler loop.
"""

from app.modules.outbox.internal.models import EVENT_STATUSES, FAILED, PENDING, PUBLISHED
from app.modules.outbox.internal.relay import (
    INBOX_WRITER_ROLES,
    ConsumerRegistry,
    Handler,
    RelayStats,
    RetryPolicy,
    default_registry,
    relay_once,
    subscribe,
)
from app.modules.outbox.internal.service import (
    MIN_REPLAY_REASON_LENGTH,
    DeadLetterRecord,
    DlqInvalidStateError,
    DlqReasonValidationError,
    DlqReplayResult,
    consume_event_once,
    count_dlq_events,
    enqueue_event,
    get_outbox_stats,
    list_dlq_events,
    normalize_replay_reason,
    replay_dlq_event,
    run_outbox_relay,
)
from app.modules.outbox.internal.store import OutboxStats, consume_once, enqueue, outbox_stats

__all__ = [
    "EVENT_STATUSES",
    "FAILED",
    "INBOX_WRITER_ROLES",
    "MIN_REPLAY_REASON_LENGTH",
    "PENDING",
    "PUBLISHED",
    "ConsumerRegistry",
    "DeadLetterRecord",
    "DlqInvalidStateError",
    "DlqReasonValidationError",
    "DlqReplayResult",
    "Handler",
    "OutboxStats",
    "RelayStats",
    "RetryPolicy",
    "consume_event_once",
    "consume_once",
    "count_dlq_events",
    "default_registry",
    "enqueue",
    "enqueue_event",
    "get_outbox_stats",
    "list_dlq_events",
    "normalize_replay_reason",
    "outbox_stats",
    "relay_once",
    "replay_dlq_event",
    "run_outbox_relay",
    "subscribe",
]
