"""SQLAlchemy ORM models for A04 provenance records & exam import sessions (spec chapter 4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7

IMPORT_SESSION_STATUSES: tuple[str, ...] = ("PENDING_REVIEW", "APPROVED", "REJECTED")


class ProvenanceRecord(Base):
    """Exact page, bounding box, label, and raw snippet provenance for an extracted question/content item."""

    __tablename__ = "provenance_records"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_provenance_records_id_tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    extraction_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    section_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    original_question_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    raw_snippet_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class ExamImportSession(Base):
    """Whole-exam reconstruction & approval session for A04 Complete Exam Import."""

    __tablename__ = "exam_import_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING_REVIEW', 'APPROVED', 'REJECTED')",
            name="ck_exam_import_sessions_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    extraction_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    proposed_structure_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'PENDING_REVIEW'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
