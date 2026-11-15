"""SQLAlchemy ORM models for G06 Vertical Packs & Localization Bundles (spec chapter 4)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class VerticalPackRecord(Base):
    """Pluggable vertical pack descriptor (`K12_VN_KNTT`, `IELTS_ACADEMIC`, `TOEIC_LR`, `HSK_3_0`)."""

    __tablename__ = "vertical_packs"

    code: Mapped[str] = mapped_column(String(60), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))


class LocaleBundleRecord(Base):
    """Per-locale UI/vertical message translation dictionary."""

    __tablename__ = "locale_bundles"

    locale_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(60), primary_key=True)
    messages_json: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
