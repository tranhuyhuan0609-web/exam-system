"""SQLAlchemy ORM models for E02 AI Tutor & Grounded Q&A (spec chapter 4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class TutorSession(Base):
    """A learner's grounded AI tutor conversation session with strict mode & tenant isolation."""

    __tablename__ = "tutor_sessions"
    __table_args__ = (
        CheckConstraint(
            "tutor_mode IN ('HINT_MODE', 'EXPLAIN_MODE', 'CHALLENGE_MODE')",
            name="ck_tutor_sessions_tutor_mode",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_tutor_sessions_id_tenant_id"),
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_tutor_sessions_membership_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # FK to question_versions(id) is enforced in PostgreSQL DDL / migrations once questions ORM lands.
    context_question_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    context_content_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_versions.id"),
        nullable=True,
    )
    tutor_mode: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        server_default=text("'HINT_MODE'"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class TutorMessage(Base):
    """Turn-by-turn messages inside a tutor session (including SYSTEM_GUARD interventions)."""

    __tablename__ = "tutor_messages"
    __table_args__ = (
        CheckConstraint(
            "sender_role IN ('USER', 'ASSISTANT', 'SYSTEM_GUARD')",
            name="ck_tutor_messages_sender_role",
        ),
        ForeignKeyConstraint(
            ["tutor_session_id", "tenant_id"],
            ["tutor_sessions.id", "tutor_sessions.tenant_id"],
            ondelete="CASCADE",
            name="fk_tutor_messages_session_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tutor_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    sender_role: Mapped[str] = mapped_column(String(20), nullable=False)
    message_md: Mapped[str] = mapped_column(Text, nullable=False)
    citations_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class RetrievalTrace(Base):
    """Audit log of grounded RAG document IDs and similarity scores for each tutor turn."""

    __tablename__ = "retrieval_traces"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tutor_session_id", "tenant_id"],
            ["tutor_sessions.id", "tutor_sessions.tenant_id"],
            ondelete="CASCADE",
            name="fk_retrieval_traces_session_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tutor_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    retrieved_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False)
    scores: Mapped[list[Decimal]] = mapped_column(ARRAY(Numeric(5, 4)), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
