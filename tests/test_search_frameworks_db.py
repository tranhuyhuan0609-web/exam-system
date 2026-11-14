"""Integration tests for B07 search/collections & C03 frameworks/blueprints (PostgreSQL + RLS)."""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.config import get_settings
from app.db import SessionLocal, engine
from app.modules.frameworks.internal.models import (  # noqa: F401
    AssessmentFramework,
    Blueprint,
    FrameworkVersion,
    ScoringSchemeVersion,
)
from app.modules.frameworks.public import (
    THPTQG_2025_CODE,
    THPTQG_2025_SCORING_CODE,
    seed_thptqg_2025_frameworks_and_scoring,
)
from app.modules.frameworks.router import router as frameworks_router
from app.modules.search.internal.models import Collection, CollectionItem, SearchDocument  # noqa: F401
from app.modules.search.public import handle_takedown_for_entity, upsert_search_document
from app.modules.search.router import router as search_router
from app.platform.auth_context import mint_hs256_token
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID, Tenant
from app.platform.tenancy import DbRole, tenant_transaction


def run(coro):
    return asyncio.run(coro)


def _api_client() -> TestClient:
    test_app = FastAPI()
    test_app.include_router(search_router)
    test_app.include_router(frameworks_router)
    return TestClient(test_app)


def _auth_headers(
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID | None = None,
    roles: list[str] | None = None,
) -> dict[str, str]:
    settings = get_settings()
    token = mint_hs256_token(
        secret=settings.secret_key,
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        membership_id=membership_id or uuid.uuid4(),
        roles=roles or ["TEACHER", "EDITOR"],
        entitlement_tier="TEACHER_PRO",
        issued_at=int(time.time()),
        ttl_seconds=900,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session", autouse=True)
def _ensure_search_frameworks_schema(_database: None) -> None:
    """Create B07/C03 tables, helper V11.3 takedown tables, RLS policies, and grants idempotently."""
    ddl_statements = (
        """
        CREATE TABLE IF NOT EXISTS search_documents (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            visibility_scope VARCHAR(20) NOT NULL DEFAULT 'TENANT_PRIVATE',
            doc_type VARCHAR(30) NOT NULL CHECK (doc_type IN ('CONTENT', 'QUESTION', 'ASSESSMENT')),
            entity_id UUID NOT NULL,
            title TEXT NOT NULL,
            body_plain TEXT NOT NULL,
            subject_code VARCHAR(50),
            grade_band VARCHAR(30),
            taxonomy_path ltree,
            search_vector tsvector GENERATED ALWAYS AS (
                setweight(to_tsvector('simple', f_unaccent(coalesce(title, ''))), 'A') ||
                setweight(to_tsvector('simple', f_unaccent(coalesce(body_plain, ''))), 'B')
            ) STORED,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (tenant_id, doc_type, entity_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_search_documents_fts_gin ON search_documents USING GIN (search_vector)",
        (
            "CREATE INDEX IF NOT EXISTS ix_search_documents_title_trgm "
            "ON search_documents USING GIN (f_unaccent(title) gin_trgm_ops)"
        ),
        """
        CREATE TABLE IF NOT EXISTS collections (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            owner_membership_id UUID NOT NULL,
            title VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (id, tenant_id),
            FOREIGN KEY (owner_membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS collection_items (
            collection_id UUID NOT NULL,
            tenant_id UUID NOT NULL,
            item_type VARCHAR(30) NOT NULL,
            item_id UUID NOT NULL,
            sequence_order INT NOT NULL DEFAULT 0,
            PRIMARY KEY (collection_id, item_type, item_id),
            FOREIGN KEY (collection_id, tenant_id) REFERENCES collections(id, tenant_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS assessment_frameworks (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            code VARCHAR(60) UNIQUE NOT NULL,
            name VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS framework_versions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            framework_id UUID NOT NULL REFERENCES assessment_frameworks(id) ON DELETE CASCADE,
            version_code VARCHAR(40) NOT NULL,
            subject_code VARCHAR(50) NOT NULL,
            total_questions INT NOT NULL,
            duration_seconds INT NOT NULL,
            section_specs_json JSONB NOT NULL,
            UNIQUE (framework_id, version_code, subject_code)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS scoring_scheme_versions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            code VARCHAR(80) UNIQUE NOT NULL,
            rules_json JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS blueprints (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            framework_id UUID NOT NULL REFERENCES assessment_frameworks(id),
            name VARCHAR(255) NOT NULL,
            rules_json JSONB NOT NULL,
            duration_seconds INT NOT NULL,
            total_points NUMERIC(6,2) NOT NULL DEFAULT 10.00,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (id, tenant_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS content_items (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            visibility_scope VARCHAR(20) NOT NULL DEFAULT 'TENANT_PRIVATE',
            slug VARCHAR(150) NOT NULL,
            content_type VARCHAR(40) NOT NULL DEFAULT 'LESSON',
            rights_status VARCHAR(30) NOT NULL DEFAULT 'OWNED',
            publication_status VARCHAR(30) NOT NULL DEFAULT 'PUBLISHED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (tenant_id, slug),
            UNIQUE (id, tenant_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS takedown_cases (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            complainant_name VARCHAR(255) NOT NULL,
            target_entity_type VARCHAR(40) NOT NULL
                CHECK (target_entity_type IN ('SOURCE_DOCUMENT', 'CONTENT_ITEM', 'QUESTION')),
            target_entity_id UUID NOT NULL,
            legal_reason TEXT NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'OPEN'
                CHECK (status IN ('OPEN', 'EXECUTED', 'REJECTED')),
            executed_by UUID,
            executed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )

    strict_rls_tables = ("collections", "collection_items", "blueprints", "takedown_cases")
    all_app_tables = (
        "search_documents",
        "collections",
        "collection_items",
        "assessment_frameworks",
        "framework_versions",
        "scoring_scheme_versions",
        "blueprints",
        "content_items",
        "takedown_cases",
    )

    async def _init() -> None:
        async with engine.begin() as conn:
            for ddl in ddl_statements:
                await conn.execute(text(ddl))

            for tbl in strict_rls_tables:
                await conn.execute(text(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY"))
                await conn.execute(text(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY"))
                await conn.execute(text(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {tbl}"))
                await conn.execute(
                    text(
                        f"CREATE POLICY tenant_isolation_policy ON {tbl} "
                        "USING (tenant_id = (SELECT current_app_tenant_id())) "
                        "WITH CHECK (tenant_id = (SELECT current_app_tenant_id()))"
                    )
                )

            for tbl in ("search_documents", "content_items"):
                await conn.execute(text(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY"))
                await conn.execute(text(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY"))
                await conn.execute(text(f"DROP POLICY IF EXISTS hybrid_write_policy ON {tbl}"))
                await conn.execute(text(f"DROP POLICY IF EXISTS hybrid_read_policy ON {tbl}"))
                await conn.execute(
                    text(
                        f"CREATE POLICY hybrid_write_policy ON {tbl} FOR ALL "
                        "USING (tenant_id = (SELECT current_app_tenant_id())) "
                        "WITH CHECK (tenant_id = (SELECT current_app_tenant_id()))"
                    )
                )
                await conn.execute(
                    text(
                        f"CREATE POLICY hybrid_read_policy ON {tbl} FOR SELECT USING ("
                        "tenant_id = (SELECT current_app_tenant_id()) "
                        "OR (tenant_id = system_public_tenant_id() AND visibility_scope = 'SYSTEM_PUBLIC')"
                        ")"
                    )
                )

            for tbl in all_app_tables:
                await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO ulp_app_rw"))

    run(_init())


def _create_tenant_with_membership(prefix: str) -> tuple[uuid.UUID, uuid.UUID]:
    async def _do() -> tuple[uuid.UUID, uuid.UUID]:
        user_id = uuid.uuid4()
        membership_id = uuid.uuid4()
        blind = hashlib.sha256(f"{prefix}-{user_id}".encode()).hexdigest()
        async with SessionLocal() as session:
            t = Tenant(
                slug=f"{prefix}-{uuid.uuid4().hex[:8]}",
                name=f"Tenant {prefix}",
            )
            session.add(t)
            await session.flush()
            tenant_id = t.id
            await session.execute(
                text(
                    "INSERT INTO users (id, email_blind_index, password_hash, status) "
                    "VALUES (:uid, :blind, 'hash', 'ACTIVE')"
                ),
                {"uid": user_id, "blind": blind},
            )
            await session.commit()

        async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
            await session.execute(
                text(
                    "INSERT INTO memberships "
                    "(id, tenant_id, user_id, status, roles, entitlement_tier) "
                    "VALUES (:mid, :tid, :uid, 'ACTIVE', ARRAY['TEACHER'], 'TEACHER_PRO')"
                ),
                {"mid": membership_id, "tid": tenant_id, "uid": user_id},
            )
        return tenant_id, membership_id

    return run(_do())


def test_search_unaccented_accented_phrase_exclusion_trigram_ltree_and_rls() -> None:
    tenant_a, member_a = _create_tenant_with_membership("srch-a")
    tenant_b, member_b = _create_tenant_with_membership("srch-b")
    client = _api_client()
    headers_a = _auth_headers(tenant_id=tenant_a, membership_id=member_a)

    public_doc_id = uuid.uuid4()
    tenant_a_phys_id = uuid.uuid4()
    tenant_a_bayes_excl_id = uuid.uuid4()
    tenant_b_secret_id = uuid.uuid4()

    async def _seed_docs() -> None:
        async with tenant_transaction(SYSTEM_PUBLIC_TENANT_ID, role=DbRole.APP_RW) as session:
            await upsert_search_document(
                session,
                tenant_id=SYSTEM_PUBLIC_TENANT_ID,
                visibility_scope="SYSTEM_PUBLIC",
                doc_type="CONTENT",
                entity_id=public_doc_id,
                title="Công thức Bayes và xác suất có điều kiện",
                body_plain="Áp dụng công thức Bayes để tính xác suất hậu nghiệm.",
                subject_code="MATH",
                grade_band="GRADE_12",
                taxonomy_path="MATH.PROB.BAYES",
            )
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            await upsert_search_document(
                session,
                tenant_id=tenant_a,
                visibility_scope="TENANT_PRIVATE",
                doc_type="QUESTION",
                entity_id=tenant_a_phys_id,
                title="Dao động tắt dần và cộng hưởng cơ học",
                body_plain="Biên độ dao động tắt dần giảm theo thời gian do lực cản.",
                subject_code="PHYSICS",
                grade_band="GRADE_12",
                taxonomy_path="PHYSICS.OSC.DAMPED",
            )
            await upsert_search_document(
                session,
                tenant_id=tenant_a,
                visibility_scope="TENANT_PRIVATE",
                doc_type="CONTENT",
                entity_id=tenant_a_bayes_excl_id,
                title="Công thức Bayes nâng cao thi học sinh giỏi",
                body_plain="Bài tập xác suất nâng cao phần phụ lục.",
                subject_code="MATH",
                grade_band="GRADE_12",
                taxonomy_path="MATH.PROB.BAYES",
            )
        async with tenant_transaction(tenant_b, role=DbRole.APP_RW) as session:
            await upsert_search_document(
                session,
                tenant_id=tenant_b,
                visibility_scope="TENANT_PRIVATE",
                doc_type="CONTENT",
                entity_id=tenant_b_secret_id,
                title="Công thức Bayes tài liệu mật Tenant B",
                body_plain="Không bao giờ được rò rỉ sang Tenant A.",
                subject_code="MATH",
                grade_band="GRADE_12",
                taxonomy_path="MATH.PROB.BAYES",
            )

    run(_seed_docs())

    # 1. Unaccented Vietnamese query matches SYSTEM_PUBLIC + Tenant A, NEVER Tenant B private
    res_unaccented = client.get("/api/v1/search", params={"q": "cong thuc bayes"}, headers=headers_a)
    assert res_unaccented.status_code == 200, res_unaccented.text
    hit_ids = {item["entity_id"] for item in res_unaccented.json()["items"]}
    assert str(public_doc_id) in hit_ids
    assert str(tenant_a_bayes_excl_id) in hit_ids
    assert str(tenant_b_secret_id) not in hit_ids

    # 2. Accented phrase + exclusion (-nang) excludes the "nang cao" doc while keeping public Bayes doc
    res_excl = client.get(
        "/api/v1/search",
        params={"q": '"xác suất có điều kiện" -nâng'},
        headers=headers_a,
    )
    assert res_excl.status_code == 200, res_excl.text
    excl_ids = [item["entity_id"] for item in res_excl.json()["items"]]
    assert excl_ids == [str(public_doc_id)]

    # 3. Physics unaccented query with d-bar ("dao dong tat dan") + ltree subtree filter (<@)
    res_phys = client.get(
        "/api/v1/search",
        params={"q": "dao dong tat dan", "taxonomy_path": "PHYSICS.OSC"},
        headers=headers_a,
    )
    assert res_phys.status_code == 200, res_phys.text
    assert [item["entity_id"] for item in res_phys.json()["items"]] == [str(tenant_a_phys_id)]

    # 4. pg_trgm similarity fallback catches slight typo in Vietnamese title
    res_trgm = client.get(
        "/api/v1/search",
        params={"q": "Dao dong tat dann", "doc_type": "QUESTION"},
        headers=headers_a,
    )
    assert res_trgm.status_code == 200, res_trgm.text
    assert [item["entity_id"] for item in res_trgm.json()["items"]] == [str(tenant_a_phys_id)]


def test_search_instant_takedown_and_collections_rls() -> None:
    tenant_a, member_a = _create_tenant_with_membership("tk-a")
    tenant_b, member_b = _create_tenant_with_membership("tk-b")
    client = _api_client()
    headers_a = _auth_headers(tenant_id=tenant_a, membership_id=member_a)
    headers_b = _auth_headers(tenant_id=tenant_b, membership_id=member_b)

    content_db_takedown = uuid.uuid4()
    question_proj_takedown = uuid.uuid4()
    takedown_case_target = uuid.uuid4()

    async def _seed() -> None:
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            await session.execute(
                text(
                    "INSERT INTO content_items "
                    "(id, tenant_id, visibility_scope, slug, content_type, rights_status, publication_status) "
                    "VALUES (:cid, :tid, 'TENANT_PRIVATE', :slug, 'LESSON', 'OWNED', 'PUBLISHED')"
                ),
                {
                    "cid": content_db_takedown,
                    "tid": tenant_a,
                    "slug": f"nguyen-ham-{uuid.uuid4().hex[:6]}",
                },
            )
            await upsert_search_document(
                session,
                tenant_id=tenant_a,
                doc_type="CONTENT",
                entity_id=content_db_takedown,
                title="Chuyên đề nguyên hàm lượng giác source-takedown",
                body_plain="Bị đánh dấu TAKEN_DOWN trực tiếp ở content_items.",
                subject_code="MATH",
            )
            await upsert_search_document(
                session,
                tenant_id=tenant_a,
                doc_type="QUESTION",
                entity_id=question_proj_takedown,
                title="Câu hỏi nguyên hàm projection-takedown",
                body_plain="Bị xóa trực tiếp qua handle_takedown_for_entity.",
                subject_code="MATH",
            )
            await upsert_search_document(
                session,
                tenant_id=tenant_a,
                doc_type="ASSESSMENT",
                entity_id=takedown_case_target,
                title="Đề thi nguyên hàm legal-takedown-case",
                body_plain="Bị ẩn tức thì qua bảng takedown_cases EXECUTED.",
                subject_code="MATH",
            )

    run(_seed())

    before = client.get("/api/v1/search", params={"q": "nguyen ham"}, headers=headers_a)
    assert before.status_code == 200
    assert {it["entity_id"] for it in before.json()["items"]} == {
        str(content_db_takedown),
        str(question_proj_takedown),
        str(takedown_case_target),
    }

    # Test ALL 3 instant takedown paths:
    # (1) content_items.publication_status='TAKEN_DOWN',
    # (2) handle_takedown_for_entity removing projection row,
    # (3) takedown_cases.status='EXECUTED'
    async def _execute_takedowns() -> None:
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            await session.execute(
                text(
                    "UPDATE content_items "
                    "SET publication_status = 'TAKEN_DOWN', rights_status = 'TAKEDOWN' "
                    "WHERE id = :cid"
                ),
                {"cid": content_db_takedown},
            )
            await session.execute(
                text(
                    "INSERT INTO takedown_cases "
                    "(tenant_id, complainant_name, target_entity_type, target_entity_id, legal_reason, status) "
                    "VALUES (:tid, 'NXB', 'CONTENT_ITEM', :eid, 'Copyright takedown', 'EXECUTED')"
                ),
                {"tid": tenant_a, "eid": takedown_case_target},
            )
            deleted = await handle_takedown_for_entity(
                session,
                tenant_id=tenant_a,
                target_entity_type="QUESTION",
                target_entity_id=question_proj_takedown,
            )
            assert deleted == 1

    run(_execute_takedowns())

    after = client.get("/api/v1/search", params={"q": "nguyen ham"}, headers=headers_a)
    assert after.status_code == 200
    assert after.json()["items"] == []

    # Collections: create + add items + RLS isolation check
    col_res = client.post(
        "/api/v1/collections",
        headers=headers_a,
        json={"title": "Bo de on thi THPTQG 2025"},
    )
    assert col_res.status_code == 201, col_res.text
    col_body = col_res.json()
    col_id = col_body["id"]
    assert col_body["owner_membership_id"] == str(member_a)

    item_1 = uuid.uuid4()
    add_res = client.post(
        f"/api/v1/collections/{col_id}/items",
        headers=headers_a,
        json={"item_type": "QUESTION", "item_id": str(item_1)},
    )
    assert add_res.status_code == 201, add_res.text
    assert add_res.json()["sequence_order"] == 0

    get_a = client.get(f"/api/v1/collections/{col_id}", headers=headers_a)
    assert get_a.status_code == 200
    assert len(get_a.json()["items"]) == 1

    # Cross-tenant collection read & append must fail closed with 404
    assert client.get(f"/api/v1/collections/{col_id}", headers=headers_b).status_code == 404
    assert (
        client.post(
            f"/api/v1/collections/{col_id}/items",
            headers=headers_b,
            json={"item_type": "QUESTION", "item_id": str(uuid.uuid4())},
        ).status_code
        == 404
    )

    async def _cleanup() -> None:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM takedown_cases"))
            await conn.execute(text("DELETE FROM content_items"))

    run(_cleanup())


def test_frameworks_seeding_thptqg_2025_blueprint_validate_and_shortage_report() -> None:
    tenant_id, member_id = _create_tenant_with_membership("fw-a")
    other_tenant, other_member = _create_tenant_with_membership("fw-b")
    client = _api_client()
    headers = _auth_headers(tenant_id=tenant_id, membership_id=member_id)
    other_headers = _auth_headers(tenant_id=other_tenant, membership_id=other_member)

    # 1. Seed canonical THPTQG 2025+ Math (22q/90m) & Physics/Chem/Bio (28q/50m) + scoring idempotently
    async def _seed_and_check() -> tuple[dict[str, tuple[int, int]], str]:
        async with SessionLocal() as session:
            fw1 = await seed_thptqg_2025_frameworks_and_scoring(session)
            fw2 = await seed_thptqg_2025_frameworks_and_scoring(session)
            await session.commit()
            assert fw1.id == fw2.id
            by_subj = {v.subject_code: (v.total_questions, v.duration_seconds) for v in fw2.versions}
            scoring = (
                await session.execute(
                    select(ScoringSchemeVersion).where(
                        ScoringSchemeVersion.code == THPTQG_2025_SCORING_CODE
                    )
                )
            ).scalar_one()
            return by_subj, scoring.code

    subj_map, scoring_code = run(_seed_and_check())
    assert subj_map == {
        "MATH": (22, 5400),
        "PHYSICS": (28, 3000),
        "CHEMISTRY": (28, 3000),
        "BIOLOGY": (28, 3000),
    }
    assert scoring_code == THPTQG_2025_SCORING_CODE

    # 2. Custom assessment framework via POST /api/v1/assessment-frameworks
    fw_res = client.post(
        "/api/v1/assessment-frameworks",
        headers=headers,
        json={
            "code": "DGTD_TSA",
            "name": "Danh gia tu duy BKHN",
            "version_code": "2026.1",
            "versions": [
                {
                    "subject_code": "MATH_LOGIC",
                    "duration_seconds": 3600,
                    "total_questions": 20,
                    "total_points": "10.00",
                    "sections": [
                        {
                            "section_code": "PART_I_MCQ",
                            "item_type": "SINGLE_CHOICE",
                            "question_count": 20,
                            "points_per_question": "0.50",
                        }
                    ],
                }
            ],
        },
    )
    assert fw_res.status_code == 201, fw_res.text
    assert fw_res.json()["code"] == "DGTD_TSA"

    # 3. Create valid THPTQG 2025 MATH blueprint (22q / 90m) and validate structure & shortage reports
    bp_res = client.post(
        "/api/v1/blueprints",
        headers=headers,
        json={
            "framework_code": THPTQG_2025_CODE,
            "subject_code": "MATH",
            "name": "De thi thu Toan THPTQG 2025 so 01",
            "duration_seconds": 5400,
            "total_points": "10.00",
            "sections": [
                {
                    "section_code": "PART_I_MCQ",
                    "item_type": "SINGLE_CHOICE",
                    "question_count": 12,
                    "points_per_question": "0.25",
                },
                {
                    "section_code": "PART_II_TF4",
                    "item_type": "CLUSTER_TRUE_FALSE",
                    "question_count": 4,
                    "points_per_question": "1.00",
                },
                {
                    "section_code": "PART_III_SHORT",
                    "item_type": "SHORT_NUMERIC",
                    "question_count": 6,
                    "points_per_question": "0.50",
                },
            ],
            "matrix_cells": [
                {
                    "taxonomy_node_code": "MATH.CALC.DERIV",
                    "cognitive_level": "NB",
                    "item_type": "SINGLE_CHOICE",
                    "required_count": 6,
                },
                {
                    "taxonomy_node_code": "MATH.PROB.BAYES",
                    "cognitive_level": "TH",
                    "item_type": "SINGLE_CHOICE",
                    "required_count": 6,
                },
                {
                    "taxonomy_node_code": "MATH.CALC.DERIV",
                    "cognitive_level": "VD",
                    "item_type": "CLUSTER_TRUE_FALSE",
                    "required_count": 4,
                },
                {
                    "taxonomy_node_code": "MATH.PROB.BAYES",
                    "cognitive_level": "VDC",
                    "item_type": "SHORT_NUMERIC",
                    "required_count": 6,
                },
            ],
        },
    )
    assert bp_res.status_code == 201, bp_res.text
    bp_id = bp_res.json()["id"]

    # Validate against default empty pool -> valid=True but total_shortage == 22
    val_empty = client.post(f"/api/v1/blueprints/{bp_id}/validate", headers=headers)
    assert val_empty.status_code == 200, val_empty.text
    assert val_empty.json()["valid"] is True
    assert val_empty.json()["shortage_report"]["has_shortage"] is True
    assert val_empty.json()["shortage_report"]["total_shortage"] == 22

    # Validate partial pool -> reports exact cell shortage
    val_partial = client.post(
        f"/api/v1/blueprints/{bp_id}/validate",
        headers=headers,
        json={
            "available_counts": [
                {
                    "taxonomy_node_code": "MATH.CALC.DERIV",
                    "cognitive_level": "NB",
                    "item_type": "SINGLE_CHOICE",
                    "available_count": 6,
                },
                {
                    "taxonomy_node_code": "MATH.PROB.BAYES",
                    "cognitive_level": "TH",
                    "item_type": "SINGLE_CHOICE",
                    "available_count": 4,
                },
                {
                    "taxonomy_node_code": "MATH.CALC.DERIV",
                    "cognitive_level": "VD",
                    "item_type": "CLUSTER_TRUE_FALSE",
                    "available_count": 4,
                },
                {
                    "taxonomy_node_code": "MATH.PROB.BAYES",
                    "cognitive_level": "VDC",
                    "item_type": "SHORT_NUMERIC",
                    "available_count": 6,
                },
            ]
        },
    )
    assert val_partial.status_code == 200, val_partial.text
    sr_part = val_partial.json()["shortage_report"]
    assert sr_part["has_shortage"] is True
    assert sr_part["total_required"] == 22
    assert sr_part["total_available_matched"] == 20
    assert sr_part["total_shortage"] == 2
    assert len(sr_part["shortages"]) == 1
    assert sr_part["shortages"][0]["taxonomy_node_code"] == "MATH.PROB.BAYES"
    assert sr_part["shortages"][0]["shortage_count"] == 2

    # Full pool -> zero shortage
    val_full = client.post(
        f"/api/v1/blueprints/{bp_id}/validate",
        headers=headers,
        json={
            "available_counts": [
                {
                    "taxonomy_node_code": "MATH.CALC.DERIV",
                    "cognitive_level": "NB",
                    "item_type": "SINGLE_CHOICE",
                    "available_count": 6,
                },
                {
                    "taxonomy_node_code": "MATH.PROB.BAYES",
                    "cognitive_level": "TH",
                    "item_type": "SINGLE_CHOICE",
                    "available_count": 6,
                },
                {
                    "taxonomy_node_code": "MATH.CALC.DERIV",
                    "cognitive_level": "VD",
                    "item_type": "CLUSTER_TRUE_FALSE",
                    "available_count": 4,
                },
                {
                    "taxonomy_node_code": "MATH.PROB.BAYES",
                    "cognitive_level": "VDC",
                    "item_type": "SHORT_NUMERIC",
                    "available_count": 6,
                },
            ]
        },
    )
    assert val_full.status_code == 200
    assert val_full.json()["shortage_report"]["has_shortage"] is False

    # 4. Cross-tenant validate blocked by RLS -> 404
    assert client.post(f"/api/v1/blueprints/{bp_id}/validate", headers=other_headers).status_code == 404

    # 5. Invalid Physics blueprint (wrong duration & section question count vs 28q/50m standard) -> valid=False
    bad_phys = client.post(
        "/api/v1/blueprints",
        headers=headers,
        json={
            "framework_code": THPTQG_2025_CODE,
            "subject_code": "PHYSICS",
            "name": "De Vat ly sai cau truc",
            "duration_seconds": 5400,
            "total_points": "10.00",
            "sections": [
                {
                    "section_code": "PART_I_MCQ",
                    "item_type": "SINGLE_CHOICE",
                    "question_count": 12,
                    "points_per_question": "0.25",
                },
                {
                    "section_code": "PART_II_TF4",
                    "item_type": "CLUSTER_TRUE_FALSE",
                    "question_count": 4,
                    "points_per_question": "1.00",
                },
                {
                    "section_code": "PART_III_SHORT",
                    "item_type": "SHORT_NUMERIC",
                    "question_count": 6,
                    "points_per_question": "0.50",
                },
            ],
        },
    )
    assert bad_phys.status_code == 201
    bad_val = client.post(f"/api/v1/blueprints/{bad_phys.json()['id']}/validate", headers=headers)
    assert bad_val.status_code == 200
    err_codes = {e["code"] for e in bad_val.json()["errors"]}
    assert "INVALID_DURATION" in err_codes
    assert "INVALID_TOTAL_QUESTIONS" in err_codes
    assert "INVALID_SECTION_QUESTION_COUNT" in err_codes
    assert bad_val.json()["valid"] is False


def test_search_and_frameworks_auth_and_antispoof_guards() -> None:
    tenant_id, member_id = _create_tenant_with_membership("guard-a")
    other_tenant, _ = _create_tenant_with_membership("guard-b")
    client = _api_client()

    # Unauthenticated -> 401
    assert client.get("/api/v1/search", params={"q": "bayes"}).status_code == 401
    assert client.post("/api/v1/collections", json={"title": "Unauth"}).status_code == 401
    assert (
        client.post(
            "/api/v1/assessment-frameworks",
            json={"code": "X", "name": "Unauth"},
        ).status_code
        == 401
    )

    # Tenant spoofing header -> 403
    spoof_headers = {
        **_auth_headers(tenant_id=tenant_id, membership_id=member_id),
        "X-Tenant-ID": str(other_tenant),
    }
    assert client.get("/api/v1/search", params={"q": "bayes"}, headers=spoof_headers).status_code == 403

    # Learner role blocked from authoring frameworks/blueprints -> 403
    learner_headers = _auth_headers(
        tenant_id=tenant_id,
        membership_id=member_id,
        roles=["LEARNER"],
    )
    assert (
        client.post(
            "/api/v1/assessment-frameworks",
            headers=learner_headers,
            json={"code": "L", "name": "Blocked"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/blueprints",
            headers=learner_headers,
            json={
                "framework_code": THPTQG_2025_CODE,
                "subject_code": "MATH",
                "name": "Blocked",
                "duration_seconds": 5400,
                "sections": [
                    {
                        "section_code": "PART_I_MCQ",
                        "item_type": "SINGLE_CHOICE",
                        "question_count": 12,
                        "points_per_question": "0.25",
                    }
                ],
            },
        ).status_code
        == 403
    )
