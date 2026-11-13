"""Unit tests for B05 iterative knowledge graph cycle detection (P11)."""

from __future__ import annotations

import time
from uuid import UUID

from app.modules.knowledge_graph.public import (
    PREREQUISITE_CYCLE_ERROR_CODE,
    KnowledgeEdgeInput,
    find_cycle,
    would_create_cycle,
)


def _u(n: int) -> UUID:
    return UUID(int=n)


def test_find_cycle_detects_dag_self_loop_and_multi_node_cycle():
    a, b, c, d = _u(1), _u(2), _u(3), _u(4)
    dag = [
        KnowledgeEdgeInput(a, b),
        KnowledgeEdgeInput(b, c),
        KnowledgeEdgeInput(a, d),
    ]
    assert find_cycle(dag) is None

    self_loop = [KnowledgeEdgeInput(a, a)]
    assert find_cycle(self_loop) == (a, a)

    cyclic = dag + [KnowledgeEdgeInput(c, a)]
    cycle = find_cycle(cyclic)
    assert cycle is not None
    assert cycle[0] == cycle[-1]
    assert set(cycle[:-1]) == {a, b, c}


def test_would_create_cycle_returns_cycle_path_and_422_error_code():
    a, b, c = _u(10), _u(20), _u(30)
    edges = [
        KnowledgeEdgeInput(a, b),
        KnowledgeEdgeInput(b, c),
    ]

    self_res = would_create_cycle(edges, KnowledgeEdgeInput(a, a))
    assert self_res.creates_cycle is True
    assert self_res.cycle_path == (a, a)
    assert self_res.error_code == PREREQUISITE_CYCLE_ERROR_CODE

    closing_res = would_create_cycle(edges, KnowledgeEdgeInput(c, a))
    assert closing_res.creates_cycle is True
    assert closing_res.cycle_path == (c, a, b, c)
    assert closing_res.error_code == PREREQUISITE_CYCLE_ERROR_CODE

    safe_res = would_create_cycle(edges, KnowledgeEdgeInput(a, c))
    assert safe_res.creates_cycle is False
    assert safe_res.cycle_path == ()
    assert safe_res.error_code is None

    # Non-PREREQUISITE edge types are allowed to form symmetric links.
    related_res = would_create_cycle(
        edges,
        KnowledgeEdgeInput(c, a, edge_type="RELATED_TO"),
    )
    assert related_res.creates_cycle is False


def test_handles_50000_edges_under_one_second_without_recursion():
    edge_count = 50_000
    nodes = [_u(i + 1) for i in range(edge_count + 1)]
    edges = [KnowledgeEdgeInput(nodes[i], nodes[i + 1]) for i in range(edge_count)]

    t0 = time.perf_counter()
    assert find_cycle(edges) is None
    cycle_check = would_create_cycle(edges, KnowledgeEdgeInput(nodes[-1], nodes[0]))
    elapsed = time.perf_counter() - t0

    assert cycle_check.creates_cycle is True
    assert cycle_check.cycle_path[0] == nodes[-1]
    assert cycle_check.cycle_path[-1] == nodes[-1]
    assert len(cycle_check.cycle_path) == edge_count + 2
    assert elapsed < 1.0, f"50k-edge cycle detection took {elapsed:.3f}s"
