"""ORM models of outbox_events and inbox_events (spec chapter 4). Owned by module G03 only."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7

PENDING, PUBLISHED, FAILED = "PENDING", "PUBLISHED", "FAILED"
EVENT_STATUSES = (PENDING, PUBLISHED, FAILED)


class OutboxEvent(Base):
    """An event written in the same transaction as the business change it announces."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'PUBLISHED', 'FAILED')", name="outbox_events_status_check"),
        Index("ix_outbox_events_pending", "status", "created_at", postgresql_where=text("status = 'PENDING'")),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    event_type: Mapped[str] = mapped_column(String(100))
    schema_version: Mapped[int] = mapped_column(Integer, server_default="1")
    aggregate_type: Mapped[str] = mapped_column(String(60))
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    correlation_id: Mapped[str | None] = mapped_column(String(100))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), server_default=PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=NOW)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InboxEvent(Base):
    """One row per (consumer, event) whose side effect has committed: the exactly-once guard."""

    __tablename__ = "inbox_events"

    consumer_name: Mapped[str] = mapped_column(String(80), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=NOW)
