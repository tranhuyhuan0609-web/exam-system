"""SQLAlchemy ORM models for E01 AI gateway (spec chapter 4, AI group).

`ai_providers`, `ai_models` and `prompt_versions` are global catalogs (app.platform.rls.GLOBAL_TABLES);
`ai_jobs` and `ai_traces` belong to a tenant. A trace is one provider call with its tokens and cost, and the
cost of today's traces is what the tenant's daily budget is measured against.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7

CIRCUIT_STATES = ("CLOSED", "OPEN", "HALF_OPEN")
JOB_STATUSES = ("QUEUED", "RUNNING", "COMPLETED", "FAILED")


class AiProvider(Base):
    __tablename__ = "ai_providers"
    __table_args__ = (
        CheckConstraint("circuit_state IN ('CLOSED', 'OPEN', 'HALF_OPEN')", name="ck_ai_providers_circuit_state"),
    )

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    circuit_state: Mapped[str] = mapped_column(String(20), nullable=False, server_default="CLOSED")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class AiModel(Base):
    __tablename__ = "ai_models"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    provider_code: Mapped[str] = mapped_column(String(40), ForeignKey("ai_providers.code"), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # OCR_VISION, HEALER_FAST, VERIFIER_REASONING, TUTOR_CHAT
    task_role: Mapped[str] = mapped_column(String(40), nullable=False)
    input_cost_per_1m: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    output_cost_per_1m: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("code", "version_no", name="uq_prompt_versions_code_version_no"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_template: Mapped[str] = mapped_column(Text, nullable=False)
    output_schema_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class AiJob(Base):
    __tablename__ = "ai_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')", name="ck_ai_jobs_status"),
        UniqueConstraint("id", "tenant_id", name="uq_ai_jobs_id_tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="QUEUED")
    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prompt_versions.id"), nullable=True
    )
    input_refs_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class AiTrace(Base):
    __tablename__ = "ai_traces"
    __table_args__ = (Index("ix_ai_traces_tenant_date", "tenant_id", "occurred_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    ai_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    model_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ai_models.id"), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
