"""Iterative DAG cycle detection for knowledge_edges (module B05)."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

KnowledgeEdgeType = Literal[
    "PREREQUISITE",
    "RELATED_TO",
    "EXTENDS",
    "APPLIED_IN",
    "COMMON_MISCONCEPTION",
]

PREREQUISITE_CYCLE_ERROR_CODE = "PREREQUISITE_CYCLE_DETECTED"

_UNVISITED = 0
_VISITING = 1
_DONE = 2


@dataclass(frozen=True, slots=True)
class KnowledgeEdgeInput:
    """Directed relationship edge between two taxonomy nodes."""

    source_node_id: UUID
    target_node_id: UUID
    edge_type: KnowledgeEdgeType = "PREREQUISITE"


@dataclass(frozen=True, slots=True)
class CycleCheckResult:
    """Result of checking whether inserting a new edge introduces a cycle."""

    creates_cycle: bool
    cycle_path: tuple[UUID, ...] = ()
    error_code: str | None = None


def _build_adjacency(
    edges: Sequence[KnowledgeEdgeInput],
    *,
    edge_type: KnowledgeEdgeType | None = "PREREQUISITE",
) -> tuple[dict[UUID, list[UUID]], list[UUID]]:
    adjacency: dict[UUID, list[UUID]] = {}
    nodes: set[UUID] = set()
    for edge in edges:
        if edge_type is not None and edge.edge_type != edge_type:
            continue
        nodes.add(edge.source_node_id)
        nodes.add(edge.target_node_id)
        adjacency.setdefault(edge.source_node_id, []).append(edge.target_node_id)
    return adjacency, sorted(nodes)


def find_cycle(
    edges: Sequence[KnowledgeEdgeInput],
    *,
    edge_type: KnowledgeEdgeType | None = "PREREQUISITE",
) -> tuple[UUID, ...] | None:
    """Return a closed cycle path `(u, ..., u)` if `edges` contains a cycle, else None.

    Uses iterative 3-color DFS so graphs with 50k+ edges execute cleanly without recursion limits.
    """
    for edge in edges:
        if edge_type is not None and edge.edge_type != edge_type:
            continue
        if edge.source_node_id == edge.target_node_id:
            return (edge.source_node_id, edge.source_node_id)

    adjacency, ordered_nodes = _build_adjacency(edges, edge_type=edge_type)
    state: dict[UUID, int] = {}
    parent: dict[UUID, UUID] = {}

    for start in ordered_nodes:
        if state.get(start, _UNVISITED) != _UNVISITED:
            continue

        # Stack stores (node_id, next_neighbor_index)
        stack: list[tuple[UUID, int]] = [(start, 0)]
        state[start] = _VISITING

        while stack:
            node, idx = stack[-1]
            neighbors = adjacency.get(node, ())
            if idx < len(neighbors):
                stack[-1] = (node, idx + 1)
                nxt = neighbors[idx]
                nxt_state = state.get(nxt, _UNVISITED)
                if nxt_state == _VISITING:
                    # Walk back from `node` to `nxt` to reconstruct the closed cycle.
                    cycle_rev: list[UUID] = [nxt, node]
                    cur = node
                    while cur != nxt:
                        cur = parent[cur]
                        cycle_rev.append(cur)
                    cycle_rev.reverse()
                    return tuple(cycle_rev)
                if nxt_state == _UNVISITED:
                    parent[nxt] = node
                    state[nxt] = _VISITING
                    stack.append((nxt, 0))
            else:
                state[node] = _DONE
                stack.pop()

    return None


def would_create_cycle(
    edges: Sequence[KnowledgeEdgeInput],
    new_edge: KnowledgeEdgeInput,
    *,
    edge_type: KnowledgeEdgeType | None = "PREREQUISITE",
) -> CycleCheckResult:
    """Check whether adding `new_edge` creates a cycle, returning the full cycle path."""
    if edge_type is not None and new_edge.edge_type != edge_type:
        return CycleCheckResult(creates_cycle=False)

    src = new_edge.source_node_id
    dst = new_edge.target_node_id

    if src == dst:
        return CycleCheckResult(
            creates_cycle=True,
            cycle_path=(src, src),
            error_code=PREREQUISITE_CYCLE_ERROR_CODE,
        )

    adjacency: dict[UUID, list[UUID]] = {}
    for edge in edges:
        if edge_type is not None and edge.edge_type != edge_type:
            continue
        adjacency.setdefault(edge.source_node_id, []).append(edge.target_node_id)

    # Shortest path dst -> ... -> src via iterative BFS means src -> dst -> ... -> src is a cycle.
    visited: set[UUID] = {dst}
    prev: dict[UUID, UUID] = {}
    queue: deque[UUID] = deque([dst])

    while queue:
        cur = queue.popleft()
        if cur == src:
            rev_path: list[UUID] = [src]
            walk = src
            while walk != dst:
                walk = prev[walk]
                rev_path.append(walk)
            rev_path.append(src)
            rev_path.reverse()
            return CycleCheckResult(
                creates_cycle=True,
                cycle_path=tuple(rev_path),
                error_code=PREREQUISITE_CYCLE_ERROR_CODE,
            )
        for nxt in adjacency.get(cur, ()):
            if nxt not in visited:
                visited.add(nxt)
                prev[nxt] = cur
                queue.append(nxt)

    existing_cycle = find_cycle(edges, edge_type=edge_type)
    if existing_cycle is not None:
        return CycleCheckResult(
            creates_cycle=True,
            cycle_path=existing_cycle,
            error_code=PREREQUISITE_CYCLE_ERROR_CODE,
        )

    return CycleCheckResult(creates_cycle=False)
