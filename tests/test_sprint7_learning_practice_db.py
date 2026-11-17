"""PostgreSQL + RLS + FastAPI integration tests for TASK-29 (Sprint 7 Part 2):
- D02 learning_path (`learning_plans`, `plan_tasks`, prerequisite-first remediation, rationale_text, non-override)
- D03 practice (`practice_sessions`, `practice_responses`, feedback modes, hints, idempotency, exam isolation)
- D05 error_notebook (`error_notes`, `remediation_tasks`, <=3 variants, 2-consecutive-correct, worker_grading RLS)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.config import get_settings
from app.db import SessionLocal
from app.modules.error_notebook.internal.models import ErrorNote, RemediationTask  # noqa: F401
from app.modules.error_notebook.public import record_error_note_attempt
from app.modules.error_notebook.router import router as error_notebook_router
from app.modules.learning_path.internal.models import LearningPlan, PlanTask  # noqa: F401
from app.modules.learning_path.router import router as learning_path_router
from app.modules.practice.internal.models import PracticeResponse, PracticeSession  # noqa: F401
from app.modules.practice.router import router as practice_router
from app.modules.privacy.internal.models import Membership, User
from app.modules.taxonomy.internal.models import TaxonomyNode
from app.platform.auth_context import mint_hs256_token
from app.platform.models import Tenant
from app.platform.tenancy import DbRole, tenant_transaction

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


def _api_client() -> TestClient:
    test_app = FastAPI()
    test_app.include_router(learning_path_router)
    test_app.include_router(practice_router)
    test_app.include_router(error_notebook_router)
    return TestClient(test_app)


def _auth_headers(
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    roles: list[str] | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    settings = get_settings()
    token = mint_hs256_token(
        secret=settings.secret_key,
        user_id=user_id or uuid.uuid4(),
        tenant_id=tenant_id,
        membership_id=membership_id,
        roles=roles or ["LEARNER"],
        entitlement_tier="STUDENT_PRO",
        issued_at=int(time.time()),
        ttl_seconds=900,
    )
    headers = {"Authorization": f"Bearer {token}"}
    if extra:
        headers.update(extra)
    return headers


@pytest.fixture
def world() -> dict[str, uuid.UUID]:
    """Seed two isolated tenants with users and memberships."""

    async def create() -> dict[str, uuid.UUID]:
        ta, tb = uuid.uuid4(), uuid.uuid4()
        ua, ua2, ub = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        ma, ma2, mb = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with SessionLocal() as session, session.begin():
            session.add_all(
                [
                    Tenant(id=ta, slug=f"t29-a-{ta.hex[:8]}", name="School 29A"),
                    Tenant(id=tb, slug=f"t29-b-{tb.hex[:8]}", name="School 29B"),
                    User(id=ua, email_blind_index=f"{ua.hex}{ua.hex}", pseudonym_handle="LearnerA"),
                    User(id=ua2, email_blind_index=f"{ua2.hex}{ua2.hex}", pseudonym_handle="PeerA"),
                    User(id=ub, email_blind_index=f"{ub.hex}{ub.hex}", pseudonym_handle="LearnerB"),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    Membership(id=ma, tenant_id=ta, user_id=ua, roles=["LEARNER"]),
                    Membership(id=ma2, tenant_id=ta, user_id=ua2, roles=["TEACHER"]),
                    Membership(id=mb, tenant_id=tb, user_id=ub, roles=["LEARNER"]),
                ]
            )
        return {"ta": ta, "tb": tb, "ua": ua, "ua2": ua2, "ub": ub, "ma": ma, "ma2": ma2, "mb": mb}

    return run(create())


async def _seed_question_with_answer(
    session,
    *,
    tenant_id: uuid.UUID,
    stable_code: str,
    taxonomy_node_id: uuid.UUID,
    expected_answer: str,
    explanation_md: str = "Solution step-by-step",
    hints: tuple[str, ...] = (),
) -> uuid.UUID:
    qid = uuid.uuid4()
    qvid = uuid.uuid4()
    stem = {"text_md": f"Question {stable_code}"}
    options = [{"key": "A", "text": "Opt A"}, {"key": "B", "text": "Opt B"}]
    chash = hashlib.sha256(f"c:{stable_code}:{qid}".encode()).hexdigest()
    shash = hashlib.sha256(f"s:{stable_code}:{qid}".encode()).hexdigest()
    await session.execute(
        text(
            """
            INSERT INTO questions (
                id, tenant_id, visibility_scope, stable_code, item_type,
                primary_taxonomy_node_id, verification_status, rights_status, publication_status
            ) VALUES (
                :qid, :tid, 'TENANT_PRIVATE', :code, 'SINGLE_CHOICE',
                :nid, 'VERIFIED', 'OWNED', 'DRAFT'
            )
            """
        ),
        {"qid": qid, "tid": tenant_id, "code": stable_code, "nid": taxonomy_node_id},
    )
    await session.execute(
        text(
            """
            INSERT INTO question_versions (
                id, tenant_id, visibility_scope, question_id, version_no,
                stem_json, options_json, content_hash, structural_hash
            ) VALUES (
                :qvid, :tid, 'TENANT_PRIVATE', :qid, 1,
                CAST(:stem AS jsonb), CAST(:opts AS jsonb), :ch, :sh
            )
            """
        ),
        {
            "qvid": qvid,
            "tid": tenant_id,
            "qid": qid,
            "stem": json.dumps(stem),
            "opts": json.dumps(options),
            "ch": chash,
            "sh": shash,
        },
    )
    await session.execute(
        text("UPDATE questions SET current_version_id = :qvid, publication_status = 'PUBLISHED' WHERE id = :qid"),
        {"qvid": qvid, "qid": qid},
    )
    await session.execute(
        text(
            """
            INSERT INTO question_version_answers (
                question_version_id, tenant_id, answer_schema, rationale_json
            ) VALUES (
                :qvid, :tid, CAST(:ans AS jsonb), CAST(:rat AS jsonb)
            )
            """
        ),
        {
            "qvid": qvid,
            "tid": tenant_id,
            "ans": json.dumps({"kind": "MCQ_SINGLE", "correct_key": expected_answer}),
            "rat": json.dumps({"explanation_md": explanation_md, "hints": list(hints)}),
        },
    )
    return qvid


def test_pep562_lazy_public_exports_keep_core_imports_db_free() -> None:
    """Pure Core callers importing D02/D03/D05 symbols via public.py must not load ORM models."""
    code = (
        "import sys\n"
        "from app.modules.learning_path.public import resolve_prerequisite_remediation_order, PrerequisiteEdge\n"
        "from app.modules.practice.public import PracticeMode, submit_practice_response\n"
        "from app.modules.error_notebook.public import apply_attempt, REQUIRED_CONSECUTIVE_CORRECT\n"
        "loaded = set(sys.modules)\n"
        "assert 'app.modules.learning_path.internal.models' not in loaded\n"
        "assert 'app.modules.practice.internal.models' not in loaded\n"
        "assert 'app.modules.error_notebook.internal.models' not in loaded\n"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr or res.stdout


def test_d02_learning_plan_prerequisite_order_rationale_recalc_and_teacher_non_override(
    world: dict[str, uuid.UUID],
) -> None:
    """D02 orders prerequisites first, enforces rationale_text, and leaves teacher assignments untouched."""
    ta, tb = world["ta"], world["tb"]
    ma, ua2, mb = world["ma"], world["ua2"], world["mb"]
    n_a, n_b, n_target = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    asg_id = uuid.uuid4()
    asg_due = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)

    async def seed() -> None:
        async with SessionLocal() as session, session.begin():
            session.add_all(
                [
                    TaxonomyNode(
                        id=n_a,
                        tenant_id=ta,
                        visibility_scope="TENANT_PRIVATE",
                        node_type="CONCEPT",
                        code="LIMIT_PREREQ",
                        code_path="TOAN.LIMIT_PREREQ",
                        is_active=True,
                    ),
                    TaxonomyNode(
                        id=n_b,
                        tenant_id=ta,
                        visibility_scope="TENANT_PRIVATE",
                        node_type="CONCEPT",
                        code="DERIV_PREREQ",
                        code_path="TOAN.DERIV_PREREQ",
                        is_active=True,
                    ),
                    TaxonomyNode(
                        id=n_target,
                        tenant_id=ta,
                        visibility_scope="TENANT_PRIVATE",
                        node_type="CONCEPT",
                        code="INTEGRAL_TARGET",
                        code_path="TOAN.INTEGRAL_TARGET",
                        is_active=True,
                    ),
                ]
            )
            await session.flush()
            await session.execute(
                text(
                    "INSERT INTO knowledge_edges (tenant_id, source_node_id, target_node_id, edge_type, graph_version) "
                    "VALUES (:tid, :s1, :t1, 'PREREQUISITE', 1), (:tid, :s2, :t2, 'PREREQUISITE', 1)"
                ),
                {"tid": ta, "s1": n_a, "t1": n_b, "s2": n_b, "t2": n_target},
            )
            await session.execute(
                text(
                    "INSERT INTO mastery_estimates (tenant_id, membership_id, taxonomy_node_id, mastery_probability) "
                    "VALUES (:tid, :mid, :n1, 0.250), (:tid, :mid, :n2, 0.400), (:tid, :mid, :n3, 0.300)"
                ),
                {"tid": ta, "mid": ma, "n1": n_a, "n2": n_b, "n3": n_target},
            )
            paper_id, paper_version_id = uuid.uuid4(), uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO assessments (id, tenant_id, title, subject_code, grade_band, exam_type_code) "
                    "VALUES (:a, :t, 'Teacher exam', 'MATH', 'GRADE_12', 'THPTQG')"
                ),
                {"a": paper_id, "t": ta},
            )
            await session.execute(
                text(
                    "INSERT INTO assessment_versions (id, tenant_id, assessment_id, version_no, scoring_scheme_id, "
                    "duration_seconds, structure_snapshot_json) "
                    "SELECT :v, :t, :a, 1, id, 5400, '{}' FROM scoring_scheme_versions ORDER BY code LIMIT 1"
                ),
                {"v": paper_version_id, "t": ta, "a": paper_id},
            )
            await session.execute(
                text(
                    "INSERT INTO assessment_assignments "
                    "(id, tenant_id, assessment_version_id, assigned_by, "
                    "available_from, due_at, max_attempts, reveal_policy) "
                    "VALUES (:aid, :tid, :avid, :by, :avail, :due, 1, 'AFTER_DUE')"
                ),
                {
                    "aid": asg_id,
                    "tid": ta,
                    "avid": paper_version_id,
                    "by": ua2,
                    "avail": datetime(2026, 11, 14, 8, 0, tzinfo=UTC),
                    "due": asg_due,
                },
            )

    run(seed())

    client = _api_client()
    headers_a = _auth_headers(tenant_id=ta, membership_id=ma, user_id=world["ua"], roles=["LEARNER"])
    headers_b = _auth_headers(tenant_id=tb, membership_id=mb, user_id=world["ub"], roles=["LEARNER"])

    create_resp = client.post(
        "/api/v1/learning-plans",
        headers=headers_a,
        json={"target_framework_code": "THPTQG_2025", "weak_node_ids": [str(n_target)]},
    )
    assert create_resp.status_code == 201, create_resp.text
    plan = create_resp.json()
    plan_id = plan["id"]
    tasks = plan["tasks"]
    assert [t["taxonomy_node_id"] for t in tasks] == [str(n_a), str(n_b), str(n_target)]
    assert [t["task_type"] for t in tasks] == ["PREREQUISITE_REVIEW", "PREREQUISITE_REVIEW", "PRACTICE_SET"]
    assert all(len(t["rationale_text"].strip()) > 0 for t in tasks)
    assert "LIMIT_PREREQ" in tasks[0]["rationale_text"]

    # Complete task A, attach an open error note on Target, and recalculate.
    patch_resp = client.patch(
        f"/api/v1/learning-plans/{plan_id}/tasks/{tasks[0]['id']}",
        headers=headers_a,
        json={"status": "COMPLETED"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["tasks"][0]["status"] == "COMPLETED"

    async def seed_error_on_target() -> None:
        async with SessionLocal() as session, session.begin():
            qv = await _seed_question_with_answer(
                session, tenant_id=ta, stable_code="D02-Q1", taxonomy_node_id=n_target, expected_answer="A"
            )
            await session.execute(
                text(
                    "UPDATE mastery_estimates SET mastery_probability = 0.900 "
                    "WHERE tenant_id = :tid AND membership_id = :mid AND taxonomy_node_id = :nid"
                ),
                {"tid": ta, "mid": ma, "nid": n_a},
            )
            await session.execute(
                text(
                    "INSERT INTO error_notes (tenant_id, membership_id, question_version_id, primary_taxonomy_node_id) "
                    "VALUES (:tid, :mid, :qvid, :nid)"
                ),
                {"tid": ta, "mid": ma, "qvid": qv, "nid": n_target},
            )

    run(seed_error_on_target())

    recalc_resp = client.post(
        f"/api/v1/learning-plans/{plan_id}/recalculate",
        headers=headers_a,
        json={"weak_node_ids": [str(n_target)]},
    )
    assert recalc_resp.status_code == 200, recalc_resp.text
    recalc_tasks = recalc_resp.json()["tasks"]
    assert [t["taxonomy_node_id"] for t in recalc_tasks] == [str(n_a), str(n_b), str(n_target)]
    assert recalc_tasks[0]["status"] == "COMPLETED"
    assert recalc_tasks[1]["task_type"] == "PREREQUISITE_REVIEW"
    assert recalc_tasks[2]["task_type"] == "ERROR_REMEDIATION"
    assert all(len(t["rationale_text"].strip()) > 0 for t in recalc_tasks)

    # Verify teacher assignment was NEVER modified or overridden by D02 recalculation.
    async def check_teacher_assignment_and_rls() -> None:
        async with tenant_transaction(ta, role=DbRole.APP_RW) as sa:
            row = (
                await sa.execute(
                    text("SELECT max_attempts, due_at, reveal_policy FROM assessment_assignments WHERE id = :aid"),
                    {"aid": asg_id},
                )
            ).one()
            assert row.max_attempts == 1
            assert row.due_at == asg_due
            assert row.reveal_policy == "AFTER_DUE"
        async with tenant_transaction(tb, role=DbRole.APP_RW) as sb:
            assert (await sb.execute(select(LearningPlan))).scalars().all() == []

    run(check_teacher_assignment_and_rls())
    assert client.get(f"/api/v1/learning-plans/{plan_id}", headers=headers_b).status_code == 404


def test_d03_practice_modes_hints_idempotency_and_exam_isolation(world: dict[str, uuid.UUID]) -> None:
    """D03 covers feedback modes, progressive hints, response_id idempotency/conflict, and attempts isolation."""
    ta, tb = world["ta"], world["tb"]
    node_id = uuid.uuid4()

    async def seed() -> tuple[uuid.UUID, uuid.UUID]:
        async with SessionLocal() as session, session.begin():
            session.add(
                TaxonomyNode(
                    id=node_id,
                    tenant_id=ta,
                    visibility_scope="TENANT_PRIVATE",
                    node_type="CONCEPT",
                    code="BAYES_PRACTICE",
                    code_path="TOAN.BAYES_PRACTICE",
                    is_active=True,
                )
            )
            await session.flush()
            qv1 = await _seed_question_with_answer(
                session,
                tenant_id=ta,
                stable_code="D03-Q1",
                taxonomy_node_id=node_id,
                expected_answer="B",
                explanation_md="Use Bayes rule P(A|B) = P(B|A)P(A)/P(B).",
                hints=("Hint 1: denominator total probability", "Hint 2: P(B)=0.45"),
            )
            qv2 = await _seed_question_with_answer(
                session,
                tenant_id=ta,
                stable_code="D03-Q2",
                taxonomy_node_id=node_id,
                expected_answer="12",
                explanation_md="Derivative f'(2) = 3 * 2^2 = 12.",
            )
            return qv1, qv2

    qv1, qv2 = run(seed())
    client = _api_client()
    headers_a = _auth_headers(tenant_id=ta, membership_id=world["ma"], user_id=world["ua"])
    headers_b = _auth_headers(tenant_id=tb, membership_id=world["mb"], user_id=world["ub"])

    # 1. Immediate-feedback LESSON_EMBED session
    start_resp = client.post(
        "/api/v1/practice-sessions",
        headers=headers_a,
        json={
            "mode": "LESSON_EMBED",
            "question_version_ids": [str(qv1), str(qv2)],
            "immediate_feedback": True,
        },
    )
    assert start_resp.status_code == 201, start_resp.text
    sess_id = start_resp.json()["id"]

    # Progressive hints (2 available, 3rd returns 409)
    h1 = client.post(
        f"/api/v1/practice-sessions/{sess_id}/hints",
        headers=headers_a,
        json={"question_version_id": str(qv1)},
    )
    assert h1.status_code == 200
    assert h1.json()["hints_used"] == 1
    assert "Hint 1" in h1.json()["hint_text"]

    h2 = client.post(
        f"/api/v1/practice-sessions/{sess_id}/hints",
        headers=headers_a,
        json={"question_version_id": str(qv1)},
    )
    assert h2.status_code == 200
    assert h2.json()["hints_used"] == 2

    h3 = client.post(
        f"/api/v1/practice-sessions/{sess_id}/hints",
        headers=headers_a,
        json={"question_version_id": str(qv1)},
    )
    assert h3.status_code == 200
    assert h3.json()["hint_text"] is None
    assert h3.json()["hints_used"] == 2

    # Submit answer (case-insensitive "b") -> immediate explanation unlocked
    r1 = client.post(
        f"/api/v1/practice-sessions/{sess_id}/responses",
        headers=headers_a,
        json={"question_version_id": str(qv1), "response_id": "resp-1", "submitted_answer": "b"},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["is_correct"] is True
    assert r1.json()["hints_used"] == 2
    assert r1.json()["explanation_md"] == "Use Bayes rule P(A|B) = P(B|A)P(A)/P(B)."

    # Idempotent replay returns same record without inserting a duplicate DB row
    r1_replay = client.post(
        f"/api/v1/practice-sessions/{sess_id}/responses",
        headers=headers_a,
        json={"question_version_id": str(qv1), "response_id": "resp-1", "submitted_answer": "b"},
    )
    assert r1_replay.status_code == 200
    assert r1_replay.json()["id"] == r1.json()["id"]

    # Mutating submitted_answer for same response_id -> 409 RESPONSE_ID_CONFLICT
    r1_conflict = client.post(
        f"/api/v1/practice-sessions/{sess_id}/responses",
        headers=headers_a,
        json={"question_version_id": str(qv1), "response_id": "resp-1", "submitted_answer": "A"},
    )
    assert r1_conflict.status_code == 409
    assert r1_conflict.json()["domain_code"] == "RESPONSE_ID_CONFLICT"

    # Wrong answer on qv2 -> complete session captures qv2 in error_notes & emits outbox
    r2 = client.post(
        f"/api/v1/practice-sessions/{sess_id}/responses",
        headers=headers_a,
        json={"question_version_id": str(qv2), "response_id": "resp-2", "submitted_answer": "10"},
    )
    assert r2.status_code == 200
    assert r2.json()["is_correct"] is False

    done = client.post(f"/api/v1/practice-sessions/{sess_id}/complete", headers=headers_a)
    assert done.status_code == 200, done.text
    done_body = done.json()
    assert done_body["session"]["status"] == "COMPLETED"
    assert done_body["correct_count"] == 1
    assert done_body["accuracy"] == 0.5
    assert done_body["error_note_question_ids"] == [str(qv2)]

    # Further hints/responses after completion are rejected with 409
    after_complete = client.post(
        f"/api/v1/practice-sessions/{sess_id}/hints",
        headers=headers_a,
        json={"question_version_id": str(qv1)},
    )
    assert after_complete.status_code == 409
    assert after_complete.json()["domain_code"] == "SESSION_ALREADY_COMPLETED"

    # 2. Delayed feedback TOPIC_DRILL hides explanation_md until completion
    delayed = client.post(
        "/api/v1/practice-sessions",
        headers=headers_a,
        json={"mode": "TOPIC_DRILL", "question_version_ids": [str(qv2)], "immediate_feedback": False},
    )
    assert delayed.status_code == 201
    delayed_id = delayed.json()["id"]
    delayed_resp = client.post(
        f"/api/v1/practice-sessions/{delayed_id}/responses",
        headers=headers_a,
        json={
            "question_version_id": str(qv2),
            "response_id": "d-1",
            "submitted_answer": "12",
            "immediate_feedback": False,
        },
    )
    assert delayed_resp.status_code == 200
    assert delayed_resp.json()["is_correct"] is None
    assert delayed_resp.json()["explanation_md"] is None
    delayed_done = client.post(f"/api/v1/practice-sessions/{delayed_id}/complete", headers=headers_a)
    assert delayed_done.status_code == 200
    assert delayed_done.json()["accuracy"] == 1.0

    # Verify DB invariants: no formal exam attempts created, error_note captured, Tenant B isolated
    async def verify_db_and_rls() -> None:
        async with tenant_transaction(ta, role=DbRole.APP_RW) as sa:
            resp_count = (
                await sa.execute(
                    text("SELECT COUNT(*) FROM practice_responses WHERE practice_session_id = :sid"),
                    {"sid": uuid.UUID(sess_id)},
                )
            ).scalar_one()
            assert resp_count == 2
            attempts_count = (await sa.execute(text("SELECT COUNT(*) FROM attempts"))).scalar_one()
            assert attempts_count == 0
            outbox_count = (
                await sa.execute(
                    text(
                        "SELECT COUNT(*) FROM outbox_events "
                        "WHERE tenant_id = :tid AND aggregate_id = :sid AND event_type = 'practice.session_completed'"
                    ),
                    {"tid": ta, "sid": uuid.UUID(sess_id)},
                )
            ).scalar_one()
            assert outbox_count == 1
        async with tenant_transaction(tb, role=DbRole.APP_RW) as sb:
            assert (await sb.execute(select(PracticeSession))).scalars().all() == []

    run(verify_db_and_rls())
    assert client.get(f"/api/v1/practice-sessions/{sess_id}", headers=headers_b).status_code == 404


def test_d05_error_notebook_capture_remediation_two_streak_and_worker_grading(
    world: dict[str, uuid.UUID],
) -> None:
    """D05 captures via WORKER_GRADING, assigns 3 variants, enforces P11 2-streak, and isolates tenants."""
    ta, tb = world["ta"], world["tb"]
    ma = world["ma"]
    node_id = uuid.uuid4()

    async def seed() -> tuple[uuid.UUID, list[uuid.UUID]]:
        async with SessionLocal() as session, session.begin():
            session.add(
                TaxonomyNode(
                    id=node_id,
                    tenant_id=ta,
                    visibility_scope="TENANT_PRIVATE",
                    node_type="CONCEPT",
                    code="D05_DERIVATIVE",
                    code_path="TOAN.D05_DERIVATIVE",
                    is_active=True,
                )
            )
            await session.flush()
            failed = await _seed_question_with_answer(
                session, tenant_id=ta, stable_code="D05-FAIL", taxonomy_node_id=node_id, expected_answer="A"
            )
            siblings = [
                await _seed_question_with_answer(
                    session, tenant_id=ta, stable_code=f"D05-SIB-{i}", taxonomy_node_id=node_id, expected_answer="B"
                )
                for i in range(1, 5)
            ]
            return failed, siblings

    failed_qv, siblings = run(seed())
    t0 = datetime(2026, 11, 15, 8, 0, tzinfo=UTC)

    # Worker grading role records initial wrong answer under least privilege
    async def capture_via_worker() -> uuid.UUID:
        async with tenant_transaction(ta, role=DbRole.WORKER_GRADING) as sa:
            note = await record_error_note_attempt(
                sa,
                tenant_id=ta,
                membership_id=ma,
                question_version_id=failed_qv,
                is_correct=False,
                primary_taxonomy_node_id=node_id,
                at=t0,
            )
            assert note is not None
            assert note.status == "OPEN"
            assert note.wrong_count == 1
            assert note.consecutive_correct_count == 0
            return note.id

    note_id = run(capture_via_worker())

    client = _api_client()
    headers_a = _auth_headers(tenant_id=ta, membership_id=ma, user_id=world["ua"])
    headers_b = _auth_headers(tenant_id=tb, membership_id=world["mb"], user_id=world["ub"])

    # Assign 3 sibling variants matching the same primary_taxonomy_node_id
    rem_resp = client.post(
        f"/api/v1/error-notes/{note_id}/remediate",
        headers=headers_a,
        json={"limit": 3},
    )
    assert rem_resp.status_code == 201, rem_resp.text
    rem_body = rem_resp.json()
    assert len(rem_body["recommended_question_version_ids"]) == 3
    assert str(failed_qv) not in rem_body["recommended_question_version_ids"]
    assert set(rem_body["recommended_question_version_ids"]).issubset({str(s) for s in siblings})
    assert rem_body["note_status"] == "IN_REMEDIATION"

    # Direct resolve check before 2 consecutive correct answers -> 422 P11 guard
    premature = client.post(f"/api/v1/error-notes/{note_id}/resolve", headers=headers_a, json={})
    assert premature.status_code == 422
    assert premature.json()["domain_code"] == "REQUIRES_TWO_CONSECUTIVE_CORRECT"

    # 1st correct attempt -> IN_REMEDIATION (streak=1, NOT RESOLVED!)
    s1 = client.post(
        f"/api/v1/error-notes/{note_id}/resolve",
        headers=headers_a,
        json={"is_correct": True, "at": (t0 + timedelta(hours=1)).isoformat()},
    )
    assert s1.status_code == 200
    assert s1.json()["consecutive_correct_count"] == 1
    assert s1.json()["status"] == "IN_REMEDIATION"
    assert s1.json()["resolved_at"] is None

    # Wrong attempt resets consecutive_correct_count to 0
    s_wrong = client.post(
        f"/api/v1/error-notes/{note_id}/resolve",
        headers=headers_a,
        json={"is_correct": False, "at": (t0 + timedelta(hours=2)).isoformat()},
    )
    assert s_wrong.status_code == 200
    assert s_wrong.json()["consecutive_correct_count"] == 0
    assert s_wrong.json()["wrong_count"] == 2
    assert s_wrong.json()["status"] == "IN_REMEDIATION"

    # Fresh 1st correct -> IN_REMEDIATION; 2nd consecutive correct -> RESOLVED!
    s2 = client.post(
        f"/api/v1/error-notes/{note_id}/resolve",
        headers=headers_a,
        json={"is_correct": True, "at": (t0 + timedelta(hours=3)).isoformat()},
    )
    assert s2.status_code == 200
    assert s2.json()["status"] == "IN_REMEDIATION"
    assert s2.json()["consecutive_correct_count"] == 1

    s3 = client.post(
        f"/api/v1/error-notes/{note_id}/resolve",
        headers=headers_a,
        json={"is_correct": True, "at": (t0 + timedelta(hours=4)).isoformat()},
    )
    assert s3.status_code == 200
    assert s3.json()["status"] == "RESOLVED"
    assert s3.json()["consecutive_correct_count"] == 2
    assert s3.json()["resolved_at"] is not None

    # Tenant B cannot see or mutate Tenant A's error notes
    assert client.get("/api/v1/error-notes", headers=headers_b).json() == []
    assert (
        client.post(f"/api/v1/error-notes/{note_id}/remediate", headers=headers_b, json={"limit": 3}).status_code == 404
    )
    assert (
        client.post(
            f"/api/v1/error-notes/{note_id}/resolve",
            headers=headers_b,
            json={"is_correct": True},
        ).status_code
        == 404
    )

    async def verify_remediation_task_and_rls() -> None:
        async with tenant_transaction(ta, role=DbRole.APP_RW) as sa:
            task = (
                await sa.execute(select(RemediationTask).where(RemediationTask.error_note_id == note_id))
            ).scalar_one()
            assert task.status == "COMPLETED"
            assert task.completed_count == 3
        async with tenant_transaction(tb, role=DbRole.APP_RW) as sb:
            assert (await sb.execute(select(ErrorNote))).scalars().all() == []

    run(verify_remediation_task_and_rls())
