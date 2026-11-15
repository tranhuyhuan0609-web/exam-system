"""SQLAlchemy ORM models for A03 Drive Sync (`source_connections`, `source_sync_cursors`, `file_pairing_candidates`)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class SourceConnection(Base):
    """OAuth/Service-Account connection binding a tenant's external source to Drive sync."""

    __tablename__ = "source_connections"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CONNECTED', 'REAUTH_REQUIRED', 'REVOKED')",
            name="ck_source_connections_status",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_source_connections_id_tenant"),
        ForeignKeyConstraint(
            ["source_id", "tenant_id"],
            ["sources.id", "sources.tenant_id"],
            ondelete="CASCADE",
            name="fk_source_connections_source_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, server_default=text("'GOOGLE_DRIVE'"))
    secret_vault_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    granted_scopes: Mapped[list[str]] = mapped_column(
        ARRAY(String(120)),
        nullable=False,
        server_default=text("ARRAY['https://www.googleapis.com/auth/drive.file']::varchar(120)[]"),
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'CONNECTED'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class SourceSyncCursor(Base):
    """Incremental delta sync cursor per Drive connection."""

    __tablename__ = "source_sync_cursors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_connection_id", "tenant_id"],
            ["source_connections.id", "source_connections.tenant_id"],
            ondelete="CASCADE",
            name="fk_source_sync_cursors_connection_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_connection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    cursor_token: Mapped[str] = mapped_column(String(512), nullable=False)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class FilePairingCandidate(Base):
    """4-signal automated exam + solution pairing candidate awaiting or holding confirmation."""

    __tablename__ = "file_pairing_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PROPOSED', 'CONFIRMED', 'REJECTED')",
            name="ck_file_pairing_candidates_status",
        ),
        ForeignKeyConstraint(
            ["exam_doc_id", "tenant_id"],
            ["source_documents.id", "source_documents.tenant_id"],
            ondelete="CASCADE",
            name="fk_file_pairing_candidates_exam_doc_tenant",
        ),
        ForeignKeyConstraint(
            ["solution_doc_id", "tenant_id"],
            ["source_documents.id", "source_documents.tenant_id"],
            ondelete="CASCADE",
            name="fk_file_pairing_candidates_solution_doc_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    exam_doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    solution_doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    similarity_score: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    signals_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'PROPOSED'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
