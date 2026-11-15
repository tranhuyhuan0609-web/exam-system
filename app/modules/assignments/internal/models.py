"""SQLAlchemy ORM models for F02 Assessment Assignments & Schedules (spec chapter 4)."""

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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class AssessmentAssignment(Base):
    """Scheduled assessment assignment targeting a class or group."""

    __tablename__ = "assessment_assignments"
    __table_args__ = (
        CheckConstraint(
            "reveal_policy IN ('IMMEDIATE', 'AFTER_DUE', 'MANUAL_RELEASE', 'NEVER')",
            name="ck_assessment_assignments_reveal_policy",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_assessment_assignments_id_tenant"),
        ForeignKeyConstraint(
            ["assessment_version_id", "tenant_id"],
            ["assessment_versions.id", "assessment_versions.tenant_id"],
            ondelete="RESTRICT",
            name="fk_assessment_assignments_version_tenant",
        ),
        ForeignKeyConstraint(
            ["class_id", "tenant_id"],
            ["classes.id", "classes.tenant_id"],
            ondelete="CASCADE",
            name="fk_assessment_assignments_class_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assessment_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # No foreign key on group_id, as in chapter 4.
    class_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    assigned_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    available_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    reveal_policy: Mapped[str] = mapped_column(String(40), nullable=False, server_default=text("'AFTER_DUE'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
