"""Public facade for the mastery module (D06)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from app.modules.mastery.internal.irt_tier import (
    IRT_TIER_BY_CODE,
    IRT_TIER_SPECS,
    CriterionCode,
    IrtCalibrationMetrics,
    IrtTierCode,
    IrtTierDecision,
    IrtTierSpec,
    select_irt_model_tier,
)

if TYPE_CHECKING:
    from app.modules.mastery.internal.models import MasteryEstimate, QuestionIrtParameter
    from app.modules.mastery.internal.service import (
        MAX_CALIBRATED_SE_THETA,
        MIN_CALIBRATED_EVIDENCE_COUNT,
        MasteryEstimateView,
        MasteryEvidenceItem,
        MasteryNotFoundError,
        MasteryScopeError,
        MasteryValidationError,
        QuestionCalibrationView,
        calibrate_question_irt,
        list_learner_mastery,
        recompute_learner_mastery,
    )
    from app.modules.mastery.router import router

_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "MasteryEstimate": ("app.modules.mastery.internal.models", "MasteryEstimate"),
    "QuestionIrtParameter": ("app.modules.mastery.internal.models", "QuestionIrtParameter"),
    "MAX_CALIBRATED_SE_THETA": ("app.modules.mastery.internal.service", "MAX_CALIBRATED_SE_THETA"),
    "MIN_CALIBRATED_EVIDENCE_COUNT": ("app.modules.mastery.internal.service", "MIN_CALIBRATED_EVIDENCE_COUNT"),
    "MasteryEstimateView": ("app.modules.mastery.internal.service", "MasteryEstimateView"),
    "MasteryEvidenceItem": ("app.modules.mastery.internal.service", "MasteryEvidenceItem"),
    "MasteryNotFoundError": ("app.modules.mastery.internal.service", "MasteryNotFoundError"),
    "MasteryScopeError": ("app.modules.mastery.internal.service", "MasteryScopeError"),
    "MasteryValidationError": ("app.modules.mastery.internal.service", "MasteryValidationError"),
    "QuestionCalibrationView": ("app.modules.mastery.internal.service", "QuestionCalibrationView"),
    "calibrate_question_irt": ("app.modules.mastery.internal.service", "calibrate_question_irt"),
    "list_learner_mastery": ("app.modules.mastery.internal.service", "list_learner_mastery"),
    "recompute_learner_mastery": ("app.modules.mastery.internal.service", "recompute_learner_mastery"),
    "router": ("app.modules.mastery.router", "router"),
}


def __getattr__(name: str) -> Any:
    """Lazily load DB/router exports so pure-core callers avoid importing ORM models."""
    target = _LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, attr_name = target
    module = importlib.import_module(module_path)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


__all__ = [
    "IRT_TIER_BY_CODE",
    "IRT_TIER_SPECS",
    "MAX_CALIBRATED_SE_THETA",
    "MIN_CALIBRATED_EVIDENCE_COUNT",
    "CriterionCode",
    "IrtCalibrationMetrics",
    "IrtTierCode",
    "IrtTierDecision",
    "IrtTierSpec",
    "MasteryEstimate",
    "MasteryEstimateView",
    "MasteryEvidenceItem",
    "MasteryNotFoundError",
    "MasteryScopeError",
    "MasteryValidationError",
    "QuestionCalibrationView",
    "QuestionIrtParameter",
    "calibrate_question_irt",
    "list_learner_mastery",
    "recompute_learner_mastery",
    "router",
    "select_irt_model_tier",
]
