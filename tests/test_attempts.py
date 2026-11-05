from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app.models import AttemptStatus, Difficulty, Exam, ExamAttempt, ExamQuestion, Question, Subject, UserRole


@pytest.fixture
def exam_id(db):
    """A published 10-minute practice exam with 3 questions."""

    async def _make(s):
        subject = Subject(name="Vật lí 11", slug="vat-li-11")
        s.add(subject)
        await s.flush()
        questions = [
            Question(
                subject_id=subject.id,
                difficulty=Difficulty.RECOGNITION,
                content=f"Câu hỏi số {i} $x^{i}$",
                choices=[
                    {"id": f"q{i}a", "content": "Đúng", "is_correct": True},
                    {"id": f"q{i}b", "content": "Sai", "is_correct": False},
                ],
                explanation=f"LOI_GIAI_BI_MAT_{i}",
            )
            for i in range(1, 4)
        ]
        s.add_all(questions)
        exam = Exam(subject_id=subject.id, title="Đề luyện tập", duration_seconds=600, is_published=True)
        s.add(exam)
        await s.flush()
        s.add_all(ExamQuestion(exam_id=exam.id, question_id=q.id, position=i) for i, q in enumerate(questions, 1))
        return exam.id

    return db(_make)


@pytest.fixture
def student(client, make_user):
    make_user("hs@example.com", name="Học Sinh")
    client.post("/login", data={"email": "hs@example.com", "password": "password123"})
    return client


def start(client, exam_id):
    return client.post(f"/exams/{exam_id}/start", follow_redirects=False)


def attempt_id_from(response) -> int:
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert location.startswith("/attempts/"), location
    return int(location.rsplit("/", 1)[1])


def test_home_and_exam_page_list_only_published_exams(client, db, exam_id):
    assert "Đề luyện tập" in client.get("/").text
    assert client.get(f"/exams/{exam_id}").status_code == 200

    db(lambda s: s.execute(update(Exam).values(is_published=False)))
    assert "Đề luyện tập" not in client.get("/").text
    assert client.get(f"/exams/{exam_id}").status_code == 404


def test_starting_requires_login(client, exam_id):
    r = start(client, exam_id)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


def test_start_creates_timed_attempt_with_snapshot(student, db, exam_id):
    attempt_id = attempt_id_from(start(student, exam_id))
    attempt = db(lambda s: s.get(ExamAttempt, attempt_id))
    assert attempt.status == AttemptStatus.IN_PROGRESS
    assert attempt.attempt_no == 1
    assert attempt.expires_at - attempt.started_at == timedelta(seconds=600)
    assert attempt.total_questions == 3
    snapshot_questions = attempt.paper_snapshot["questions"]
    assert [q["choices"][0]["is_correct"] for q in snapshot_questions] == [True, True, True]
    assert student.get(f"/attempts/{attempt_id}").status_code == 200


def test_starting_again_resumes_the_active_attempt(student, db, exam_id):
    first = attempt_id_from(start(student, exam_id))
    second = attempt_id_from(start(student, exam_id))
    assert first == second
    assert db(lambda s: s.scalar(select(func.count()).select_from(ExamAttempt))) == 1


def test_paper_api_never_leaks_answers_or_explanations(student, exam_id):
    attempt_id = attempt_id_from(start(student, exam_id))
    r = student.get(f"/api/attempts/{attempt_id}/paper")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    raw = r.text
    assert "is_correct" not in raw
    assert "LOI_GIAI_BI_MAT" not in raw
    assert "explanation" not in raw

    data = r.json()
    assert [q["number"] for q in data["questions"]] == [1, 2, 3]
    assert data["questions"][0]["choices"] == [{"id": "q1a", "content": "Đúng"}, {"id": "q1b", "content": "Sai"}]
    assert set(data["questions"][0]) == {"id", "number", "content", "choices"}
    assert 590 < data["remaining_seconds"] <= 600
    assert data["status"] == "IN_PROGRESS"


def test_paper_is_frozen_when_the_attempt_starts(student, db, exam_id):
    attempt_id = attempt_id_from(start(student, exam_id))
    db(lambda s: s.execute(update(Question).values(content="ĐÃ SỬA SAU KHI BẮT ĐẦU")))
    data = student.get(f"/api/attempts/{attempt_id}/paper").json()
    assert data["questions"][0]["content"].startswith("Câu hỏi số 1")


def test_other_students_cannot_see_an_attempt(student, make_user, exam_id):
    attempt_id = attempt_id_from(start(student, exam_id))
    student.post("/logout")
    make_user("khac@example.com")
    student.post("/login", data={"email": "khac@example.com", "password": "password123"})
    assert student.get(f"/attempts/{attempt_id}").status_code == 404
    assert student.get(f"/api/attempts/{attempt_id}/paper").status_code == 404


def test_api_without_login_returns_401_json(client, exam_id):
    r = client.get("/api/attempts/1/paper")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/json")


def test_expired_attempt_has_no_time_left_and_a_new_one_can_start(student, db, exam_id):
    first = attempt_id_from(start(student, exam_id))
    db(
        lambda s: s.execute(
            update(ExamAttempt).values(
                expires_at=func.now() - timedelta(seconds=1), started_at=func.now() - timedelta(minutes=11)
            )
        )
    )
    assert student.get(f"/api/attempts/{first}/clock").json()["remaining_seconds"] == 0
    second = attempt_id_from(start(student, exam_id))
    assert second != first
    assert db(lambda s: s.get(ExamAttempt, second)).attempt_no == 2


def test_max_attempts_is_enforced(student, db, exam_id):
    db(lambda s: s.execute(update(Exam).values(max_attempts=1)))
    attempt_id_from(start(student, exam_id))
    db(lambda s: s.execute(update(ExamAttempt).values(expires_at=func.now() - timedelta(seconds=1))))
    r = student.post(f"/exams/{exam_id}/start")
    assert "hết 1 lượt" in r.text
    assert db(lambda s: s.scalar(select(func.count()).select_from(ExamAttempt))) == 1


def test_admin_can_preview_a_draft_exam(client, make_user, db, exam_id):
    db(lambda s: s.execute(update(Exam).values(is_published=False)))
    make_user("admin@example.com", role=UserRole.ADMIN)
    client.post("/login", data={"email": "admin@example.com", "password": "password123"})
    assert client.get(f"/exams/{exam_id}").status_code == 200
    attempt_id_from(start(client, exam_id))
