"""SQLAlchemy ORM models for F06 Notifications & Deliveries (Decision #7 tenant_id)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class Notification(Base):
    """Learner/teacher notification deduplicated globally by `dedup_key`."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_notifications_id_tenant"),
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_notifications_membership_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class NotificationDelivery(Base):
    """Per-channel delivery log carrying explicit `tenant_id` (Decision #7) for direct RLS."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('IN_APP', 'EMAIL')",
            name="ck_notification_deliveries_channel",
        ),
        UniqueConstraint(
            "notification_id",
            "channel",
            name="uq_notification_deliveries_notif_channel",
        ),
        ForeignKeyConstraint(
            ["notification_id", "tenant_id"],
            ["notifications.id", "notifications.tenant_id"],
            ondelete="CASCADE",
            name="fk_notification_deliveries_notification_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    notification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'SENT'"))
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
