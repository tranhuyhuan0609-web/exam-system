"""SQLAlchemy ORM models for B02 authoring (spec chapter 4, content group).

* `content_drafts`: an author's working copy of the blocks of one content item, with the last validation errors.
* `content_blocks`: the blocks of a content version as rows (written when the version is published).
* `comment_threads`: review comments on any target (an item, a version, a block).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class ContentDraft(Base):
    __tablename__ = "content_drafts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["content_item_id", "tenant_id"],
            ["content_items.id", "content_items.tenant_id"],
            ondelete="CASCADE",
            name="fk_content_drafts_item_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    content_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    working_blocks_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'")
    )
    validation_errors_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'")
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class ContentBlock(Base):
    __tablename__ = "content_blocks"
    __table_args__ = (
        CheckConstraint(
            "block_type IN ('THEORY_SUMMARY', 'FORMULA_BOX', 'METHOD_STEPS', 'TIP_WARNING', "
            "'WORKED_EXAMPLE', 'INTERACTIVE_QUESTION_EMBED', 'HTML_TABLE')",
            name="ck_content_blocks_block_type",
        ),
        UniqueConstraint("content_version_id", "block_order", name="uq_content_blocks_content_version_id_block_order"),
        ForeignKeyConstraint(
            ["content_version_id", "tenant_id"],
            ["content_versions.id", "content_versions.tenant_id"],
            ondelete="CASCADE",
            name="fk_content_blocks_version_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    content_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    block_order: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class CommentThread(Base):
    __tablename__ = "comment_threads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
