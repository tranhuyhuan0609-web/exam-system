"""Public entry point of the frameworks module (C03)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from app.modules.frameworks.internal.blueprints import (
    THPTQG_2025_BIOLOGY_FRAMEWORK,
    THPTQG_2025_CHEMISTRY_FRAMEWORK,
    THPTQG_2025_FRAMEWORKS,
    THPTQG_2025_MATH_FRAMEWORK,
    THPTQG_2025_PHYSICS_FRAMEWORK,
    MatrixKey,
    get_thptqg_2025_framework,
    shortage_report,
    validate_blueprint,
)

if TYPE_CHECKING:
    from app.modules.frameworks.internal.service import (
        THPTQG_2025_CODE,
        THPTQG_2025_NAME,
        THPTQG_2025_SCORING_CODE,
        THPTQG_2025_SCORING_RULES,
        THPTQG_2025_VERSION_CODE,
        AssessmentFrameworkView,
        BlueprintNotFoundError,
        BlueprintValidationReportView,
        BlueprintView,
        FrameworkNotFoundError,
        FrameworkValidationError,
        FrameworkVersionView,
        create_assessment_framework,
        create_blueprint,
        seed_thptqg_2025_frameworks_and_scoring,
        validate_blueprint_and_shortage,
    )

_SERVICE_EXPORTS = frozenset(
    {
        "THPTQG_2025_CODE",
        "THPTQG_2025_NAME",
        "THPTQG_2025_SCORING_CODE",
        "THPTQG_2025_SCORING_RULES",
        "THPTQG_2025_VERSION_CODE",
        "AssessmentFrameworkView",
        "BlueprintNotFoundError",
        "BlueprintValidationReportView",
        "BlueprintView",
        "FrameworkNotFoundError",
        "FrameworkValidationError",
        "FrameworkVersionView",
        "create_assessment_framework",
        "create_blueprint",
        "seed_thptqg_2025_frameworks_and_scoring",
        "validate_blueprint_and_shortage",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily load DB service exports so pure-core callers avoid registering ORM models."""
    if name in _SERVICE_EXPORTS:
        service_mod = importlib.import_module("app.modules.frameworks.internal.service")
        value = getattr(service_mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "THPTQG_2025_BIOLOGY_FRAMEWORK",
    "THPTQG_2025_CHEMISTRY_FRAMEWORK",
    "THPTQG_2025_CODE",
    "THPTQG_2025_FRAMEWORKS",
    "THPTQG_2025_MATH_FRAMEWORK",
    "THPTQG_2025_NAME",
    "THPTQG_2025_PHYSICS_FRAMEWORK",
    "THPTQG_2025_SCORING_CODE",
    "THPTQG_2025_SCORING_RULES",
    "THPTQG_2025_VERSION_CODE",
    "AssessmentFrameworkView",
    "BlueprintNotFoundError",
    "BlueprintValidationReportView",
    "BlueprintView",
    "FrameworkNotFoundError",
    "FrameworkValidationError",
    "FrameworkVersionView",
    "MatrixKey",
    "create_assessment_framework",
    "create_blueprint",
    "get_thptqg_2025_framework",
    "seed_thptqg_2025_frameworks_and_scoring",
    "shortage_report",
    "validate_blueprint",
    "validate_blueprint_and_shortage",
]
