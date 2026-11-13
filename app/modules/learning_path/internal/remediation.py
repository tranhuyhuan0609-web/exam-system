"""Iterative prerequisite remediation planner for weak mastery targets (D02)."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

ReasonCode = Literal["WEAK_TARGET", "PREREQUISITE_OF"]

DEFAULT_PREREQUISITE_MASTERY_THRESHOLD = 0.60

_UNVISITED = 0
_VISITING = 1
_DONE = 2


@dataclass(frozen=True, slots=True)
class PrerequisiteEdge:
    """Directed edge: `prerequisite_id` must be mastered before `dependent_id`."""

    prerequisite_id: UUID
    dependent_id: UUID


@dataclass(frozen=True, slots=True)
class RemediationStep:
    """Ordered remediation step emitted with a structured reason_code."""

    node_id: UUID
    reason_code: ReasonCode
    dependent_node_id: UUID | None = None


def resolve_prerequisite_remediation_order(
    weak_node_ids: Sequence[UUID],
    prereq_edges: Sequence[PrerequisiteEdge],
    mastery_by_node: Mapping[UUID, float],
    *,
    prerequisite_mastery_threshold: float = DEFAULT_PREREQUISITE_MASTERY_THRESHOLD,
) -> tuple[RemediationStep, ...]:
    """Compute a deterministic remediation sequence placing weak prerequisites first.

    Uses an explicit stack (no recursion) so cyclic or deeply nested prerequisite graphs
    terminate cleanly without recursion limits.
    """
    if not math.isfinite(prerequisite_mastery_threshold) or not (0.0 <= prerequisite_mastery_threshold <= 1.0):
        raise ValueError("prerequisite_mastery_threshold must be finite and within [0.0, 1.0]")

    parents_by_node: dict[UUID, set[UUID]] = {}
    for edge in prereq_edges:
        if edge.prerequisite_id != edge.dependent_id:
            parents_by_node.setdefault(edge.dependent_id, set()).add(edge.prerequisite_id)

    sorted_parents: dict[UUID, tuple[UUID, ...]] = {
        node_id: tuple(sorted(parents))
        for node_id, parents in parents_by_node.items()
    }

    ordered_weak_ids = tuple(dict.fromkeys(weak_node_ids))
    weak_set = set(ordered_weak_ids)

    state: dict[UUID, int] = {}
    plan: list[RemediationStep] = []

    for weak_id in ordered_weak_ids:
        if state.get(weak_id, _UNVISITED) != _UNVISITED:
            continue

        # Stack entries: (node_id, reason_code, dependent_node_id, entering)
        stack: list[tuple[UUID, ReasonCode, UUID | None, bool]] = [
            (weak_id, "WEAK_TARGET", None, True)
        ]

        while stack:
            node_id, reason_code, dependent_id, entering = stack.pop()
            current_state = state.get(node_id, _UNVISITED)

            if entering:
                if current_state != _UNVISITED:
                    continue
                state[node_id] = _VISITING
                step_reason: ReasonCode = "WEAK_TARGET" if node_id in weak_set else reason_code
                step_dependent: UUID | None = None if node_id in weak_set else dependent_id
                stack.append((node_id, step_reason, step_dependent, False))

                parents = sorted_parents.get(node_id, ())
                for parent_id in reversed(parents):
                    parent_mastery = mastery_by_node.get(parent_id, 0.0)
                    if (
                        state.get(parent_id, _UNVISITED) == _UNVISITED
                        and parent_mastery < prerequisite_mastery_threshold
                    ):
                        if parent_id in weak_set:
                            stack.append((parent_id, "WEAK_TARGET", None, True))
                        else:
                            stack.append((parent_id, "PREREQUISITE_OF", node_id, True))
            else:
                if current_state == _VISITING:
                    state[node_id] = _DONE
                    plan.append(
                        RemediationStep(
                            node_id=node_id,
                            reason_code=reason_code,
                            dependent_node_id=dependent_id,
                        )
                    )

    return tuple(plan)
