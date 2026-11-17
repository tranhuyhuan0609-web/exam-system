"""Automatic grading: the outbox consumer of C05/C06 that grades an attempt as soon as it is closed.

`attempt.submitted` (the learner submitted in time) and `attempt.expired` (the deadline passed, the attempt was
auto-submitted by the expiry sweep or a late close) both name the attempt as the aggregate. The consumer runs as
``ulp_worker_grading`` in the event's tenant transaction and writes revision 1 of the grade; releasing the grade to the
learner stays a teacher decision (`release_grade`).

Delivery is at least once, so the handler is idempotent: an attempt that already has a grade (a teacher graded it by
hand first, or the event is redelivered after a crash) is left alone. Anything else that stops the grade (no answer key
on the paper, an answer missing) raises, so the relay retries and finally parks the event as FAILED, where the dead
letter queue shows it, instead of swallowing it.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.events import EventEnvelopeContract
from app.modules.grading.internal.service import GradingConflictError, grade_attempt
from app.modules.outbox.public import ConsumerRegistry, default_registry
from app.platform.tenancy import DbRole

logger = logging.getLogger(__name__)

GRADING_CONSUMER_NAME = "grading.grade_attempt"
GRADED_ON_EVENTS = ("attempt.submitted", "attempt.expired")


async def grade_closed_attempt(session: AsyncSession, envelope: EventEnvelopeContract) -> None:
    """Grade the attempt named by `envelope` (INITIAL grade, no human grader); a no-op if it is already graded."""
    try:
        await grade_attempt(
            session,
            envelope.aggregate_id,
            graded_by=None,
            grade_reason="INITIAL",
            correlation_id=envelope.correlation_id,
        )
    except GradingConflictError as exc:
        if exc.code != "ALREADY_GRADED":
            raise
        logger.info("attempt %s is already graded, event %s skipped", envelope.aggregate_id, envelope.event_id)


def register_grading_consumers(registry: ConsumerRegistry | None = None) -> None:
    """Subscribe the grading consumer to the events that close an attempt. Safe to call more than once."""
    target = registry if registry is not None else default_registry
    for event_type in GRADED_ON_EVENTS:
        if any(s.consumer_name == GRADING_CONSUMER_NAME for s in target.subscribers(event_type)):
            continue
        target.subscribe(event_type, GRADING_CONSUMER_NAME, grade_closed_attempt, role=DbRole.WORKER_GRADING)
