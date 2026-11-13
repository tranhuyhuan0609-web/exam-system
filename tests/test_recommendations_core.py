"""Tests for E05 explainable next-step recommendations."""

from __future__ import annotations

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
