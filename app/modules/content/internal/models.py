"""SQLAlchemy ORM models for B01 content (spec chapter 4, content group).

A content item carries the four status axes (acquisition, verification, rights, publication; fail-closed
defaults) and points at its current version. Versions are immutable once published (database trigger).
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

VISIBILITY_SCOPES = ("SYSTEM_PUBLIC", "TENANT_PRIVATE", "SHARED_PARTNER")
CONTENT_TYPES = ("ARTICLE", "LESSON", "VIDEO", "DOCUMENT", "FLASHCARD_SET", "VOCABULARY_SET")
ACQUISITION_STATUSES = ("INGESTED", "EXTRACTED", "NORMALIZED", "FAILED")
VERIFICATION_STATUSES = ("UNCHECKED", "AUTO_VERIFIED", "VERIFIED", "DISPUTED", "REJECTED")
RIGHTS_STATUSES = ("PENDING_REVIEW", "OWNED", "LICENSED", "ALLOWED_DERIVATIVE", "RESTRICTED", "TAKEDOWN")
CLEARED_RIGHTS = ("OWNED", "LICENSED", "ALLOWED_DERIVATIVE")
PUBLICATION_STATUSES = ("DRAFT", "READY", "PUBLISHED", "ARCHIVED", "TAKEN_DOWN")
ACCESS_TIERS = ("FREE", "STUDENT_PRO", "TEACHER_PRO")
VERSION_STATUSES = ("DRAFT", "PUBLISHED", "SUPERSEDED", "TAKEN_DOWN")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ContentItem(Base):
    """A learning content item (article, lesson, video, document, flashcard or vocabulary set)."""

    __tablename__ = "content_items"
    __table_args__ = (
        CheckConstraint(_in("visibility_scope", VISIBILITY_SCOPES), name="ck_content_items_visibility_scope"),
        CheckConstraint(_in("content_type", CONTENT_TYPES), name="ck_content_items_content_type"),
        CheckConstraint(_in("acquisition_status", ACQUISITION_STATUSES), name="ck_content_items_acquisition_status"),
        CheckConstraint(_in("verification_status", VERIFICATION_STATUSES), name="ck_content_items_verification_status"),
        CheckConstraint(_in("rights_status", RIGHTS_STATUSES), name="ck_content_items_rights_status"),
        CheckConstraint(_in("publication_status", PUBLICATION_STATUSES), name="ck_content_items_publication_status"),
        CheckConstraint(_in("access_tier", ACCESS_TIERS), name="ck_content_items_access_tier"),
        # Hard gate: nothing is PUBLISHED unverified, without cleared rights or without a current version.
        CheckConstraint(
            "publication_status <> 'PUBLISHED' OR (verification_status = 'VERIFIED' "
            "AND rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE') AND current_version_id IS NOT NULL)",
            name="chk_content_publish_gate",
        ),
        UniqueConstraint("tenant_id", "slug", name="uq_content_items_tenant_id_slug"),
        UniqueConstraint("id", "tenant_id", name="uq_content_items_id_tenant_id"),
        ForeignKeyConstraint(
            ["current_version_id", "tenant_id"],
            ["content_versions.id", "content_versions.tenant_id"],
            name="fk_content_items_current_version",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    visibility_scope: Mapped[str] = mapped_column(String(20), nullable=False, server_default="TENANT_PRIVATE")
    content_type: Mapped[str] = mapped_column(String(40), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    curriculum_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("curriculum_nodes.id", ondelete="SET NULL"), nullable=True
    )
    primary_taxonomy_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("taxonomy_nodes.id", ondelete="SET NULL"), nullable=True
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    acquisition_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="NORMALIZED")
    verification_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="UNCHECKED")
    rights_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="PENDING_REVIEW")
    publication_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="DRAFT")
    access_tier: Mapped[str] = mapped_column(String(20), nullable=False, server_default="FREE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class ContentVersion(Base):
    """One version of an item. `is_immutable` is set by the database when status becomes PUBLISHED."""

    __tablename__ = "content_versions"
    __table_args__ = (
        CheckConstraint(_in("status", VERSION_STATUSES), name="ck_content_versions_status"),
        UniqueConstraint("content_item_id", "version_no", name="uq_content_versions_content_item_id_version_no"),
        UniqueConstraint("id", "tenant_id", name="uq_content_versions_id_tenant_id"),
        ForeignKeyConstraint(
            ["content_item_id", "tenant_id"],
            ["content_items.id", "content_items.tenant_id"],
            ondelete="CASCADE",
            name="fk_content_versions_item_tenant",
        ),
        ForeignKeyConstraint(
            ["branded_asset_id", "tenant_id"],
            ["assets.id", "assets.tenant_id"],
            name="fk_content_versions_branded_asset_tenant",
        ),
        ForeignKeyConstraint(
            ["preview_asset_id", "tenant_id"],
            ["assets.id", "assets.tenant_id"],
            name="fk_content_versions_preview_asset_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    content_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocks_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'"))
    branded_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    preview_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="DRAFT")
    is_immutable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class ContentAsset(Base):
    """An asset of the tenant referenced by a content version (same-tenant composite keys)."""

    __tablename__ = "content_assets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["content_version_id", "tenant_id"],
            ["content_versions.id", "content_versions.tenant_id"],
            ondelete="CASCADE",
            name="fk_content_assets_version_tenant",
        ),
        ForeignKeyConstraint(
            ["asset_id", "tenant_id"],
            ["assets.id", "assets.tenant_id"],
            ondelete="RESTRICT",
            name="fk_content_assets_asset_tenant",
        ),
    )

    content_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class Publication(Base):
    """Append-only log of publications (content, question, assessment)."""

    __tablename__ = "publications"
    __table_args__ = (
        CheckConstraint(_in("entity_type", ("CONTENT", "QUESTION", "ASSESSMENT")), name="ck_publications_entity_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    published_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class AccessGrant(Base):
    """A membership's explicit access to a resource (for example a PRO lesson), optionally expiring."""

    __tablename__ = "access_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_access_grants_membership_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    membership_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
