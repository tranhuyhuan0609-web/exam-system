"""SQLAlchemy ORM models for C04 assembly (spec chapter 4, assessment group).

A paper (`assessments`) carries the four status axes (fail-closed defaults) and points at its current version. A
version is DRAFT while `is_immutable` is false (assembled, still replaceable) and frozen by publishing; its
`structure_snapshot_json` never holds an answer, which lives in `assessment_answer_keys` (revisioned, strict RLS).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.dialects.postgresql import CHAR, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7

VISIBILITY_SCOPES = ("SYSTEM_PUBLIC", "TENANT_PRIVATE")
ACCESS_TIERS = ("FREE", "STUDENT_PRO", "TEACHER_PRO")
VERIFICATION_STATUSES = ("UNCHECKED", "AUTO_VERIFIED", "VERIFIED", "DISPUTED", "REJECTED")
RIGHTS_STATUSES = ("PENDING_REVIEW", "OWNED", "LICENSED", "ALLOWED_DERIVATIVE", "RESTRICTED", "TAKEDOWN")
CLEARED_RIGHTS = ("OWNED", "LICENSED", "ALLOWED_DERIVATIVE")
PUBLICATION_STATUSES = ("DRAFT", "READY", "PUBLISHED", "ARCHIVED", "TAKEN_DOWN")
DEFAULT_BASE_SEED = 20261111


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Assessment(Base):
    """A paper: title, subject, status axes and its current (published) version."""

    __tablename__ = "assessments"
    __table_args__ = (
        CheckConstraint(_in("visibility_scope", VISIBILITY_SCOPES), name="ck_assessments_visibility_scope"),
        CheckConstraint(_in("access_tier", ACCESS_TIERS), name="ck_assessments_access_tier"),
        CheckConstraint(_in("verification_status", VERIFICATION_STATUSES), name="ck_assessments_verification_status"),
        CheckConstraint(_in("rights_status", RIGHTS_STATUSES), name="ck_assessments_rights_status"),
        CheckConstraint(_in("publication_status", PUBLICATION_STATUSES), name="ck_assessments_publication_status"),
        # Hard gate: nothing is PUBLISHED unverified, without cleared rights or without a current version.
        CheckConstraint(
            "publication_status <> 'PUBLISHED' OR (verification_status = 'VERIFIED' "
            "AND rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE') AND current_version_id IS NOT NULL)",
            name="chk_assessment_publish_gate",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_assessments_id_tenant_id"),
        ForeignKeyConstraint(
            ["current_version_id", "tenant_id"],
            ["assessment_versions.id", "assessment_versions.tenant_id"],
            name="fk_assessments_current_version",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    visibility_scope: Mapped[str] = mapped_column(String(20), nullable=False, server_default="TENANT_PRIVATE")
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    subject_code: Mapped[str] = mapped_column(String(50), nullable=False)
    grade_band: Mapped[str] = mapped_column(String(30), nullable=False)
    exam_type_code: Mapped[str] = mapped_column(String(50), nullable=False)
    source_archive_doc_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    branded_pdf_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    preview_pdf_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    access_tier: Mapped[str] = mapped_column(String(20), nullable=False, server_default="FREE")
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    verification_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="UNCHECKED")
    rights_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="PENDING_REVIEW")
    publication_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class AssessmentVersion(Base):
    """One version of a paper: scoring scheme, duration, seed and the answer-free structure snapshot."""

    __tablename__ = "assessment_versions"
    __table_args__ = (
        UniqueConstraint("assessment_id", "version_no", name="uq_assessment_versions_assessment_id_version_no"),
        UniqueConstraint("id", "tenant_id", name="uq_assessment_versions_id_tenant_id"),
        ForeignKeyConstraint(
            ["assessment_id", "tenant_id"],
            ["assessments.id", "assessments.tenant_id"],
            ondelete="RESTRICT",
            name="fk_assessment_versions_assessment_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assessment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    scoring_scheme_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scoring_scheme_versions.id"), nullable=False
    )
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    total_points: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, server_default=text("10.00"))
    base_seed: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text(str(DEFAULT_BASE_SEED)))
    structure_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_immutable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, server_default=NOW)


class AssessmentItem(Base):
    """One question of a version, with its section, position and points."""

    __tablename__ = "assessment_items"
    __table_args__ = (
        UniqueConstraint(
            "assessment_version_id", "sequence_order", name="uq_assessment_items_version_id_sequence_order"
        ),
        ForeignKeyConstraint(
            ["assessment_version_id", "tenant_id"],
            ["assessment_versions.id", "assessment_versions.tenant_id"],
            ondelete="CASCADE",
            name="fk_assessment_items_version_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assessment_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("question_versions.id", ondelete="RESTRICT"), nullable=False
    )
    section_code: Mapped[str] = mapped_column(String(30), nullable=False)
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False)
    points: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)


class AssessmentAnswerKey(Base):
    """The grading key of a version, kept apart from the structure and revisioned for regrade and errata."""

    __tablename__ = "assessment_answer_keys"
    __table_args__ = (
        ForeignKeyConstraint(
            ["assessment_version_id", "tenant_id"],
            ["assessment_versions.id", "assessment_versions.tenant_id"],
            ondelete="RESTRICT",
            name="fk_assessment_answer_keys_version_tenant",
        ),
        Index(
            "uq_assessment_answer_keys_current",
            "assessment_version_id",
            unique=True,
            postgresql_where=text("is_current = TRUE"),
        ),
    )

    assessment_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    key_revision_no: Mapped[int] = mapped_column(Integer, primary_key=True, server_default=text("1"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    answer_key_payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    key_checksum_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    change_reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="INITIAL_PUBLISH")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
