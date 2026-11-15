"""SQLAlchemy ORM models for B05 Knowledge Graph (spec chapter 4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7

SYSTEM_PUBLIC_TENANT_ID_SQL = text("'00000000-0000-0000-0000-000000000000'")
KNOWLEDGE_EDGE_TYPES = (
    "PREREQUISITE",
    "RELATED_TO",
    "EXTENDS",
    "APPLIED_IN",
    "COMMON_MISCONCEPTION",
)


class KnowledgeEdge(Base):
    """Directed relationship edge between two taxonomy nodes (hybrid multi-tenant + SYSTEM_PUBLIC)."""

    __tablename__ = "knowledge_edges"
    __table_args__ = (
        CheckConstraint(
            "edge_type IN ('PREREQUISITE', 'RELATED_TO', 'EXTENDS', 'APPLIED_IN', 'COMMON_MISCONCEPTION')",
            name="ck_knowledge_edges_edge_type",
        ),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_knowledge_edges_confidence"),
        CheckConstraint("source_node_id <> target_node_id", name="ck_knowledge_edges_no_self_loop"),
        UniqueConstraint(
            "tenant_id",
            "source_node_id",
            "target_node_id",
            "edge_type",
            "graph_version",
            name="uq_knowledge_edges_tenant_src_dst_type_ver",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        server_default=SYSTEM_PUBLIC_TENANT_ID_SQL,
    )
    source_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("taxonomy_nodes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("taxonomy_nodes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    edge_type: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(4, 3),
        nullable=False,
        server_default=text("1.000"),
    )
    graph_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class GraphValidationRun(Base):
    """Recorded DAG validation result for a tenant's prerequisite graph snapshot."""

    __tablename__ = "graph_validation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    graph_version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_dag_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cycle_path_json: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'"),
    )
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
