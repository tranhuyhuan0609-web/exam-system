"""Public API for app.modules.error_notebook (D05 Error Notebook & Remediation)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from app.modules.error_notebook.internal.state import (
    REQUIRED_CONSECUTIVE_CORRECT,
    ErrorNoteState,
    apply_attempt,
)

if TYPE_CHECKING:
    from app.modules.error_notebook.internal.service import (
        ErrorNotebookNotFoundError,
        ErrorNotebookPolicyError,
        ErrorNotebookValidationError,
        ErrorNoteView,
        RemediationTaskView,
        assert_can_mark_resolved,
        create_remediation_task,
        list_error_notes,
        record_error_note_attempt,
        submit_remediation_attempt,
    )

_SERVICE_EXPORTS = frozenset(
    {
        "ErrorNoteView",
        "ErrorNotebookNotFoundError",
        "ErrorNotebookPolicyError",
        "ErrorNotebookValidationError",
        "RemediationTaskView",
        "assert_can_mark_resolved",
        "create_remediation_task",
        "list_error_notes",
        "record_error_note_attempt",
        "submit_remediation_attempt",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily load DB service exports so pure-core callers avoid importing ORM models."""
    if name in _SERVICE_EXPORTS:
        service_mod = importlib.import_module("app.modules.error_notebook.internal.service")
        value = getattr(service_mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ErrorNoteState",
    "ErrorNoteView",
    "ErrorNotebookNotFoundError",
    "ErrorNotebookPolicyError",
    "ErrorNotebookValidationError",
    "REQUIRED_CONSECUTIVE_CORRECT",
    "RemediationTaskView",
    "apply_attempt",
    "assert_can_mark_resolved",
    "create_remediation_task",
    "list_error_notes",
    "record_error_note_attempt",
    "submit_remediation_attempt",
]
