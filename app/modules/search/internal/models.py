"""SQLAlchemy ORM models for B07 Search, Discovery & Collections (spec chapter 4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    cast,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from app.db import Base
from app.platform.models import NOW, UUID_V7


class LtreeType(UserDefinedType[str]):
    """PostgreSQL ltree column type supporting `<@` and `@>` hierarchy queries."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "ltree"

    def bind_processor(self, dialect: Any):
        def process(value: str | None) -> str | None:
            return value

        return process

    def result_processor(self, dialect: Any, coltype: Any):
        def process(value: str | None) -> str | None:
            return value

        return process

    class comparator_factory(UserDefinedType.Comparator[str]):
        def descendant_of(self, ancestor_path: str) -> Any:
            return self.op("<@")(cast(ancestor_path, LtreeType()))

        def ancestor_of(self, descendant_path: str) -> Any:
            return self.op("@>")(cast(descendant_path, LtreeType()))


ischema_names.setdefault("ltree", LtreeType)

SEARCH_DOC_TYPES = ("CONTENT", "QUESTION", "ASSESSMENT")


class SearchDocument(Base):
    """Denormalized Vietnamese FTS & pg_trgm search projection table."""

    __tablename__ = "search_documents"
    __table_args__ = (
        CheckConstraint(
            "doc_type IN ('CONTENT', 'QUESTION', 'ASSESSMENT')",
            name="ck_search_documents_doc_type",
        ),
        UniqueConstraint("tenant_id", "doc_type", "entity_id", name="uq_search_documents_tenant_doc_entity"),
        Index("ix_search_documents_fts_gin", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    visibility_scope: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'TENANT_PRIVATE'"),
    )
    doc_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body_plain: Mapped[str] = mapped_column(Text, nullable=False)
    subject_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    grade_band: Mapped[str | None] = mapped_column(String(30), nullable=True)
    taxonomy_path: Mapped[str | None] = mapped_column(LtreeType(), nullable=True)
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('simple', f_unaccent(coalesce(title, ''))), 'A') || "
            "setweight(to_tsvector('simple', f_unaccent(coalesce(body_plain, ''))), 'B')",
            persisted=True,
        ),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class Collection(Base):
    """User-curated saved collection inside a tenant."""

    __tablename__ = "collections"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_collections_id_tenant_id"),
        ForeignKeyConstraint(
            ["owner_membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_collections_owner_membership_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_membership_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class CollectionItem(Base):
    """Ordered item bookmark inside a tenant collection."""

    __tablename__ = "collection_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["collection_id", "tenant_id"],
            ["collections.id", "collections.tenant_id"],
            ondelete="CASCADE",
            name="fk_collection_items_collection_tenant",
        ),
    )

    collection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    item_type: Mapped[str] = mapped_column(String(30), primary_key=True)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
