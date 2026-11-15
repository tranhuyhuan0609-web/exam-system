"""Public facade for the knowledge_graph module (B05)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from app.modules.knowledge_graph.internal.cycles import (
    PREREQUISITE_CYCLE_ERROR_CODE,
    CycleCheckResult,
    KnowledgeEdgeInput,
    KnowledgeEdgeType,
    find_cycle,
    would_create_cycle,
)

if TYPE_CHECKING:
    from app.modules.knowledge_graph.internal.service import (
        GraphValidationRunView,
        KnowledgeEdgeView,
        KnowledgeGraphNotFoundError,
        KnowledgeGraphValidationError,
        PrerequisiteCycleError,
        PrerequisitePathView,
        create_knowledge_edge,
        get_prerequisite_path,
        list_knowledge_edges,
        validate_knowledge_graph,
    )

_SERVICE_EXPORTS = frozenset(
    {
        "GraphValidationRunView",
        "KnowledgeEdgeView",
        "KnowledgeGraphNotFoundError",
        "KnowledgeGraphValidationError",
        "PrerequisiteCycleError",
        "PrerequisitePathView",
        "create_knowledge_edge",
        "get_prerequisite_path",
        "list_knowledge_edges",
        "validate_knowledge_graph",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily load DB service exports so pure-core callers avoid importing ORM models."""
    if name in _SERVICE_EXPORTS:
        service_mod = importlib.import_module("app.modules.knowledge_graph.internal.service")
        value = getattr(service_mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "PREREQUISITE_CYCLE_ERROR_CODE",
    "CycleCheckResult",
    "GraphValidationRunView",
    "KnowledgeEdgeInput",
    "KnowledgeEdgeType",
    "KnowledgeEdgeView",
    "KnowledgeGraphNotFoundError",
    "KnowledgeGraphValidationError",
    "PrerequisiteCycleError",
    "PrerequisitePathView",
    "create_knowledge_edge",
    "find_cycle",
    "get_prerequisite_path",
    "list_knowledge_edges",
    "validate_knowledge_graph",
    "would_create_cycle",
]
