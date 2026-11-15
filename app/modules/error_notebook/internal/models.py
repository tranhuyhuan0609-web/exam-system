"""SQLAlchemy ORM models for D05 Error Notebook & Remediation (spec chapter 4)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class ErrorNote(Base):
    """Learner error-notebook record requiring two consecutive correct attempts to resolve."""

    __tablename__ = "error_notes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN', 'IN_REMEDIATION', 'RESOLVED')",
            name="ck_error_notes_status",
        ),
        UniqueConstraint(
            "membership_id",
            "question_version_id",
            name="uq_error_notes_membership_qv",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_error_notes_id_tenant"),
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_error_notes_membership_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("question_versions.id"),
        nullable=False,
    )
    # Nullable as in chapter 4; the service always resolves a topic when it captures a note.
    primary_taxonomy_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("taxonomy_nodes.id"),
        nullable=True,
    )
    wrong_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    consecutive_correct_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        server_default=text("'OPEN'"),
    )
    last_wrong_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RemediationTask(Base):
    """Sibling variant drills assigned to remediate an error note."""

    __tablename__ = "remediation_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ASSIGNED', 'COMPLETED')",
            name="ck_remediation_tasks_status",
        ),
        ForeignKeyConstraint(
            ["error_note_id", "tenant_id"],
            ["error_notes.id", "error_notes.tenant_id"],
            ondelete="CASCADE",
            name="fk_remediation_tasks_error_note_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    error_note_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    recommended_question_version_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
    )
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        server_default=text("'ASSIGNED'"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
