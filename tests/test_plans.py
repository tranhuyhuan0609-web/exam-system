"""Plans: free accounts take a few exams a day, Pro is unlimited, a teacher's private test never counts."""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from app.application.categories import create_category
from app.config import get_settings
from app.models import Difficulty, Exam, ExamAttempt, ExamQuestion, ExamVisibility, Question, User, UserRole
from app.plans import check_exam_quota, day_start, vn_day


def _exams(db, n):
    async def make(s):
        subject = await create_category(s, "Vật lí 11", slug="vat-li-11")
        question = Question(
            root_id=subject.id,
            category_id=subject.id,
            difficulty=Difficulty.RECOGNITION,
            content="Câu hỏi $x$",
            choices=[{"id": "a", "content": "Đúng", "is_correct": True}, {"id": "b", "content": "Sai", "is_correct": False}],
        )
        s.add(question)
        exams = [
            Exam(root_id=subject.id, category_id=subject.id, title=f"Đề {i}", duration_seconds=600, is_published=True)
            for i in range(n)
        ]
        s.add_all(exams)
        await s.flush()
        s.add_all(ExamQuestion(exam_id=e.id, question_id=question.id, position=1) for e in exams)
        return [e.id for e in exams]

    return db(make)


def _start(client, exam_id):
    return client.post(f"/exams/{exam_id}/start", follow_redirects=False).headers["location"]


def test_free_student_takes_a_few_exams_a_day_pro_is_unlimited(client, make_user, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "free_exams_per_day", 2)
    first, second, third = _exams(db, 3)
    make_user("hs@example.com")
    client.post("/login", data={"email": "hs@example.com", "password": "password123"})

    assert _start(client, first).startswith("/attempts/")
    assert _start(client, first).startswith("/attempts/")  # resuming the running attempt is not a new exam
    assert _start(client, second).startswith("/attempts/")
    assert _start(client, third) == f"/exams/{third}"  # limit reached
    assert "2 đề mỗi ngày" in client.get(f"/exams/{third}").text
    assert "đã làm 2/2 đề" in client.get("/plans").text

    # Yesterday's attempts do not count.
    db(lambda s: s.execute(update(ExamAttempt).values(started_at=func.now() - timedelta(days=1))))
    assert _start(client, third).startswith("/attempts/")

    db(lambda s: s.execute(update(User).values(pro_until=func.now() + timedelta(days=30))))
    db(lambda s: s.execute(update(ExamAttempt).values(started_at=func.now())))
    more = _exams_after(db)
    assert _start(client, more).startswith("/attempts/")  # Pro: unlimited


def _exams_after(db):
    async def make(s):
        subject_id = await s.scalar(select(Exam.root_id).limit(1))
        question_id = await s.scalar(select(Question.id).limit(1))
        exam = Exam(
            root_id=subject_id, category_id=subject_id, title="Đề thêm", duration_seconds=600, is_published=True
        )
        s.add(exam)
        await s.flush()
        s.add(ExamQuestion(exam_id=exam.id, question_id=question_id, position=1))
        return exam.id

    return db(make)


def test_teacher_private_tests_never_count():
    student = User(id=1, email="hs@example.com", password_hash="", full_name="HS", role=UserRole.STUDENT)
    test = Exam(visibility=ExamVisibility.PRIVATE)
    assert asyncio.run(check_exam_quota(None, student, test, datetime.now(UTC))) is None  # no query at all


def test_the_day_is_vietnam_time():
    late = datetime(2026, 11, 5, 17, 30, tzinfo=UTC)  # 00:30 on 6 Nov in Vietnam
    assert vn_day(late).isoformat() == "2026-11-06"
    assert day_start(late) == datetime(2026, 11, 5, 17, 0, tzinfo=UTC)


def test_students_can_request_pro_and_admin_grants_it(client, admin_client, make_user, db):
    from fastapi.testclient import TestClient

    from app.main import app

    make_user("hs@example.com")
    student = TestClient(app)
    student.post("/login", data={"email": "hs@example.com", "password": "password123"})
    student.post("/plans/request")
    assert "Đã gửi yêu cầu" in student.get("/plans").text
    page = admin_client.get("/admin/users").text  # the requests tab includes students
    assert "hs@example.com" in page
    user_id = db(lambda s: s.scalar(select(User.id).where(User.email == "hs@example.com")))
    admin_client.post(f"/admin/users/{user_id}/pro", data={"days": "30"})
    assert "còn hạn đến" in student.get("/plans").text
