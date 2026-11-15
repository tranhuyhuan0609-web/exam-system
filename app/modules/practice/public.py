"""Public API for app.modules.practice (D03 Interactive Practice Engine)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from app.modules.practice.internal.engine import (
    MasterySignal,
    PracticeCompletionReport,
    PracticeEngineError,
    PracticeItemSpec,
    PracticeMode,
    PracticeResponseRecord,
    PracticeSessionState,
    PracticeSessionStatus,
    aggregate_signal_summary,
    complete_practice_session,
    reveal_next_hint,
    submit_practice_response,
)

if TYPE_CHECKING:
    from app.modules.practice.internal.service import (
        PracticeCompletionView,
        PracticeDomainError,
        PracticeNotFoundError,
        PracticeResponseView,
        PracticeSessionView,
        PracticeValidationError,
        complete_session,
        get_practice_session,
        reveal_practice_hint,
        start_practice_session,
        submit_practice_answer,
    )

_SERVICE_EXPORTS = frozenset(
    {
        "PracticeCompletionView",
        "PracticeDomainError",
        "PracticeNotFoundError",
        "PracticeResponseView",
        "PracticeSessionView",
        "PracticeValidationError",
        "complete_session",
        "get_practice_session",
        "reveal_practice_hint",
        "start_practice_session",
        "submit_practice_answer",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily load DB service exports so pure-core callers avoid importing ORM models."""
    if name in _SERVICE_EXPORTS:
        service_mod = importlib.import_module("app.modules.practice.internal.service")
        value = getattr(service_mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "MasterySignal",
    "PracticeCompletionReport",
    "PracticeCompletionView",
    "PracticeDomainError",
    "PracticeEngineError",
    "PracticeItemSpec",
    "PracticeMode",
    "PracticeNotFoundError",
    "PracticeResponseRecord",
    "PracticeResponseView",
    "PracticeSessionState",
    "PracticeSessionStatus",
    "PracticeSessionView",
    "PracticeValidationError",
    "aggregate_signal_summary",
    "complete_practice_session",
    "complete_session",
    "get_practice_session",
    "reveal_next_hint",
    "reveal_practice_hint",
    "start_practice_session",
    "submit_practice_answer",
    "submit_practice_response",
]
