"""Public API for app.modules.tutor (E02 AI Tutor Exam Lock & Anti-Leakage core)."""

from app.modules.tutor.internal.guard import (
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

__all__ = [
    "LearnerAttemptSnapshot",
    "OutputFilterResult",
    "PromptCheckResult",
    "RetrievalCandidate",
    "TutorGuardError",
    "TutorMode",
    "TutorSenderRole",
    "assert_exam_not_active",
    "assert_tutor_mode_allowed",
    "filter_grounded_retrieval_candidates",
    "filter_tutor_output",
    "screen_user_prompt",
]
