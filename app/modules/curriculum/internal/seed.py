"""Idempotent seed command for KNTT Math & Physics Grades 10-12 into SYSTEM_PUBLIC."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.k12_curriculum_data import get_master_k12_catalog
from app.modules.curriculum.internal.models import (
    CurriculumNode,
    CurriculumVersion,
    TaxonomyMapping,
)
from app.modules.curriculum.internal.service import (
    create_curriculum_node,
    create_curriculum_version,
    map_curriculum_to_taxonomy,
)
from app.modules.taxonomy.public import (
    TaxonomyNodeRecord,
    create_taxonomy_node,
    get_node_by_code,
    normalize_ltree_label,
)
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID
from app.platform.tenancy import DbRole, tenant_transaction

TARGET_GRADES = frozenset({10, 11, 12})
TARGET_SUBJECT_SLUGS = frozenset(
    {
        "toan-10",
        "vat-li-10",
        "toan-11",
        "vat-li-11",
        "toan-12",
        "vat-li-12",
    }
)
SUBJECT_TITLES = {
    "MATH": "Toán học",
    "PHYSICS": "Vật lí",
}


@dataclass(frozen=True, slots=True)
class SeedKnttResult:
    """Counts of newly inserted records from a KNTT seed run."""

    taxonomy_nodes_created: int
    curriculum_versions_created: int
    curriculum_nodes_created: int
    mappings_created: int


async def _ensure_taxonomy_node(
    session: AsyncSession,
    *,
    code: str,
    node_type: str,
    name: str,
    parent: TaxonomyNodeRecord | None,
) -> tuple[TaxonomyNodeRecord, int]:
    existing = await get_node_by_code(session, code=code, tenant_id=SYSTEM_PUBLIC_TENANT_ID)
    if existing is not None:
        return existing, 0
    leaf = normalize_ltree_label(code.rsplit(".", 1)[-1])
    code_path = f"{parent.code_path}.{leaf}" if parent is not None else leaf
    node, _ = await create_taxonomy_node(
        session,
        tenant_id=SYSTEM_PUBLIC_TENANT_ID,
        node_type=node_type,
        code=code,
        name=name,
        visibility_scope="SYSTEM_PUBLIC",
        parent_id=parent.id if parent is not None else None,
        code_path=code_path,
        is_active=True,
    )
    return node, 1


async def _ensure_curriculum_version(
    session: AsyncSession,
    *,
    code: str,
    name: str,
    subject_code: str,
    grade: int,
) -> tuple[uuid.UUID, int]:
    existing_id = await session.scalar(
        select(CurriculumVersion.id).where(
            CurriculumVersion.tenant_id == SYSTEM_PUBLIC_TENANT_ID,
            CurriculumVersion.code == code,
        )
    )
    if existing_id is not None:
        return existing_id, 0
    created = await create_curriculum_version(
        session,
        tenant_id=SYSTEM_PUBLIC_TENANT_ID,
        code=code,
        name=name,
        subject_code=subject_code,
        grade_band=f"GRADE_{grade}",
        version_label="KNTT",
        visibility_scope="SYSTEM_PUBLIC",
        status="PUBLISHED",
    )
    return created.id, 1


async def _ensure_curriculum_node(
    session: AsyncSession,
    *,
    curriculum_version_id: uuid.UUID,
    node_level: str,
    code: str,
    title: str,
    parent_id: uuid.UUID | None,
    sequence_order: int,
) -> tuple[uuid.UUID, int]:
    existing_id = await session.scalar(
        select(CurriculumNode.id).where(
            CurriculumNode.curriculum_version_id == curriculum_version_id,
            CurriculumNode.code == code,
        )
    )
    if existing_id is not None:
        return existing_id, 0
    created = await create_curriculum_node(
        session,
        tenant_id=SYSTEM_PUBLIC_TENANT_ID,
        curriculum_version_id=curriculum_version_id,
        node_level=node_level,
        code=code,
        title=title,
        parent_id=parent_id,
        sequence_order=sequence_order,
    )
    return created.id, 1


async def _ensure_mapping(
    session: AsyncSession,
    *,
    curriculum_node_id: uuid.UUID,
    taxonomy_node_id: uuid.UUID,
) -> int:
    existing_id = await session.scalar(
        select(TaxonomyMapping.id).where(
            TaxonomyMapping.curriculum_node_id == curriculum_node_id,
            TaxonomyMapping.taxonomy_node_id == taxonomy_node_id,
        )
    )
    if existing_id is not None:
        return 0
    await map_curriculum_to_taxonomy(
        session,
        tenant_id=SYSTEM_PUBLIC_TENANT_ID,
        curriculum_node_id=curriculum_node_id,
        taxonomy_node_id=taxonomy_node_id,
        mapping_weight=Decimal("1.00"),
    )
    return 1


async def _seed_subject_book(
    session: AsyncSession,
    *,
    grade: int,
    subject: dict,
    subject_root: TaxonomyNodeRecord,
) -> SeedKnttResult:
    slug = subject["slug"]
    subject_code = subject["subject_code"]

    grade_topic, tax_cnt = await _ensure_taxonomy_node(
        session, code=slug, node_type="TOPIC", name=subject["name"], parent=subject_root
    )
    cv_id, cv_cnt = await _ensure_curriculum_version(
        session,
        code=f"kntt-{slug}",
        name=f"{subject['name']} - Kết nối tri thức",
        subject_code=subject_code,
        grade=grade,
    )

    cn_cnt = 0
    map_cnt = 0
    for ch_idx, chapter in enumerate(subject.get("chapters", []), start=1):
        ch_tax_code = f"{slug}.{chapter['id']}"
        ch_tax, added = await _ensure_taxonomy_node(
            session, code=ch_tax_code, node_type="TOPIC", name=chapter["title"], parent=grade_topic
        )
        tax_cnt += added

        ch_cn_id, added = await _ensure_curriculum_node(
            session,
            curriculum_version_id=cv_id,
            node_level="CHAPTER",
            code=chapter["id"],
            title=chapter["title"],
            parent_id=None,
            sequence_order=ch_idx,
        )
        cn_cnt += added
        map_cnt += await _ensure_mapping(session, curriculum_node_id=ch_cn_id, taxonomy_node_id=ch_tax.id)

        for l_idx, lesson in enumerate(chapter.get("lessons", []), start=1):
            l_tax, added = await _ensure_taxonomy_node(
                session,
                code=f"{ch_tax_code}.{lesson['id']}",
                node_type="CONCEPT",
                name=lesson["title"],
                parent=ch_tax,
            )
            tax_cnt += added

            l_cn_id, added = await _ensure_curriculum_node(
                session,
                curriculum_version_id=cv_id,
                node_level="LESSON",
                code=f"{chapter['id']}.{lesson['id']}",
                title=lesson["title"],
                parent_id=ch_cn_id,
                sequence_order=l_idx,
            )
            cn_cnt += added
            map_cnt += await _ensure_mapping(session, curriculum_node_id=l_cn_id, taxonomy_node_id=l_tax.id)

    return SeedKnttResult(
        taxonomy_nodes_created=tax_cnt,
        curriculum_versions_created=cv_cnt,
        curriculum_nodes_created=cn_cnt,
        mappings_created=map_cnt,
    )


async def seed_kntt_math_physics(session: AsyncSession) -> SeedKnttResult:
    """Seed SGK Ket noi tri thuc (KNTT) Math & Physics Grades 10-12 into SYSTEM_PUBLIC."""
    tax_created = 0
    cv_created = 0
    cn_created = 0
    map_created = 0

    domain_node, added = await _ensure_taxonomy_node(
        session, code="k12", node_type="DOMAIN", name="GDPT 2018 - K12", parent=None
    )
    tax_created += added

    subject_nodes: dict[str, TaxonomyNodeRecord] = {}
    for subject_code, subject_name in SUBJECT_TITLES.items():
        sub_node, added = await _ensure_taxonomy_node(
            session, code=f"k12.{subject_code.lower()}", node_type="SUBJECT", name=subject_name, parent=domain_node
        )
        tax_created += added
        subject_nodes[subject_code] = sub_node

    for grade_entry in get_master_k12_catalog():
        grade = grade_entry.get("grade")
        if grade not in TARGET_GRADES:
            continue
        for subject in grade_entry.get("subjects", []):
            if subject["slug"] not in TARGET_SUBJECT_SLUGS:
                continue
            res = await _seed_subject_book(
                session,
                grade=grade,
                subject=subject,
                subject_root=subject_nodes[subject["subject_code"]],
            )
            tax_created += res.taxonomy_nodes_created
            cv_created += res.curriculum_versions_created
            cn_created += res.curriculum_nodes_created
            map_created += res.mappings_created

    return SeedKnttResult(
        taxonomy_nodes_created=tax_created,
        curriculum_versions_created=cv_created,
        curriculum_nodes_created=cn_created,
        mappings_created=map_created,
    )


async def _cli_main() -> None:
    async with tenant_transaction(SYSTEM_PUBLIC_TENANT_ID, role=DbRole.APP_RW) as session:
        result = await seed_kntt_math_physics(session)
        print(
            f"Seeded KNTT Math/Physics (10-12): taxonomy_nodes={result.taxonomy_nodes_created}, "
            f"curriculum_versions={result.curriculum_versions_created}, "
            f"curriculum_nodes={result.curriculum_nodes_created}, mappings={result.mappings_created}"
        )


if __name__ == "__main__":
    asyncio.run(_cli_main())
