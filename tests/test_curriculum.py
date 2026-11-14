"""Tests for B06 curriculum module: hybrid RLS, many-to-many taxonomy mappings and KNTT seeding."""

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import SessionLocal
from app.modules.curriculum.public import (
    create_curriculum_node,
    create_curriculum_version,
    get_curriculum_version_by_code,
    list_curriculum_nodes,
    list_mappings_for_curriculum_node,
    list_mappings_for_taxonomy_node,
    map_curriculum_to_taxonomy,
    seed_kntt_math_physics,
)
from app.modules.taxonomy.public import create_taxonomy_node, list_subtree
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID, Tenant
from app.platform.tenancy import DbRole, tenant_transaction


def run(coro):
    return asyncio.run(coro)


async def _create_tenants() -> tuple[uuid.UUID, uuid.UUID]:
    suffix = uuid.uuid4().hex[:8]
    async with SessionLocal() as session, session.begin():
        tenant_a = Tenant(slug=f"curr-a-{suffix}", name="Curriculum Tenant A")
        tenant_b = Tenant(slug=f"curr-b-{suffix}", name="Curriculum Tenant B")
        session.add_all([tenant_a, tenant_b])
        await session.flush()
        return tenant_a.id, tenant_b.id


def test_curriculum_hybrid_rls_and_many_to_many_mappings():
    async def scenario():
        tenant_a, tenant_b = await _create_tenants()

        async with tenant_transaction(SYSTEM_PUBLIC_TENANT_ID, role=DbRole.APP_RW) as session:
            pub_tax, _ = await create_taxonomy_node(
                session,
                tenant_id=SYSTEM_PUBLIC_TENANT_ID,
                node_type="CONCEPT",
                code="sys-bayes",
                name="System Bayes",
                visibility_scope="SYSTEM_PUBLIC",
                is_active=True,
            )
            pub_cv = await create_curriculum_version(
                session,
                tenant_id=SYSTEM_PUBLIC_TENANT_ID,
                code="sys-published-book",
                name="Published System Curriculum",
                subject_code="MATH",
                grade_band="GRADE_12",
                version_label="SYS-1",
                visibility_scope="SYSTEM_PUBLIC",
                status="PUBLISHED",
            )
            await create_curriculum_node(
                session,
                tenant_id=SYSTEM_PUBLIC_TENANT_ID,
                curriculum_version_id=pub_cv.id,
                node_level="CHAPTER",
                code="c1",
                title="System Chapter 1",
                sequence_order=1,
            )
            await create_curriculum_version(
                session,
                tenant_id=SYSTEM_PUBLIC_TENANT_ID,
                code="sys-draft-book",
                name="Draft System Curriculum",
                subject_code="MATH",
                grade_band="GRADE_12",
                version_label="SYS-DRAFT",
                visibility_scope="SYSTEM_PUBLIC",
                status="DRAFT",
            )

        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            priv_tax, _ = await create_taxonomy_node(
                session,
                tenant_id=tenant_a,
                node_type="SKILL",
                code="priv-bayesian-tree",
                name="Tenant A Bayesian Skill",
                visibility_scope="TENANT_PRIVATE",
            )
            priv_cv = await create_curriculum_version(
                session,
                tenant_id=tenant_a,
                code="tenant-a-book",
                name="Tenant A Custom Curriculum",
                subject_code="MATH",
                grade_band="GRADE_12",
                version_label="CUSTOM",
                visibility_scope="TENANT_PRIVATE",
                status="PUBLISHED",
            )
            cn_1 = await create_curriculum_node(
                session,
                tenant_id=tenant_a,
                curriculum_version_id=priv_cv.id,
                node_level="LESSON",
                code="l1",
                title="Custom Bayes Lesson",
                sequence_order=1,
            )
            cn_2 = await create_curriculum_node(
                session,
                tenant_id=tenant_a,
                curriculum_version_id=priv_cv.id,
                node_level="LESSON",
                code="l2",
                title="Custom Practice Lesson",
                sequence_order=2,
            )
            # Many-to-many: cn_1 -> (pub_tax, priv_tax), and pub_tax <- (cn_1, cn_2).
            await map_curriculum_to_taxonomy(
                session,
                tenant_id=tenant_a,
                curriculum_node_id=cn_1.id,
                taxonomy_node_id=pub_tax.id,
                mapping_weight=Decimal("0.70"),
            )
            await map_curriculum_to_taxonomy(
                session,
                tenant_id=tenant_a,
                curriculum_node_id=cn_1.id,
                taxonomy_node_id=priv_tax.id,
                mapping_weight=Decimal("0.30"),
            )
            await map_curriculum_to_taxonomy(
                session,
                tenant_id=tenant_a,
                curriculum_node_id=cn_2.id,
                taxonomy_node_id=pub_tax.id,
                mapping_weight=Decimal("1.00"),
            )

            cn1_maps = await list_mappings_for_curriculum_node(session, cn_1.id)
            pub_tax_maps = await list_mappings_for_taxonomy_node(session, pub_tax.id)

        async with tenant_transaction(tenant_b, role=DbRole.APP_RW) as session:
            b_cv_codes = (await session.scalars(text("SELECT code FROM curriculum_versions ORDER BY code"))).all()
            b_cn_codes = (await session.scalars(text("SELECT code FROM curriculum_nodes ORDER BY code"))).all()
            b_mappings = (await session.scalars(text("SELECT id FROM taxonomy_mappings"))).all()

        return {
            "pub_tax_id": pub_tax.id,
            "priv_tax_id": priv_tax.id,
            "cn1_id": cn_1.id,
            "cn2_id": cn_2.id,
            "cn1_maps": cn1_maps,
            "pub_tax_maps": pub_tax_maps,
            "b_cv_codes": list(b_cv_codes),
            "b_cn_codes": list(b_cn_codes),
            "b_mappings": list(b_mappings),
            "tenant_b": tenant_b,
        }

    result = run(scenario())
    assert [m.taxonomy_node_id for m in result["cn1_maps"]] == [
        result["pub_tax_id"],
        result["priv_tax_id"],
    ]
    assert [m.curriculum_node_id for m in result["pub_tax_maps"]] == [
        result["cn2_id"],
        result["cn1_id"],
    ]
    assert result["b_cv_codes"] == ["sys-published-book"]
    assert result["b_cn_codes"] == ["c1"]
    assert result["b_mappings"] == []

    async def write_system_curriculum_as_tenant_b():
        async with tenant_transaction(result["tenant_b"], role=DbRole.APP_RW) as session:
            await create_curriculum_version(
                session,
                tenant_id=SYSTEM_PUBLIC_TENANT_ID,
                code="smuggled-cv",
                name="Smuggled",
                subject_code="MATH",
                grade_band="GRADE_10",
                version_label="X",
                visibility_scope="SYSTEM_PUBLIC",
                status="PUBLISHED",
            )

    with pytest.raises(DBAPIError, match="row-level security"):
        run(write_system_curriculum_as_tenant_b())


def test_seed_kntt_math_physics_grades_10_to_12_and_idempotency():
    expected_books = [
        "kntt-toan-10",
        "kntt-vat-li-10",
        "kntt-toan-11",
        "kntt-vat-li-11",
        "kntt-toan-12",
        "kntt-vat-li-12",
    ]

    async def scenario():
        _, tenant_b = await _create_tenants()

        async with tenant_transaction(SYSTEM_PUBLIC_TENANT_ID, role=DbRole.APP_RW) as session:
            first_run = await seed_kntt_math_physics(session)
        async with tenant_transaction(SYSTEM_PUBLIC_TENANT_ID, role=DbRole.APP_RW) as session:
            second_run = await seed_kntt_math_physics(session)

        async with tenant_transaction(tenant_b, role=DbRole.APP_RW) as session:
            visible_books = (await session.scalars(text("SELECT code FROM curriculum_versions ORDER BY code"))).all()
            math12_cv = await get_curriculum_version_by_code(
                session, code="kntt-toan-12", tenant_id=SYSTEM_PUBLIC_TENANT_ID
            )
            assert math12_cv is not None
            math12_nodes = await list_curriculum_nodes(session, math12_cv.id)
            phys12_subtree = await list_subtree(session, "k12.physics.vat_li_12")
            first_lesson = next(n for n in math12_nodes if n.node_level == "LESSON")
            lesson_maps = await list_mappings_for_curriculum_node(session, first_lesson.id)

        return first_run, second_run, list(visible_books), math12_nodes, phys12_subtree, lesson_maps

    first_run, second_run, visible_books, math12_nodes, phys12_subtree, lesson_maps = run(scenario())
    assert first_run.curriculum_versions_created == 6
    assert first_run.curriculum_nodes_created > 100
    assert first_run.taxonomy_nodes_created > 100
    assert first_run.mappings_created == first_run.curriculum_nodes_created

    assert second_run.curriculum_versions_created == 0
    assert second_run.curriculum_nodes_created == 0
    assert second_run.taxonomy_nodes_created == 0
    assert second_run.mappings_created == 0

    assert visible_books == sorted(expected_books)
    assert any(n.node_level == "CHAPTER" for n in math12_nodes)
    assert any(n.node_level == "LESSON" for n in math12_nodes)
    assert len(phys12_subtree) > 10
    assert len(lesson_maps) == 1
