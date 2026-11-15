"""Integration tests for A08 dedup & A04 exam_import DB models, services, RLS, and routers."""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.config import get_settings
from app.db import SessionLocal
from app.infrastructure.pdf_engine import PDFEngineAdapter
from app.infrastructure.storage import get_bronze_storage
from app.modules.dedup.internal.models import DuplicateCluster, DuplicateMember  # noqa: F401
from app.modules.dedup.public import compute_question_hashes
from app.modules.dedup.router import router as dedup_router
from app.modules.exam_import.internal.models import ExamImportSession, ProvenanceRecord  # noqa: F401
from app.modules.exam_import.router import router as exam_import_router
from app.modules.outbox.internal.models import OutboxEvent
from app.modules.parsers.internal.models import ExtractionRun, ParserDefinition  # noqa: F401
from app.modules.parsers.router import router as parsers_router
from app.platform.auth_context import mint_hs256_token
from app.platform.models import Tenant
from app.platform.tenancy import DbRole, tenant_transaction
from tests.pdf_fixtures import A4, PdfSketch


def run(coro):
    return asyncio.run(coro)


def _api_client() -> TestClient:
    test_app = FastAPI()
    test_app.include_router(parsers_router)
    test_app.include_router(dedup_router)
    test_app.include_router(exam_import_router)
    return TestClient(test_app)


def _auth_headers(*, tenant_id: uuid.UUID, roles: list[str] | None = None) -> dict[str, str]:
    settings = get_settings()
    token = mint_hs256_token(
        secret=settings.secret_key,
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        membership_id=uuid.uuid4(),
        roles=roles or ["TEACHER", "REVIEWER"],
        entitlement_tier="TEACHER_PRO",
        issued_at=int(time.time()),
        ttl_seconds=900,
    )
    return {"Authorization": f"Bearer {token}"}


def _make_close_header_exam_pdf() -> bytes:
    """Create a 1-page PDF where Cau 1 starts at y0=75pt (< 120pt threshold)."""
    sketch = PdfSketch()
    page = sketch.new_page(*A4)
    page.text(50, 75, "Cau 1. Tinh dao ham cua ham so y = x^3 - 3x + 2.", size=11)
    page.text(50, 100, "A. 3x^2 - 3   B. 3x^2 + 3   C. x^2 - 3   D. 3x", size=11)
    page.text(50, 145, "Cau 2. Gia tri cuc dai cua ham so la bao nhieu?", size=11)
    page.text(50, 170, "A. 4   B. 0   C. 2   D. -2", size=11)
    return sketch.tobytes()


def _create_tenant(slug_prefix: str = "a04a08") -> uuid.UUID:
    async def _go() -> uuid.UUID:
        async with SessionLocal() as session:
            async with session.begin():
                t = Tenant(
                    id=uuid.uuid4(),
                    slug=f"{slug_prefix}-{uuid.uuid4().hex[:8]}",
                    name="A04 A08 Integration Tenant",
                    status="ACTIVE",
                )
                session.add(t)
                await session.flush()
                return t.id

    return run(_go())


def _insert_candidate(*, tenant_id: uuid.UUID, stem_md: str, options_md: list[str]) -> uuid.UUID:
    hashes = compute_question_hashes(stem_md, options_md)
    cid = uuid.uuid4()

    async def _go() -> uuid.UUID:
        import json

        async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
            payload = {"stem_md": stem_md, "options_md": options_md}
            await session.execute(
                text(
                    """
                    INSERT INTO question_candidates (
                        id, tenant_id, acquisition_method, item_type,
                        payload_json, content_hash, structural_hash, rights_status
                    )
                    VALUES (
                        :id, :tenant_id, 'MANUAL_AUTHOR', 'SINGLE_CHOICE',
                        CAST(:payload_json AS jsonb), :content_hash, :structural_hash, 'OWNED'
                    )
                    """
                ),
                {
                    "id": cid,
                    "tenant_id": tenant_id,
                    "payload_json": json.dumps(payload),
                    "content_hash": hashes.content_hash,
                    "structural_hash": hashes.structural_hash,
                },
            )
        return cid

    return run(_go())


def _upload_and_extract_exam_pdf(client: TestClient, headers: dict[str, str]) -> uuid.UUID:
    pdf_bytes = _make_close_header_exam_pdf()
    up = client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("de_toan_close_top.pdf", pdf_bytes, "application/pdf")},
    )
    assert up.status_code == 201, up.text
    doc_id = up.json()["source_document_id"]
    ext = client.post(f"/api/v1/documents/{doc_id}/extract", headers=headers)
    assert ext.status_code == 201, ext.text
    return uuid.UUID(ext.json()["id"])


def test_dedup_distinguishes_chemical_formulas_and_clusters_numeric_variants() -> None:
    tenant_id = _create_tenant("dedup-chem")
    client = _api_client()
    headers = _auth_headers(tenant_id=tenant_id)

    c_h2so4 = _insert_candidate(
        tenant_id=tenant_id,
        stem_md="Cho 10g dung dịch H2SO4 tác dụng với Zn dư thu được H2.",
        options_md=["A. 0.1 mol", "B. 0.2 mol"],
    )
    c_h2so3 = _insert_candidate(
        tenant_id=tenant_id,
        stem_md="Cho 10g dung dịch H2SO3 tác dụng với Zn dư thu được H2.",
        options_md=["A. 0.1 mol", "B. 0.2 mol"],
    )
    # 1. H2SO3 must NEVER match H2SO4 at tier 1 or tier 2 (disable semantic threshold lower than 0.99 to check strict)
    res_chem = client.post(
        "/api/v1/dedup/match",
        headers=headers,
        json={
            "candidate_id": str(c_h2so3),
            "stem_md": "Cho 10g dung dịch H2SO3 tác dụng với Zn dư thu được H2.",
            "options_md": ["A. 0.1 mol", "B. 0.2 mol"],
            "enable_semantic_tier": False,
        },
    )
    assert res_chem.status_code == 200, res_chem.text
    chem_body = res_chem.json()
    assert chem_body["matches"] == []
    assert chem_body["cluster"] is None

    # 2. Numeric change (10g -> 20g) with SAME chemistry H2SO4 MUST cluster as STRUCTURAL_VARIANT
    c_h2so4_variant = _insert_candidate(
        tenant_id=tenant_id,
        stem_md="Cho 20g dung dịch H2SO4 tác dụng với Zn dư thu được H2.",
        options_md=["B. 0.2 mol", "A. 0.1 mol"],
    )
    res_var = client.post(
        "/api/v1/dedup/match",
        headers=headers,
        json={
            "candidate_id": str(c_h2so4_variant),
            "stem_md": "Cho 20g dung dịch H2SO4 tác dụng với Zn dư thu được H2.",
            "options_md": ["B. 0.2 mol", "A. 0.1 mol"],
        },
    )
    assert res_var.status_code == 200, res_var.text
    var_body = res_var.json()
    assert var_body["cluster"] is not None
    assert var_body["cluster"]["cluster_type"] == "STRUCTURAL_VARIANT"
    assert var_body["cluster"]["status"] == "PENDING_REVIEW"
    member_ids = {m["candidate_id"] for m in var_body["cluster"]["members"]}
    assert {str(c_h2so4), str(c_h2so4_variant)}.issubset(member_ids)
    assert len(var_body["matches"]) >= 1
    assert var_body["matches"][0]["match_tier"] == "STRUCTURAL_VARIANT"

    cluster_id = var_body["cluster"]["id"]

    # 3. GET /api/v1/duplicate-clusters/{id}
    get_res = client.get(f"/api/v1/duplicate-clusters/{cluster_id}", headers=headers)
    assert get_res.status_code == 200
    assert get_res.json()["id"] == cluster_id

    # 4. POST /api/v1/duplicate-clusters/{id}/decisions -> RESOLVED_KEEP_VARIANTS + outbox event
    dec_res = client.post(
        f"/api/v1/duplicate-clusters/{cluster_id}/decisions",
        headers=headers,
        json={"decision": "RESOLVED_KEEP_VARIANTS", "note": "Keep both mass variants"},
    )
    assert dec_res.status_code == 200, dec_res.text
    assert dec_res.json()["status"] == "RESOLVED_KEEP_VARIANTS"

    async def _check_outbox() -> list[str]:
        async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
            evs = (
                (
                    await session.execute(
                        select(OutboxEvent.event_type).where(OutboxEvent.aggregate_id == uuid.UUID(cluster_id))
                    )
                )
                .scalars()
                .all()
            )
            return list(evs)

    events = run(_check_outbox())
    assert "dedup.cluster_created" in events
    assert "dedup.cluster_resolved" in events


def test_dedup_exact_match_and_rls_isolation() -> None:
    tenant_a = _create_tenant("dedup-a")
    tenant_b = _create_tenant("dedup-b")
    client = _api_client()
    headers_a = _auth_headers(tenant_id=tenant_a)
    headers_b = _auth_headers(tenant_id=tenant_b)

    c1 = _insert_candidate(
        tenant_id=tenant_a,
        stem_md="Tính nguyên hàm của hàm số f(x) = e^x.",
        options_md=["e^x + C", "ln|x| + C"],
    )
    c2 = _insert_candidate(
        tenant_id=tenant_a,
        stem_md="Tính nguyên hàm của hàm số f(x) = e^x.",
        options_md=["ln|x| + C", "e^x + C"],
    )

    match_res = client.post(
        "/api/v1/dedup/match",
        headers=headers_a,
        json={
            "candidate_id": str(c2),
            "stem_md": "Tính nguyên hàm của hàm số f(x) = e^x.",
            "options_md": ["ln|x| + C", "e^x + C"],
        },
    )
    assert match_res.status_code == 200, match_res.text
    cluster = match_res.json()["cluster"]
    assert cluster["cluster_type"] == "EXACT"
    cluster_id = cluster["id"]

    # Tenant B cannot read or decide Tenant A's cluster
    assert client.get(f"/api/v1/duplicate-clusters/{cluster_id}", headers=headers_b).status_code == 404
    assert (
        client.post(
            f"/api/v1/duplicate-clusters/{cluster_id}/decisions",
            headers=headers_b,
            json={"decision": "RESOLVED_MERGED", "canonical_question_id": str(c1)},
        ).status_code
        == 404
    )

    # Tenant A merges cluster with canonical question
    merge_res = client.post(
        f"/api/v1/duplicate-clusters/{cluster_id}/decisions",
        headers=headers_a,
        json={"decision": "RESOLVED_MERGED", "canonical_question_id": str(c1)},
    )
    assert merge_res.status_code == 200
    assert merge_res.json()["status"] == "RESOLVED_MERGED"
    assert merge_res.json()["canonical_question_id"] == str(c1)


def test_exam_import_banner_expansion_provenance_and_branded_pdf_rights_gate() -> None:
    tenant_id = _create_tenant("exam-import")
    other_tenant = _create_tenant("exam-import-other")
    client = _api_client()
    headers = _auth_headers(tenant_id=tenant_id)
    other_headers = _auth_headers(tenant_id=other_tenant)

    run_id = _upload_and_extract_exam_pdf(client, headers)

    # 1. Create PENDING_REVIEW import with LICENSED rights -> cannot emit BRANDED_PDF
    create_licensed = client.post(
        "/api/v1/exam-imports",
        headers=headers,
        json={
            "extraction_run_id": str(run_id),
            "rights_status_override": "LICENSED",
            "expected_total_points": 10.0,
            "sections": [
                {
                    "section_code": "PART_I_MCQ",
                    "title": "Phan I. Trac nghiem",
                    "questions": [
                        {
                            "question_no": 1,
                            "points": 5.0,
                            "page_number": 1,
                            "paired_answer": "A",
                            "stem_md": "Cau 1. Dao ham y = x^3 - 3x + 2?",
                            "options_md": ["3x^2 - 3", "3x^2 + 3"],
                            "bbox_json": {"x0": 50, "y0": 65, "x1": 520, "y1": 115},
                        },
                        {
                            "question_no": 2,
                            "points": 4.0,
                            "page_number": 1,
                            "paired_answer": "B",
                            "stem_md": "Cau 2. Gia tri cuc dai?",
                            "options_md": ["4", "0"],
                            "bbox_json": {"x0": 50, "y0": 135, "x1": 520, "y1": 185},
                        },
                    ],
                }
            ],
        },
    )
    assert create_licensed.status_code == 201, create_licensed.text
    licensed_body = create_licensed.json()
    licensed_id = licensed_body["id"]
    assert licensed_body["status"] == "PENDING_REVIEW"
    # Cau 1 at y0 < 120pt triggered 70pt top banner expansion!
    assert licensed_body["banner_plan"]["header_expanded"] is True
    assert licensed_body["banner_plan"]["top_expansion_pt"] == 70.0
    assert len(licensed_body["provenance_records"]) == 2
    assert licensed_body["validation"]["branded_pdf_allowed"] is False

    # 2. Requesting BRANDED_PDF under LICENSED rights must be blocked with 403 RIGHTS_RESTRICTED
    blocked_approve = client.post(
        f"/api/v1/exam-imports/{licensed_id}/approve",
        headers=headers,
        json={"require_answer_keys": True, "generate_branded_pdf": True},
    )
    assert blocked_approve.status_code == 403, blocked_approve.text
    assert blocked_approve.json()["error_code"] == "RIGHTS_RESTRICTED"

    # 3. Attempting approve with wrong points (9.0 != 10.0) without branded PDF -> 422 VALIDATION_ERROR
    bad_pts_approve = client.post(
        f"/api/v1/exam-imports/{licensed_id}/approve",
        headers=headers,
        json={"require_answer_keys": True, "generate_branded_pdf": False},
    )
    assert bad_pts_approve.status_code == 422

    # 4. PATCH /api/v1/exam-imports/{id}/structure fixes point distribution to 10.0
    patch_res = client.patch(
        f"/api/v1/exam-imports/{licensed_id}/structure",
        headers=headers,
        json={
            "sections": [
                {
                    "section_code": "PART_I_MCQ",
                    "title": "Phan I. Trac nghiem",
                    "questions": [
                        {
                            "question_no": 1,
                            "points": 5.0,
                            "page_number": 1,
                            "paired_answer": "A",
                            "stem_md": "Cau 1. Dao ham y = x^3 - 3x + 2?",
                        },
                        {
                            "question_no": 2,
                            "points": 5.0,
                            "page_number": 1,
                            "paired_answer": "B",
                            "stem_md": "Cau 2. Gia tri cuc dai?",
                        },
                    ],
                }
            ]
        },
    )
    assert patch_res.status_code == 200, patch_res.text
    assert patch_res.json()["validation"]["valid"] is True

    # Approve without branded PDF succeeds for LICENSED
    ok_licensed = client.post(
        f"/api/v1/exam-imports/{licensed_id}/approve",
        headers=headers,
        json={"require_answer_keys": True, "generate_branded_pdf": False},
    )
    assert ok_licensed.status_code == 200, ok_licensed.text
    assert ok_licensed.json()["status"] == "APPROVED"
    assert ok_licensed.json()["branded_pdf_asset_id"] is None

    # 5. OWNED rights -> generate_branded_pdf=True succeeds, extends page 0 by 70pt, and stores BRANDED_PDF asset
    create_owned = client.post(
        "/api/v1/exam-imports",
        headers=headers,
        json={
            "extraction_run_id": str(run_id),
            "rights_status_override": "OWNED",
            "expected_total_points": 10.0,
            "sections": [
                {
                    "section_code": "PART_I_MCQ",
                    "title": "Phan I",
                    "questions": [
                        {
                            "question_no": 1,
                            "points": 10.0,
                            "page_number": 1,
                            "paired_answer": "A",
                            "stem_md": "Cau 1. Toan tu duy.",
                        }
                    ],
                }
            ],
        },
    )
    assert create_owned.status_code == 201, create_owned.text
    owned_id = create_owned.json()["id"]

    approve_owned = client.post(
        f"/api/v1/exam-imports/{owned_id}/approve",
        headers=headers,
        json={"require_answer_keys": True, "generate_branded_pdf": True},
    )
    assert approve_owned.status_code == 200, approve_owned.text
    owned_body = approve_owned.json()
    assert owned_body["status"] == "APPROVED"
    branded_asset_id = owned_body["branded_pdf_asset_id"]
    assert branded_asset_id is not None

    # Re-approving APPROVED session -> 409 conflict
    assert (
        client.post(
            f"/api/v1/exam-imports/{owned_id}/approve",
            headers=headers,
            json={"require_answer_keys": True, "generate_branded_pdf": False},
        ).status_code
        == 409
    )

    # Cross-tenant RLS check -> 404
    assert client.get(f"/api/v1/exam-imports/{owned_id}/structure", headers=other_headers).status_code == 404

    # Verify BRANDED_PDF asset bytes expanded top height by +70pt and include banner text via PDFEngineAdapter
    async def _verify_branded_pdf() -> tuple[str, float, str, list[str]]:
        async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
            row = (
                (
                    await session.execute(
                        text("SELECT asset_role, object_key FROM assets WHERE id = :aid"),
                        {"aid": uuid.UUID(branded_asset_id)},
                    )
                )
                .mappings()
                .one()
            )
            storage = get_bronze_storage()
            pdf_bytes = await storage.get(str(row["object_key"]))
            with PDFEngineAdapter.open(pdf_bytes) as pdf:
                _, height = pdf.page_size(0)
                page_text = pdf.text(0)
            events = (
                (
                    await session.execute(
                        select(OutboxEvent.event_type).where(OutboxEvent.aggregate_id == uuid.UUID(owned_id))
                    )
                )
                .scalars()
                .all()
            )
            return str(row["asset_role"]), height, page_text, list(events)

    role_val, height_pt, text_val, outbox_events = run(_verify_branded_pdf())
    assert role_val == "BRANDED_PDF"
    assert height_pt == pytest.approx(A4[1] + 70.0, abs=1.0)
    assert "H2Edu" in text_val
    assert "Cau 1" in text_val
    assert "exam_import.approved" in outbox_events


def test_dedup_and_exam_import_auth_and_antispoof_guards() -> None:
    tenant_id = _create_tenant("auth-a04a08")
    other_tenant = _create_tenant("auth-other")
    client = _api_client()
    headers = _auth_headers(tenant_id=tenant_id)

    assert (
        client.post(
            "/api/v1/dedup/match",
            json={"stem_md": "Cau hoi khong co token."},
        ).status_code
        == 401
    )
    spoof_headers = {**headers, "X-Tenant-ID": str(other_tenant)}
    assert (
        client.get(
            f"/api/v1/exam-imports/{uuid.uuid4()}/structure",
            headers=spoof_headers,
        ).status_code
        == 403
    )
