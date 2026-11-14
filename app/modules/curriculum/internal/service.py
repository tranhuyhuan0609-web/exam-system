"""Curriculum version/node management and curriculum-to-taxonomy mapping operations."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.curriculum.internal.models import (
    CurriculumNode,
    CurriculumVersion,
    TaxonomyMapping,
)
from app.modules.taxonomy.public import get_node_by_id

VALID_CURRICULUM_VISIBILITY_SCOPES = frozenset({"SYSTEM_PUBLIC", "TENANT_PRIVATE"})
VALID_CURRICULUM_STATUSES = frozenset({"DRAFT", "PUBLISHED", "ARCHIVED"})
VALID_CURRICULUM_NODE_LEVELS = frozenset({"CHAPTER", "LESSON", "SECTION", "PROBLEM_TYPE"})
COGNITIVE_LEVELS = ("NB", "TH", "VD", "VDC")


class DuplicateMappingError(ValueError):
    """Raised when a curriculum_node_id -> taxonomy_node_id link already exists."""


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


@dataclass(frozen=True, slots=True)
class CurriculumNodeInputSpec:
    """Hierarchical or flat node definition for curriculum creation/versioning."""

    node_level: str
    code: str
    title: str
    parent_code: str | None = None
    sequence_order: int = 0
    children: tuple[CurriculumNodeInputSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class CognitiveCounts:
    """Question counts bucketed by cognitive level (NB, TH, VD, VDC)."""

    NB: int
    TH: int
    VD: int
    VDC: int
    total: int


@dataclass(frozen=True, slots=True)
class CoverageNodeRecord:
    """Coverage statistics for a single curriculum node (typically PROBLEM_TYPE)."""

    curriculum_node_id: uuid.UUID
    parent_id: uuid.UUID | None
    node_level: str
    code: str
    title: str
    mapped_taxonomy_node_ids: tuple[uuid.UUID, ...]
    mapped_taxonomy_count: int
    is_mapped: bool
    question_counts: CognitiveCounts


@dataclass(frozen=True, slots=True)
class CoverageReportRecord:
    """Aggregated B06 CoverageReport across a curriculum version."""

    curriculum_version_id: uuid.UUID
    tenant_id: uuid.UUID
    code: str
    name: str
    subject_code: str
    grade_band: str
    version_label: str
    total_nodes: int
    reported_node_count: int
    total_problem_types: int
    mapped_node_count: int
    unmapped_node_count: int
    mapping_coverage_ratio: float
    question_counts: CognitiveCounts
    question_bank_available: bool
    generated_in_ms: float
    items: tuple[CoverageNodeRecord, ...]


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


def _flatten_node_specs(
    nodes: Sequence[CurriculumNodeInputSpec],
    *,
    inherited_parent_code: str | None = None,
) -> list[tuple[str, str, str, str | None, int]]:
    flat: list[tuple[str, str, str, str | None, int]] = []
    for spec in nodes:
        code = spec.code.strip()
        title = spec.title.strip()
        if not code or not title:
            raise ValueError("curriculum node code and title must not be blank")
        if spec.node_level not in VALID_CURRICULUM_NODE_LEVELS:
            raise ValueError(f"unsupported curriculum node_level {spec.node_level!r}")
        parent_code = spec.parent_code.strip() if spec.parent_code is not None else inherited_parent_code
        flat.append((spec.node_level, code, title, parent_code, spec.sequence_order))
        if spec.children:
            flat.extend(_flatten_node_specs(spec.children, inherited_parent_code=code))
    return flat


async def _create_nodes_for_version(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    curriculum_version_id: uuid.UUID,
    nodes: Sequence[CurriculumNodeInputSpec],
) -> list[CurriculumNodeRecord]:
    if not nodes:
        return []
    flat = _flatten_node_specs(nodes)
    seen_codes: set[str] = set()
    for _, code, _, _, _ in flat:
        if code in seen_codes:
            raise ValueError(f"duplicate curriculum node code {code!r}")
        seen_codes.add(code)

    pending = list(flat)
    code_to_id: dict[str, uuid.UUID] = {}
    created: list[CurriculumNodeRecord] = []
    while pending:
        next_round: list[tuple[str, str, str, str | None, int]] = []
        progressed = False
        for node_level, code, title, parent_code, sequence_order in pending:
            if parent_code is not None and parent_code not in code_to_id:
                if parent_code not in seen_codes:
                    raise ValueError(f"unknown parent_code {parent_code!r} for curriculum node {code!r}")
                next_round.append((node_level, code, title, parent_code, sequence_order))
                continue
            parent_id = code_to_id[parent_code] if parent_code is not None else None
            rec = await create_curriculum_node(
                session,
                tenant_id=tenant_id,
                curriculum_version_id=curriculum_version_id,
                node_level=node_level,
                code=code,
                title=title,
                parent_id=parent_id,
                sequence_order=sequence_order,
            )
            code_to_id[code] = rec.id
            created.append(rec)
            progressed = True
        if not progressed:
            raise ValueError("cycle detected in curriculum node parent_code references")
        pending = next_round
    return created


async def create_curriculum_with_nodes(
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
    nodes: Sequence[CurriculumNodeInputSpec] = (),
) -> tuple[CurriculumVersionRecord, list[CurriculumNodeRecord]]:
    """Create a curriculum version together with its optional 4-level node hierarchy."""
    if visibility_scope not in VALID_CURRICULUM_VISIBILITY_SCOPES:
        raise ValueError(f"unsupported visibility_scope {visibility_scope!r}")
    if status not in VALID_CURRICULUM_STATUSES:
        raise ValueError(f"unsupported curriculum status {status!r}")
    if not all(val.strip() for val in (code, name, subject_code, grade_band, version_label)):
        raise ValueError("code, name, subject_code, grade_band, and version_label must not be blank")

    version = await create_curriculum_version(
        session,
        tenant_id=tenant_id,
        code=code.strip(),
        name=name.strip(),
        subject_code=subject_code.strip(),
        grade_band=grade_band.strip(),
        version_label=version_label.strip(),
        visibility_scope=visibility_scope,
        status=status,
    )
    created_nodes = await _create_nodes_for_version(
        session,
        tenant_id=tenant_id,
        curriculum_version_id=version.id,
        nodes=nodes,
    )
    return version, created_nodes


def _order_nodes_topologically(rows: Sequence[CurriculumNode]) -> list[CurriculumNode]:
    by_id = {row.id: row for row in rows}
    ordered: list[CurriculumNode] = []
    placed: set[uuid.UUID] = set()
    remaining = sorted(rows, key=lambda r: (r.sequence_order, r.code))
    while remaining:
        next_remaining: list[CurriculumNode] = []
        progressed = False
        for row in remaining:
            if row.parent_id is None or row.parent_id in placed or row.parent_id not in by_id:
                ordered.append(row)
                placed.add(row.id)
                progressed = True
            else:
                next_remaining.append(row)
        if not progressed:
            ordered.extend(next_remaining)
            break
        remaining = next_remaining
    return ordered


async def fork_curriculum_version(
    session: AsyncSession,
    *,
    source_curriculum_id: uuid.UUID,
    tenant_id: uuid.UUID,
    version_label: str,
    code: str | None = None,
    name: str | None = None,
    visibility_scope: str = "TENANT_PRIVATE",
    status: str = "DRAFT",
    copy_nodes: bool = True,
    copy_mappings: bool = True,
    nodes: Sequence[CurriculumNodeInputSpec] = (),
) -> tuple[CurriculumVersionRecord, list[CurriculumNodeRecord]]:
    """Create a new version of an existing curriculum, cloning nodes and mappings by default."""
    if visibility_scope not in VALID_CURRICULUM_VISIBILITY_SCOPES:
        raise ValueError(f"unsupported visibility_scope {visibility_scope!r}")
    if status not in VALID_CURRICULUM_STATUSES:
        raise ValueError(f"unsupported curriculum status {status!r}")
    clean_label = version_label.strip()
    if not clean_label:
        raise ValueError("version_label must not be blank")

    source = await session.scalar(select(CurriculumVersion).where(CurriculumVersion.id == source_curriculum_id))
    if source is None:
        raise LookupError(f"curriculum version {source_curriculum_id} not found")

    if code is not None:
        resolved_code = code.strip()
        if not resolved_code:
            raise ValueError("code must not be blank")
    else:
        suffix = f"@{clean_label}"
        prefix_len = max(1, 80 - len(suffix))
        resolved_code = f"{source.code[:prefix_len]}{suffix}"[:80]

    resolved_name = (name if name is not None else source.name).strip()
    if not resolved_name:
        raise ValueError("name must not be blank")

    new_version = await create_curriculum_version(
        session,
        tenant_id=tenant_id,
        code=resolved_code,
        name=resolved_name,
        subject_code=source.subject_code,
        grade_band=source.grade_band,
        version_label=clean_label,
        visibility_scope=visibility_scope,
        status=status,
    )

    if nodes:
        created_nodes = await _create_nodes_for_version(
            session,
            tenant_id=tenant_id,
            curriculum_version_id=new_version.id,
            nodes=nodes,
        )
        return new_version, created_nodes

    if not copy_nodes:
        return new_version, []

    source_rows = (
        await session.scalars(
            select(CurriculumNode).where(CurriculumNode.curriculum_version_id == source.id)
        )
    ).all()
    ordered_rows = _order_nodes_topologically(source_rows)
    id_map: dict[uuid.UUID, uuid.UUID] = {}
    cloned_nodes: list[CurriculumNodeRecord] = []
    for old_row in ordered_rows:
        cloned = await create_curriculum_node(
            session,
            tenant_id=tenant_id,
            curriculum_version_id=new_version.id,
            node_level=old_row.node_level,
            code=old_row.code,
            title=old_row.title,
            parent_id=id_map.get(old_row.parent_id) if old_row.parent_id is not None else None,
            sequence_order=old_row.sequence_order,
        )
        id_map[old_row.id] = cloned.id
        cloned_nodes.append(cloned)

    if copy_mappings and id_map:
        source_mappings = (
            await session.scalars(
                select(TaxonomyMapping).where(TaxonomyMapping.curriculum_node_id.in_(list(id_map.keys())))
            )
        ).all()
        for m in source_mappings:
            tax_visible = await get_node_by_id(session, m.taxonomy_node_id)
            if tax_visible is None:
                continue
            await map_curriculum_to_taxonomy(
                session,
                tenant_id=tenant_id,
                curriculum_node_id=id_map[m.curriculum_node_id],
                taxonomy_node_id=m.taxonomy_node_id,
                mapping_weight=m.mapping_weight,
            )

    return new_version, cloned_nodes


async def link_curriculum_to_taxonomy(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    curriculum_node_id: uuid.UUID,
    taxonomy_node_id: uuid.UUID,
    mapping_weight: Decimal | str = Decimal("1.00"),
) -> TaxonomyMappingRecord:
    """Safely link an owned curriculum node to a visible taxonomy node."""
    cn = await session.scalar(select(CurriculumNode).where(CurriculumNode.id == curriculum_node_id))
    if cn is None:
        raise LookupError(f"curriculum node {curriculum_node_id} not found")
    if cn.tenant_id != tenant_id:
        raise PermissionError("cannot attach taxonomy mappings to a curriculum node owned by another tenant")

    tax_node = await get_node_by_id(session, taxonomy_node_id)
    if tax_node is None:
        raise LookupError(f"taxonomy node {taxonomy_node_id} not found")

    existing = await session.scalar(
        select(TaxonomyMapping.id).where(
            TaxonomyMapping.curriculum_node_id == curriculum_node_id,
            TaxonomyMapping.taxonomy_node_id == taxonomy_node_id,
        )
    )
    if existing is not None:
        raise DuplicateMappingError("mapping between curriculum_node_id and taxonomy_node_id already exists")

    return await map_curriculum_to_taxonomy(
        session,
        tenant_id=tenant_id,
        curriculum_node_id=curriculum_node_id,
        taxonomy_node_id=taxonomy_node_id,
        mapping_weight=mapping_weight,
    )


async def _has_v113_questions_table(session: AsyncSession) -> bool:
    """Return True only once V11.3 C01 `questions` columns exist in the database."""
    cnt = await session.scalar(
        text(
            """
            SELECT count(*)
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'questions'
              AND column_name IN (
                  'curriculum_node_id',
                  'primary_taxonomy_node_id',
                  'difficulty_level',
                  'publication_status'
              )
            """
        )
    )
    return int(cnt or 0) == 4


async def _fetch_question_counts_by_node(
    session: AsyncSession,
    *,
    target_node_ids: Sequence[uuid.UUID],
    published_only: bool,
) -> dict[uuid.UUID, dict[str, int]]:
    if not target_node_ids:
        return {}
    pub_clause = "AND q.publication_status = 'PUBLISHED'" if published_only else ""
    sql = text(
        f"""
        SELECT matched.target_node_id, matched.difficulty_level, COUNT(DISTINCT matched.question_id) AS q_count
        FROM (
            SELECT cn.id AS target_node_id, q.id AS question_id, q.difficulty_level
            FROM curriculum_nodes cn
            JOIN questions q ON q.curriculum_node_id = cn.id
            WHERE cn.id IN :target_ids
              {pub_clause}
            UNION
            SELECT tm.curriculum_node_id AS target_node_id, q.id AS question_id, q.difficulty_level
            FROM taxonomy_mappings tm
            JOIN questions q ON q.primary_taxonomy_node_id = tm.taxonomy_node_id
            WHERE tm.curriculum_node_id IN :target_ids
              {pub_clause}
        ) AS matched
        GROUP BY matched.target_node_id, matched.difficulty_level
        """
    ).bindparams(bindparam("target_ids", expanding=True))
    rows = (await session.execute(sql, {"target_ids": list(target_node_ids)})).all()
    counts: dict[uuid.UUID, dict[str, int]] = defaultdict(lambda: {"NB": 0, "TH": 0, "VD": 0, "VDC": 0})
    for target_id, diff, q_count in rows:
        if diff in COGNITIVE_LEVELS:
            counts[target_id][diff] = int(q_count)
    return counts


async def get_curriculum_coverage(
    session: AsyncSession,
    *,
    curriculum_version_id: uuid.UUID,
    node_level: str | None = None,
    published_only: bool = True,
) -> CoverageReportRecord:
    """Compute B06 CoverageReport across target curriculum nodes in <200ms."""
    started = time.perf_counter()

    version = await session.scalar(
        select(CurriculumVersion).where(CurriculumVersion.id == curriculum_version_id)
    )
    if version is None:
        raise LookupError(f"curriculum version {curriculum_version_id} not found")

    all_nodes = (
        await session.scalars(
            select(CurriculumNode)
            .where(CurriculumNode.curriculum_version_id == version.id)
            .order_by(CurriculumNode.sequence_order.asc(), CurriculumNode.code.asc())
        )
    ).all()

    parent_ids = {n.parent_id for n in all_nodes if n.parent_id is not None}
    problem_type_nodes = [n for n in all_nodes if n.node_level == "PROBLEM_TYPE"]

    if node_level is not None:
        normalized_level = node_level.strip().upper()
        if normalized_level == "ALL":
            target_nodes = list(all_nodes)
        else:
            if normalized_level not in VALID_CURRICULUM_NODE_LEVELS:
                raise ValueError(f"unsupported node_level {node_level!r}")
            target_nodes = [n for n in all_nodes if n.node_level == normalized_level]
    elif problem_type_nodes:
        target_nodes = problem_type_nodes
    else:
        target_nodes = [n for n in all_nodes if n.id not in parent_ids]

    target_ids = [n.id for n in target_nodes]
    mappings_by_node: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    if target_ids:
        mapping_rows = (
            await session.scalars(
                select(TaxonomyMapping)
                .where(TaxonomyMapping.curriculum_node_id.in_(target_ids))
                .order_by(TaxonomyMapping.mapping_weight.desc(), TaxonomyMapping.id.asc())
            )
        ).all()
        for m in mapping_rows:
            mappings_by_node[m.curriculum_node_id].append(m.taxonomy_node_id)

    q_bank_ready = await _has_v113_questions_table(session)
    q_counts_by_node: dict[uuid.UUID, dict[str, int]] = {}
    if q_bank_ready and target_ids:
        q_counts_by_node = await _fetch_question_counts_by_node(
            session,
            target_node_ids=target_ids,
            published_only=published_only,
        )

    items: list[CoverageNodeRecord] = []
    agg_nb = agg_th = agg_vd = agg_vdc = 0
    mapped_count = 0

    for node in target_nodes:
        tax_ids = tuple(mappings_by_node.get(node.id, ()))
        is_mapped = len(tax_ids) > 0
        if is_mapped:
            mapped_count += 1
        raw_counts = q_counts_by_node.get(node.id, {})
        nb = raw_counts.get("NB", 0)
        th = raw_counts.get("TH", 0)
        vd = raw_counts.get("VD", 0)
        vdc = raw_counts.get("VDC", 0)
        agg_nb += nb
        agg_th += th
        agg_vd += vd
        agg_vdc += vdc
        items.append(
            CoverageNodeRecord(
                curriculum_node_id=node.id,
                parent_id=node.parent_id,
                node_level=node.node_level,
                code=node.code,
                title=node.title,
                mapped_taxonomy_node_ids=tax_ids,
                mapped_taxonomy_count=len(tax_ids),
                is_mapped=is_mapped,
                question_counts=CognitiveCounts(
                    NB=nb,
                    TH=th,
                    VD=vd,
                    VDC=vdc,
                    total=nb + th + vd + vdc,
                ),
            )
        )

    reported_count = len(items)
    ratio = round(mapped_count / reported_count, 4) if reported_count > 0 else 0.0
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)

    return CoverageReportRecord(
        curriculum_version_id=version.id,
        tenant_id=version.tenant_id,
        code=version.code,
        name=version.name,
        subject_code=version.subject_code,
        grade_band=version.grade_band,
        version_label=version.version_label,
        total_nodes=len(all_nodes),
        reported_node_count=reported_count,
        total_problem_types=len(problem_type_nodes),
        mapped_node_count=mapped_count,
        unmapped_node_count=reported_count - mapped_count,
        mapping_coverage_ratio=ratio,
        question_counts=CognitiveCounts(
            NB=agg_nb,
            TH=agg_th,
            VD=agg_vd,
            VDC=agg_vdc,
            total=agg_nb + agg_th + agg_vd + agg_vdc,
        ),
        question_bank_available=q_bank_ready,
        generated_in_ms=elapsed_ms,
        items=tuple(items),
    )
