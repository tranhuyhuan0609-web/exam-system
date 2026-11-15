"""Database service layer for B05 Knowledge Graph (DAG enforcement & H-01 active-endpoint visibility)."""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import cast

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.knowledge_graph.internal.cycles import (
    PREREQUISITE_CYCLE_ERROR_CODE,
    KnowledgeEdgeInput,
    KnowledgeEdgeType,
    find_cycle,
    would_create_cycle,
)
from app.modules.knowledge_graph.internal.models import (
    KNOWLEDGE_EDGE_TYPES,
    GraphValidationRun,
    KnowledgeEdge,
)
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID

_THREE_DP = Decimal("0.001")


class KnowledgeGraphValidationError(ValueError):
    """Raised when edge payload, confidence, or duplicate constraint fails validation."""


class KnowledgeGraphNotFoundError(LookupError):
    """Raised when a referenced taxonomy node or edge is missing/inactive."""


class PrerequisiteCycleError(ValueError):
    """Raised when adding a `PREREQUISITE` edge would introduce a cycle (`422 PREREQUISITE_CYCLE_DETECTED`)."""

    def __init__(self, cycle_path: tuple[uuid.UUID, ...]) -> None:
        self.code = PREREQUISITE_CYCLE_ERROR_CODE
        self.cycle_path = cycle_path
        chain = " -> ".join(str(node) for node in cycle_path)
        super().__init__(f"PREREQUISITE_CYCLE_DETECTED: cycle {chain}")


@dataclass(frozen=True, slots=True)
class KnowledgeEdgeView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    source_node_id: uuid.UUID
    target_node_id: uuid.UUID
    edge_type: str
    confidence: Decimal
    graph_version: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GraphValidationRunView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    graph_version: int
    is_dag_valid: bool
    cycle_path: tuple[uuid.UUID, ...]
    validated_at: datetime


@dataclass(frozen=True, slots=True)
class PrerequisitePathView:
    target_node_id: uuid.UUID
    graph_version: int
    prerequisite_node_ids: tuple[uuid.UUID, ...]
    edges: tuple[KnowledgeEdgeView, ...]


def _edge_view(edge: KnowledgeEdge) -> KnowledgeEdgeView:
    return KnowledgeEdgeView(
        id=edge.id,
        tenant_id=edge.tenant_id,
        source_node_id=edge.source_node_id,
        target_node_id=edge.target_node_id,
        edge_type=edge.edge_type,
        confidence=Decimal(edge.confidence).quantize(_THREE_DP, rounding=ROUND_HALF_UP),
        graph_version=edge.graph_version,
        created_at=edge.created_at,
    )


def _validation_view(run: GraphValidationRun) -> GraphValidationRunView:
    return GraphValidationRunView(
        id=run.id,
        tenant_id=run.tenant_id,
        graph_version=run.graph_version,
        is_dag_valid=run.is_dag_valid,
        cycle_path=tuple(uuid.UUID(str(item)) for item in (run.cycle_path_json or ())),
        validated_at=run.validated_at,
    )


def _parse_edge_type(raw: str) -> KnowledgeEdgeType:
    cleaned = raw.strip().upper()
    if cleaned not in KNOWLEDGE_EDGE_TYPES:
        raise KnowledgeGraphValidationError(f"unsupported edge_type {raw!r}")
    return cast(KnowledgeEdgeType, cleaned)


def _parse_confidence(value: Decimal | int | str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise KnowledgeGraphValidationError("confidence must be a finite Decimal between 0 and 1")
    try:
        dec = Decimal(value).quantize(_THREE_DP, rounding=ROUND_HALF_UP)
    except Exception as exc:
        raise KnowledgeGraphValidationError("confidence must be a valid decimal") from exc
    if not dec.is_finite() or dec < Decimal("0") or dec > Decimal("1"):
        raise KnowledgeGraphValidationError("confidence must be between 0.000 and 1.000")
    return dec


async def _fetch_active_taxonomy_nodes(
    session: AsyncSession,
    *,
    node_ids: tuple[uuid.UUID, ...],
) -> set[uuid.UUID]:
    """Return subset of `node_ids` that exist and have `is_active IS TRUE` under caller RLS."""
    if not node_ids:
        return set()
    rows = (
        await session.execute(
            text(
                "SELECT id FROM taxonomy_nodes "
                "WHERE id = ANY(:node_ids) AND is_active IS TRUE"
            ),
            {"node_ids": list(node_ids)},
        )
    ).scalars().all()
    return {uuid.UUID(str(r)) for r in rows}


async def _load_active_edges(
    session: AsyncSession,
    *,
    graph_version: int,
    edge_type: str | None = None,
    active_only: bool = True,
) -> list[KnowledgeEdge]:
    """Load visible `knowledge_edges` enforcing active endpoints per H-01."""
    edge_clause = " AND ke.edge_type = :edge_type" if edge_type is not None else ""
    params: dict[str, object] = {"graph_version": graph_version}
    if edge_type is not None:
        params["edge_type"] = edge_type

    if active_only:
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT ke.id
                    FROM knowledge_edges ke
                    JOIN taxonomy_nodes s ON s.id = ke.source_node_id
                    JOIN taxonomy_nodes t ON t.id = ke.target_node_id
                    WHERE ke.graph_version = :graph_version
                      {edge_clause}
                      AND s.is_active IS TRUE
                      AND t.is_active IS TRUE
                    ORDER BY ke.created_at ASC, ke.id ASC
                    """
                ),
                params,
            )
        ).scalars().all()
        visible_ids = [uuid.UUID(str(eid)) for eid in rows]
        if not visible_ids:
            return []
        stmt = (
            select(KnowledgeEdge)
            .where(KnowledgeEdge.id.in_(visible_ids))
            .order_by(KnowledgeEdge.created_at.asc(), KnowledgeEdge.id.asc())
        )
        return list((await session.execute(stmt)).scalars().all())

    stmt = select(KnowledgeEdge).where(KnowledgeEdge.graph_version == graph_version)
    if edge_type is not None:
        stmt = stmt.where(KnowledgeEdge.edge_type == edge_type)
    stmt = stmt.order_by(KnowledgeEdge.created_at.asc(), KnowledgeEdge.id.asc())
    return list((await session.execute(stmt)).scalars().all())


async def create_knowledge_edge(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_node_id: uuid.UUID,
    target_node_id: uuid.UUID,
    edge_type: str = "PREREQUISITE",
    confidence: Decimal | int | str = Decimal("1.000"),
    graph_version: int = 1,
) -> KnowledgeEdgeView:
    """Create a directed knowledge edge after active-node and iterative DAG cycle checks."""
    if graph_version < 1:
        raise KnowledgeGraphValidationError("graph_version must be >= 1")
    parsed_type = _parse_edge_type(edge_type)
    parsed_confidence = _parse_confidence(confidence)
    if source_node_id == target_node_id:
        if parsed_type == "PREREQUISITE":
            raise PrerequisiteCycleError((source_node_id, source_node_id))
        raise KnowledgeGraphValidationError("source_node_id and target_node_id must differ")

    active_nodes = await _fetch_active_taxonomy_nodes(
        session, node_ids=(source_node_id, target_node_id)
    )
    missing = [nid for nid in (source_node_id, target_node_id) if nid not in active_nodes]
    if missing:
        raise KnowledgeGraphNotFoundError(
            f"active taxonomy node(s) not found or inactive: {', '.join(str(x) for x in missing)}"
        )

    if parsed_type == "PREREQUISITE":
        existing_prereqs = await _load_active_edges(
            session,
            graph_version=graph_version,
            edge_type="PREREQUISITE",
            active_only=True,
        )
        existing_inputs = [
            KnowledgeEdgeInput(
                source_node_id=e.source_node_id,
                target_node_id=e.target_node_id,
                edge_type="PREREQUISITE",
            )
            for e in existing_prereqs
        ]
        check = would_create_cycle(
            existing_inputs,
            KnowledgeEdgeInput(
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                edge_type="PREREQUISITE",
            ),
            edge_type="PREREQUISITE",
        )
        if check.creates_cycle:
            raise PrerequisiteCycleError(check.cycle_path)

    edge = KnowledgeEdge(
        tenant_id=tenant_id,
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        edge_type=parsed_type,
        confidence=parsed_confidence,
        graph_version=graph_version,
    )
    session.add(edge)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise KnowledgeGraphValidationError(
            "duplicate or invalid knowledge edge for (tenant_id, source, target, edge_type, graph_version)"
        ) from exc
    return _edge_view(edge)


async def list_knowledge_edges(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    graph_version: int = 1,
    edge_type: str | None = None,
    include_system_public: bool = True,
    active_only: bool = True,
) -> tuple[KnowledgeEdgeView, ...]:
    """List knowledge edges visible to `tenant_id`, hiding edges whose endpoints are inactive."""
    if graph_version < 1:
        raise KnowledgeGraphValidationError("graph_version must be >= 1")
    parsed_type = _parse_edge_type(edge_type) if edge_type is not None else None
    edges = await _load_active_edges(
        session,
        graph_version=graph_version,
        edge_type=parsed_type,
        active_only=active_only,
    )
    if not include_system_public:
        edges = [e for e in edges if e.tenant_id == tenant_id]
    else:
        allowed_tenants = {tenant_id, SYSTEM_PUBLIC_TENANT_ID}
        edges = [e for e in edges if e.tenant_id in allowed_tenants]
    return tuple(_edge_view(e) for e in edges)


async def validate_knowledge_graph(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    graph_version: int = 1,
) -> GraphValidationRunView:
    """Validate the `PREREQUISITE` subgraph is acyclic and record an immutable validation run."""
    if graph_version < 1:
        raise KnowledgeGraphValidationError("graph_version must be >= 1")

    prereq_edges = await _load_active_edges(
        session,
        graph_version=graph_version,
        edge_type="PREREQUISITE",
        active_only=True,
    )
    inputs = [
        KnowledgeEdgeInput(
            source_node_id=e.source_node_id,
            target_node_id=e.target_node_id,
            edge_type="PREREQUISITE",
        )
        for e in prereq_edges
    ]
    cycle = find_cycle(inputs, edge_type="PREREQUISITE")
    run = GraphValidationRun(
        tenant_id=tenant_id,
        graph_version=graph_version,
        is_dag_valid=cycle is None,
        cycle_path_json=[str(node_id) for node_id in cycle] if cycle is not None else [],
    )
    session.add(run)
    await session.flush()
    return _validation_view(run)


async def get_prerequisite_path(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    target_node_id: uuid.UUID,
    graph_version: int = 1,
) -> PrerequisitePathView:
    """Resolve active prerequisites leading into `target_node_id` in prerequisite-first order."""
    if graph_version < 1:
        raise KnowledgeGraphValidationError("graph_version must be >= 1")

    active_targets = await _fetch_active_taxonomy_nodes(session, node_ids=(target_node_id,))
    if target_node_id not in active_targets:
        return PrerequisitePathView(
            target_node_id=target_node_id,
            graph_version=graph_version,
            prerequisite_node_ids=(),
            edges=(),
        )

    all_prereqs = await list_knowledge_edges(
        session,
        tenant_id=tenant_id,
        graph_version=graph_version,
        edge_type="PREREQUISITE",
        include_system_public=True,
        active_only=True,
    )
    incoming_by_target: dict[uuid.UUID, list[KnowledgeEdgeView]] = {}
    for edge in all_prereqs:
        incoming_by_target.setdefault(edge.target_node_id, []).append(edge)

    # Reverse BFS to collect all reachable ancestor prerequisites of target_node_id.
    visited_nodes: set[uuid.UUID] = {target_node_id}
    collected_edges: list[KnowledgeEdgeView] = []
    queue: deque[uuid.UUID] = deque([target_node_id])
    while queue:
        cur = queue.popleft()
        for edge in incoming_by_target.get(cur, ()):
            collected_edges.append(edge)
            if edge.source_node_id not in visited_nodes:
                visited_nodes.add(edge.source_node_id)
                queue.append(edge.source_node_id)

    # Iterative Kahn topological ordering of ancestors so foundational prerequisites appear first.
    ancestor_nodes = sorted(visited_nodes - {target_node_id})
    in_degree: dict[uuid.UUID, int] = {nid: 0 for nid in ancestor_nodes}
    adj: dict[uuid.UUID, list[uuid.UUID]] = {nid: [] for nid in ancestor_nodes}
    for edge in collected_edges:
        if edge.source_node_id in in_degree and edge.target_node_id in in_degree:
            adj[edge.source_node_id].append(edge.target_node_id)
            in_degree[edge.target_node_id] += 1

    ready: list[uuid.UUID] = sorted(nid for nid, deg in in_degree.items() if deg == 0)
    ordered: list[uuid.UUID] = []
    while ready:
        cur = ready.pop(0)
        ordered.append(cur)
        for nxt in adj[cur]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                ready.append(nxt)
                ready.sort()

    for remaining in ancestor_nodes:
        if remaining not in ordered:
            ordered.append(remaining)

    return PrerequisitePathView(
        target_node_id=target_node_id,
        graph_version=graph_version,
        prerequisite_node_ids=tuple(ordered),
        edges=tuple(collected_edges),
    )
