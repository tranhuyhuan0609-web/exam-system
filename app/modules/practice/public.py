"""Public API for app.modules.practice (D03 Practice Engine)."""

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

__all__ = [
    "MasterySignal",
    "PracticeCompletionReport",
    "PracticeEngineError",
    "PracticeItemSpec",
    "PracticeMode",
    "PracticeResponseRecord",
    "PracticeSessionState",
    "PracticeSessionStatus",
    "aggregate_signal_summary",
    "complete_practice_session",
    "reveal_next_hint",
    "submit_practice_response",
]
