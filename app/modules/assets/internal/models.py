"""SQLAlchemy ORM model for B03 assets (spec chapter 4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7

ASSET_ROLES = (
    "RAW_SOURCE",
    "BRANDED_PDF",
    "PREVIEW_PDF",
    "PAGE_RENDER",
    "FIGURE_CROP",
    "AUDIO",
    "THUMBNAIL",
)


class Asset(Base):
    """Binary or media artifact tracked per tenant with immutable bronze raw-source flag."""

    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint(
            "asset_role IN ('RAW_SOURCE', 'BRANDED_PDF', 'PREVIEW_PDF', "
            "'PAGE_RENDER', 'FIGURE_CROP', 'AUDIO', 'THUMBNAIL')",
            name="ck_assets_asset_role",
        ),
        CheckConstraint("size_bytes >= 0", name="ck_assets_size_bytes_non_negative"),
        UniqueConstraint("id", "tenant_id", name="uq_assets_id_tenant_id"),
        Index("ix_assets_tenant_sha256", "tenant_id", "sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    parent_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="SET NULL"),
        nullable=True,
    )
    asset_role: Mapped[str] = mapped_column(String(40), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_immutable_bronze: Mapped[bool] = mapped_column(
        Boolean,
        Computed("asset_role = 'RAW_SOURCE'", persisted=True),
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        server_default=text("'{}'"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
