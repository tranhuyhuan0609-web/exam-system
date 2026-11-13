"""Multi-criteria IRT model tier selection gate (D06).

Canonical thresholds mirror `irt_tier_registry` in `docs/architecture/platform_manifest.yaml`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

IrtTierCode = Literal[
    "RULE_WEIGHTED_L1",
    "RASCH_1PL_L2",
    "IRT_2PL_GPCM_L3",
    "IRT_3PL_GPCM_L4",
]

CriterionCode = Literal[
    "NOT_CONVERGED",
    "MIN_ITEM_RESPONSES",
    "MIN_LEARNERS",
    "MIN_ABILITY_SD",
    "MAX_SE_B",
    "INFIT_MSQ_RANGE",
    "MAX_ABS_DIF",
]


@dataclass(frozen=True, slots=True)
class IrtTierSpec:
    """Calibration gate specification for a single IRT complexity tier."""

    code: IrtTierCode
    min_item_responses: int
    min_learners: int
    min_ability_sd: float | None = None
    max_se_b: float | None = None
    infit_msq_range: tuple[float, float] | None = None
    max_abs_dif: float | None = None
    fallback_to: IrtTierCode | None = None


@dataclass(frozen=True, slots=True)
class IrtCalibrationMetrics:
    """Observed calibration statistics evaluated by the IRT tier gate."""

    sample_size: int
    se_b: float
    learner_count: int = 1000
    ability_sd: float = 0.80
    converged: bool = True
    infit_msq: float = 1.00
    max_abs_dif: float = 0.10


@dataclass(frozen=True, slots=True)
class IrtTierDecision:
    """Result of evaluating IRT tier eligibility along with blockers for the next tier."""

    selected_tier: IrtTierCode
    next_tier: IrtTierCode | None
    failed_next_tier_criteria: tuple[CriterionCode, ...]


IRT_TIER_SPECS: tuple[IrtTierSpec, ...] = (
    IrtTierSpec(
        code="RULE_WEIGHTED_L1",
        min_item_responses=1,
        min_learners=1,
        fallback_to=None,
    ),
    IrtTierSpec(
        code="RASCH_1PL_L2",
        min_item_responses=150,
        min_learners=100,
        min_ability_sd=0.60,
        max_se_b=0.30,
        infit_msq_range=(0.70, 1.30),
        max_abs_dif=0.50,
        fallback_to="RULE_WEIGHTED_L1",
    ),
    IrtTierSpec(
        code="IRT_2PL_GPCM_L3",
        min_item_responses=300,
        min_learners=250,
        min_ability_sd=0.65,
        max_se_b=0.25,
        infit_msq_range=(0.75, 1.25),
        max_abs_dif=0.45,
        fallback_to="RASCH_1PL_L2",
    ),
    IrtTierSpec(
        code="IRT_3PL_GPCM_L4",
        min_item_responses=1000,
        min_learners=800,
        min_ability_sd=0.70,
        max_se_b=0.20,
        infit_msq_range=(0.80, 1.20),
        max_abs_dif=0.40,
        fallback_to="IRT_2PL_GPCM_L3",
    ),
)

IRT_TIER_BY_CODE: dict[IrtTierCode, IrtTierSpec] = {spec.code: spec for spec in IRT_TIER_SPECS}


def _validate_metrics(metrics: IrtCalibrationMetrics) -> None:
    if metrics.sample_size < 0:
        raise ValueError("sample_size must be non-negative")
    if metrics.learner_count < 0:
        raise ValueError("learner_count must be non-negative")
    for name, val in (
        ("se_b", metrics.se_b),
        ("ability_sd", metrics.ability_sd),
        ("infit_msq", metrics.infit_msq),
        ("max_abs_dif", metrics.max_abs_dif),
    ):
        if not math.isfinite(val) or val < 0:
            raise ValueError(f"{name} must be a finite non-negative float, got {val!r}")


def _failed_criteria(metrics: IrtCalibrationMetrics, spec: IrtTierSpec) -> tuple[CriterionCode, ...]:
    failed: list[CriterionCode] = []
    if not metrics.converged:
        failed.append("NOT_CONVERGED")
    if metrics.sample_size < spec.min_item_responses:
        failed.append("MIN_ITEM_RESPONSES")
    if metrics.learner_count < spec.min_learners:
        failed.append("MIN_LEARNERS")
    if spec.min_ability_sd is not None and metrics.ability_sd < spec.min_ability_sd:
        failed.append("MIN_ABILITY_SD")
    if spec.max_se_b is not None and metrics.se_b >= spec.max_se_b:
        failed.append("MAX_SE_B")
    if spec.infit_msq_range is not None:
        low, high = spec.infit_msq_range
        if not (low <= metrics.infit_msq <= high):
            failed.append("INFIT_MSQ_RANGE")
    if spec.max_abs_dif is not None and metrics.max_abs_dif > spec.max_abs_dif:
        failed.append("MAX_ABS_DIF")
    return tuple(failed)


def select_irt_model_tier(metrics: IrtCalibrationMetrics) -> IrtTierDecision:
    """Select the highest eligible IRT model tier and report blockers for the next tier."""
    _validate_metrics(metrics)

    selected_idx = 0
    for idx in range(1, len(IRT_TIER_SPECS)):
        failed = _failed_criteria(metrics, IRT_TIER_SPECS[idx])
        if failed:
            return IrtTierDecision(
                selected_tier=IRT_TIER_SPECS[selected_idx].code,
                next_tier=IRT_TIER_SPECS[idx].code,
                failed_next_tier_criteria=failed,
            )
        selected_idx = idx

    return IrtTierDecision(
        selected_tier=IRT_TIER_SPECS[selected_idx].code,
        next_tier=None,
        failed_next_tier_criteria=(),
    )
