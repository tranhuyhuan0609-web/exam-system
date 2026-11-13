"""Curriculum version/node management and curriculum-to-taxonomy mapping operations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.curriculum.internal.models import (
    CurriculumNode,
    CurriculumVersion,
    TaxonomyMapping,
)


@dataclass(frozen=True, slots=True)
class CurriculumVersionRecord:
    """Public snapshot of a curriculum version."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    visibility_scope: str
    code: str
    name: str
    subject_code: str
    grade_band: str
    version_label: str
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CurriculumNodeRecord:
    """Public snapshot of a curriculum node."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    curriculum_version_id: uuid.UUID
    parent_id: uuid.UUID | None
    node_level: str
    code: str
    title: str
    sequence_order: int


@dataclass(frozen=True, slots=True)
class TaxonomyMappingRecord:
    """Public snapshot of a curriculum-node to taxonomy-node mapping."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    curriculum_node_id: uuid.UUID
    taxonomy_node_id: uuid.UUID
    mapping_weight: Decimal


def _to_version_record(row: CurriculumVersion) -> CurriculumVersionRecord:
    return CurriculumVersionRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        visibility_scope=row.visibility_scope,
        code=row.code,
        name=row.name,
        subject_code=row.subject_code,
        grade_band=row.grade_band,
        version_label=row.version_label,
        status=row.status,
        created_at=row.created_at,
    )


def _to_node_record(row: CurriculumNode) -> CurriculumNodeRecord:
    return CurriculumNodeRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        curriculum_version_id=row.curriculum_version_id,
        parent_id=row.parent_id,
        node_level=row.node_level,
        code=row.code,
        title=row.title,
        sequence_order=row.sequence_order,
    )


def _to_mapping_record(row: TaxonomyMapping) -> TaxonomyMappingRecord:
    return TaxonomyMappingRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        curriculum_node_id=row.curriculum_node_id,
        taxonomy_node_id=row.taxonomy_node_id,
        mapping_weight=row.mapping_weight,
    )


async def create_curriculum_version(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    code: str,
    name: str,
    subject_code: str,
    grade_band: str,
    version_label: str,
    visibility_scope: str = "TENANT_PRIVATE",
    status: str = "PUBLISHED",
) -> CurriculumVersionRecord:
    """Create a curriculum version row."""
    version = CurriculumVersion(
        tenant_id=tenant_id,
        visibility_scope=visibility_scope,
        code=code,
        name=name,
        subject_code=subject_code,
        grade_band=grade_band,
        version_label=version_label,
        status=status,
    )
    session.add(version)
    await session.flush()
    await session.refresh(version)
    return _to_version_record(version)


async def create_curriculum_node(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    curriculum_version_id: uuid.UUID,
    node_level: str,
    code: str,
    title: str,
    parent_id: uuid.UUID | None = None,
    sequence_order: int = 0,
) -> CurriculumNodeRecord:
    """Create a chapter/lesson/section/problem-type node in a curriculum version."""
    node = CurriculumNode(
        tenant_id=tenant_id,
        curriculum_version_id=curriculum_version_id,
        parent_id=parent_id,
        node_level=node_level,
        code=code,
        title=title,
        sequence_order=sequence_order,
    )
    session.add(node)
    await session.flush()
    await session.refresh(node)
    return _to_node_record(node)


async def map_curriculum_to_taxonomy(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    curriculum_node_id: uuid.UUID,
    taxonomy_node_id: uuid.UUID,
    mapping_weight: Decimal | str = Decimal("1.00"),
) -> TaxonomyMappingRecord:
    """Link a curriculum node to a taxonomy node (many-to-many)."""
    mapping = TaxonomyMapping(
        tenant_id=tenant_id,
        curriculum_node_id=curriculum_node_id,
        taxonomy_node_id=taxonomy_node_id,
        mapping_weight=Decimal(mapping_weight),
    )
    session.add(mapping)
    await session.flush()
    await session.refresh(mapping)
    return _to_mapping_record(mapping)


async def list_mappings_for_curriculum_node(
    session: AsyncSession,
    curriculum_node_id: uuid.UUID,
) -> list[TaxonomyMappingRecord]:
    """Return all taxonomy mappings attached to a curriculum node."""
    stmt = (
        select(TaxonomyMapping)
        .where(TaxonomyMapping.curriculum_node_id == curriculum_node_id)
        .order_by(TaxonomyMapping.mapping_weight.desc(), TaxonomyMapping.id.asc())
    )
    rows = (await session.scalars(stmt)).all()
    return [_to_mapping_record(r) for r in rows]


async def list_mappings_for_taxonomy_node(
    session: AsyncSession,
    taxonomy_node_id: uuid.UUID,
) -> list[TaxonomyMappingRecord]:
    """Return all curriculum mappings pointing at a taxonomy node."""
    stmt = (
        select(TaxonomyMapping)
        .where(TaxonomyMapping.taxonomy_node_id == taxonomy_node_id)
        .order_by(TaxonomyMapping.mapping_weight.desc(), TaxonomyMapping.id.asc())
    )
    rows = (await session.scalars(stmt)).all()
    return [_to_mapping_record(r) for r in rows]


async def get_curriculum_version_by_code(
    session: AsyncSession,
    *,
    code: str,
    tenant_id: uuid.UUID | None = None,
) -> CurriculumVersionRecord | None:
    """Fetch a curriculum version by code (optionally scoped to tenant_id)."""
    stmt = select(CurriculumVersion).where(CurriculumVersion.code == code)
    if tenant_id is not None:
        stmt = stmt.where(CurriculumVersion.tenant_id == tenant_id)
    row = await session.scalar(stmt)
    return _to_version_record(row) if row is not None else None


async def list_curriculum_nodes(
    session: AsyncSession,
    curriculum_version_id: uuid.UUID,
) -> list[CurriculumNodeRecord]:
    """Return nodes of a curriculum version ordered by sequence_order and code."""
    stmt = (
        select(CurriculumNode)
        .where(CurriculumNode.curriculum_version_id == curriculum_version_id)
        .order_by(CurriculumNode.sequence_order.asc(), CurriculumNode.code.asc())
    )
    rows = (await session.scalars(stmt)).all()
    return [_to_node_record(r) for r in rows]

