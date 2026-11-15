"""SQLAlchemy ORM models for F03 Class Gradebook Projections (spec chapter 4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKeyConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW


class GradebookProjection(Base):
    """Materialized per-class, per-assignment gradebook statistics and top wrong items."""

    __tablename__ = "gradebook_projections"

    __table_args__ = (
        # Chapter 4 gives the projection a foreign key to its class only (not to the assignment).
        ForeignKeyConstraint(
            ["class_id", "tenant_id"],
            ["classes.id", "classes.tenant_id"],
            ondelete="CASCADE",
            name="fk_gradebook_projections_class_tenant",
        ),
    )

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
