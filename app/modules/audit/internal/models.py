"""SQLAlchemy ORM models for G02 audit (spec chapter 4): `takedown_cases` and the partitioned `audit_events`.

`audit_events` is RANGE-partitioned by month on `occurred_at`; its primary key includes the partition key.
Partitions are created by migrations and by `ensure_monthly_partitions()`; they are not mapped here (all
reads and writes go through the parent table, which is also the only one runtime roles may touch).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7

TAKEDOWN_TARGET_TYPES = ("SOURCE_DOCUMENT", "CONTENT_ITEM", "QUESTION")
TAKEDOWN_STATUSES = ("OPEN", "EXECUTED", "REJECTED")


class TakedownCase(Base):
    __tablename__ = "takedown_cases"
    __table_args__ = (
        CheckConstraint(
            "target_entity_type IN ('SOURCE_DOCUMENT', 'CONTENT_ITEM', 'QUESTION')",
            name="ck_takedown_cases_target_entity_type",
        ),
        CheckConstraint("status IN ('OPEN', 'EXECUTED', 'REJECTED')", name="ck_takedown_cases_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    complainant_name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    legal_reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="OPEN")
    executed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index(
            "ix_audit_events_tenant_target",
            "tenant_id",
            "target_type",
            "target_id",
            text("occurred_at DESC"),
        ),
        {"postgresql_partition_by": "RANGE (occurred_at)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(String(60), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False, server_default=NOW
    )
