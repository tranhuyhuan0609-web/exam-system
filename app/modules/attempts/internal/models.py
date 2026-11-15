"""SQLAlchemy ORM models for C06 attempts (spec chapter 4): responses, the idempotency registry, response revisions.

`response_revisions` is partitioned by month on `recorded_at` (the partitions are created by the migration and by
`ensure_monthly_partitions`, never by the ORM) and append-only (database trigger).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class Response(Base):
    """The current answer of one question of one attempt."""

    __tablename__ = "responses"
    __table_args__ = (
        UniqueConstraint("attempt_id", "question_version_id", name="uq_responses_attempt_id_question_version_id"),
        UniqueConstraint("id", "tenant_id", name="uq_responses_id_tenant_id"),
        ForeignKeyConstraint(
            ["attempt_id", "tenant_id"],
            ["attempts.id", "attempts.tenant_id"],
            ondelete="CASCADE",
            name="fk_responses_attempt_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("question_versions.id", ondelete="RESTRICT"), nullable=False
    )
    current_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    current_revision_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class AutosaveIdempotencyRegistry(Base):
    """One Idempotency-Key of an attempt and what it committed: checked before anything else."""

    __tablename__ = "autosave_idempotency_registry"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "attempt_id", "idempotency_key"),
        ForeignKeyConstraint(
            ["attempt_id", "tenant_id"],
            ["attempts.id", "attempts.tenant_id"],
            ondelete="CASCADE",
            name="fk_autosave_idempotency_registry_attempt_tenant",
        ),
        Index("ix_autosave_idem_registry_recorded_at", "recorded_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    response_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    client_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class ResponseRevision(Base):
    """One committed save, kept forever (append-only, partitioned by month)."""

    __tablename__ = "response_revisions"
    __table_args__ = (
        PrimaryKeyConstraint("id", "recorded_at"),
        ForeignKeyConstraint(
            ["response_id", "tenant_id"],
            ["responses.id", "responses.tenant_id"],
            ondelete="CASCADE",
            name="fk_response_revisions_response_tenant",
        ),
        Index("ix_response_revisions_lookup", "tenant_id", "response_id", "revision_no"),
        {"postgresql_partition_by": "RANGE (recorded_at)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    response_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    client_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
