"""SQLAlchemy ORM models for A08 deduplication clusters & members (spec chapter 4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7

CLUSTER_TYPES: tuple[str, ...] = ("EXACT", "STRUCTURAL_VARIANT", "SEMANTIC_NEAR")
CLUSTER_STATUSES: tuple[str, ...] = ("PENDING_REVIEW", "RESOLVED_MERGED", "RESOLVED_KEEP_VARIANTS")


class DuplicateCluster(Base):
    """Candidate cluster grouping exact, structural-variant, or semantic-near questions."""

    __tablename__ = "duplicate_clusters"
    __table_args__ = (
        CheckConstraint(
            "cluster_type IN ('EXACT', 'STRUCTURAL_VARIANT', 'SEMANTIC_NEAR')",
            name="ck_duplicate_clusters_type",
        ),
        CheckConstraint(
            "status IN ('PENDING_REVIEW', 'RESOLVED_MERGED', 'RESOLVED_KEEP_VARIANTS')",
            name="ck_duplicate_clusters_status",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_duplicate_clusters_id_tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    cluster_type: Mapped[str] = mapped_column(String(30), nullable=False)
    canonical_question_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'PENDING_REVIEW'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class DuplicateMember(Base):
    """Member association linking question_candidates to a duplicate_cluster with similarity score."""

    __tablename__ = "duplicate_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["cluster_id", "tenant_id"],
            ["duplicate_clusters.id", "duplicate_clusters.tenant_id"],
            ondelete="CASCADE",
            name="fk_duplicate_members_cluster_tenant",
        ),
    )

    cluster_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    similarity_score: Mapped[Decimal] = mapped_column(
        Numeric(4, 3),
        nullable=False,
        server_default=text("1.000"),
    )
