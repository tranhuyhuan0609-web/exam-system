"""SQLAlchemy ORM models for A02 Permitted Web Crawler (`crawl_urls`, `crawl_fetches`)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class CrawlUrl(Base):
    """Discovered/scheduled URL per crawler source, deduplicated by `(source_id, url_hash)`."""

    __tablename__ = "crawl_urls"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'FETCHED', 'SKIPPED_304', 'FAILED', 'BLOCKED_ROBOTS')",
            name="ck_crawl_urls_status",
        ),
        UniqueConstraint("source_id", "url_hash", name="uq_crawl_urls_source_url_hash"),
        UniqueConstraint("id", "tenant_id", name="uq_crawl_urls_id_tenant"),
        ForeignKeyConstraint(
            ["source_id", "tenant_id"],
            ["sources.id", "sources.tenant_id"],
            ondelete="CASCADE",
            name="fk_crawl_urls_source_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    url_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_modified_header: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'PENDING'"))
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CrawlFetch(Base):
    """Immutable log of each executed crawl fetch with pinned resolved IP & parse yield."""

    __tablename__ = "crawl_fetches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["crawl_url_id", "tenant_id"],
            ["crawl_urls.id", "crawl_urls.tenant_id"],
            ondelete="CASCADE",
            name="fk_crawl_fetches_crawl_url_tenant",
        ),
        ForeignKeyConstraint(
            ["raw_asset_id", "tenant_id"],
            ["assets.id", "assets.tenant_id"],
            name="fk_crawl_fetches_raw_asset_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    crawl_url_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    resolved_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    parse_yield_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
