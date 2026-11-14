"""SQLAlchemy ORM models for A07 extraction (spec chapter 4): content and question candidates.

`provenance_id` and `duplicate_cluster_id` are plain UUID columns until A04/A08 create their parent tables
(TASK-18 adds the foreign keys); the page/bbox anchors of a candidate live in its JSON payload meanwhile.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7

RIGHTS = "('PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')"
ACQUISITION = "('INGESTED', 'EXTRACTED', 'NORMALIZED', 'FAILED')"


class ContentCandidate(Base):
    """Theory, method and formula blocks split out of a study guide, waiting for review."""

    __tablename__ = "content_candidates"
    __table_args__ = (
        CheckConstraint(f"acquisition_status IN {ACQUISITION}", name="ck_content_candidates_acquisition_status"),
        CheckConstraint(
            "verification_status IN ('UNCHECKED', 'AUTO_VERIFIED', 'VERIFIED', 'REJECTED')",
            name="ck_content_candidates_verification_status",
        ),
        CheckConstraint(f"rights_status IN {RIGHTS}", name="ck_content_candidates_rights_status"),
        UniqueConstraint("id", "tenant_id", name="uq_content_candidates_id_tenant_id"),
        ForeignKeyConstraint(
            ["extraction_run_id", "tenant_id"],
            ["extraction_runs.id", "extraction_runs.tenant_id"],
            ondelete="CASCADE",
            name="fk_content_candidates_run_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    extraction_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provenance_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    blocks_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'"))
    acquisition_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="EXTRACTED")
    verification_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="UNCHECKED")
    rights_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="PENDING_REVIEW")
    promoted_content_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class QuestionCandidate(Base):
    """One question split out of a document, a crawl or an AI generation, before it becomes a question version."""

    __tablename__ = "question_candidates"
    __table_args__ = (
        CheckConstraint(
            "acquisition_method IN ('DOCUMENT_IMPORT', 'WEB_CRAWL', 'DRIVE_SYNC', 'AI_GENERATED', 'MANUAL_AUTHOR')",
            name="ck_question_candidates_acquisition_method",
        ),
        CheckConstraint(
            "item_type IN ('SINGLE_CHOICE', 'MULTIPLE_SELECT', 'CLUSTER_TRUE_FALSE', 'SHORT_NUMERIC', 'ESSAY', "
            "'GROUP_STEM')",
            name="ck_question_candidates_item_type",
        ),
        CheckConstraint(f"acquisition_status IN {ACQUISITION}", name="ck_question_candidates_acquisition_status"),
        CheckConstraint(
            "verification_status IN ('UNCHECKED', 'AUTO_VERIFIED', 'VERIFIED', 'DISPUTED', 'REJECTED')",
            name="ck_question_candidates_verification_status",
        ),
        CheckConstraint(f"rights_status IN {RIGHTS}", name="ck_question_candidates_rights_status"),
        CheckConstraint(
            "candidate_status IN ('NEEDS_REVIEW', 'APPROVED', 'REJECTED')",
            name="ck_question_candidates_candidate_status",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_question_candidates_id_tenant_id"),
        ForeignKeyConstraint(
            ["extraction_run_id", "tenant_id"],
            ["extraction_runs.id", "extraction_runs.tenant_id"],
            ondelete="CASCADE",
            name="fk_question_candidates_run_tenant",
        ),
        Index("ix_question_candidates_content_hash", "tenant_id", "content_hash"),
        Index("ix_question_candidates_struct_hash", "tenant_id", "structural_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    extraction_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    provenance_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    parent_candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    acquisition_method: Mapped[str] = mapped_column(String(40), nullable=False)
    item_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    structural_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    embedding_vec: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
    duplicate_cluster_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    extraction_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    acquisition_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="EXTRACTED")
    verification_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="UNCHECKED")
    rights_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="PENDING_REVIEW")
    candidate_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="NEEDS_REVIEW")
    promoted_question_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
