"""Public API for app.modules.dashboard (D01 Learner Dashboard Projection)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from app.modules.dashboard.internal.dashboard import (
    ChapterMasterySummary,
    DashboardError,
    InProgressLessonRef,
    LearnerDashboardProjection,
    UpcomingAssignmentRef,
    build_learner_dashboard_projection,
)

if TYPE_CHECKING:
    from app.modules.dashboard.internal.service import (
        ActivityEventView,
        DashboardNotFoundError,
        DashboardValidationError,
        StoredDashboardProjectionView,
        get_learner_dashboard,
        list_activity_events,
        recompute_learner_dashboard_projection,
        record_activity_event,
    )

_SERVICE_EXPORTS = frozenset(
    {
        "ActivityEventView",
        "DashboardNotFoundError",
        "DashboardValidationError",
        "StoredDashboardProjectionView",
        "get_learner_dashboard",
        "list_activity_events",
        "recompute_learner_dashboard_projection",
        "record_activity_event",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily load DB service exports so pure-core callers avoid importing ORM models."""
    if name in _SERVICE_EXPORTS:
        service_mod = importlib.import_module("app.modules.dashboard.internal.service")
        value = getattr(service_mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ActivityEventView",
    "ChapterMasterySummary",
    "DashboardError",
    "DashboardNotFoundError",
    "DashboardValidationError",
    "InProgressLessonRef",
    "LearnerDashboardProjection",
    "StoredDashboardProjectionView",
    "UpcomingAssignmentRef",
    "build_learner_dashboard_projection",
    "get_learner_dashboard",
    "list_activity_events",
    "recompute_learner_dashboard_projection",
    "record_activity_event",
]
