"""Tests for E06 Teacher AI Copilot."""

from __future__ import annotations

import pytest

from app.modules.teacher_copilot.public import (
    CrossClassScopeError,
    DraftAssignmentStatus,
    StudentMistakeObservation,
    confirm_remedial_draft,
    generate_class_copilot_summary,
)


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
