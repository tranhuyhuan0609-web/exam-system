"""SQLAlchemy ORM models for A05 parser registry & extraction runs (spec chapter 4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
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


class ParserDefinition(Base):
    """Global registry entry describing an available parser strategy & version."""

    __tablename__ = "parser_definitions"

    code: Mapped[str] = mapped_column(String(60), primary_key=True)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    supported_mimes: Mapped[list[str]] = mapped_column(
        ARRAY(String(120)).with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    config_schema_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        server_default=text("'{}'"),
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class ExtractionRun(Base):
    """Versioned extraction execution record tied to an immutable document version."""

    __tablename__ = "extraction_runs"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_extraction_runs_id_tenant_id"),
        ForeignKeyConstraint(
            ["document_version_id", "tenant_id"],
            ["document_versions.id", "document_versions.tenant_id"],
            ondelete="CASCADE",
            name="fk_extraction_runs_doc_version_tenant",
        ),
        ForeignKeyConstraint(
            ["acquisition_job_id", "tenant_id"],
            ["acquisition_jobs.id", "acquisition_jobs.tenant_id"],
            name="fk_extraction_runs_job_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    acquisition_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    profile_code: Mapped[str] = mapped_column(String(60), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(40), nullable=False)
    ocr_engine_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    silver_artifact_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
