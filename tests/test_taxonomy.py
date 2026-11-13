"""Tests for B04 taxonomy module: hybrid RLS, immutable id/code_path versioning and ltree <@ subtrees."""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import SessionLocal
from app.modules.taxonomy.public import (
    add_taxonomy_alias,
    create_node_version,
    create_taxonomy_node,
    list_node_versions,
    list_subtree,
)
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID, Tenant
from app.platform.tenancy import DbRole, tenant_transaction


def run(coro):
    return asyncio.run(coro)


async def _create_tenants() -> tuple[uuid.UUID, uuid.UUID]:
    suffix = uuid.uuid4().hex[:8]
    async with SessionLocal() as session, session.begin():
        tenant_a = Tenant(slug=f"tax-a-{suffix}", name="Tenant A")
        tenant_b = Tenant(slug=f"tax-b-{suffix}", name="Tenant B")
        session.add_all([tenant_a, tenant_b])
        await session.flush()
        return tenant_a.id, tenant_b.id


def test_hybrid_rls_system_public_vs_tenant_private_runtime_role():
    async def scenario():
        tenant_a, tenant_b = await _create_tenants()

        async with tenant_transaction(SYSTEM_PUBLIC_TENANT_ID, role=DbRole.APP_RW) as session:
            active_pub, _ = await create_taxonomy_node(
                session,
                tenant_id=SYSTEM_PUBLIC_TENANT_ID,
                node_type="SUBJECT",
                code="pub-math",
                name="Public Math",
                visibility_scope="SYSTEM_PUBLIC",
                is_active=True,
            )
            await add_taxonomy_alias(
                session,
                tenant_id=SYSTEM_PUBLIC_TENANT_ID,
                taxonomy_node_id=active_pub.id,
                alias_name="Toán THPT",
            )
            retired_pub, _ = await create_taxonomy_node(
                session,
                tenant_id=SYSTEM_PUBLIC_TENANT_ID,
                node_type="SUBJECT",
                code="pub-retired",
                name="Retired Public Subject",
                visibility_scope="SYSTEM_PUBLIC",
                is_active=False,
            )

        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            priv_a, _ = await create_taxonomy_node(
                session,
                tenant_id=tenant_a,
                node_type="TOPIC",
                code="tenant-a-topic",
                name="Tenant A Secret Topic",
                visibility_scope="TENANT_PRIVATE",
            )
            await add_taxonomy_alias(
                session,
                tenant_id=tenant_a,
                taxonomy_node_id=priv_a.id,
                alias_name="Secret Alias A",
            )

        async with tenant_transaction(tenant_b, role=DbRole.APP_RW) as session:
            visible_codes = (
                await session.scalars(text("SELECT code FROM taxonomy_nodes ORDER BY code"))
            ).all()
            visible_versions = (
                await session.scalars(text("SELECT name FROM taxonomy_node_versions ORDER BY name"))
            ).all()
            visible_aliases = (
                await session.scalars(text("SELECT normalized_alias FROM taxonomy_aliases"))
            ).all()
            updated_pub = (
                await session.execute(
                    text("UPDATE taxonomy_nodes SET is_active = false WHERE id = :id"),
                    {"id": active_pub.id},
                )
            ).rowcount
            deleted_pub = (
                await session.execute(
                    text("DELETE FROM taxonomy_nodes WHERE id = :id"),
                    {"id": active_pub.id},
                )
            ).rowcount

        return {
            "active_pub_id": active_pub.id,
            "retired_pub_id": retired_pub.id,
            "priv_a_id": priv_a.id,
            "visible_codes": list(visible_codes),
            "visible_versions": list(visible_versions),
            "visible_aliases": list(visible_aliases),
            "updated_pub": updated_pub,
            "deleted_pub": deleted_pub,
            "tenant_b": tenant_b,
        }

    result = run(scenario())
    assert result["visible_codes"] == ["pub-math"]
    assert result["visible_versions"] == ["Public Math"]
    assert result["visible_aliases"] == ["toán thpt"]
    assert result["updated_pub"] == 0
    assert result["deleted_pub"] == 0

    async def write_system_from_tenant_b():
        async with tenant_transaction(result["tenant_b"], role=DbRole.APP_RW) as session:
            await create_taxonomy_node(
                session,
                tenant_id=SYSTEM_PUBLIC_TENANT_ID,
                node_type="DOMAIN",
                code="smuggled-sys",
                name="Smuggled",
                visibility_scope="SYSTEM_PUBLIC",
            )

    with pytest.raises(DBAPIError, match="row-level security"):
        run(write_system_from_tenant_b())


def test_new_version_preserves_id_and_code_path():
    async def scenario():
        tenant_a, _ = await _create_tenants()
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            v1_node, v1 = await create_taxonomy_node(
                session,
                tenant_id=tenant_a,
                node_type="CONCEPT",
                code="bayes-theorem",
                name="Định lí Bayes v1",
                description_md="Initial description",
                visibility_scope="TENANT_PRIVATE",
            )
            v2_node, v2 = await create_node_version(
                session,
                taxonomy_node_id=v1_node.id,
                name="Định lí Bayes v2",
                description_md="Updated formulation",
            )
            versions = await list_node_versions(session, v1_node.id)
        return v1_node, v1, v2_node, v2, versions

    v1_node, v1, v2_node, v2, versions = run(scenario())
    assert v1.version_no == 1
    assert v2.version_no == 2
    assert v2_node.id == v1_node.id
    assert v2_node.code_path == v1_node.code_path == "bayes_theorem"
    assert v2_node.current_version_no == 2
    assert [v.version_no for v in versions] == [1, 2]
    assert [v.name for v in versions] == ["Định lí Bayes v1", "Định lí Bayes v2"]


def test_ltree_subtree_query_returns_exact_descendants():
    async def scenario():
        tenant_a, _ = await _create_tenants()
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            math, _ = await create_taxonomy_node(
                session,
                tenant_id=tenant_a,
                node_type="SUBJECT",
                code="math",
                name="Math",
            )
            algebra, _ = await create_taxonomy_node(
                session,
                tenant_id=tenant_a,
                node_type="TOPIC",
                code="algebra",
                name="Algebra",
                parent_id=math.id,
            )
            quad, _ = await create_taxonomy_node(
                session,
                tenant_id=tenant_a,
                node_type="CONCEPT",
                code="quadratic-eq",
                name="Quadratic Equations",
                parent_id=algebra.id,
            )
            geom, _ = await create_taxonomy_node(
                session,
                tenant_id=tenant_a,
                node_type="TOPIC",
                code="geometry",
                name="Geometry",
                parent_id=math.id,
            )
            physics, _ = await create_taxonomy_node(
                session,
                tenant_id=tenant_a,
                node_type="SUBJECT",
                code="physics",
                name="Physics",
            )
            await create_taxonomy_node(
                session,
                tenant_id=tenant_a,
                node_type="TOPIC",
                code="mechanics",
                name="Mechanics",
                parent_id=physics.id,
            )

            with_root = await list_subtree(session, algebra.code_path, include_root=True)
            strict_desc = await list_subtree(session, algebra.code_path, include_root=False)
            all_math = await list_subtree(session, math.code_path, include_root=True)

        return (
            [n.code_path for n in with_root],
            [n.code_path for n in strict_desc],
            [n.code_path for n in all_math],
            quad.code_path,
            geom.code_path,
        )

    with_root, strict_desc, all_math, quad_path, geom_path = run(scenario())
    assert quad_path == "math.algebra.quadratic_eq"
    assert geom_path == "math.geometry"
    assert with_root == ["math.algebra", "math.algebra.quadratic_eq"]
    assert strict_desc == ["math.algebra.quadratic_eq"]
    assert all_math == ["math", "math.algebra", "math.algebra.quadratic_eq", "math.geometry"]

