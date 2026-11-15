"""SQLAlchemy ORM models for E03 AI Question Generation (`generation_requests`)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class GenerationRequestRecord(Base):
    """Grounded AI matrix gap-fill generation request queued for C02 + A09 human review."""

    __tablename__ = "generation_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    taxonomy_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("taxonomy_nodes.id"), nullable=False
    )
    target_difficulty: Mapped[str] = mapped_column(String(20), nullable=False)
    source_anchors_json: Mapped[list[dict[str, Any]] | dict[str, Any]] = mapped_column(JSONB, nullable=False)
    generated_candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'DRAFT_CREATED'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
