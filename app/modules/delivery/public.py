"""Public facade of the exam delivery module (C05)."""

from app.modules.delivery.internal.sanitizer import sanitize_exam_paper
from app.modules.delivery.internal.service import (
    AttemptRecord,
    AttemptSummary,
    DeliveryConflictError,
    DeliveryForbiddenError,
    DeliveryNotFoundError,
    StartResult,
    SubmitResult,
    derive_attempt_seed,
    get_attempt_paper,
    list_attempts,
    start_exam_session,
    submit_attempt,
)

__all__ = [
    "AttemptRecord",
    "AttemptSummary",
    "DeliveryConflictError",
    "DeliveryForbiddenError",
    "DeliveryNotFoundError",
    "StartResult",
    "SubmitResult",
    "derive_attempt_seed",
    "get_attempt_paper",
    "list_attempts",
    "sanitize_exam_paper",
    "start_exam_session",
    "submit_attempt",
]
