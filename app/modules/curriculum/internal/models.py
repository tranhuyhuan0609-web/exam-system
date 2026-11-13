"""SQLAlchemy ORM models for B06 curriculum_versions, curriculum_nodes and taxonomy_mappings."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

SYSTEM_PUBLIC_TENANT_ID_SQL = text("'00000000-0000-0000-0000-000000000000'")
UUID_V7 = text("uuid_generate_v7()")
NOW = text("now()")


class CurriculumVersion(Base):
    """Published or draft textbook/curriculum edition (system-public or tenant-private)."""

    __tablename__ = "curriculum_versions"
    __table_args__ = (
        CheckConstraint(
            "visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE')",
            name="ck_curriculum_versions_visibility_scope",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')",
            name="ck_curriculum_versions_status",
        ),
        UniqueConstraint("tenant_id", "code", name="uq_curriculum_versions_tenant_code"),
        UniqueConstraint("id", "tenant_id", name="uq_curriculum_versions_id_tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        server_default=SYSTEM_PUBLIC_TENANT_ID_SQL,
    )
    visibility_scope: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="SYSTEM_PUBLIC"
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    subject_code: Mapped[str] = mapped_column(String(50), nullable=False)
    grade_band: Mapped[str] = mapped_column(String(30), nullable=False)
    version_label: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="PUBLISHED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class CurriculumNode(Base):
    """Hierarchical unit (CHAPTER, LESSON, SECTION, PROBLEM_TYPE) in a curriculum version."""

    __tablename__ = "curriculum_nodes"
    __table_args__ = (
        CheckConstraint(
            "node_level IN ('CHAPTER', 'LESSON', 'SECTION', 'PROBLEM_TYPE')",
            name="ck_curriculum_nodes_node_level",
        ),
        UniqueConstraint("curriculum_version_id", "code", name="uq_curriculum_nodes_version_code"),
        UniqueConstraint("id", "tenant_id", name="uq_curriculum_nodes_id_tenant_id"),
        ForeignKeyConstraint(
            ["curriculum_version_id", "tenant_id"],
            ["curriculum_versions.id", "curriculum_versions.tenant_id"],
            ondelete="CASCADE",
            name="fk_curriculum_nodes_version_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    curriculum_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("curriculum_nodes.id", ondelete="CASCADE"), nullable=True
    )
    node_level: Mapped[str] = mapped_column(String(40), nullable=False)
    code: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class TaxonomyMapping(Base):
    """Many-to-many link between a curriculum node and canonical taxonomy nodes."""

    __tablename__ = "taxonomy_mappings"
    __table_args__ = (
        UniqueConstraint(
            "curriculum_node_id", "taxonomy_node_id", name="uq_taxonomy_mappings_curriculum_taxonomy"
        ),
        ForeignKeyConstraint(
            ["curriculum_node_id", "tenant_id"],
            ["curriculum_nodes.id", "curriculum_nodes.tenant_id"],
            ondelete="CASCADE",
            name="fk_taxonomy_mappings_curriculum_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    curriculum_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    taxonomy_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("taxonomy_nodes.id", ondelete="CASCADE"), nullable=False
    )
    mapping_weight: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False, server_default="1.00")

