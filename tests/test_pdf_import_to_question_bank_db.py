"""Integration tests for real PDF extraction, PNG page rasterization, and promote-to-question-bank."""

from __future__ import annotations

import asyncio
import time
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal
from app.modules.exam_import.router import router as exam_import_router
from app.modules.parsers.router import router as parsers_router
from app.modules.questions.router import router as questions_router
from app.platform.auth_context import mint_hs256_token
from app.platform.models import Tenant
from tests.pdf_fixtures import A4, PdfSketch


def _run(coro):
    return asyncio.run(coro)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(parsers_router)
    app.include_router(exam_import_router)
    app.include_router(questions_router)
    return TestClient(app)


def _headers(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    roles: list[str] | None = None,
) -> dict[str, str]:
    settings = get_settings()
    token = mint_hs256_token(
        secret=settings.secret_key,
        user_id=user_id or uuid.uuid4(),
        tenant_id=tenant_id,
        membership_id=uuid.uuid4(),
        roles=roles or ["TEACHER", "REVIEWER"],
        entitlement_tier="TEACHER_PRO",
        issued_at=int(time.time()),
        ttl_seconds=900,
    )
    return {"Authorization": f"Bearer {token}"}


def _create_tenant_and_user(prefix: str = "pdf-e2e") -> tuple[uuid.UUID, uuid.UUID]:
    async def _go() -> tuple[uuid.UUID, uuid.UUID]:
        async with SessionLocal() as session:
            async with session.begin():
                t = Tenant(
                    id=uuid.uuid4(),
                    slug=f"{prefix}-{uuid.uuid4().hex[:8]}",
                    name="PDF Import E2E Tenant",
                    status="ACTIVE",
                )
                session.add(t)
                await session.flush()
                user_id = (
                    await session.execute(
                        text("INSERT INTO users (email_blind_index) VALUES (:b) RETURNING id"),
                        {"b": uuid.uuid4().hex + uuid.uuid4().hex},
                    )
                ).scalar_one()
                return t.id, user_id

    return _run(_go())


def _sample_two_page_exam_pdf() -> bytes:
    sketch = PdfSketch()
    p1 = sketch.new_page(*A4)
    p1.text(48, 36, "DE THI THU TOAN 12", size=12)
    p1.text(48, 82, "Cau 1. Tinh dao ham cua ham so y = x^3 - 3x.", size=11)
    p1.text(48, 102, "A. 3x^2 - 3   B. 3x^2 + 3   C. x^2 - 3   D. 3x", size=11)
    p1.text(48, 150, "Cau 2. Gia tri lon nhat cua f(x) = -x^2 + 4 tren R la:", size=11)
    p1.text(48, 170, "A. 2", size=11)
    p1.text(48, 188, "B. 3", size=11)
    p1.text(48, 206, "C. 4", size=11)
    p1.text(48, 224, "D. 5", size=11)
    p1.text(48, 244, "[Dap an: C]", size=10)

    p2 = sketch.new_page(*A4)
    p2.text(48, 40, "PHAN II. Tu luan", size=12)
    p2.text(48, 88, "Cau 3. Trinh bay cach tim cuc tri cua ham so bac ba.", size=11)
    return sketch.tobytes()


def test_real_pdf_extract_png_render_and_promote_to_question_bank() -> None:
    tenant_a, user_a = _create_tenant_and_user("pdf-a")
    tenant_b, user_b = _create_tenant_and_user("pdf-b")
    client = _client()
    teacher_a = _headers(tenant_id=tenant_a, user_id=user_a, roles=["TEACHER", "REVIEWER"])
    student_a = _headers(tenant_id=tenant_a, user_id=user_a, roles=["STUDENT"])
    teacher_b = _headers(tenant_id=tenant_b, user_id=user_b, roles=["TEACHER", "REVIEWER"])

    # 1. Upload & extract real 2-page PDF
    up = client.post(
        "/api/v1/documents",
        headers=teacher_a,
        files={"file": ("toan_12.pdf", _sample_two_page_exam_pdf(), "application/pdf")},
        data={"title": "Đề Toán 12 Chương 1"},
    )
    assert up.status_code == 201, up.text
    doc_id = up.json()["source_document_id"]

    ext = client.post(f"/api/v1/documents/{doc_id}/extract", headers=teacher_a, json={})
    assert ext.status_code == 201, ext.text
    ext_body = ext.json()
    run_id = ext_body["id"]

    extracted = ext_body["extracted_structure"]
    assert extracted is not None
    assert len(extracted["pages"]) == 2
    all_questions = [q for sec in extracted["sections"] for q in sec["questions"]]
    assert len(all_questions) == 3

    q1, q2, q3 = all_questions
    assert q1["item_type"] == "SINGLE_CHOICE"
    assert q1["options_md"] == ["3x^2 - 3", "3x^2 + 3", "x^2 - 3", "3x"]
    assert q1["paired_answer"] is None  # missing answer on Q1 initially
    assert q1["bbox_json"]["x0"] < q1["bbox_json"]["x1"]
    assert q1["bbox_json"]["y0"] < q1["bbox_json"]["y1"] <= A4[1]

    assert q2["item_type"] == "SINGLE_CHOICE"
    assert q2["options_md"] == ["2", "3", "4", "5"]
    assert q2["paired_answer"] == "C"

    assert q3["page_number"] == 2
    assert q3["item_type"] == "ESSAY"

    # Also test GET /api/v1/extraction-runs/{run_id}/structure + RLS
    get_struct = client.get(f"/api/v1/extraction-runs/{run_id}/structure", headers=teacher_a)
    assert get_struct.status_code == 200
    assert len(get_struct.json()["pages"]) == 2
    assert client.get(f"/api/v1/extraction-runs/{run_id}/structure", headers=teacher_b).status_code == 404

    # 2. Test empty exam-import -> auto-extract endpoint
    empty_import = client.post(
        "/api/v1/exam-imports",
        headers=teacher_a,
        json={"extraction_run_id": run_id, "sections": [], "create_question_candidates": False},
    )
    assert empty_import.status_code == 201, empty_import.text
    import_id = empty_import.json()["id"]
    assert empty_import.json()["proposed_structure_json"]["document_title"] == "Đề Toán 12 Chương 1"
    assert len(empty_import.json()["proposed_structure_json"]["pages"]) == 2

    auto_res = client.post(f"/api/v1/exam-imports/{import_id}/auto-extract", headers=teacher_a)
    assert auto_res.status_code == 200, auto_res.text
    auto_qs = [
        q for s in auto_res.json()["proposed_structure_json"]["sections"] for q in s["questions"]
    ]
    assert len(auto_qs) == 3

    # 3. Real PNG page rendering endpoint + RBAC + RLS + bounds check
    png1 = client.get(f"/api/v1/exam-imports/{import_id}/pages/1.png?dpi=100", headers=teacher_a)
    assert png1.status_code == 200
    assert png1.headers["content-type"].startswith("image/png")
    assert png1.content.startswith(b"\x89PNG\r\n\x1a\n")

    png2 = client.get(f"/api/v1/exam-imports/{import_id}/pages/2.png?dpi=100", headers=teacher_a)
    assert png2.status_code == 200 and png2.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert client.get(f"/api/v1/exam-imports/{import_id}/pages/99.png", headers=teacher_a).status_code == 404
    assert client.get(f"/api/v1/exam-imports/{import_id}/pages/1.png", headers=student_a).status_code == 403
    assert client.get(f"/api/v1/exam-imports/{import_id}/pages/1.png", headers=teacher_b).status_code == 404

    # 4. Promote fails closed with 422 while Q1 has no paired_answer
    fail_approve = client.post(
        f"/api/v1/exam-imports/{import_id}/approve",
        headers=teacher_a,
        json={"require_answer_keys": True, "generate_branded_pdf": False, "promote_to_question_bank": True},
    )
    assert fail_approve.status_code == 422

    # 5. Patch Q1 answer key -> approve & promote all 3 questions into V11.3 questions table
    raw_sections = auto_res.json()["proposed_structure_json"]["sections"]
    patched_sections = [
        {
            "section_code": sec["section_code"],
            "title": sec["title"],
            "questions": [
                {
                    "question_no": q["question_no"],
                    "points": q["points"],
                    "page_number": q["page_number"],
                    "item_type": q["item_type"],
                    "stem_md": q["stem_md"],
                    "options_md": q["options_md"],
                    "paired_answer": q["paired_answer"],
                    "bbox_json": q["bbox_json"],
                    "original_question_label": q["original_question_label"],
                }
                for q in sec["questions"]
            ],
        }
        for sec in raw_sections
    ]
    patched_sections[0]["questions"][0]["paired_answer"] = "A"
    # Adjust ESSAY Q3 to have default paired_answer or require_answer_keys=False for mixed exam
    patched_sections[-1]["questions"][-1]["paired_answer"] = "Tính y' = 0 và xét dấu."

    patch_res = client.patch(
        f"/api/v1/exam-imports/{import_id}/structure",
        headers=teacher_a,
        json={"sections": patched_sections, "expected_total_points": 10.0},
    )
    assert patch_res.status_code == 200, patch_res.text

    approve_res = client.post(
        f"/api/v1/exam-imports/{import_id}/approve",
        headers=teacher_a,
        json={"require_answer_keys": True, "generate_branded_pdf": False, "promote_to_question_bank": True},
    )
    assert approve_res.status_code == 200, approve_res.text
    approve_body = approve_res.json()
    assert approve_body["status"] == "APPROVED"
    created_qids = approve_body["created_question_ids"]
    assert len(created_qids) == 3

    # 6. Verify all 3 promoted questions show up in GET /api/v1/questions
    q_list = client.get("/api/v1/questions?limit=50", headers=teacher_a)
    assert q_list.status_code == 200
    listed_ids = {item["id"] for item in q_list.json()["items"]}
    assert set(created_qids).issubset(listed_ids)
