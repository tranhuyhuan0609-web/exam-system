"""Public entry point of the flashcards module (D04)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from app.modules.flashcards.internal.fsrs import (
    FSRS_V5_DEFAULT_WEIGHTS,
    SCHEDULER_VERSION,
    VN_TZ,
    FSRSRating,
    FSRSReviewResult,
    FSRSState,
    RatingValue,
    compute_retrievability,
    next_interval_days,
    review_card,
    step_fsrs_v5,
)

if TYPE_CHECKING:
    from app.modules.flashcards.internal.service import (
        FlashcardDeckView,
        FlashcardNotFoundError,
        FlashcardReviewView,
        FlashcardValidationError,
        FlashcardView,
        NewFlashcardInput,
        RevisionScheduleItemView,
        create_flashcard_deck,
        get_revision_schedule,
        review_flashcard,
    )

_SERVICE_EXPORTS = frozenset(
    {
        "FlashcardDeckView",
        "FlashcardNotFoundError",
        "FlashcardReviewView",
        "FlashcardValidationError",
        "FlashcardView",
        "NewFlashcardInput",
        "RevisionScheduleItemView",
        "create_flashcard_deck",
        "get_revision_schedule",
        "review_flashcard",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily load DB service exports so pure-core callers avoid importing ORM models."""
    if name in _SERVICE_EXPORTS:
        service_mod = importlib.import_module("app.modules.flashcards.internal.service")
        value = getattr(service_mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "FSRS_V5_DEFAULT_WEIGHTS",
    "SCHEDULER_VERSION",
    "VN_TZ",
    "FSRSRating",
    "FSRSReviewResult",
    "FSRSState",
    "FlashcardDeckView",
    "FlashcardNotFoundError",
    "FlashcardReviewView",
    "FlashcardValidationError",
    "FlashcardView",
    "NewFlashcardInput",
    "RatingValue",
    "RevisionScheduleItemView",
    "compute_retrievability",
    "create_flashcard_deck",
    "get_revision_schedule",
    "next_interval_days",
    "review_card",
    "review_flashcard",
    "step_fsrs_v5",
]
