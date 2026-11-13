"""Public facade for the error_notebook module (D05)."""

from app.modules.error_notebook.internal.state import (
    REQUIRED_CONSECUTIVE_CORRECT,
    ErrorNoteState,
    ErrorNoteStatus,
    RemediationTaskStatus,
    apply_attempt,
)

__all__ = [
    "REQUIRED_CONSECUTIVE_CORRECT",
    "ErrorNoteState",
    "ErrorNoteStatus",
    "RemediationTaskStatus",
    "apply_attempt",
]
