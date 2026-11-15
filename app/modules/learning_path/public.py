"""Public API for app.modules.learning_path (D02 Personalized Learning Path)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from app.modules.learning_path.internal.remediation import (
    DEFAULT_PREREQUISITE_MASTERY_THRESHOLD,
    PrerequisiteEdge,
    RemediationStep,
    resolve_prerequisite_remediation_order,
)

if TYPE_CHECKING:
    from app.modules.learning_path.internal.service import (
        LearningPathNotFoundError,
        LearningPathValidationError,
        LearningPlanView,
        PlanTaskView,
        create_learning_plan,
        get_learning_plan,
        list_learning_plans,
        recalculate_learning_plan,
        update_plan_task_status,
    )

_SERVICE_EXPORTS = frozenset(
    {
        "LearningPathNotFoundError",
        "LearningPathValidationError",
        "LearningPlanView",
        "PlanTaskView",
        "create_learning_plan",
        "get_learning_plan",
        "list_learning_plans",
        "recalculate_learning_plan",
        "update_plan_task_status",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily load DB service exports so pure-core callers avoid importing ORM models."""
    if name in _SERVICE_EXPORTS:
        service_mod = importlib.import_module("app.modules.learning_path.internal.service")
        value = getattr(service_mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DEFAULT_PREREQUISITE_MASTERY_THRESHOLD",
    "LearningPathNotFoundError",
    "LearningPathValidationError",
    "LearningPlanView",
    "PlanTaskView",
    "PrerequisiteEdge",
    "RemediationStep",
    "create_learning_plan",
    "get_learning_plan",
    "list_learning_plans",
    "recalculate_learning_plan",
    "resolve_prerequisite_remediation_order",
    "update_plan_task_status",
]
