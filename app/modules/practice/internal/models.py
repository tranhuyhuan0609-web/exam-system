"""SQLAlchemy ORM models for D03 Practice Engine (spec chapter 4)."""

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
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class PracticeSession(Base):
    """Interactive practice session completely isolated from formal exam attempts."""

    __tablename__ = "practice_sessions"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('LESSON_EMBED', 'TOPIC_DRILL', 'REMEDIATION_DRILL')",
            name="ck_practice_sessions_mode",
        ),
        CheckConstraint(
            "status IN ('IN_PROGRESS', 'COMPLETED')",
            name="ck_practice_sessions_status",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_practice_sessions_id_tenant"),
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_practice_sessions_membership_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    mode: Mapped[str] = mapped_column(String(40), nullable=False, server_default=text("'TOPIC_DRILL'"))
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        server_default=text("'IN_PROGRESS'"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PracticeResponse(Base):
    """Submitted learner answer within a practice session."""

    __tablename__ = "practice_responses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["practice_session_id", "tenant_id"],
            ["practice_sessions.id", "practice_sessions.tenant_id"],
            ondelete="CASCADE",
            name="fk_practice_responses_session_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    practice_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("question_versions.id"),
        nullable=False,
    )
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    hints_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
