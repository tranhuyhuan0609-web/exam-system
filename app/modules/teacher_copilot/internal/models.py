"""SQLAlchemy ORM model for E06 Teacher AI Copilot Jobs (spec chapter 4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKeyConstraint, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class TeacherAssistJob(Base):
    """Class-scoped misconception clustering & confirmation-gated remedial draft job."""

    __tablename__ = "teacher_assist_jobs"

    __table_args__ = (
        ForeignKeyConstraint(
            ["class_id", "tenant_id"],
            ["classes.id", "classes.tenant_id"],
            ondelete="CASCADE",
            name="fk_teacher_assist_jobs_class_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    class_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assignment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    summary_report_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'READY_FOR_TEACHER'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
