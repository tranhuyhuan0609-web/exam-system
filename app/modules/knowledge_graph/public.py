"""Public facade for the knowledge_graph module (B05)."""

from app.modules.knowledge_graph.internal.cycles import (
    PREREQUISITE_CYCLE_ERROR_CODE,
    CycleCheckResult,
    KnowledgeEdgeInput,
    KnowledgeEdgeType,
    find_cycle,
    would_create_cycle,
)

__all__ = [
    "PREREQUISITE_CYCLE_ERROR_CODE",
    "CycleCheckResult",
    "KnowledgeEdgeInput",
    "KnowledgeEdgeType",
    "find_cycle",
    "would_create_cycle",
]
