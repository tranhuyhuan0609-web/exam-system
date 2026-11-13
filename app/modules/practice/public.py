"""Public API for app.modules.practice (D01 Dashboard Projection & D03 Practice Engine)."""

from app.modules.practice.internal.dashboard import (
    ChapterMasterySummary,
    InProgressLessonRef,
    LearnerDashboardProjection,
    UpcomingAssignmentRef,
    build_learner_dashboard_projection,
)
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
    "ChapterMasterySummary",
    "InProgressLessonRef",
    "LearnerDashboardProjection",
    "MasterySignal",
    "PracticeCompletionReport",
    "PracticeEngineError",
    "PracticeItemSpec",
    "PracticeMode",
    "PracticeResponseRecord",
    "PracticeSessionState",
    "PracticeSessionStatus",
    "UpcomingAssignmentRef",
    "aggregate_signal_summary",
    "build_learner_dashboard_projection",
    "complete_practice_session",
    "reveal_next_hint",
    "submit_practice_response",
]
