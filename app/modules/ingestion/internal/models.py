"""SQLAlchemy ORM models for A01 Unified Ingestion Backbone (spec chapter 4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class Source(Base):
    """Registered content ingestion source (manual upload, Drive, web crawler, partner API)."""

    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('MANUAL_UPLOAD', 'GOOGLE_DRIVE', 'WEB_CRAWLER', 'PARTNER_API')",
            name="ck_sources_source_type",
        ),
        CheckConstraint(
            "rights_status IN ('PENDING_REVIEW', 'OWNED', 'LICENSED', "
            "'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')",
            name="ck_sources_rights_status",
        ),
        CheckConstraint(
            "policy_status IN ('ACTIVE', 'PAUSED_DRIFT', 'PAUSED_MANUAL', 'REVOKED')",
            name="ck_sources_policy_status",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_sources_id_tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    adapter_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        server_default=text("'{}'"),
    )
    rights_status: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default="PENDING_REVIEW"
    )
    attribution_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    policy_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class SourceDocument(Base):
    """Logical source document discovered or uploaded within a tenant source."""

    __tablename__ = "source_documents"
    __table_args__ = (
        CheckConstraint(
            "acquisition_status IN ('INGESTED', 'EXTRACTED', 'NORMALIZED', 'FAILED')",
            name="ck_source_documents_acquisition_status",
        ),
        CheckConstraint(
            "rights_status IN ('PENDING_REVIEW', 'OWNED', 'LICENSED', "
            "'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')",
            name="ck_source_documents_rights_status",
        ),
        UniqueConstraint("source_id", "external_id", name="uq_source_documents_source_external"),
        UniqueConstraint("id", "tenant_id", name="uq_source_documents_id_tenant_id"),
        ForeignKeyConstraint(
            ["source_id", "tenant_id"],
            ["sources.id", "sources.tenant_id"],
            ondelete="RESTRICT",
            name="fk_source_documents_source_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    acquisition_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="INGESTED")
    rights_status: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default="PENDING_REVIEW"
    )
    inferred_metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        server_default=text("'{}'"),
    )
    paired_solution_doc_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class DocumentVersion(Base):
    """Immutable SHA-256 content version of a source document linked to a bronze asset."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("source_document_id", "version_no", name="uq_document_versions_doc_version"),
        UniqueConstraint("id", "tenant_id", name="uq_document_versions_id_tenant_id"),
        ForeignKeyConstraint(
            ["source_document_id", "tenant_id"],
            ["source_documents.id", "source_documents.tenant_id"],
            ondelete="CASCADE",
            name="fk_document_versions_source_doc_tenant",
        ),
        ForeignKeyConstraint(
            ["raw_asset_id", "tenant_id"],
            ["assets.id", "assets.tenant_id"],
            ondelete="RESTRICT",
            name="fk_document_versions_raw_asset_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class AcquisitionJob(Base):
    """Durable state-machine job orchestrating A01 document acquisition and processing stages."""

    __tablename__ = "acquisition_jobs"
    __table_args__ = (
        CheckConstraint(
            "job_type IN ('EXAM_IMPORT', 'DRIVE_SYNC', 'WEB_CRAWL', 'REPROCESS_EXTRACTION')",
            name="ck_acquisition_jobs_job_type",
        ),
        CheckConstraint(
            "job_status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'DEAD_LETTER', 'CANCELLED')",
            name="ck_acquisition_jobs_job_status",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_acquisition_jobs_id_tenant_id"),
        ForeignKeyConstraint(
            ["source_id", "tenant_id"],
            ["sources.id", "sources.tenant_id"],
            name="fk_acquisition_jobs_source_tenant",
        ),
        ForeignKeyConstraint(
            ["document_version_id", "tenant_id"],
            ["document_versions.id", "document_versions.tenant_id"],
            name="fk_acquisition_jobs_doc_version_tenant",
        ),
        Index("ix_acquisition_jobs_status_created", "job_status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    job_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="QUEUED")
    current_stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    metrics_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        server_default=text("'{}'"),
    )
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class PipelineStageRun(Base):
    """Per-stage execution tracking inside an acquisition job."""

    __tablename__ = "pipeline_stage_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('RUNNING', 'COMPLETED', 'FAILED', 'SKIPPED')",
            name="ck_pipeline_stage_runs_status",
        ),
        ForeignKeyConstraint(
            ["acquisition_job_id", "tenant_id"],
            ["acquisition_jobs.id", "acquisition_jobs.tenant_id"],
            ondelete="CASCADE",
            name="fk_pipeline_stage_runs_job_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    acquisition_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    stage_name: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="RUNNING")
    worker_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    input_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    output_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeadLetterItem(Base):
    """Poison/max-retry ingestion payload captured for manual inspection or replay."""

    __tablename__ = "dead_letter_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    acquisition_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_queue: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    error_trace: Mapped[str] = mapped_column(Text, nullable=False)
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
