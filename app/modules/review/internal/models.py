"""SQLAlchemy ORM models for A09 review (spec chapter 4): the review queue, its decisions and source rights."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class SourceRight(Base):
    """Licence evidence for a source: what the owner may do with its material."""

    __tablename__ = "source_rights"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "tenant_id"],
            ["sources.id", "sources.tenant_id"],
            ondelete="CASCADE",
            name="fk_source_rights_source_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    license_type: Mapped[str] = mapped_column(String(60), nullable=False)
    allow_rebranding: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    allow_public_redistribution: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    evidence_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewTask(Base):
    __tablename__ = "review_tasks"
    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('QUESTION_CANDIDATE', 'QUESTION_VERSION', 'EXAM_ARCHIVE', 'CONTENT_VERSION')",
            name="ck_review_tasks_entity_type",
        ),
        CheckConstraint("severity IN ('LOW', 'NORMAL', 'HIGH', 'CRITICAL')", name="ck_review_tasks_severity"),
        CheckConstraint("status IN ('OPEN', 'IN_REVIEW', 'RESOLVED', 'CANCELLED')", name="ck_review_tasks_status"),
        UniqueConstraint("id", "tenant_id", name="uq_review_tasks_id_tenant_id"),
        Index("ix_review_tasks_queue_status", "tenant_id", "queue_name", "status", "severity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    queue_name: Mapped[str] = mapped_column(String(60), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, server_default="NORMAL")
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="OPEN")
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('APPROVE', 'REJECT', 'REQUEST_REPROCESS', 'SPLIT_MERGE')", name="ck_review_decisions_decision"
        ),
        ForeignKeyConstraint(
            ["review_task_id", "tenant_id"],
            ["review_tasks.id", "review_tasks.tenant_id"],
            ondelete="CASCADE",
            name="fk_review_decisions_task_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    review_task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
