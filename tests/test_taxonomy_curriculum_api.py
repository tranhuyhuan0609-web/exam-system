"""Integration tests for B04 taxonomy & B06 curriculum API routers."""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import SessionLocal
from app.modules.curriculum.public import (
    get_curriculum_version_by_code,
    list_mappings_for_taxonomy_node,
    seed_kntt_math_physics,
)
from app.modules.curriculum.router import router as curriculum_router
from app.modules.taxonomy.public import list_node_versions
from app.modules.taxonomy.router import router as taxonomy_router
from app.platform.auth_context import mint_hs256_token
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID, Tenant
from app.platform.tenancy import DbRole, tenant_transaction


def run(coro):
    return asyncio.run(coro)


def _api_client() -> TestClient:
    test_app = FastAPI()
    test_app.include_router(taxonomy_router)
    test_app.include_router(curriculum_router)
    return TestClient(test_app)


def _auth_headers(
    *,
    tenant_id: uuid.UUID,
    roles: list[str],
    user_id: uuid.UUID | None = None,
    membership_id: uuid.UUID | None = None,
) -> dict[str, str]:
    settings = get_settings()
    token = mint_hs256_token(
        secret=settings.secret_key,
        user_id=user_id or uuid.uuid4(),
        tenant_id=tenant_id,
        membership_id=membership_id or uuid.uuid4(),
        roles=roles,
        issued_at=int(time.time()),
        ttl_seconds=900,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def tenants() -> tuple[uuid.UUID, uuid.UUID]:
    async def create() -> tuple[uuid.UUID, uuid.UUID]:
        suffix = uuid.uuid4().hex[:8]
        async with SessionLocal() as session, session.begin():
            a = Tenant(slug=f"tax-curr-a-{suffix}", name="Tax/Curr Tenant A")
            b = Tenant(slug=f"tax-curr-b-{suffix}", name="Tax/Curr Tenant B")
            session.add_all([a, b])
            await session.flush()
            return a.id, b.id

    return run(create())


def test_taxonomy_curriculum_auth_roles_and_anti_spoofing(tenants: tuple[uuid.UUID, uuid.UUID]):
    tenant_a, tenant_b = tenants
    client = _api_client()

    # 1. Missing token -> 401 RFC 7807 Problem+JSON
    unauth = client.get("/api/v1/taxonomy/nodes", headers={"X-Correlation-Id": "req-tax-unauth-1"})
    assert unauth.status_code == 401
    assert unauth.headers["content-type"].startswith("application/problem+json")
    assert unauth.json()["error_code"] == "UNAUTHORIZED"

    unauth_curr = client.post(
        "/api/v1/curricula",
        json={
            "code": "unauth-curr",
            "name": "No Auth",
            "subject_code": "MATH",
            "grade_band": "GRADE_12",
        },
    )
    assert unauth_curr.status_code == 401

    # 2. Spoofed X-Tenant-Id header -> 403 FORBIDDEN
    teacher_a = _auth_headers(tenant_id=tenant_a, roles=["TEACHER"])
    spoof_resp = client.get(
        "/api/v1/taxonomy/nodes",
        headers={**teacher_a, "X-Tenant-Id": str(tenant_b)},
    )
    assert spoof_resp.status_code == 403
    assert spoof_resp.json()["error_code"] == "FORBIDDEN"

    # 3. STUDENT role can read but cannot write
    student_a = _auth_headers(tenant_id=tenant_a, roles=["STUDENT"])
    assert client.get("/api/v1/taxonomy/nodes", headers=student_a).status_code == 200
    forbid_node = client.post(
        "/api/v1/taxonomy/nodes",
        headers=student_a,
        json={"node_type": "SUBJECT", "code": "math", "name": "Math"},
    )
    assert forbid_node.status_code == 403

    forbid_curr = client.post(
        "/api/v1/curricula",
        headers=student_a,
        json={"code": "c1", "name": "C1", "subject_code": "MATH", "grade_band": "GRADE_10"},
    )
    assert forbid_curr.status_code == 403

    # 4. Non-system tenant cannot publish SYSTEM_PUBLIC
    sys_forbid = client.post(
        "/api/v1/taxonomy/nodes",
        headers=teacher_a,
        json={
            "node_type": "DOMAIN",
            "code": "forged-sys",
            "name": "Forged System",
            "visibility_scope": "SYSTEM_PUBLIC",
        },
    )
    assert sys_forbid.status_code == 403

    curr_sys_forbid = client.post(
        "/api/v1/curricula",
        headers=teacher_a,
        json={
            "code": "forged-sys-curr",
            "name": "Forged Sys Curr",
            "subject_code": "MATH",
            "grade_band": "GRADE_10",
            "visibility_scope": "SYSTEM_PUBLIC",
        },
    )
    assert curr_sys_forbid.status_code == 403

    # 5. Extra tenant_id in body rejected by schema extra="forbid"
    body_spoof = client.post(
        "/api/v1/taxonomy/nodes",
        headers=teacher_a,
        json={"node_type": "SUBJECT", "code": "math", "name": "Math", "tenant_id": str(tenant_b)},
    )
    assert body_spoof.status_code == 422


def test_taxonomy_ltree_subtree_pagination_and_multi_tenant_rls(tenants: tuple[uuid.UUID, uuid.UUID]):
    tenant_a, tenant_b = tenants
    client = _api_client()
    sys_admin = _auth_headers(tenant_id=SYSTEM_PUBLIC_TENANT_ID, roles=["ADMIN"])
    teacher_a = _auth_headers(tenant_id=tenant_a, roles=["TEACHER"])
    teacher_b = _auth_headers(tenant_id=tenant_b, roles=["TEACHER"])

    # Seed active and inactive SYSTEM_PUBLIC nodes via API
    pub_root = client.post(
        "/api/v1/taxonomy/nodes",
        headers=sys_admin,
        json={
            "node_type": "DOMAIN",
            "code": "pub-k12",
            "name": "Public K12",
            "code_path": "pub_k12",
            "visibility_scope": "SYSTEM_PUBLIC",
            "is_active": True,
        },
    )
    assert pub_root.status_code == 201
    pub_inactive = client.post(
        "/api/v1/taxonomy/nodes",
        headers=sys_admin,
        json={
            "node_type": "SUBJECT",
            "code": "pub-retired",
            "name": "Retired Public",
            "code_path": "pub_retired",
            "visibility_scope": "SYSTEM_PUBLIC",
            "is_active": False,
        },
    )
    assert pub_inactive.status_code == 201

    # Tenant A builds math -> math.prob -> math.prob.bayes and math.geom
    math_res = client.post(
        "/api/v1/taxonomy/nodes",
        headers=teacher_a,
        json={"node_type": "SUBJECT", "code": "math", "name": "Toán học", "aliases": ["Toán THPT"]},
    )
    assert math_res.status_code == 201
    math_id = math_res.json()["node"]["id"]

    prob_res = client.post(
        "/api/v1/taxonomy/nodes",
        headers=teacher_a,
        json={"node_type": "TOPIC", "code": "prob", "name": "Xác suất", "parent_id": math_id},
    )
    assert prob_res.status_code == 201
    prob_id = prob_res.json()["node"]["id"]

    bayes_res = client.post(
        "/api/v1/taxonomy/nodes",
        headers=teacher_a,
        json={"node_type": "CONCEPT", "code": "bayes", "name": "Công thức Bayes", "parent_id": prob_id},
    )
    assert bayes_res.status_code == 201
    assert bayes_res.json()["node"]["code_path"] == "math.prob.bayes"

    geom_res = client.post(
        "/api/v1/taxonomy/nodes",
        headers=teacher_a,
        json={"node_type": "TOPIC", "code": "geom", "name": "Hình học", "parent_id": math_id},
    )
    assert geom_res.status_code == 201

    # Duplicate code within same tenant -> 409 Conflict
    dup_res = client.post(
        "/api/v1/taxonomy/nodes",
        headers=teacher_a,
        json={"node_type": "TOPIC", "code": "geom", "name": "Duplicate Geom"},
    )
    assert dup_res.status_code == 409

    # Tenant B builds private node; must remain invisible to Tenant A
    bio_res = client.post(
        "/api/v1/taxonomy/nodes",
        headers=teacher_b,
        json={"node_type": "SUBJECT", "code": "bio", "name": "Sinh học B"},
    )
    assert bio_res.status_code == 201

    # ltree <@ query on Tenant A subtree
    subtree_with_root = client.get(
        "/api/v1/taxonomy/nodes",
        headers=teacher_a,
        params={"root_path": "math.prob", "include_root": "true"},
    )
    assert subtree_with_root.status_code == 200
    assert [n["code_path"] for n in subtree_with_root.json()["items"]] == ["math.prob", "math.prob.bayes"]

    strict_desc = client.get(
        "/api/v1/taxonomy/nodes",
        headers=teacher_a,
        params={"root_path": "math.prob", "include_root": "false"},
    )
    assert strict_desc.status_code == 200
    assert [n["code_path"] for n in strict_desc.json()["items"]] == ["math.prob.bayes"]

    # Invalid ltree root_path -> 422
    bad_path = client.get(
        "/api/v1/taxonomy/nodes",
        headers=teacher_a,
        params={"root_path": "math..bad!"},
    )
    assert bad_path.status_code == 422

    # Keyset cursor pagination across Tenant A math subtree
    p1 = client.get(
        "/api/v1/taxonomy/nodes",
        headers=teacher_a,
        params={"root_path": "math", "limit": 2},
    )
    assert p1.status_code == 200
    body_p1 = p1.json()
    assert len(body_p1["items"]) == 2
    assert body_p1["has_more"] is True
    cursor = body_p1["next_cursor"]
    assert cursor is not None

    # Tenant B reusing Tenant A's cursor must be rejected
    forged_cursor = client.get(
        "/api/v1/taxonomy/nodes",
        headers=teacher_b,
        params={"root_path": "math", "cursor": cursor},
    )
    assert forged_cursor.status_code == 422

    p2 = client.get(
        "/api/v1/taxonomy/nodes",
        headers=teacher_a,
        params={"root_path": "math", "limit": 2, "cursor": cursor},
    )
    assert p2.status_code == 200
    all_math_paths = [n["code_path"] for n in body_p1["items"] + p2.json()["items"]]
    assert all_math_paths == ["math", "math.geom", "math.prob", "math.prob.bayes"]
    assert p2.json()["has_more"] is False

    # Tenant B RLS visibility check: sees pub_k12 + bio, never pub-retired or Tenant A math
    b_all = client.get("/api/v1/taxonomy/nodes", headers=teacher_b)
    assert b_all.status_code == 200
    b_codes = {n["code"] for n in b_all.json()["items"]}
    assert b_codes == {"pub-k12", "bio"}


def test_node_version_upgrade_preserves_id_code_path_and_curriculum_links(
    tenants: tuple[uuid.UUID, uuid.UUID],
):
    tenant_a, tenant_b = tenants
    client = _api_client()
    sys_admin = _auth_headers(tenant_id=SYSTEM_PUBLIC_TENANT_ID, roles=["ADMIN"])
    teacher_a = _auth_headers(tenant_id=tenant_a, roles=["TEACHER"])
    teacher_b = _auth_headers(tenant_id=tenant_b, roles=["TEACHER"])

    # System public node (to verify non-owner cannot version it)
    sys_node = client.post(
        "/api/v1/taxonomy/nodes",
        headers=sys_admin,
        json={
            "node_type": "CONCEPT",
            "code": "sys-derivative",
            "name": "System Derivative",
            "visibility_scope": "SYSTEM_PUBLIC",
        },
    )
    assert sys_node.status_code == 201
    sys_node_id = sys_node.json()["node"]["id"]

    # Tenant A taxonomy node
    node_res = client.post(
        "/api/v1/taxonomy/nodes",
        headers=teacher_a,
        json={
            "node_type": "CONCEPT",
            "code": "bayes-theorem",
            "name": "Định lí Bayes v1",
            "description_md": "Bản gốc",
            "code_path": "math.prob.bayes_theorem",
        },
    )
    assert node_res.status_code == 201
    tax_node = node_res.json()["node"]
    tax_node_id = uuid.UUID(tax_node["id"])

    # Create 4-level curriculum + problem type and map to tax_node_id
    curr_res = client.post(
        "/api/v1/curricula",
        headers=teacher_a,
        json={
            "code": "kntt-12-prob",
            "name": "Toán 12 - Xác suất",
            "subject_code": "MATH",
            "grade_band": "GRADE_12",
            "version_label": "v1",
            "nodes": [
                {
                    "node_level": "CHAPTER",
                    "code": "ch6",
                    "title": "Chương VI: Xác suất có điều kiện",
                    "sequence_order": 1,
                    "children": [
                        {
                            "node_level": "LESSON",
                            "code": "b19",
                            "title": "Công thức Bayes",
                            "sequence_order": 1,
                            "children": [
                                {
                                    "node_level": "SECTION",
                                    "code": "sec1",
                                    "title": "Công thức xác suất toàn phần và Bayes",
                                    "sequence_order": 1,
                                    "children": [
                                        {
                                            "node_level": "PROBLEM_TYPE",
                                            "code": "pt_bayes",
                                            "title": "Tính xác suất hậu nghiệm bằng Bayes",
                                            "sequence_order": 1,
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )
    assert curr_res.status_code == 201
    curr_body = curr_res.json()
    curr_id = curr_body["curriculum"]["id"]
    pt_node = next(n for n in curr_body["nodes"] if n["node_level"] == "PROBLEM_TYPE")

    map_res = client.post(
        "/api/v1/curriculum-mappings",
        headers=teacher_a,
        json={
            "curriculum_node_id": pt_node["id"],
            "taxonomy_node_id": tax_node["id"],
            "mapping_weight": "0.85",
        },
    )
    assert map_res.status_code == 201
    assert map_res.json()["mapping_weight"] == "0.85"

    # Duplicate mapping rejected with 409
    dup_map = client.post(
        "/api/v1/curriculum-mappings",
        headers=teacher_a,
        json={"curriculum_node_id": pt_node["id"], "taxonomy_node_id": tax_node["id"]},
    )
    assert dup_map.status_code == 409

    # Cross-tenant mapping attempts rejected
    assert (
        client.post(
            "/api/v1/curriculum-mappings",
            headers=teacher_b,
            json={"curriculum_node_id": pt_node["id"], "taxonomy_node_id": sys_node_id},
        ).status_code
        == 404
    )

    # Upgrade Tenant A taxonomy node to version 2
    v2_res = client.post(
        f"/api/v1/taxonomy/nodes/{tax_node['id']}/versions",
        headers=teacher_a,
        json={"name": "Công thức Bayes (Cập nhật v2)", "description_md": "Bổ sung ví dụ y sinh"},
    )
    assert v2_res.status_code == 201
    v2_body = v2_res.json()
    assert v2_body["node"]["id"] == tax_node["id"]
    assert v2_body["node"]["code_path"] == "math.prob.bayes_theorem"
    assert v2_body["node"]["current_version_no"] == 2
    assert v2_body["node"]["name"] == "Công thức Bayes (Cập nhật v2)"
    assert v2_body["version"]["version_no"] == 2

    # Tenant B cannot version Tenant A private node (404) or SYSTEM_PUBLIC node (403)
    assert (
        client.post(
            f"/api/v1/taxonomy/nodes/{tax_node['id']}/versions",
            headers=teacher_b,
            json={"name": "Tamper"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/taxonomy/nodes/{sys_node_id}/versions",
            headers=teacher_b,
            json={"name": "Tamper Sys"},
        ).status_code
        == 403
    )

    # Verify links stay intact and both versions exist
    list_v2 = client.get(
        "/api/v1/taxonomy/nodes",
        headers=teacher_a,
        params={"root_path": "math.prob.bayes_theorem"},
    )
    assert list_v2.status_code == 200
    assert list_v2.json()["items"][0]["name"] == "Công thức Bayes (Cập nhật v2)"
    assert list_v2.json()["items"][0]["current_version_no"] == 2

    cov = client.get(f"/api/v1/curricula/{curr_id}/coverage", headers=teacher_a)
    assert cov.status_code == 200
    cov_body = cov.json()
    assert cov_body["mapped_node_count"] == 1
    assert cov_body["items"][0]["mapped_taxonomy_node_ids"] == [tax_node["id"]]

    async def verify_db_links():
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            versions = await list_node_versions(session, tax_node_id)
            mappings = await list_mappings_for_taxonomy_node(session, tax_node_id)
            return versions, mappings

    versions, mappings = run(verify_db_links())
    assert [v.version_no for v in versions] == [1, 2]
    assert len(mappings) == 1
    assert str(mappings[0].curriculum_node_id) == pt_node["id"]


def test_curriculum_version_fork_coverage_report_and_kntt_performance(
    tenants: tuple[uuid.UUID, uuid.UUID],
):
    tenant_a, tenant_b = tenants
    client = _api_client()
    teacher_a = _auth_headers(tenant_id=tenant_a, roles=["TEACHER"])
    teacher_b = _auth_headers(tenant_id=tenant_b, roles=["TEACHER"])

    # Create taxonomy node for mapping
    tax_res = client.post(
        "/api/v1/taxonomy/nodes",
        headers=teacher_a,
        json={"node_type": "SKILL", "code": "opt-extreme", "name": "Cực trị hàm số"},
    )
    assert tax_res.status_code == 201
    tax_id = tax_res.json()["node"]["id"]

    # Create v1 curriculum with 2 PROBLEM_TYPE nodes (1 mapped, 1 unmapped)
    v1_res = client.post(
        "/api/v1/curricula",
        headers=teacher_a,
        json={
            "code": "curr-math-12",
            "name": "Toán 12 Chuyên đề",
            "subject_code": "MATH",
            "grade_band": "GRADE_12",
            "version_label": "v1",
            "nodes": [
                {"node_level": "CHAPTER", "code": "c1", "title": "Chương I", "sequence_order": 1},
                {
                    "node_level": "LESSON",
                    "code": "l1",
                    "title": "Bài 1",
                    "parent_code": "c1",
                    "sequence_order": 1,
                },
                {
                    "node_level": "SECTION",
                    "code": "s1",
                    "title": "Mục 1",
                    "parent_code": "l1",
                    "sequence_order": 1,
                },
                {
                    "node_level": "PROBLEM_TYPE",
                    "code": "pt_1",
                    "title": "Dạng 1: Cực trị cơ bản",
                    "parent_code": "s1",
                    "sequence_order": 1,
                },
                {
                    "node_level": "PROBLEM_TYPE",
                    "code": "pt_2",
                    "title": "Dạng 2: Cực trị tham số m",
                    "parent_code": "s1",
                    "sequence_order": 2,
                },
            ],
        },
    )
    assert v1_res.status_code == 201
    v1_body = v1_res.json()
    v1_id = v1_body["curriculum"]["id"]
    pt1_id = next(n["id"] for n in v1_body["nodes"] if n["code"] == "pt_1")

    assert (
        client.post(
            "/api/v1/curriculum-mappings",
            headers=teacher_a,
            json={"curriculum_node_id": pt1_id, "taxonomy_node_id": tax_id},
        ).status_code
        == 201
    )

    # CoverageReport on v1
    cov_v1 = client.get(f"/api/v1/curricula/{v1_id}/coverage", headers=teacher_a)
    assert cov_v1.status_code == 200
    report_v1 = cov_v1.json()
    assert report_v1["total_nodes"] == 5
    assert report_v1["reported_node_count"] == 2
    assert report_v1["total_problem_types"] == 2
    assert report_v1["mapped_node_count"] == 1
    assert report_v1["unmapped_node_count"] == 1
    assert report_v1["mapping_coverage_ratio"] == 0.5
    assert report_v1["question_counts"] == {"NB": 0, "TH": 0, "VD": 0, "VDC": 0, "total": 0}
    assert report_v1["question_bank_available"] is False
    assert report_v1["generated_in_ms"] < 200.0

    by_code = {item["code"]: item for item in report_v1["items"]}
    assert by_code["pt_1"]["is_mapped"] is True
    assert by_code["pt_1"]["mapped_taxonomy_node_ids"] == [tax_id]
    assert by_code["pt_2"]["is_mapped"] is False
    assert by_code["pt_2"]["mapped_taxonomy_node_ids"] == []

    # Fork v2 preserving 4-level hierarchy and taxonomy mappings
    v2_res = client.post(
        f"/api/v1/curricula/{v1_id}/versions",
        headers=teacher_a,
        json={"version_label": "v2", "status": "PUBLISHED"},
    )
    assert v2_res.status_code == 201
    v2_body = v2_res.json()
    v2_id = v2_body["curriculum"]["id"]
    assert v2_id != v1_id
    assert v2_body["curriculum"]["code"] == "curr-math-12@v2"
    assert len(v2_body["nodes"]) == 5

    cov_v2 = client.get(f"/api/v1/curricula/{v2_id}/coverage", headers=teacher_a)
    assert cov_v2.status_code == 200
    assert cov_v2.json()["mapped_node_count"] == 1
    assert cov_v2.json()["unmapped_node_count"] == 1
    assert cov_v2.json()["generated_in_ms"] < 200.0

    # Cross-tenant RLS blocks Tenant B from v1 coverage or forking v1
    assert client.get(f"/api/v1/curricula/{v1_id}/coverage", headers=teacher_b).status_code == 404
    assert (
        client.post(
            f"/api/v1/curricula/{v1_id}/versions",
            headers=teacher_b,
            json={"version_label": "stolen"},
        ).status_code
        == 404
    )

    # Seed full KNTT catalog into SYSTEM_PUBLIC and verify Tenant B CoverageReport < 200ms
    async def seed_and_lookup() -> uuid.UUID:
        async with tenant_transaction(SYSTEM_PUBLIC_TENANT_ID, role=DbRole.APP_RW) as session:
            await seed_kntt_math_physics(session)
            kntt = await get_curriculum_version_by_code(
                session, code="kntt-toan-12", tenant_id=SYSTEM_PUBLIC_TENANT_ID
            )
            assert kntt is not None
            return kntt.id

    kntt_id = run(seed_and_lookup())
    kntt_cov = client.get(f"/api/v1/curricula/{kntt_id}/coverage", headers=teacher_b)
    assert kntt_cov.status_code == 200
    kntt_body = kntt_cov.json()
    assert kntt_body["reported_node_count"] > 10
    assert kntt_body["mapped_node_count"] == kntt_body["reported_node_count"]
    assert kntt_body["mapping_coverage_ratio"] == 1.0
    assert kntt_body["generated_in_ms"] < 200.0
