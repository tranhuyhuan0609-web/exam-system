"""Public API for app.modules.tutor (E02 AI Tutor Exam Lock & Anti-Leakage core)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from app.modules.tutor.internal.guard import (
    KNOWN_ATTEMPT_STATUSES,
    LearnerAttemptSnapshot,
    OutputFilterResult,
    PromptCheckResult,
    RetrievalCandidate,
    TutorGuardError,
    TutorMode,
    TutorSenderRole,
    assert_exam_not_active,
    assert_tutor_mode_allowed,
    filter_grounded_retrieval_candidates,
    filter_tutor_output,
    screen_user_prompt,
)

if TYPE_CHECKING:
    from app.modules.tutor.internal.service import (
        GroundedCitation,
        ReplyGenerator,
        TutorMessageView,
        TutorNotFoundError,
        TutorSessionView,
        TutorTurnView,
        TutorValidationError,
        create_tutor_session,
        send_tutor_message,
    )

_SERVICE_EXPORTS = frozenset(
    {
        "GroundedCitation",
        "ReplyGenerator",
        "TutorMessageView",
        "TutorNotFoundError",
        "TutorSessionView",
        "TutorTurnView",
        "TutorValidationError",
        "create_tutor_session",
        "send_tutor_message",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily load DB service exports so pure-core callers avoid importing ORM models."""
    if name in _SERVICE_EXPORTS:
        service_mod = importlib.import_module("app.modules.tutor.internal.service")
        value = getattr(service_mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "KNOWN_ATTEMPT_STATUSES",
    "GroundedCitation",
    "LearnerAttemptSnapshot",
    "OutputFilterResult",
    "PromptCheckResult",
    "ReplyGenerator",
    "RetrievalCandidate",
    "TutorGuardError",
    "TutorMessageView",
    "TutorMode",
    "TutorNotFoundError",
    "TutorSenderRole",
    "TutorSessionView",
    "TutorTurnView",
    "TutorValidationError",
    "assert_exam_not_active",
    "assert_tutor_mode_allowed",
    "create_tutor_session",
    "filter_grounded_retrieval_candidates",
    "filter_tutor_output",
    "screen_user_prompt",
    "send_tutor_message",
]
