"""Public facade of the exam delivery module (C05)."""

from app.modules.delivery.internal.sanitizer import sanitize_exam_paper
from app.modules.delivery.internal.service import (
    AttemptRecord,
    DeliveryConflictError,
    DeliveryForbiddenError,
    DeliveryNotFoundError,
    StartResult,
    SubmitResult,
    derive_attempt_seed,
    get_attempt_paper,
    start_exam_session,
    submit_attempt,
)

__all__ = [
    "AttemptRecord",
    "DeliveryConflictError",
    "DeliveryForbiddenError",
    "DeliveryNotFoundError",
    "StartResult",
    "SubmitResult",
    "derive_attempt_seed",
    "get_attempt_paper",
    "sanitize_exam_paper",
    "start_exam_session",
    "submit_attempt",
]
