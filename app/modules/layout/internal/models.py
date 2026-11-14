"""SQLAlchemy ORM models for A06 layout segmentation & margin detection (spec chapter 4)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import UUID_V7

ALLOWED_REGION_TYPES = (
    "HEADER",
    "FOOTER",
    "QUESTION_BLOCK",
    "FIGURE",
    "TABLE",
    "FORMULA_BLOCK",
)


class ExtractedPage(Base):
    """Per-page extracted geometry, raw text, and detected repeating header/footer boundaries."""

    __tablename__ = "extracted_pages"
    __table_args__ = (
        UniqueConstraint("extraction_run_id", "page_no", name="uq_extracted_pages_run_page_no"),
        UniqueConstraint("id", "tenant_id", name="uq_extracted_pages_id_tenant_id"),
        ForeignKeyConstraint(
            ["extraction_run_id", "tenant_id"],
            ["extraction_runs.id", "extraction_runs.tenant_id"],
            ondelete="CASCADE",
            name="fk_extracted_pages_extraction_run_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    extraction_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    render_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    width_pt: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    height_pt: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    detected_header_y0: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    detected_footer_y1: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class LayoutRegion(Base):
    """Typed bounding box inside an extracted page (question, figure, table, formula, margin)."""

    __tablename__ = "layout_regions"
    __table_args__ = (
        CheckConstraint(
            "region_type IN ('HEADER', 'FOOTER', 'QUESTION_BLOCK', 'FIGURE', 'TABLE', 'FORMULA_BLOCK')",
            name="ck_layout_regions_region_type",
        ),
        ForeignKeyConstraint(
            ["extracted_page_id", "tenant_id"],
            ["extracted_pages.id", "extracted_pages.tenant_id"],
            ondelete="CASCADE",
            name="fk_layout_regions_extracted_page_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    extracted_page_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    region_type: Mapped[str] = mapped_column(String(40), nullable=False)
    bbox_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    cropped_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
