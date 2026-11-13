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
from app.modules.outbox.internal.store import OutboxStats, consume_once, enqueue, outbox_stats

__all__ = [
    "EVENT_STATUSES",
    "FAILED",
    "INBOX_WRITER_ROLES",
    "PENDING",
    "PUBLISHED",
    "ConsumerRegistry",
    "Handler",
    "OutboxStats",
    "RelayStats",
    "RetryPolicy",
    "consume_once",
    "default_registry",
    "enqueue",
    "outbox_stats",
    "relay_once",
    "subscribe",
]
