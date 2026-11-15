"""SQLAlchemy ORM models for G05 Billing, Subscriptions, Usage Quota Ledger & Webhooks (spec chapter 4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7

SUBSCRIPTION_STATUSES = ("ACTIVE", "PAST_DUE", "CANCELLED", "EXPIRED")


class SubscriptionPlanRecord(Base):
    """Global plan catalog defining monthly pricing and per-resource quota caps."""

    __tablename__ = "subscription_plans"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    price_vnd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quotas_json: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)


class SubscriptionRecord(Base):
    """Tenant & membership-scoped active/historical entitlement subscription."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'PAST_DUE', 'CANCELLED', 'EXPIRED')",
            name="ck_subscriptions_status",
        ),
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="RESTRICT",
            name="fk_subscriptions_membership_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    membership_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    plan_code: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("subscription_plans.code"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'ACTIVE'"))
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class UsageLedgerRecord(Base):
    """Idempotent metered quota consumption ledger entry inside strict tenant RLS."""

    __tablename__ = "usage_ledger"
    __table_args__ = (
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="RESTRICT",
            name="fk_usage_ledger_membership_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    resource_code: Mapped[str] = mapped_column(String(60), nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class PaymentWebhookRecord(Base):
    """Idempotent webhook deduplication receipt keyed by `webhook_id`."""

    __tablename__ = "payment_webhooks"

    webhook_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    raw_payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
