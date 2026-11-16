"""A teacher builds papers, never the bank: the PDF -> paper -> exam room flow, and the topic x count matrix.

* a TEACHER is refused by every route that reads or writes the question bank or feeds it (403);
* `POST /api/v1/teacher/assessments/from-pdf`: the PDF's questions are solved (scripted AI), silently stored and
  published in the bank under the teacher's own review decision, a paper holding them is published and assigned to
  the teacher's class; the answer gives the paper and the room only. A learner of the class sits it and the grade
  follows the AI's / the PDF's keys;
* `POST /assessments/{id}/assemble` with `taxonomy_counts`: N questions of each topic, points shared equally.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.contracts.autosave import compute_payload_hash
from app.db import SessionLocal
from app.main import app
from app.modules.assembly import router as assembly_router
from app.ops.seed_demo import seed_demo
from tests.ai_fakes import fake_solver, no_ai_settings
from tests.pdf_fixtures import A4, PdfSketch
from tests.test_question_bank_api_db import _login

RESPONSE_KEYS = {
    "assessment_id", "assessment_version_id", "assignment_id", "title", "question_count", "skipped_question_count",
    "duration_minutes", "exam_room_url", "assignment_url",
}  # fmt: skip


def _exam_pdf(title: str = "DE KIEM TRA 15 PHUT - TOAN 12") -> bytes:
    """Q1: no key (the AI solves it); Q2: key C printed; Q3: numeric, key 3; Q4: two options only (no paper)."""
    sketch = PdfSketch()
    page = sketch.new_page(*A4)
    page.text(48, 36, title, size=12)
    page.text(48, 82, "Cau 1. Dao ham cua ham so y = x^3 - 3x la:", size=11)
    page.text(48, 102, "A. 3x^2 - 3   B. 3x^2 + 3   C. x^2 - 3   D. 3x", size=11)
    page.text(48, 150, "Cau 2. Gia tri lon nhat cua f(x) = -x^2 + 4 tren R la:", size=11)
    for n, option in enumerate(("A. 2", "B. 3", "C. 4", "D. 5")):
        page.text(48, 170 + 18 * n, option, size=11)
    page.text(48, 244, "[Dap an: C]", size=10)
    page.text(48, 290, "Cau 3. Phuong trinh x^2 - 5x + 6 = 0 co nghiem lon nhat bang bao nhieu?", size=11)
    page.text(48, 310, "[Dap an: 3]", size=10)
    page.text(48, 350, "Cau 4. Chon dap an dung: log2(8) bang", size=11)
    page.text(48, 370, "A. 2   B. 3", size=11)
    return sketch.tobytes()


async def _rows(sql: str, **params):
    async with SessionLocal() as session:
        return (await session.execute(text(sql), params)).mappings().all()


def _q(sql: str, **params):
    return asyncio.run(_rows(sql, **params))


@pytest.fixture
def world(client):
    seeded = asyncio.run(seed_demo())
    solver, gemini = fake_solver({"Dao ham cua ham so": {"answer": "A", "solution_steps": "y' = 3x^2 - 3"}})
    state = {"solver": solver}
    app.dependency_overrides[assembly_router.get_paper_solver] = lambda: state["solver"]
    yield {
        "tenant_id": uuid.UUID(seeded["tenant"]["id"]),
        "class_id": seeded["entities"]["class_id"],
        "teacher": _login(client, "teacher@h2edu.demo"),
        "learner": _login(client, "learner@h2edu.demo"),
        "author": _login(client, "content_author@h2edu.demo"),
        "state": state,
        "gemini": gemini,
    }
    app.dependency_overrides.pop(assembly_router.get_paper_solver, None)


def _upload(client, w, pdf: bytes | None = None, headers=None, **form):
    fields = {"title": "Kiểm tra 15 phút - Đạo hàm", "subject_code": "math", "duration_minutes": "15", **form}
    return client.post(
        "/api/v1/teacher/assessments/from-pdf",
        headers=headers or w["teacher"],
        files={"file": ("de-15-phut.pdf", pdf if pdf is not None else _exam_pdf(), "application/pdf")},
        data=fields,
    )


def test_a_teacher_is_refused_by_the_question_bank_and_its_feeds(client, world):
    teacher = world["teacher"]
    body = {
        "stable_code": "GV-01",
        "item_type": "SHORT_NUMERIC",
        "stem_text": "1 + 1 = ?",
        "answer_schema": {"value": "2"},
    }
    assert client.post("/api/v1/questions", headers=teacher, json=body).status_code == 403
    assert client.get("/api/v1/questions", headers=teacher).status_code == 403
    assert client.post(f"/api/v1/questions/{uuid.uuid4()}/publish", headers=teacher).status_code == 403
    task = {"entity_type": "QUESTION_VERSION", "entity_id": str(uuid.uuid4()), "queue_name": "x"}
    assert client.post("/api/v1/review-tasks", headers=teacher, json=task).status_code == 403
    pdf = {"file": ("de.pdf", _exam_pdf(), "application/pdf")}
    assert client.post("/api/v1/imports", headers=teacher, files=pdf).status_code == 403
    assert client.post("/api/v1/documents", headers=teacher, files=pdf).status_code == 403
    assert client.get("/api/v1/exam-imports", headers=teacher).status_code == 403
    # what a teacher still reads: the topics (to build a matrix) and the counts of the pool, never a question
    assert client.get("/api/v1/taxonomy/nodes", headers=teacher).status_code == 200
    summary = client.get("/api/v1/assessments/pool-summary", params={"subject_code": "math"}, headers=teacher)
    assert summary.status_code == 200 and summary.json() and set(summary.json()[0]) == {
        "taxonomy_node_id", "code_path", "item_type", "difficulty_level", "available"
    }  # fmt: skip
    # the author of the bank still writes it
    assert client.post("/api/v1/questions", headers=world["author"], json=body).status_code == 201


def test_a_pdf_becomes_a_published_paper_assigned_to_the_class_and_the_teacher_sees_no_question(client, world):
    made = _upload(client, world, class_id=world["class_id"])
    assert made.status_code == 201, made.text
    paper = made.json()
    assert set(paper) == RESPONSE_KEYS, "the paper and its room, nothing of the bank"
    assert (paper["question_count"], paper["skipped_question_count"], paper["duration_minutes"]) == (3, 1, 15)
    assert paper["assignment_id"] and paper["exam_room_url"] == f"/teacher/classes/{world['class_id']}"

    # silently in the bank: published, verified by the teacher's own review decision, the keys of the PDF / the AI
    stored = _q(
        "SELECT q.stable_code, q.publication_status, q.verification_status, q.rights_status, a.answer_schema "
        "FROM questions q JOIN question_versions v ON v.id = q.current_version_id "
        "JOIN question_version_answers a ON a.question_version_id = v.id "
        "WHERE q.tenant_id = :t AND q.stable_code LIKE 'PDF-%' ORDER BY q.stable_code",
        t=world["tenant_id"],
    )
    assert [r["answer_schema"] for r in stored] == [
        {"correct_option": "A"}, {"correct_option": "C"}, {"value": "3"}
    ]  # fmt: skip
    assert {(r["publication_status"], r["verification_status"], r["rights_status"]) for r in stored} == {
        ("PUBLISHED", "VERIFIED", "OWNED")
    }
    decisions = _q(
        "SELECT d.decision, d.reason, t.queue_name FROM review_decisions d "
        "JOIN review_tasks t ON t.id = d.review_task_id "
        "WHERE t.tenant_id = :t AND t.entity_type = 'QUESTION_VERSION'",
        t=world["tenant_id"],
    )
    assert len(decisions) == 3 and {(d["decision"], d["queue_name"]) for d in decisions} == {("APPROVE", "teacher-pdf")}
    assert all("teacher PDF upload" in d["reason"] for d in decisions)
    version = _q(
        "SELECT p.publication_status, v.is_immutable, v.duration_seconds, v.total_points, "
        "       (SELECT count(*) FROM assessment_answer_keys k WHERE k.assessment_version_id = v.id) AS keys "
        "FROM assessments p JOIN assessment_versions v ON v.id = p.current_version_id WHERE p.id = :a",
        a=uuid.UUID(paper["assessment_id"]),
    )[0]
    assert version["publication_status"] == "PUBLISHED" and version["is_immutable"] is True
    assert (version["duration_seconds"], version["keys"]) == (900, 1) and version["total_points"] == Decimal("7.50")

    # the learner of the class sits it, answers right, submits; the grade follows the keys
    start = client.post(
        "/api/v1/exam-sessions/start",
        headers=world["learner"],
        json={"assessment_id": paper["assessment_id"], "assignment_id": paper["assignment_id"]},
    )
    assert start.status_code == 201, start.text
    attempt = start.json()["attempt_id"]
    sheet = client.get(f"/api/v1/attempts/{attempt}/paper", headers=world["learner"]).json()
    assert len(sheet["questions"]) == 3
    right = {"3x^2 - 3", "4"}
    for q in sheet["questions"]:
        if q["item_type"] == "SINGLE_CHOICE":
            label = next(o["display_label"] for o in q["options"] if o["content_json"]["text"] in right)
            payload = {"selected_key": label}
        else:
            payload = {"text_value": "3"}
        key = f"k-{uuid.uuid4().hex}"
        saved = client.put(
            f"/api/v1/attempts/{attempt}/responses/{q['question_version_id']}",
            headers={**world["learner"], "Idempotency-Key": key},
            json={"client_sequence": 1, "expected_response_version": 0, "idempotency_key": key,
                  "payload_hash": compute_payload_hash(payload), "response_payload": payload, "is_flagged": False},
        )  # fmt: skip
        assert saved.status_code == 200, saved.text
    assert client.post(f"/api/v1/attempts/{attempt}/submit", headers=world["learner"]).status_code == 200
    graded = client.post(f"/api/v1/attempts/{attempt}/grade", headers=world["teacher"], json={})
    assert graded.status_code == 201, graded.text
    assert Decimal(graded.json()["total_score"]) == Decimal("7.50"), graded.json()


def test_the_same_pdf_again_makes_a_new_paper_from_the_same_questions(client, world):
    first = _upload(client, world)
    second = _upload(client, world)
    assert first.status_code == second.status_code == 201, second.text
    assert first.json()["assessment_id"] != second.json()["assessment_id"]
    assert first.json()["assignment_id"] is None and first.json()["exam_room_url"] is None, "no class: no room yet"
    count = _q(
        "SELECT count(*) AS n FROM questions WHERE tenant_id = :t AND stable_code LIKE 'PDF-%'", t=world["tenant_id"]
    )
    assert count[0]["n"] == 3, "the bank keeps one copy of each question (dedup on the content hash)"


def test_without_an_ai_only_the_questions_the_pdf_gives_a_key_for_go_into_the_paper(client, world):
    world["state"]["solver"], _ = fake_solver(settings=no_ai_settings())
    made = _upload(client, world)
    assert made.status_code == 201, made.text
    assert (made.json()["question_count"], made.json()["skipped_question_count"]) == (2, 2)


def test_bad_inputs_and_foreign_classes_are_refused(client, world):
    nothing = PdfSketch()
    nothing.new_page(*A4).text(48, 60, "Trang trong", size=11)
    empty = _upload(client, world, nothing.tobytes())
    assert empty.status_code == 422 and empty.json()["reason"] == "NO_QUESTIONS_FOUND", empty.text
    fake = _upload(client, world, b"MZ not a pdf at all")
    assert fake.status_code == 422 and fake.json()["reason"] == "INVALID_FILE", fake.text
    odd = _upload(client, world, duration_minutes="37")
    assert odd.status_code == 422 and odd.json()["reason"] == "INVALID_DURATION"
    foreign = _upload(client, world, class_id=str(uuid.uuid4()))
    assert foreign.status_code == 404, "a class the teacher does not run is not found"
    assert _upload(client, world, headers=world["learner"]).status_code == 403
    assert _upload(client, world, headers=world["author"]).status_code == 403, "an author feeds the bank, not rooms"
    assert _q("SELECT count(*) AS n FROM assessments WHERE exam_type_code = 'TEACHER_PDF'")[0]["n"] == 0


def test_a_teacher_assembles_a_paper_from_topics_and_counts_and_assigns_it(client, world):
    teacher = world["teacher"]
    bayes = _q("SELECT id FROM taxonomy_nodes WHERE CAST(code_path AS text) = 'MATH.BAYES'")[0]["id"]
    created = client.post(
        "/api/v1/assessments",
        headers=teacher,
        json={"title": "Ma trận Bayes", "subject_code": "math", "grade_band": "GRADE_12", "exam_type_code": "MATRIX",
              "rights_status": "OWNED"},
    )  # fmt: skip
    assert created.status_code == 201, created.text
    url = f"/api/v1/assessments/{created.json()['id']}/assemble"
    matrix = {
        "random_seed": 11,
        "total_points": "10",
        "duration_minutes": 30,
        "taxonomy_counts": [
            {"taxonomy_node_id": str(bayes), "question_count": 5, "item_types": ["SINGLE_CHOICE"]},
            {"taxonomy_node_id": str(bayes), "question_count": 2, "item_types": ["SHORT_NUMERIC"]},
        ],
    }
    assembled = client.post(url, headers=teacher, json=matrix)
    assert assembled.status_code == 200, assembled.text
    version = assembled.json()
    assert len(version["items"]) == 7 and version["duration_seconds"] == 1800
    assert sum(Decimal(i["points"]) for i in version["items"]) == Decimal("10.00")
    assert [i["section_code"] for i in version["items"]] == ["PART_I_MCQ"] * 5 + ["PART_III_SHORT"] * 2
    assert len({i["question_version_id"] for i in version["items"]}) == 7, "no question twice"
    again = client.post(url, headers=teacher, json=matrix).json()
    assert [i["question_version_id"] for i in again["items"]] == [i["question_version_id"] for i in version["items"]]

    too_many = client.post(url, headers=teacher, json={**matrix, "taxonomy_counts": [
        {"taxonomy_node_id": str(bayes), "question_count": 99, "item_types": ["SINGLE_CHOICE"]}]})  # fmt: skip
    assert too_many.status_code == 422 and too_many.json()["reason"] == "POOL_SHORTAGE", too_many.text

    assert client.post(url, headers=teacher, json=matrix).status_code == 200
    published = client.post(f"/api/v1/assessments/{created.json()['id']}/publish", headers=teacher)
    assert published.status_code == 200, published.text
    assigned = client.post(
        "/api/v1/assignments",
        headers=teacher,
        json={"assessment_version_id": published.json()["version"]["id"], "class_id": world["class_id"]},
    )
    assert assigned.status_code == 201, assigned.text


def test_no_database_transaction_is_open_while_the_ai_solves_the_pdf(client, world, monkeypatch):
    from app.modules.assembly.internal import from_pdf
    from tests.ai_fakes import watch_transactions

    watch = watch_transactions(monkeypatch, from_pdf)
    world["gemini"].watch = watch
    made = _upload(client, world, class_id=world["class_id"])
    assert made.status_code == 201, made.text
    assert world["gemini"].calls, "the AI solved the question that had no key"
    assert watch.io_inside_transaction == 0
    assert watch.opened == 2, "one short transaction before the AI, one after"


def test_a_word_file_works_like_a_pdf(client, world):
    """The reader is the one of the BBox review: a .docx of the same paper becomes the same kind of room."""
    import io

    from docx import Document

    document = Document()
    for line in (
        "Cau 1. Dao ham cua ham so y = x^3 - 3x la:",
        "A. 3x^2 - 3",
        "B. 3x^2 + 3",
        "C. x^2 - 3",
        "D. 3x",
        "Cau 2. Phuong trinh x^2 - 5x + 6 = 0 co nghiem lon nhat bang bao nhieu?",
        "Dap an: 3",
    ):
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    made = client.post(
        "/api/v1/teacher/assessments/from-pdf",
        headers=world["teacher"],
        files={
            "file": (
                "de-word.docx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={"title": "Đề Word", "subject_code": "math", "duration_minutes": "15", "class_id": world["class_id"]},
    )
    assert made.status_code == 201, made.text
    assert made.json()["question_count"] == 2 and made.json()["assignment_id"]
