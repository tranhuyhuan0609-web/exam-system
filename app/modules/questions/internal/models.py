"""SQLAlchemy ORM models for C01 questions (spec chapter 4, question bank group).

A question carries the four status axes (acquisition, verification, rights, publication; fail-closed defaults)
and points at its current version. A version is frozen (`is_immutable`) by the database once its question is
PUBLISHED with it as current version; a change is then `version_no + 1`. Answers live in their own table,
which the exam-room, ingestion, scheduler and analytics roles cannot read at all.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CHAR, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7

VISIBILITY_SCOPES = ("SYSTEM_PUBLIC", "TENANT_PRIVATE", "SHARED_PARTNER")
ITEM_TYPES = ("SINGLE_CHOICE", "MULTIPLE_SELECT", "CLUSTER_TRUE_FALSE", "SHORT_NUMERIC", "ESSAY", "GROUP_STEM")
DIFFICULTY_LEVELS = ("NB", "TH", "VD", "VDC")
ACQUISITION_STATUSES = ("INGESTED", "EXTRACTED", "NORMALIZED", "FAILED")
VERIFICATION_STATUSES = ("UNCHECKED", "AUTO_VERIFIED", "VERIFIED", "DISPUTED", "REJECTED")
RIGHTS_STATUSES = ("PENDING_REVIEW", "OWNED", "LICENSED", "ALLOWED_DERIVATIVE", "RESTRICTED", "TAKEDOWN")
CLEARED_RIGHTS = ("OWNED", "LICENSED", "ALLOWED_DERIVATIVE")
PUBLICATION_STATUSES = ("DRAFT", "READY", "PUBLISHED", "ARCHIVED", "TAKEN_DOWN")
ACCESS_TIERS = ("FREE", "STUDENT_PRO", "TEACHER_PRO")
STIMULUS_TYPES = ("READING_PASSAGE", "EXPERIMENT_CONTEXT", "DATA_TABLE", "SHARED_FIGURE")
DEFAULT_NUMERIC_PARSE_POLICY = (
    '{"decimal_sep": "COMMA_OR_DOT", "allow_thousand_sep": false, "max_chars": 4, "tolerance": 0.01}'
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Question(Base):
    """A bank question: identity (`stable_code`), classification, status axes and its current version."""

    __tablename__ = "questions"
    __table_args__ = (
        CheckConstraint(_in("visibility_scope", VISIBILITY_SCOPES), name="ck_questions_visibility_scope"),
        CheckConstraint(_in("item_type", ITEM_TYPES), name="ck_questions_item_type"),
        CheckConstraint(_in("difficulty_level", DIFFICULTY_LEVELS), name="ck_questions_difficulty_level"),
        CheckConstraint(_in("acquisition_status", ACQUISITION_STATUSES), name="ck_questions_acquisition_status"),
        CheckConstraint(_in("verification_status", VERIFICATION_STATUSES), name="ck_questions_verification_status"),
        CheckConstraint(_in("rights_status", RIGHTS_STATUSES), name="ck_questions_rights_status"),
        CheckConstraint(_in("publication_status", PUBLICATION_STATUSES), name="ck_questions_publication_status"),
        CheckConstraint(_in("access_tier", ACCESS_TIERS), name="ck_questions_access_tier"),
        # Hard gate: nothing is PUBLISHED unverified, without cleared rights or without a current version.
        CheckConstraint(
            "publication_status <> 'PUBLISHED' OR (verification_status = 'VERIFIED' "
            "AND rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE') AND current_version_id IS NOT NULL)",
            name="chk_question_publish_gate",
        ),
        UniqueConstraint("tenant_id", "stable_code", name="uq_questions_tenant_id_stable_code"),
        UniqueConstraint("id", "tenant_id", name="uq_questions_id_tenant_id"),
        ForeignKeyConstraint(
            ["current_version_id", "tenant_id"],
            ["question_versions.id", "question_versions.tenant_id"],
            name="fk_questions_current_version",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("ix_questions_exam_tags_gin", "exam_framework_tags", postgresql_using="gin"),
        Index(
            "ix_questions_pool_lookup",
            "tenant_id",
            "publication_status",
            "verification_status",
            "primary_taxonomy_node_id",
            "difficulty_level",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    visibility_scope: Mapped[str] = mapped_column(String(20), nullable=False, server_default="TENANT_PRIVATE")
    stable_code: Mapped[str] = mapped_column(String(100), nullable=False)
    item_type: Mapped[str] = mapped_column(String(40), nullable=False)
    primary_taxonomy_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("taxonomy_nodes.id"), nullable=True
    )
    curriculum_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("curriculum_nodes.id"), nullable=True
    )
    exam_framework_tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), nullable=False, server_default=text("'{SGK_KNTT}'")
    )
    difficulty_level: Mapped[str] = mapped_column(String(20), nullable=False, server_default="TH")
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    acquisition_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="NORMALIZED")
    verification_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="UNCHECKED")
    rights_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="PENDING_REVIEW")
    publication_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="DRAFT")
    access_tier: Mapped[str] = mapped_column(String(20), nullable=False, server_default="FREE")
    duplicate_cluster_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class QuestionStimulus(Base):
    """Shared context (passage, experiment, data table, figure) of a GROUP_STEM cluster."""

    __tablename__ = "question_stimuli"
    __table_args__ = (
        CheckConstraint(_in("stimulus_type", STIMULUS_TYPES), name="ck_question_stimuli_stimulus_type"),
        UniqueConstraint("id", "tenant_id", name="uq_question_stimuli_id_tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    stimulus_type: Mapped[str] = mapped_column(String(40), nullable=False)
    body_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class QuestionVersion(Base):
    """One version of a question, without its answer. Frozen by the database once published."""

    __tablename__ = "question_versions"
    __table_args__ = (
        CheckConstraint(_in("visibility_scope", VISIBILITY_SCOPES), name="ck_question_versions_visibility_scope"),
        UniqueConstraint("question_id", "version_no", name="uq_question_versions_question_id_version_no"),
        UniqueConstraint("id", "tenant_id", name="uq_question_versions_id_tenant_id"),
        ForeignKeyConstraint(
            ["question_id", "tenant_id"],
            ["questions.id", "questions.tenant_id"],
            ondelete="RESTRICT",
            name="fk_question_versions_question_tenant",
        ),
        ForeignKeyConstraint(
            ["stimulus_id", "tenant_id"],
            ["question_stimuli.id", "question_stimuli.tenant_id"],
            name="fk_question_versions_stimulus_tenant",
        ),
        Index("ix_question_versions_hashes", "tenant_id", "content_hash", "structural_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    visibility_scope: Mapped[str] = mapped_column(String(20), nullable=False, server_default="TENANT_PRIVATE")
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    stimulus_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    stem_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    options_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'"))
    numeric_parse_policy_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text(f"'{DEFAULT_NUMERIC_PARSE_POLICY}'")
    )
    content_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    structural_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    is_immutable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    change_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class QuestionVersionAnswer(Base):
    """The answer key and rationale of a version (strict tenant RLS, hidden from the exam-room role)."""

    __tablename__ = "question_version_answers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["question_version_id", "tenant_id"],
            ["question_versions.id", "question_versions.tenant_id"],
            ondelete="CASCADE",
            name="fk_question_version_answers_version_tenant",
        ),
    )

    question_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    answer_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    rationale_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    is_immutable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class QuestionOption(Base):
    """One option (A..D) or statement (a..d) of a version; never says which one is correct."""

    __tablename__ = "question_options"
    __table_args__ = (
        UniqueConstraint(
            "question_version_id", "option_key", name="uq_question_options_question_version_id_option_key"
        ),
        ForeignKeyConstraint(
            ["question_version_id", "tenant_id"],
            ["question_versions.id", "question_versions.tenant_id"],
            ondelete="CASCADE",
            name="fk_question_options_version_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    question_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    option_key: Mapped[str] = mapped_column(String(10), nullable=False)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False)


class QuestionAsset(Base):
    """An asset of the tenant used by a version (same-tenant composite keys)."""

    __tablename__ = "question_assets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["question_version_id", "tenant_id"],
            ["question_versions.id", "question_versions.tenant_id"],
            ondelete="CASCADE",
            name="fk_question_assets_version_tenant",
        ),
        ForeignKeyConstraint(
            ["asset_id", "tenant_id"],
            ["assets.id", "assets.tenant_id"],
            ondelete="RESTRICT",
            name="fk_question_assets_asset_tenant",
        ),
    )

    question_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
