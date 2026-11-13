"""Unit tests for D06 IRT model tier selection gate (P10)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.modules.mastery.public import (
    IRT_TIER_BY_CODE,
    IRT_TIER_SPECS,
    IrtCalibrationMetrics,
    select_irt_model_tier,
)

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "docs" / "architecture" / "platform_manifest.yaml"


def test_thresholds_and_fallback_chain_match_platform_manifest():
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    registry = manifest["irt_tier_registry"]

    assert [entry["code"] for entry in registry] == [spec.code for spec in IRT_TIER_SPECS]

    for entry in registry:
        spec = IRT_TIER_BY_CODE[entry["code"]]
        assert spec.min_item_responses == entry["min_item_responses"]
        assert spec.min_learners == entry["min_learners"]
        assert spec.min_ability_sd == entry.get("min_ability_sd")
        assert spec.max_se_b == entry.get("max_se_b")
        expected_infit = tuple(entry["infit_msq_range"]) if "infit_msq_range" in entry else None
        assert spec.infit_msq_range == expected_infit
        assert spec.max_abs_dif == entry.get("max_abs_dif")
        assert spec.fallback_to == entry.get("fallback_to")


def test_boundary_values_for_each_tier_and_next_tier_failures():
    # Exact L4 boundaries -> selects L4 cleanly.
    l4_ok = select_irt_model_tier(
        IrtCalibrationMetrics(
            sample_size=1000,
            learner_count=800,
            ability_sd=0.70,
            se_b=0.199,
            converged=True,
            infit_msq=0.80,
            max_abs_dif=0.40,
        )
    )
    assert l4_ok.selected_tier == "IRT_3PL_GPCM_L4"
    assert l4_ok.next_tier is None
    assert l4_ok.failed_next_tier_criteria == ()

    # Just outside L4 (se_b == 0.20, sample_size == 999) -> lands on L3 with explicit L4 blockers.
    l3_fallback = select_irt_model_tier(
        IrtCalibrationMetrics(
            sample_size=999,
            learner_count=800,
            ability_sd=0.70,
            se_b=0.20,
            converged=True,
            infit_msq=1.20,
            max_abs_dif=0.40,
        )
    )
    assert l3_fallback.selected_tier == "IRT_2PL_GPCM_L3"
    assert l3_fallback.next_tier == "IRT_3PL_GPCM_L4"
    assert l3_fallback.failed_next_tier_criteria == ("MIN_ITEM_RESPONSES", "MAX_SE_B")

    # Exact L3 lower boundaries -> selects L3.
    l3_exact = select_irt_model_tier(
        IrtCalibrationMetrics(
            sample_size=300,
            learner_count=250,
            ability_sd=0.65,
            se_b=0.249,
            converged=True,
            infit_msq=1.25,
            max_abs_dif=0.45,
        )
    )
    assert l3_exact.selected_tier == "IRT_2PL_GPCM_L3"
    assert l3_exact.next_tier == "IRT_3PL_GPCM_L4"

    # Just below L3 (learner_count=249, infit_msq=1.26) -> selects L2 with L3 blockers.
    l2_fallback = select_irt_model_tier(
        IrtCalibrationMetrics(
            sample_size=300,
            learner_count=249,
            ability_sd=0.65,
            se_b=0.249,
            converged=True,
            infit_msq=1.26,
            max_abs_dif=0.45,
        )
    )
    assert l2_fallback.selected_tier == "RASCH_1PL_L2"
    assert l2_fallback.next_tier == "IRT_2PL_GPCM_L3"
    assert l2_fallback.failed_next_tier_criteria == ("MIN_LEARNERS", "INFIT_MSQ_RANGE")

    # Exact L2 boundaries -> selects L2.
    l2_exact = select_irt_model_tier(
        IrtCalibrationMetrics(
            sample_size=150,
            learner_count=100,
            ability_sd=0.60,
            se_b=0.299,
            converged=True,
            infit_msq=0.70,
            max_abs_dif=0.50,
        )
    )
    assert l2_exact.selected_tier == "RASCH_1PL_L2"
    assert l2_exact.next_tier == "IRT_2PL_GPCM_L3"

    # Just below L2 or unconverged -> falls back to L1 with all failing L2 criteria reported.
    l1_unconverged = select_irt_model_tier(
        IrtCalibrationMetrics(
            sample_size=149,
            learner_count=99,
            ability_sd=0.59,
            se_b=0.30,
            converged=False,
            infit_msq=1.31,
            max_abs_dif=0.51,
        )
    )
    assert l1_unconverged.selected_tier == "RULE_WEIGHTED_L1"
    assert l1_unconverged.next_tier == "RASCH_1PL_L2"
    assert l1_unconverged.failed_next_tier_criteria == (
        "NOT_CONVERGED",
        "MIN_ITEM_RESPONSES",
        "MIN_LEARNERS",
        "MIN_ABILITY_SD",
        "MAX_SE_B",
        "INFIT_MSQ_RANGE",
        "MAX_ABS_DIF",
    )


@pytest.mark.parametrize(
    "bad_metrics",
    [
        IrtCalibrationMetrics(sample_size=-1, se_b=0.1),
        IrtCalibrationMetrics(sample_size=200, learner_count=-5, se_b=0.1),
        IrtCalibrationMetrics(sample_size=200, se_b=-0.01),
        IrtCalibrationMetrics(sample_size=200, se_b=float("nan")),
        IrtCalibrationMetrics(sample_size=200, se_b=0.1, ability_sd=float("inf")),
        IrtCalibrationMetrics(sample_size=200, se_b=0.1, infit_msq=float("-inf")),
        IrtCalibrationMetrics(sample_size=200, se_b=0.1, max_abs_dif=-0.1),
        IrtCalibrationMetrics(sample_size=200, se_b=0.1, max_abs_dif=float("nan")),
    ],
)
def test_rejects_nan_infinity_and_negative_inputs(bad_metrics):
    with pytest.raises(ValueError):
        select_irt_model_tier(bad_metrics)
