"""SQLAlchemy ORM models for B04 taxonomy_nodes, taxonomy_node_versions and taxonomy_aliases."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from app.db import Base

SYSTEM_PUBLIC_TENANT_ID_SQL = text("'00000000-0000-0000-0000-000000000000'")
UUID_V7 = text("uuid_generate_v7()")
NOW = text("now()")


class LtreeType(UserDefinedType[str]):
    """PostgreSQL ltree column type with native `<@` subtree comparison."""

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
        """Expose ltree hierarchical operators (`<@`, `@>`)."""

        def descendant_of(self, ancestor_path: str) -> Any:
            return self.op("<@")(cast(ancestor_path, LtreeType()))

        def ancestor_of(self, descendant_path: str) -> Any:
            return self.op("@>")(cast(descendant_path, LtreeType()))


# Register ltree so Alembic reflection recognizes the column type cleanly without NullType drift.
ischema_names.setdefault("ltree", LtreeType)


class TaxonomyNode(Base):
    """Canonical node in the hybrid multi-tenant knowledge taxonomy tree."""

    __tablename__ = "taxonomy_nodes"
    __table_args__ = (
        CheckConstraint(
            "visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE', 'SHARED_PARTNER')",
            name="ck_taxonomy_nodes_visibility_scope",
        ),
        CheckConstraint(
            "node_type IN ('DOMAIN', 'SUBJECT', 'TOPIC', 'CONCEPT', 'SKILL')",
            name="ck_taxonomy_nodes_node_type",
        ),
        UniqueConstraint("tenant_id", "code", name="uq_taxonomy_nodes_tenant_id_code"),
        UniqueConstraint("id", "tenant_id", name="uq_taxonomy_nodes_id_tenant_id"),
        Index("ix_taxonomy_nodes_ltree_gist", "code_path", postgresql_using="gist"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        server_default=SYSTEM_PUBLIC_TENANT_ID_SQL,
    )
    visibility_scope: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="SYSTEM_PUBLIC"
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("taxonomy_nodes.id", ondelete="RESTRICT"), nullable=True
    )
    node_type: Mapped[str] = mapped_column(String(40), nullable=False)
    code: Mapped[str] = mapped_column(String(120), nullable=False)
    code_path: Mapped[str] = mapped_column(LtreeType(), nullable=False)
    current_version_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    retired_into_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("taxonomy_nodes.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class TaxonomyNodeVersion(Base):
    """Immutable versioned metadata (name, description) for a taxonomy node."""

    __tablename__ = "taxonomy_node_versions"
    __table_args__ = (
        UniqueConstraint("taxonomy_node_id", "version_no", name="uq_taxonomy_node_versions_node_version"),
        ForeignKeyConstraint(
            ["taxonomy_node_id", "tenant_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.tenant_id"],
            ondelete="CASCADE",
            name="fk_taxonomy_node_versions_node_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    taxonomy_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Spec has `created_by UUID REFERENCES users(id)`, omitted until F04 replaces legacy integer users.id with UUID.
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class TaxonomyAlias(Base):
    """Searchable alternative names mapped onto a taxonomy node."""

    __tablename__ = "taxonomy_aliases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "normalized_alias", name="uq_taxonomy_aliases_tenant_norm_alias"),
        ForeignKeyConstraint(
            ["taxonomy_node_id", "tenant_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.tenant_id"],
            ondelete="CASCADE",
            name="fk_taxonomy_aliases_node_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    taxonomy_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    alias_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False)

