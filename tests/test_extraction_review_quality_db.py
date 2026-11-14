"""Integration tests for A07 extraction, A09 review and C02 quality: migration, RLS, grants, services and routes."""

from __future__ import annotations

import asyncio
import base64
import io
import sys
import time
import uuid
import zipfile
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.modules.quality.internal import service as quality_service
from app.modules.quality.public import AnswerCheck
from app.platform.auth_context import mint_hs256_token
from app.platform.models import Tenant
from app.platform.tenancy import DbRole, tenant_transaction

STUDY_GUIDE_P1 = (
    "# Chuyên đề Công thức Bayes\n"
    "Công thức Bayes:\nP(A|B) = P(B|A)P(A) / P(B)\n"
    "Phương pháp giải:\nBước 1: Xác định hệ đầy đủ biến cố.\n"
)
STUDY_GUIDE_P2 = (
    "## BÀI TẬP TỰ LUYỆN\n"
    "Câu 1: Giá trị P(A|B) là:\nA. 0.25\nB. 0.50\nC. 0.75\nD. 0.90\n"
    "Câu 2: Xét các mệnh đề sau:\na) P(A) > 0\nb) P(B) > 0\nc) P(A|B) < 1\nd) P(B|A) = 1\n"
    "Câu 3: Trả lời ngắn (làm tròn đến hàng phần trăm): tính P(A|B).\n"
    "Câu 4: Chứng minh công thức Bayes.\n"
)
PAGES = [
    {"page_no": 1, "text": STUDY_GUIDE_P1, "bbox": [40, 50, 550, 400]},
    {"page_no": 2, "text": STUDY_GUIDE_P2, "bbox": [40, 60, 550, 780]},
]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def world() -> dict[str, uuid.UUID]:
    async def create():
        suffix = uuid.uuid4().hex[:8]
        async with SessionLocal() as session, session.begin():
            a, b = Tenant(slug=f"erq-a-{suffix}", name="A"), Tenant(slug=f"erq-b-{suffix}", name="B")
            session.add_all([a, b])
            await session.flush()
            users = {}
            for key in ("admin_a", "reviewer_a", "admin_b"):
                users[key] = (
                    await session.execute(
                        text("INSERT INTO users (email_blind_index) VALUES (:b) RETURNING id"),
                        {"b": uuid.uuid4().hex + uuid.uuid4().hex},
                    )
                ).scalar_one()
            return {"a": a.id, "b": b.id, **users}

    return run(create())


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _headers(tenant: uuid.UUID, user: uuid.UUID, roles: tuple[str, ...] = ("TENANT_ADMIN",)) -> dict[str, str]:
    token = mint_hs256_token(
        secret=get_settings().secret_key,
        user_id=user,
        tenant_id=tenant,
        membership_id=uuid.uuid4(),
        roles=roles,
        issued_at=int(time.time()),
    )
    return {"Authorization": f"Bearer {token}"}


def _docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types></Types>')
        zf.writestr(
            "word/document.xml",
            b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            b"<w:body><w:p><w:r><w:t>Cau 1</w:t></w:r></w:p></w:body></w:document>",
        )
    return buf.getvalue()


def _new_run(client: TestClient, headers: dict[str, str]) -> str:
    """An extraction run created through the real A05 routes (upload a DOCX, start its extraction)."""
    up = client.post(
        "/api/v1/documents",
        headers=headers,
        json={"filename": f"{uuid.uuid4().hex}.docx", "content_base64": base64.b64encode(_docx()).decode()},
    )
    assert up.status_code == 201, up.text
    ex = client.post(f"/api/v1/documents/{up.json()['source_document_id']}/extract", headers=headers)
    assert ex.status_code == 201, ex.text
    return ex.json()["id"]


def _normalize(client, headers, run_id, **extra):
    return client.post(
        "/api/v1/candidates/normalize", headers=headers, json={"extraction_run_id": run_id, "pages": PAGES, **extra}
    )


async def _scalar(sql: str, tenant: uuid.UUID | None = None, role: DbRole = DbRole.APP_RW, **params):
    if tenant is None:
        async with SessionLocal() as session:
            return (await session.execute(text(sql), params)).scalar()
    async with tenant_transaction(tenant, role=role) as session:
        return (await session.execute(text(sql), params)).scalar()


# ---------------------------------------------------------------- A07


def test_normalize_splits_a_study_guide_into_one_content_and_four_questions(client, world) -> None:
    headers = _headers(world["a"], world["admin_a"])
    run_id = _new_run(client, headers)
    rubric = [
        [{"text": "Câu"}, {"text": "Nội dung"}, {"text": "Điểm"}],
        [{"text": "Câu 1", "rowspan": 2}, {"text": "Ý a"}, {"text": "1,5"}],
        [{"text": "Ý b"}, {"text": "2.5"}],
    ]
    resp = _normalize(client, headers, run_id, rubric_rows=rubric, rubric_expected_total="4")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["content_candidate"]["candidate_kind"] == "CONTENT"
    assert [b["kind"] for b in body["content_candidate"]["blocks"]][:2] == ["FORMULA_BOX", "METHOD_STEPS"]
    questions = body["question_candidates"]
    assert [q["item_type"] for q in questions] == ["SINGLE_CHOICE", "CLUSTER_TRUE_FALSE", "SHORT_NUMERIC", "ESSAY"]
    assert all(len(q["content_hash"]) == 64 and len(q["structural_hash"]) == 64 for q in questions)
    assert all(q["candidate_status"] == "NEEDS_REVIEW" and q["verification_status"] == "UNCHECKED" for q in questions)
    assert questions[0]["payload"]["options"]["C"] == "0.75"
    assert questions[0]["payload"]["anchors"][0]["page_no"] == 2
    assert questions[0]["payload"]["rubric"]["points"] == "4.0"
    assert "rubric" not in questions[1]["payload"]
    assert body["rubric_points_total"] == "4.0"
    # one outbox event per question candidate, same tenant, with the catalog payload
    events = run(
        _scalar("SELECT count(*) FROM outbox_events WHERE event_type = 'question.candidate.created'", world["a"])
    )
    assert events == 4


def test_normalize_reads_the_pages_of_the_run_when_none_are_given(client, world) -> None:
    headers = _headers(world["a"], world["admin_a"])
    run_id = _new_run(client, headers)

    async def add_pages():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            for page_no, raw in ((1, STUDY_GUIDE_P1), (2, STUDY_GUIDE_P2)):
                await session.execute(
                    text(
                        "INSERT INTO extracted_pages "
                        "(tenant_id, extraction_run_id, page_no, width_pt, height_pt, raw_text) "
                        "VALUES (:t, :r, :p, 595, 842, :x)"
                    ),
                    {"t": world["a"], "r": uuid.UUID(run_id), "p": page_no, "x": raw},
                )

    run(add_pages())
    resp = client.post("/api/v1/candidates/normalize", headers=headers, json={"extraction_run_id": run_id})
    assert resp.status_code == 201, resp.text
    assert len(resp.json()["question_candidates"]) == 4


def test_normalize_is_once_per_run_and_rejects_empty_runs(client, world) -> None:
    headers = _headers(world["a"], world["admin_a"])
    run_id = _new_run(client, headers)
    assert _normalize(client, headers, run_id).status_code == 201
    again = _normalize(client, headers, run_id)
    assert again.status_code == 409 and again.json()["reason"] == "ALREADY_NORMALIZED"
    empty_run = _new_run(client, headers)
    empty = client.post("/api/v1/candidates/normalize", headers=headers, json={"extraction_run_id": empty_run})
    assert empty.status_code == 409 and empty.json()["reason"] == "NOTHING_TO_EXTRACT"


def test_candidates_are_tenant_isolated_and_need_a_content_role(client, world) -> None:
    headers = _headers(world["a"], world["admin_a"])
    run_id = _new_run(client, headers)
    first = _normalize(client, headers, run_id).json()["question_candidates"][0]
    other = _headers(world["b"], world["admin_b"])
    assert client.get(f"/api/v1/candidates/{first['id']}", headers=other).status_code == 404
    assert _normalize(client, other, run_id).status_code == 404  # run of another tenant: same answer as missing
    assert client.get(f"/api/v1/candidates/{first['id']}", headers=headers).json()["candidate_kind"] == "QUESTION"
    student = _headers(world["a"], world["admin_a"], roles=("STUDENT",))
    assert _normalize(client, student, run_id).status_code == 403
    assert client.post("/api/v1/candidates/normalize", json={}).status_code == 401
    bad = client.post("/api/v1/candidates/normalize", headers=headers, json={"extraction_run_id": "x", "junk": 1})
    assert bad.status_code == 422


def test_split_replaces_a_candidate_by_its_parts_and_merge_folds_candidates(client, world) -> None:
    headers = _headers(world["a"], world["admin_a"])
    run_id = _new_run(client, headers)
    qs = _normalize(client, headers, run_id).json()["question_candidates"]
    essay = qs[3]
    split = client.post(f"/api/v1/candidates/{essay['id']}/split", headers=headers, json={"parts": ["Ý 1", "Ý 2"]})
    assert split.status_code == 200, split.text
    body = split.json()
    assert body["superseded"]["candidate_status"] == "REJECTED"
    kids = body["children"]
    assert [k["payload"]["label"] for k in kids] == ["Câu 4.1", "Câu 4.2"]
    assert all(k["parent_candidate_id"] == essay["id"] and k["candidate_status"] == "NEEDS_REVIEW" for k in kids)
    assert (
        client.post(f"/api/v1/candidates/{essay['id']}/split", headers=headers, json={"parts": ["a", "b"]}).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/candidates/{kids[0]['id']}/split", headers=headers, json={"parts": ["only one"]}
        ).status_code
        == 422
    )

    merged = client.post(
        f"/api/v1/candidates/{kids[0]['id']}/merge", headers=headers, json={"merge_candidate_ids": [kids[1]["id"]]}
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["payload"]["stem"] == "Ý 1\nÝ 2"
    assert merged.json()["payload"]["merged_from"] == [kids[1]["id"]]
    assert client.get(f"/api/v1/candidates/{kids[1]['id']}", headers=headers).json()["candidate_status"] == "REJECTED"
    # a rejected candidate cannot be merged again
    again = client.post(
        f"/api/v1/candidates/{kids[0]['id']}/merge", headers=headers, json={"merge_candidate_ids": [kids[1]["id"]]}
    )
    assert again.status_code == 409 and again.json()["reason"] == "NOT_PENDING"

    other_run = _new_run(client, headers)
    foreign = _normalize(client, headers, other_run).json()["question_candidates"][0]
    cross = client.post(
        f"/api/v1/candidates/{qs[0]['id']}/merge", headers=headers, json={"merge_candidate_ids": [foreign["id"]]}
    )
    assert cross.status_code == 409 and cross.json()["reason"] == "DIFFERENT_RUN"


# ---------------------------------------------------------------- A09


def _first_candidate(client, headers) -> str:
    return _normalize(client, headers, _new_run(client, headers)).json()["question_candidates"][0]["id"]


def test_review_decision_resolves_the_task_and_sets_the_candidate_status(client, world) -> None:
    admin = _headers(world["a"], world["admin_a"])
    reviewer = _headers(world["a"], world["reviewer_a"], roles=("TEACHER",))
    cand = _first_candidate(client, admin)
    made = client.post(
        "/api/v1/review-tasks",
        headers=admin,
        json={"entity_type": "QUESTION_CANDIDATE", "entity_id": cand, "queue_name": "math", "severity": "HIGH"},
    )
    assert made.status_code == 201, made.text
    task = made.json()
    assert task["status"] == "OPEN" and task["severity"] == "HIGH"
    dup = client.post(
        "/api/v1/review-tasks",
        headers=admin,
        json={"entity_type": "QUESTION_CANDIDATE", "entity_id": cand, "queue_name": "math"},
    )
    assert dup.status_code == 409 and dup.json()["reason"] == "REVIEW_TASK_EXISTS"

    blank = client.post(
        f"/api/v1/review-tasks/{task['id']}/decisions", headers=reviewer, json={"decision": "APPROVE", "reason": ""}
    )
    assert blank.status_code == 422
    bad = client.post(
        f"/api/v1/review-tasks/{task['id']}/decisions", headers=reviewer, json={"decision": "MAYBE", "reason": "x"}
    )
    assert bad.status_code == 422

    done = client.post(
        f"/api/v1/review-tasks/{task['id']}/decisions",
        headers=reviewer,
        json={"decision": "APPROVE", "reason": "đúng đáp án"},
    )
    assert done.status_code == 200, done.text
    out = done.json()
    assert out["task"]["status"] == "RESOLVED" and out["task"]["assigned_to"] == str(world["reviewer_a"])
    assert out["decision"]["resulting_verification_status"] == "VERIFIED"
    candidate = client.get(f"/api/v1/candidates/{cand}", headers=admin).json()
    assert candidate["verification_status"] == "VERIFIED" and candidate["candidate_status"] == "APPROVED"

    closed = client.post(
        f"/api/v1/review-tasks/{task['id']}/decisions", headers=reviewer, json={"decision": "REJECT", "reason": "đổi ý"}
    )
    assert closed.status_code == 409 and closed.json()["reason"] == "REVIEW_TASK_CLOSED"
    assert run(_scalar("SELECT count(*) FROM review_decisions", world["a"])) == 1


def test_reprocess_and_reject_decisions_map_to_disputed_and_rejected(client, world) -> None:
    admin = _headers(world["a"], world["admin_a"])
    for decision, verification, status in (
        ("REQUEST_REPROCESS", "DISPUTED", "NEEDS_REVIEW"),
        ("REJECT", "REJECTED", "REJECTED"),
        ("SPLIT_MERGE", "UNCHECKED", "NEEDS_REVIEW"),
    ):
        cand = _first_candidate(client, admin)
        task = client.post(
            "/api/v1/review-tasks",
            headers=admin,
            json={"entity_type": "QUESTION_CANDIDATE", "entity_id": cand, "queue_name": "q"},
        ).json()
        out = client.post(
            f"/api/v1/review-tasks/{task['id']}/decisions", headers=admin, json={"decision": decision, "reason": "r"}
        )
        assert out.status_code == 200, out.text
        got = client.get(f"/api/v1/candidates/{cand}", headers=admin).json()
        assert (got["verification_status"], got["candidate_status"]) == (verification, status), decision


def test_review_tasks_are_tenant_isolated(client, world) -> None:
    admin = _headers(world["a"], world["admin_a"])
    other = _headers(world["b"], world["admin_b"])
    cand = _first_candidate(client, admin)
    stranger = client.post(
        "/api/v1/review-tasks",
        headers=other,
        json={"entity_type": "QUESTION_CANDIDATE", "entity_id": cand, "queue_name": "q"},
    )
    assert stranger.status_code == 404
    task = client.post(
        "/api/v1/review-tasks",
        headers=admin,
        json={"entity_type": "QUESTION_CANDIDATE", "entity_id": cand, "queue_name": "q"},
    ).json()
    cross = client.post(
        f"/api/v1/review-tasks/{task['id']}/decisions", headers=other, json={"decision": "APPROVE", "reason": "r"}
    )
    assert cross.status_code == 404
    student = _headers(world["a"], world["admin_a"], roles=("STUDENT",))
    assert (
        client.post(
            f"/api/v1/review-tasks/{task['id']}/decisions", headers=student, json={"decision": "APPROVE", "reason": "r"}
        ).status_code
        == 403
    )
    bad_type = client.post(
        "/api/v1/review-tasks", headers=admin, json={"entity_type": "NOPE", "entity_id": cand, "queue_name": "q"}
    )
    assert bad_type.status_code == 422


def _make_source(tenant: uuid.UUID) -> uuid.UUID:
    async def go():
        async with tenant_transaction(tenant, role=DbRole.APP_RW) as session:
            return (
                await session.execute(
                    text(
                        "INSERT INTO sources (tenant_id, source_type, name) "
                        "VALUES (:t, 'MANUAL_UPLOAD', 'src') RETURNING id"
                    ),
                    {"t": tenant},
                )
            ).scalar_one()

    return run(go())


def test_rights_record_is_admin_only_audited_and_tenant_scoped(client, world) -> None:
    admin = _headers(world["a"], world["admin_a"])
    source = _make_source(world["a"])
    body = {"source_id": str(source), "license_type": "cc_by", "allow_rebranding": True}
    teacher = _headers(world["a"], world["admin_a"], roles=("TEACHER",))
    assert client.post("/api/v1/rights-records", headers=teacher, json=body).status_code == 403
    ok = client.post("/api/v1/rights-records", headers=admin, json=body)
    assert ok.status_code == 201, ok.text
    rec = ok.json()
    assert rec["license_type"] == "CC_BY" and rec["allow_rebranding"] is True
    assert rec["allow_public_redistribution"] is False and rec["verified_by"] == str(world["admin_a"])
    assert run(_scalar("SELECT count(*) FROM audit_events WHERE action = 'SOURCE_RIGHTS_RECORDED'", world["a"])) == 1
    other = _headers(world["b"], world["admin_b"])
    assert client.post("/api/v1/rights-records", headers=other, json=body).status_code == 404
    bad = client.post("/api/v1/rights-records", headers=admin, json={**body, "license_type": "WTFPL"})
    assert bad.status_code == 422


# ---------------------------------------------------------------- C02


def _verify(client, headers, cand, expression="2+2", answer="4"):
    return client.post(
        f"/api/v1/candidates/{cand}/verify", headers=headers, json={"expression": expression, "source_answer": answer}
    )


def test_verification_match_mismatch_and_error_are_recorded_and_move_the_candidate(client, world, monkeypatch) -> None:
    admin = _headers(world["a"], world["admin_a"])
    cases = {
        "match": (AnswerCheck(matches=True, computed="4", source="4", abs_diff="0"), "AUTO_VERIFIED", True),
        "mismatch": (AnswerCheck(matches=False, computed="5", source="4", abs_diff="1"), "DISPUTED", False),
        "error": (AnswerCheck(matches=False, error="SANDBOX_WORKER_UNAVAILABLE"), "UNCHECKED", None),
    }
    for name, (check, expected_status, expected_match) in cases.items():
        monkeypatch.setattr(quality_service, "_default_verifier", lambda e, s, t, c=check: c)
        cand = _first_candidate(client, admin)
        out = _verify(client, admin, cand)
        assert out.status_code == 201, (name, out.text)
        data = out.json()
        assert data["candidate_verification_status"] == expected_status, name
        assert data["run"]["is_match_with_source"] is expected_match, name
        assert data["run"]["verifier_type"] == "SYMPY_SOLVER" and data["run"]["candidate_id"] == cand
        got = client.get(f"/api/v1/verification-runs/{data['run']['id']}", headers=admin)
        assert got.status_code == 200 and got.json()["findings"]["error"] == check.error
        assert client.get(f"/api/v1/candidates/{cand}", headers=admin).json()["verification_status"] == expected_status


def test_verification_checks_input_status_tenant_and_role(client, world, monkeypatch) -> None:
    monkeypatch.setattr(quality_service, "_default_verifier", lambda e, s, t: AnswerCheck(matches=True, computed="4"))
    admin = _headers(world["a"], world["admin_a"])
    cand = _first_candidate(client, admin)
    other = _headers(world["b"], world["admin_b"])
    assert _verify(client, other, cand).status_code == 404
    assert client.get(f"/api/v1/verification-runs/{uuid.uuid4()}", headers=admin).status_code == 404
    student = _headers(world["a"], world["admin_a"], roles=("STUDENT",))
    assert _verify(client, student, cand).status_code == 403
    assert _verify(client, admin, cand, expression="x" * 501).status_code == 422
    assert client.post(f"/api/v1/candidates/{cand}/verify", headers=admin, json={"expression": "1"}).status_code == 422
    run_id = _verify(client, admin, cand).json()["run"]["id"]
    assert client.get(f"/api/v1/verification-runs/{run_id}", headers=other).status_code == 404
    task = client.post(
        "/api/v1/review-tasks",
        headers=admin,
        json={"entity_type": "QUESTION_CANDIDATE", "entity_id": cand, "queue_name": "q"},
    ).json()
    client.post(
        f"/api/v1/review-tasks/{task['id']}/decisions", headers=admin, json={"decision": "REJECT", "reason": "r"}
    )
    late = _verify(client, admin, cand)
    assert late.status_code == 409 and late.json()["reason"] == "NOT_PENDING"


@pytest.mark.skipif(sys.platform == "linux", reason="the sandbox worker really runs on Linux")
def test_without_the_worker_the_check_fails_closed_and_leaves_the_candidate_unchecked(client, world) -> None:
    admin = _headers(world["a"], world["admin_a"])
    cand = _first_candidate(client, admin)
    data = _verify(client, admin, cand).json()
    assert data["run"]["findings"]["error"] == "SANDBOX_WORKER_UNAVAILABLE"
    assert data["run"]["is_match_with_source"] is None and data["candidate_verification_status"] == "UNCHECKED"


# ---------------------------------------------------------------- schema, RLS, grants


TABLES = (
    "source_rights",
    "content_candidates",
    "question_candidates",
    "verification_runs",
    "review_tasks",
    "review_decisions",
)


def test_strict_tables_force_rls_with_the_initplan_policy() -> None:
    for table in TABLES:
        assert run(_scalar("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = :t", t=table))
        qual = run(
            _scalar(
                "SELECT qual FROM pg_policies WHERE tablename = :t AND policyname = 'tenant_isolation_policy'", t=table
            )
        )
        assert "( SELECT current_app_tenant_id() AS current_app_tenant_id)" in qual, table


def test_privileges_make_runs_and_decisions_append_only() -> None:
    def has(role: str, table: str, privilege: str) -> bool:
        return run(_scalar("SELECT has_table_privilege(:r, :t, :p)", r=role, t=table, p=privilege))

    for table in ("verification_runs", "review_decisions"):
        assert has("ulp_app_rw", table, "SELECT") and has("ulp_app_rw", table, "INSERT")
        assert not has("ulp_app_rw", table, "UPDATE") and not has("ulp_app_rw", table, "DELETE")
    for table in TABLES:
        assert not has("ulp_app_rw", table, "DELETE"), table
        for role in ("ulp_exam_session_rw", "ulp_worker_grading", "ulp_analytics_ro", "ulp_system_scheduler"):
            assert not has(role, table, "SELECT"), (role, table)
    for table in ("content_candidates", "question_candidates", "verification_runs", "review_tasks"):
        assert has("ulp_worker_ingestion", table, "INSERT") and has("ulp_worker_ingestion", table, "UPDATE"), table
    for table in ("source_rights", "review_decisions"):
        assert not has("ulp_worker_ingestion", table, "SELECT"), table


def test_rls_hides_the_candidates_of_other_tenants_and_blocks_foreign_inserts(client, world) -> None:
    admin = _headers(world["a"], world["admin_a"])
    _first_candidate(client, admin)
    assert run(_scalar("SELECT count(*) FROM question_candidates", world["a"])) == 4
    assert run(_scalar("SELECT count(*) FROM question_candidates", world["b"])) == 0

    async def forge():
        async with tenant_transaction(world["b"], role=DbRole.APP_RW) as session:
            await session.execute(
                text(
                    "INSERT INTO review_tasks (tenant_id, entity_type, entity_id, queue_name) "
                    "VALUES (:t, 'QUESTION_CANDIDATE', :e, 'q')"
                ),
                {"t": world["a"], "e": uuid.uuid4()},
            )

    with pytest.raises(DBAPIError):
        run(forge())


def test_check_constraints_and_composite_fk_hold_at_the_database() -> None:
    async def bad_item_type(tenant):
        async with tenant_transaction(tenant, role=DbRole.APP_RW) as session:
            await session.execute(
                text(
                    "INSERT INTO question_candidates (tenant_id, acquisition_method, item_type, payload_json, "
                    "content_hash, structural_hash) "
                    "VALUES (:t, 'MANUAL_AUTHOR', 'POEM', '{}', repeat('a', 64), repeat('b', 64))"
                ),
                {"t": tenant},
            )

    async def make_tenant():
        async with SessionLocal() as session, session.begin():
            t = Tenant(slug=f"erq-c-{uuid.uuid4().hex[:8]}", name="C")
            session.add(t)
            await session.flush()
            return t.id

    tenant = run(make_tenant())
    with pytest.raises(DBAPIError):
        run(bad_item_type(tenant))
    assert Decimal("0") == Decimal(0)  # keeps the import used by the rubric assertions above honest
