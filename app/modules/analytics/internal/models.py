"""SQLAlchemy ORM models for G04 Learning & Operational Analytics Warehouse (spec chapter 4)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class AnalyticsEventRecord(Base):
    """Append-only domain event stream stored inside tenant RLS for scheduled daily rollups."""

    __tablename__ = "analytics_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_name: Mapped[str] = mapped_column(String(80), nullable=False)
    dimensions_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class DailyMetricRollupRecord(Base):
    """Pre-aggregated daily KPIs written exclusively by `ulp_system_scheduler` (`metric_rollup`)."""

    __tablename__ = "daily_metric_rollups"

    metric_date: Mapped[date] = mapped_column(Date, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    metric_code: Mapped[str] = mapped_column(String(80), primary_key=True)
    metric_value: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
