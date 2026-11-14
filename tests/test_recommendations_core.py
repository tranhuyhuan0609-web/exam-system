"""Tests for E05 explainable next-step recommendations."""

from __future__ import annotations

import pytest

from app.modules.recommendations.public import (
    LearnerSignalNode,
    RecommendationActionType,
    RecommendationFeedbackKind,
    apply_recommendation_feedback,
    rank_next_step_recommendations,
)


def test_e05_ranks_top_3_explainable_recommendations_with_evidence_and_feedback() -> None:
    nodes = (
        LearnerSignalNode("N_STRONG", "Hàm số cơ bản", mastery_estimate=0.92),
        LearnerSignalNode("N_PREREQ", "Xác suất có điều kiện", mastery_estimate=0.35),
        LearnerSignalNode(
            "N_BAYES",
            "Công thức Bayes",
            mastery_estimate=0.30,
            unresolved_question_ids=("q-10", "q-11"),
            prerequisite_ids=("N_PREREQ",),
        ),
        LearnerSignalNode(
            "N_WAVE",
            "Dao động điều hòa",
            mastery_estimate=0.45,
            unresolved_question_ids=("q-22",),
        ),
    )

    recs = rank_next_step_recommendations(learner_id="stu-1", nodes=nodes, top_k=3)
    assert len(recs) == 3
    assert recs[0].taxonomy_node_id == "N_BAYES"
    assert recs[0].action_type == RecommendationActionType.REVIEW_PREREQUISITE
    assert recs[0].evidence.failed_question_ids == ("q-10", "q-11")
    assert recs[0].evidence.weak_prerequisite_ids == ("N_PREREQ",)
    assert "N_PREREQ" in recs[0].evidence.rationale_text

    hidden = apply_recommendation_feedback(recs[0], feedback=RecommendationFeedbackKind.NOT_RELEVANT)
    assert hidden.hidden is True
    assert hidden.feedback == RecommendationFeedbackKind.NOT_RELEVANT


def _rank(nodes, **kw):
    return rank_next_step_recommendations(learner_id="stu-1", nodes=tuple(nodes), **kw)


def test_strong_nodes_without_errors_are_not_recommended() -> None:
    assert _rank([LearnerSignalNode("N1", "A", mastery_estimate=0.95)]) == ()


def test_empty_nodes_give_empty_result() -> None:
    assert _rank([]) == ()


@pytest.mark.parametrize("mastery", [float("nan"), float("inf"), float("-inf"), None, True, "0.5"])
def test_non_finite_or_non_numeric_mastery_is_rejected(mastery: object) -> None:
    with pytest.raises(ValueError):
        _rank([LearnerSignalNode("N1", "A", mastery_estimate=mastery)])  # type: ignore[arg-type]


def test_duplicate_node_ids_are_rejected() -> None:
    nodes = [LearnerSignalNode("N1", "A", 0.2), LearnerSignalNode("N1", "B", 0.3)]
    with pytest.raises(ValueError, match="duplicate"):
        _rank(nodes)


@pytest.mark.parametrize("top_k", [0, -1, True, 2.5])
def test_top_k_must_be_a_positive_integer(top_k: object) -> None:
    with pytest.raises(ValueError):
        _rank([LearnerSignalNode("N1", "A", 0.2)], top_k=top_k)  # type: ignore[arg-type]


def test_blank_learner_is_rejected() -> None:
    with pytest.raises(ValueError):
        rank_next_step_recommendations(learner_id="  ", nodes=())


def test_dismissed_nodes_are_skipped_and_top_k_truncates_with_stable_tie_break() -> None:
    nodes = [LearnerSignalNode(f"N{i}", f"T{i}", 0.3) for i in (3, 1, 2)]
    recs = _rank(nodes, top_k=2, dismissed_node_ids=frozenset({"N1"}))
    assert [r.taxonomy_node_id for r in recs] == ["N2", "N3"]


def test_out_of_range_mastery_is_clamped_for_scoring() -> None:
    low = _rank([LearnerSignalNode("N1", "A", -5.0)])[0]
    zero = _rank([LearnerSignalNode("N1", "A", 0.0)])[0]
    assert low.expected_gain_score == zero.expected_gain_score == 50.0
    assert _rank([LearnerSignalNode("N1", "A", 7.0)]) == ()


def test_unknown_prerequisite_is_not_treated_as_weak() -> None:
    node = LearnerSignalNode("N1", "A", 0.9, prerequisite_ids=("GHOST",))
    assert _rank([node]) == ()


def test_feedback_kinds_and_unknown_feedback() -> None:
    rec = _rank([LearnerSignalNode("N1", "A", 0.2)])[0]
    assert apply_recommendation_feedback(rec, feedback=RecommendationFeedbackKind.HELPFUL).hidden is False
    assert apply_recommendation_feedback(rec, feedback=RecommendationFeedbackKind.DISMISSED).hidden is True
    assert rec.hidden is False and rec.feedback is None
    with pytest.raises(ValueError):
        apply_recommendation_feedback(rec, feedback="LOVE_IT")  # type: ignore[arg-type]
