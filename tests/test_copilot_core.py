"""Tests for E05 explainable recommendations and E06 Teacher AI Copilot."""

from __future__ import annotations

import pytest

from app.modules.copilot.public import (
    CrossClassScopeError,
    DraftAssignmentStatus,
    LearnerSignalNode,
    RecommendationActionType,
    RecommendationFeedbackKind,
    StudentMistakeObservation,
    apply_recommendation_feedback,
    confirm_remedial_draft,
    generate_class_copilot_summary,
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


def test_e06_clusters_class_mistakes_requires_confirmation_and_enforces_class_rls() -> None:
    obs = (
        StudentMistakeObservation("t1", "cls-12A", "s1", "MATH.INT", "q1", "B", "A"),
        StudentMistakeObservation("t1", "cls-12A", "s2", "MATH.INT", "q1", "B", "A"),
        StudentMistakeObservation("t1", "cls-12A", "s3", "MATH.INT", "q2", "C", "D"),
        StudentMistakeObservation("t1", "cls-12A", "s4", "MATH.LOG", "q9", "A", "A"),  # correct ignored
    )
    summary = generate_class_copilot_summary(tenant_id="t1", class_id="cls-12A", observations=obs)
    assert len(summary.clusters) == 1
    assert summary.clusters[0].taxonomy_node_id == "MATH.INT"
    assert summary.clusters[0].top_distractor == "B"
    assert summary.clusters[0].affected_student_ids == ("s1", "s2", "s3")

    draft = summary.draft_assignments[0]
    assert draft.status == DraftAssignmentStatus.AWAITING_TEACHER_CONFIRMATION
    confirmed = confirm_remedial_draft(draft, teacher_id="teacher-1", actor_tenant_id="t1")
    assert confirmed.status == DraftAssignmentStatus.CONFIRMED

    foreign_obs = obs + (
        StudentMistakeObservation("t1", "cls-OTHER", "s9", "MATH.INT", "q1", "C", "A"),
    )
    with pytest.raises(CrossClassScopeError):
        generate_class_copilot_summary(tenant_id="t1", class_id="cls-12A", observations=foreign_obs)
