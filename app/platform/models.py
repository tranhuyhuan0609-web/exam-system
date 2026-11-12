"""Global platform tables: no tenant column of their own, hence no row-level security."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CHAR, DateTime, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

SYSTEM_PUBLIC_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
"""Owner of the shared, public learning material (matches system_public_tenant_id() in SQL)."""

UUID_V7 = text("uuid_generate_v7()")
NOW = text("now()")


class Tenant(Base):
    """A school, centre or the system itself. Every business row carries a tenant_id pointing here."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), server_default="ACTIVE")  # ACTIVE | SUSPENDED | ARCHIVED
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    daily_ai_budget_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2), server_default="10.00")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=NOW)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=NOW)


class FtsDictionaryVersion(Base):
    """Pins the unaccent dictionary that the IMMUTABLE f_unaccent() wraps.

    `rules_sha256` holds fts_unaccent_fingerprint() as it was when the search indexes were last built.
    """

    __tablename__ = "fts_dictionary_versions"

    dict_name: Mapped[str] = mapped_column(String(80), primary_key=True)
    rules_sha256: Mapped[str] = mapped_column(CHAR(64))
    func_version: Mapped[int] = mapped_column(Integer, server_default="1")
    last_reindexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=NOW)
