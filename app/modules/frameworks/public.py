"""Public entry point of the frameworks module (C03)."""

from __future__ import annotations

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

__all__ = [
    "THPTQG_2025_BIOLOGY_FRAMEWORK",
    "THPTQG_2025_CHEMISTRY_FRAMEWORK",
    "THPTQG_2025_FRAMEWORKS",
    "THPTQG_2025_MATH_FRAMEWORK",
    "THPTQG_2025_PHYSICS_FRAMEWORK",
    "MatrixKey",
    "get_thptqg_2025_framework",
    "shortage_report",
    "validate_blueprint",
]
