"""Unit tests for D02 iterative prerequisite remediation ordering (P10)."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.modules.learning_path.public import (
    PrerequisiteEdge,
    RemediationStep,
    resolve_prerequisite_remediation_order,
)


def _u(n: int) -> UUID:
    return UUID(int=n)


def test_prerequisite_precedes_dependent_with_reason_codes():
    a, b, target, mastered = _u(1), _u(2), _u(3), _u(4)
    edges = [
        PrerequisiteEdge(a, b),
        PrerequisiteEdge(b, target),
        PrerequisiteEdge(mastered, target),
    ]
    mastery = {a: 0.25, b: 0.40, target: 0.30, mastered: 0.85}

    plan = resolve_prerequisite_remediation_order([target], edges, mastery)
    assert plan == (
        RemediationStep(node_id=a, reason_code="PREREQUISITE_OF", dependent_node_id=b),
        RemediationStep(node_id=b, reason_code="PREREQUISITE_OF", dependent_node_id=target),
        RemediationStep(node_id=target, reason_code="WEAK_TARGET", dependent_node_id=None),
    )


def test_handles_cycles_self_loops_and_duplicate_edges_deterministically():
    n1, n2, n3 = _u(10), _u(20), _u(30)
    edges = [
        PrerequisiteEdge(n1, n2),
        PrerequisiteEdge(n2, n3),
        PrerequisiteEdge(n3, n1),  # cycle
        PrerequisiteEdge(n2, n2),  # self-loop
        PrerequisiteEdge(n1, n2),  # duplicate
    ]
    mastery = {n1: 0.10, n2: 0.20, n3: 0.30}

    p1 = resolve_prerequisite_remediation_order([n3, n1], edges, mastery)
    p2 = resolve_prerequisite_remediation_order([n3, n1], list(reversed(edges)), mastery)

    assert p1 == p2
    assert len(p1) == 3
    assert {step.node_id for step in p1} == {n1, n2, n3}
    assert all(
        step.reason_code == "WEAK_TARGET" and step.dependent_node_id is None
        for step in p1
        if step.node_id in {n1, n3}
    )


def test_deep_graph_exceeding_recursion_limit_runs_iteratively():
    chain_len = 2500
    nodes = [_u(i + 1) for i in range(chain_len)]
    edges = [PrerequisiteEdge(nodes[i], nodes[i + 1]) for i in range(chain_len - 1)]
    mastery = dict.fromkeys(nodes, 0.20)

    plan = resolve_prerequisite_remediation_order([nodes[-1]], edges, mastery)
    assert len(plan) == chain_len
    assert [step.node_id for step in plan] == nodes


def test_configurable_prerequisite_mastery_threshold():
    prereq, target = _u(100), _u(200)
    edges = [PrerequisiteEdge(prereq, target)]
    mastery = {prereq: 0.55, target: 0.30}

    # Default 0.60 includes prereq (< 0.60); threshold 0.50 omits prereq (>= 0.50).
    plan_default = resolve_prerequisite_remediation_order([target], edges, mastery)
    plan_50 = resolve_prerequisite_remediation_order(
        [target], edges, mastery, prerequisite_mastery_threshold=0.50
    )

    assert [s.node_id for s in plan_default] == [prereq, target]
    assert [s.node_id for s in plan_50] == [target]

    with pytest.raises(ValueError):
        resolve_prerequisite_remediation_order(
            [target], edges, mastery, prerequisite_mastery_threshold=1.5
        )


def test_preserves_caller_weak_priority_order_and_marks_dual_role_as_weak_target():
    low_id, high_id = _u(10), _u(999)
    mastery = {low_id: 0.20, high_id: 0.25}

    # Caller puts higher UUID first -> must NOT be re-sorted by UUID.
    plan = resolve_prerequisite_remediation_order([high_id, low_id], [], mastery)
    assert [s.node_id for s in plan] == [high_id, low_id]

    # When prereq is BOTH a prerequisite of target AND explicitly in weak_node_ids,
    # it must still carry WEAK_TARGET (even when target comes first in weak_node_ids).
    prereq, target = _u(500), _u(1)
    dual_plan = resolve_prerequisite_remediation_order(
        [target, prereq],
        [PrerequisiteEdge(prereq, target)],
        {prereq: 0.15, target: 0.20},
    )
    assert dual_plan == (
        RemediationStep(node_id=prereq, reason_code="WEAK_TARGET", dependent_node_id=None),
        RemediationStep(node_id=target, reason_code="WEAK_TARGET", dependent_node_id=None),
    )
