"""SQLAlchemy ORM models for F03 Class Gradebook Projections (spec chapter 4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW


class GradebookProjection(Base):
    """Materialized per-class, per-assignment gradebook statistics and top wrong items."""

    __tablename__ = "gradebook_projections"

    # Composite FKs to classes(id, tenant_id) and assessment_assignments(id, tenant_id)
    # enforced in PostgreSQL DDL/Alembic.
    class_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    assignment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    stats_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'"),
    )
    top_wrong_questions_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'"),
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
