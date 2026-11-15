"""Public API for app.modules.gradebook (F03 Gradebook Analytics & CSV)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from app.modules.gradebook.internal.analytics import (
    ClassAnalyticsSummary,
    GradebookError,
    ItemResponseObservation,
    QuestionDistractorReport,
    StudentAttemptGrade,
    compute_class_analytics,
    render_gradebook_csv,
)

if TYPE_CHECKING:
    from app.modules.gradebook.internal.service import (
        GradebookNotFoundError,
        GradebookProjectionView,
        GradebookValidationError,
        export_gradebook_csv,
        get_gradebook_projection,
        load_class_gradebook_inputs,
        recompute_gradebook_projection,
    )

_SERVICE_EXPORTS = frozenset(
    {
        "GradebookNotFoundError",
        "GradebookProjectionView",
        "GradebookValidationError",
        "export_gradebook_csv",
        "get_gradebook_projection",
        "load_class_gradebook_inputs",
        "recompute_gradebook_projection",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily load DB service exports so pure-core callers avoid importing ORM models."""
    if name in _SERVICE_EXPORTS:
        service_mod = importlib.import_module("app.modules.gradebook.internal.service")
        value = getattr(service_mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ClassAnalyticsSummary",
    "GradebookError",
    "GradebookNotFoundError",
    "GradebookProjectionView",
    "GradebookValidationError",
    "ItemResponseObservation",
    "QuestionDistractorReport",
    "StudentAttemptGrade",
    "compute_class_analytics",
    "export_gradebook_csv",
    "get_gradebook_projection",
    "load_class_gradebook_inputs",
    "recompute_gradebook_projection",
    "render_gradebook_csv",
]
