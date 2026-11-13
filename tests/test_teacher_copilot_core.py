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
    summary = generate_class_copilot_summary(tenant_id="t1", class_id="cls-12A", run_id="r1", observations=obs)
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
        generate_class_copilot_summary(tenant_id="t1", class_id="cls-12A", run_id="r1", observations=foreign_obs)


def _obs(student="s1", node="MATH.INT", q="q1", chosen="B", correct="A", tenant="t1", cls="cls-12A"):
    return StudentMistakeObservation(tenant, cls, student, node, q, chosen, correct)


def _summary(observations, run_id="run-1", **kw):
    return generate_class_copilot_summary(
        tenant_id="t1", class_id="cls-12A", run_id=run_id, observations=tuple(observations), **kw
    )


def test_draft_ids_do_not_collide_across_runs() -> None:
    # Regression: draft_id was remedial-{class}-{idx}, identical for every run of the same class.
    obs = [_obs(), _obs(student="s2", node="MATH.LOG", q="q2")]
    first = _summary(obs, run_id="2026-11-13T10")
    second = _summary(obs, run_id="2026-11-14T10")
    ids1 = {d.draft_id for d in first.draft_assignments}
    ids2 = {d.draft_id for d in second.draft_assignments}
    assert len(ids1) == 2 and len(ids2) == 2
    assert ids1.isdisjoint(ids2)


def test_same_run_is_deterministic() -> None:
    obs = [_obs(), _obs(student="s2", node="MATH.LOG", q="q2")]
    assert _summary(obs) == _summary(obs)


@pytest.mark.parametrize("run_id", ["", "  ", "a/b", "../x", "a" * 65, "x\n", None])
def test_run_id_must_be_a_safe_identifier(run_id: object) -> None:
    with pytest.raises(ValueError):
        _summary([_obs()], run_id=run_id)


def test_empty_observations_give_empty_summary() -> None:
    summary = _summary([])
    assert summary.clusters == () and summary.draft_assignments == ()


def test_all_correct_answers_produce_no_cluster() -> None:
    assert _summary([_obs(chosen="A", correct="A")]).clusters == ()


def test_foreign_tenant_observation_is_rejected() -> None:
    with pytest.raises(CrossClassScopeError):
        _summary([_obs(), _obs(tenant="t2")])


@pytest.mark.parametrize("target", [0, -1, True])
def test_question_target_must_be_positive_integer(target: object) -> None:
    with pytest.raises(ValueError):
        _summary([_obs()], max_question_target=target)


def test_top_distractor_ties_break_alphabetically_and_clusters_sort_by_volume() -> None:
    obs = [_obs(student="s1", chosen="C"), _obs(student="s2", chosen="B"),
           _obs(student="s3", node="MATH.LOG", q="q5", chosen="D"),
           _obs(student="s4", node="MATH.LOG", q="q5", chosen="D"),
           _obs(student="s5", node="MATH.LOG", q="q5", chosen="D")]
    summary = _summary(obs)
    assert [c.taxonomy_node_id for c in summary.clusters] == ["MATH.LOG", "MATH.INT"]
    assert summary.clusters[1].top_distractor == "B"


@pytest.mark.parametrize("actor", ["", "  ", "t2", None])
def test_confirm_requires_matching_non_empty_tenant(actor: object) -> None:
    draft = _summary([_obs()]).draft_assignments[0]
    with pytest.raises(CrossClassScopeError):
        confirm_remedial_draft(draft, teacher_id="teacher-1", actor_tenant_id=actor)  # type: ignore[arg-type]


def test_confirm_requires_teacher_and_cannot_repeat() -> None:
    draft = _summary([_obs()]).draft_assignments[0]
    with pytest.raises(ValueError):
        confirm_remedial_draft(draft, teacher_id="  ", actor_tenant_id="t1")
    confirmed = confirm_remedial_draft(draft, teacher_id="teacher-1", actor_tenant_id="t1")
    assert confirmed.confirmed_by_teacher_id == "teacher-1"
    assert draft.status == DraftAssignmentStatus.AWAITING_TEACHER_CONFIRMATION  # original untouched
    with pytest.raises(ValueError):
        confirm_remedial_draft(confirmed, teacher_id="teacher-2", actor_tenant_id="t1")
