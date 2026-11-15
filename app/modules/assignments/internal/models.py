"""SQLAlchemy ORM models for F02 Assessment Assignments & Schedules (spec chapter 4)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, text
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
        CheckConstraint("max_attempts >= 1", name="ck_assessment_assignments_max_attempts"),
        UniqueConstraint("id", "tenant_id", name="uq_assessment_assignments_id_tenant"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    # FK to assessment_versions(id, tenant_id) enforced in PostgreSQL DDL/Alembic.
    assessment_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # FKs to classes(id, tenant_id) and groups(id, tenant_id) enforced in PostgreSQL DDL/Alembic.
    class_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    assigned_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    available_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    reveal_policy: Mapped[str] = mapped_column(String(40), nullable=False, server_default=text("'AFTER_DUE'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
