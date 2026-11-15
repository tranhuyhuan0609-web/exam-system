"""SQLAlchemy ORM models for C07 grading (spec chapter 4): rubrics, revisioned grades, per-question details, appeals."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7

GRADE_REASONS = ("INITIAL", "APPEAL_REGRADE", "ERRATA_REGRADE", "MANUAL_OVERRIDE")
APPEAL_STATUSES = ("OPEN", "APPROVED_REGRADED", "REJECTED")


class RubricVersion(Base):
    __tablename__ = "rubric_versions"
    __table_args__ = (UniqueConstraint("id", "tenant_id", name="uq_rubric_versions_id_tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    criteria_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class Grade(Base):
    """One grading of an attempt; a regrade adds `revision_no + 1` and retires the current one."""

    __tablename__ = "grades"
    __table_args__ = (
        CheckConstraint(
            "grade_reason IN ('INITIAL', 'APPEAL_REGRADE', 'ERRATA_REGRADE', 'MANUAL_OVERRIDE')",
            name="ck_grades_grade_reason",
        ),
        UniqueConstraint("attempt_id", "revision_no", name="uq_grades_attempt_id_revision_no"),
        UniqueConstraint("id", "tenant_id", name="uq_grades_id_tenant_id"),
        ForeignKeyConstraint(
            ["attempt_id", "tenant_id"],
            ["attempts.id", "attempts.tenant_id"],
            ondelete="RESTRICT",
            name="fk_grades_attempt_tenant",
        ),
        Index("uq_grades_current_per_attempt", "attempt_id", unique=True, postgresql_where=text("is_current = TRUE")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    grade_reason: Mapped[str] = mapped_column(String(40), nullable=False, server_default="INITIAL")
    scoring_scheme_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scoring_scheme_versions.id"), nullable=False
    )
    total_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    section_breakdown_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    item_details_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    graded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    graded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GradingDetail(Base):
    __tablename__ = "grading_details"
    __table_args__ = (
        ForeignKeyConstraint(
            ["grade_id", "tenant_id"],
            ["grades.id", "grades.tenant_id"],
            ondelete="CASCADE",
            name="fk_grading_details_grade_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    grade_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("question_versions.id"), nullable=False
    )
    earned_points: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    max_points: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sub_item_breakdown_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))


class Appeal(Base):
    __tablename__ = "appeals"
    __table_args__ = (
        CheckConstraint("status IN ('OPEN', 'APPROVED_REGRADED', 'REJECTED')", name="ck_appeals_status"),
        ForeignKeyConstraint(
            ["grade_id", "tenant_id"],
            ["grades.id", "grades.tenant_id"],
            ondelete="RESTRICT",
            name="fk_appeals_grade_tenant",
        ),
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="RESTRICT",
            name="fk_appeals_membership_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    grade_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    question_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("question_versions.id"), nullable=True
    )
    reason_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="OPEN")
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
