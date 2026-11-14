"""SQLAlchemy ORM models for E04 AI Step-by-Step Explanations (spec chapter 4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class ExplanationRun(Base):
    """Persisted 4-step solution draft with isolated SymPy verification status."""

    __tablename__ = "explanation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    # FK to question_versions(id) is enforced in PostgreSQL DDL / migrations once questions ORM lands.
    question_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    draft_rationale_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    sympy_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        server_default=text("'PENDING_APPROVAL'"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
