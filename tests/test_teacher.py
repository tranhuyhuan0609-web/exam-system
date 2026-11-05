"""Teachers (Pro): private scheduled tests reached by link + access code."""

import io
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import func, select, text, update

from app.main import app
from app.models import (
    Difficulty,
    Exam,
    ExamAttempt,
    ExamEnrollment,
    ExamQuestion,
    ExamVisibility,
    Question,
    QuestionType,
    User,
    UserRole,
)
from app.templating import DISPLAY_TZ
from tests.factories import make_tree


@pytest.fixture
def bank(db):
    """Vật lí 11 → Chương 1: 12 MC (8 recognition, 4 understanding) and 3 true/false."""

    async def _make(s):
        t = await make_tree(s, {"Vật lí 11": {"Chương 1": {}}})
        root, chapter = t["Vật lí 11"], t["Chương 1"]
        questions = [
            Question(
                root_id=root.id,
                category_id=chapter.id,
                difficulty=Difficulty.RECOGNITION if i < 8 else Difficulty.UNDERSTANDING,
                content=f"MC{i}",
                choices=[{"id": f"{i}{x}", "content": x, "is_correct": x == "A"} for x in "ABCD"],
            )
            for i in range(12)
        ] + [
            Question(
                root_id=root.id,
                category_id=chapter.id,
                difficulty=Difficulty.UNDERSTANDING,
                question_type=QuestionType.TRUE_FALSE,
                content=f"TF{i}",
                choices=[{"id": f"t{i}{k}", "content": k, "is_correct": True} for k in "abcd"],
            )
            for i in range(3)
        ]
        s.add_all(questions)
        return chapter.id

    return db(_make)


def login(make_user, email, role=UserRole.STUDENT, name="Test User"):
    make_user(email, role=role, name=name)
    c = TestClient(app)
    c.post("/login", data={"email": email, "password": "password123"})
    return c


def db_now(db):
    return db(lambda s: s.scalar(text("SELECT now()")))


def local(dt):
    return dt.astimezone(DISPLAY_TZ).strftime("%Y-%m-%dT%H:%M")


def grant_pro(db, email, days=30):
    async def _grant(s):
        await s.execute(update(User).where(User.email == email).values(pro_until=func.now() + timedelta(days=days)))

    db(_grant)


def form_data(category_id, now, mc=5, tf=1, starts_in=60, length=120, minutes=30, same_paper="0", **extra):
    return {
        "title": "Kiểm tra 15 phút lớp 11A2",
        "category_id": str(category_id),
        "count_MULTIPLE_CHOICE": str(mc),
        "count_TRUE_FALSE": str(tf),
        "count_SHORT_ANSWER": "0",
        "count_ESSAY": "0",
        "levels": ["RECOGNITION", "UNDERSTANDING", "APPLICATION", "ADVANCED_APPLICATION"],
        "starts_at": local(now + timedelta(minutes=starts_in)),
        "ends_at": local(now + timedelta(minutes=starts_in + length)),
        "minutes": str(minutes),
        "same_paper": same_paper,
        "shuffle": "1",
        **extra,
    }


def create(teacher, db, bank, **kw):
    r = teacher.post("/teacher/tests", data=form_data(bank, db_now(db), **kw), follow_redirects=False)
    assert r.status_code == 303, r.text[:500]
    exam_id = int(r.headers["location"].rsplit("/", 1)[1])
    return db(lambda s: s.get(Exam, exam_id))


def shift(db, exam_id, open_offset_min, close_offset_min):
    """Move a test's window relative to the database clock (negative = in the past)."""

    async def _move(s):
        await s.execute(
            update(Exam)
            .where(Exam.id == exam_id)
            .values(
                available_from=func.now() + timedelta(minutes=open_offset_min),
                available_until=func.now() + timedelta(minutes=close_offset_min),
            )
        )

    db(_move)


@pytest.fixture
def teacher(make_user, db):
    c = login(make_user, "gv@example.com", UserRole.TEACHER, "Cô Lan")
    grant_pro(db, "gv@example.com")
    return c


# ---------------------------------------------------------------- accounts & Pro


def test_register_as_teacher_and_get_pro(client, admin_client, db):
    c = TestClient(app)
    r = c.post(
        "/register",
        data={
            "full_name": "Thầy Minh",
            "email": "minh@example.com",
            "password": "password123",
            "password_confirm": "password123",
            "account_type": "TEACHER",
        },
        follow_redirects=False,
    )
    assert r.headers["location"] == "/teacher"
    page = c.get("/teacher").text
    assert "Yêu cầu nâng cấp Pro" in page
    assert c.get("/teacher/tests/new").status_code == 403  # no Pro yet
    c.post("/teacher/upgrade")
    assert "Đã gửi yêu cầu" in c.get("/teacher").text

    requests = admin_client.get("/admin/users").text  # default filter: pending requests
    assert "minh@example.com" in requests
    teacher_id = db(lambda s: s.scalar(select(User.id).where(User.email == "minh@example.com")))
    admin_client.post(f"/admin/users/{teacher_id}/pro", data={"days": "30", "back": "https://evil.example"})
    t = db(lambda s: s.get(User, teacher_id))
    assert t.pro_until is not None and t.pro_requested_at is None
    assert "minh@example.com" not in admin_client.get("/admin/users").text
    assert c.get("/teacher/tests/new").status_code == 200
    # Revoking closes the form again.
    admin_client.post(f"/admin/users/{teacher_id}/pro", data={"days": "0"})
    assert c.get("/teacher/tests/new").status_code == 403


def test_students_cannot_use_teacher_pages(make_user):
    c = login(make_user, "hs@example.com")
    assert c.get("/teacher").status_code == 403
    assert c.post("/teacher/tests", data={}).status_code == 403


def test_admin_switches_roles(admin_client, make_user, db):
    make_user("a@example.com")
    uid = db(lambda s: s.scalar(select(User.id).where(User.email == "a@example.com")))
    admin_client.post(f"/admin/users/{uid}/role", data={"role": "TEACHER"})
    assert db(lambda s: s.get(User, uid)).role == UserRole.TEACHER
    admin_id = db(lambda s: s.scalar(select(User.id).where(User.role == UserRole.ADMIN)))
    admin_client.post(f"/admin/users/{admin_id}/role", data={"role": "STUDENT"})
    assert db(lambda s: s.get(User, admin_id)).role == UserRole.ADMIN  # admins are never demoted here


# ---------------------------------------------------------------- creating tests


def test_create_validates_schedule_and_bank(teacher, db, bank):
    now = db_now(db)
    r = teacher.post("/teacher/tests", data=form_data(bank, now, mc=13))
    assert r.status_code == 400 and "chỉ có 12 câu trắc nghiệm" in r.text
    r = teacher.post("/teacher/tests", data=form_data(bank, now, length=10, minutes=30))
    assert "ngắn hơn thời gian làm bài" in r.text
    r = teacher.post("/teacher/tests", data=form_data(bank, now, starts_in=-200, length=60))
    assert "Giờ đóng đã qua" in r.text
    assert db(lambda s: s.scalar(select(func.count()).select_from(Exam))) == 0

    exam = create(teacher, db, bank)
    assert exam.visibility == ExamVisibility.PRIVATE and exam.max_attempts == 1
    assert len(exam.access_code) == 6 and len(exam.access_token) >= 20
    page = teacher.get(f"/teacher/tests/{exam.id}").text
    assert f"/k/{exam.access_token}" in page and exam.access_code in page and "Sắp mở" in page
    assert "Kiểm tra 15 phút lớp 11A2" in teacher.get("/teacher").text


def test_private_test_is_hidden_everywhere(teacher, make_user, admin_client, db, bank):
    exam = create(teacher, db, bank)
    student = login(make_user, "hs@example.com")
    assert student.get(f"/exams/{exam.id}").status_code == 404
    assert "Kiểm tra 15 phút" not in student.get("/").text
    assert "Kiểm tra 15 phút" not in admin_client.get("/admin/exams").text
    other = login(make_user, "gv2@example.com", UserRole.TEACHER)
    grant_pro(db, "gv2@example.com")
    assert other.get(f"/teacher/tests/{exam.id}").status_code == 404
    assert other.post(f"/teacher/tests/{exam.id}/delete").status_code == 404
    assert student.get("/k/not-a-token").status_code == 404


# ---------------------------------------------------------------- taking a test


def test_join_with_code_countdown_and_take_test(teacher, make_user, db, bank):
    exam = create(teacher, db, bank, mc=4, tf=1, minutes=30)
    link = f"/k/{exam.access_token}"
    anon = TestClient(app)
    assert "/login" in anon.get(link, follow_redirects=False).headers["location"]

    student = login(make_user, "hs@example.com", name="Nguyễn An")
    assert "Nhập mã truy cập" in student.get(link).text
    assert student.post(f"{link}/start", follow_redirects=False).status_code == 303
    assert db(lambda s: s.scalar(select(func.count()).select_from(ExamAttempt))) == 0  # code first
    student.post(f"{link}/join", data={"code": "WRONG1"}, follow_redirects=False)
    assert "Mã truy cập không đúng" in student.get(link).text
    page = student.post(f"{link}/join", data={"code": exam.access_code.lower()}).text  # case-insensitive
    assert 'id="countdown"' in page
    student.post(f"{link}/start")
    assert db(lambda s: s.scalar(select(func.count()).select_from(ExamAttempt))) == 0  # not open yet

    # Open now, closing in 10 minutes: the 30-minute attempt is cut at closing time.
    shift(db, exam.id, -1, 10)
    r = student.post(f"{link}/start", follow_redirects=False)
    attempt_id = int(r.headers["location"].rsplit("/", 1)[1])
    attempt = db(lambda s: s.get(ExamAttempt, attempt_id))
    closes = db(lambda s: s.get(Exam, exam.id)).available_until
    assert attempt.expires_at == closes and attempt.total_questions == 5

    # Submitting while open: no score, answers or solutions yet.
    data = student.post(f"/api/attempts/{attempt_id}/submit", json={"answers": {}}).json()
    assert "score" not in data
    locked = student.get(f"/attempts/{attempt_id}/result").text
    assert "Đã nộp bài" in locked and "sau khi bài kiểm tra đóng" in locked
    solutions = student.get(f"/attempts/{attempt_id}/solutions", follow_redirects=False)
    assert solutions.headers["location"] == f"/attempts/{attempt_id}/result"
    assert student.get(f"/api/attempts/{attempt_id}/grading").json()["score"] is None
    # One attempt only.
    student.post(f"{link}/start")
    assert db(lambda s: s.scalar(select(func.count()).select_from(ExamAttempt))) == 1

    # The teacher sees the student; after closing, the student sees the report.
    page = teacher.get(f"/teacher/tests/{exam.id}").text
    assert "Nguyễn An" in page and "Đã nộp" in page
    shift(db, exam.id, -60, -1)
    report = student.get(f"/attempts/{attempt_id}/result").text
    assert "Đã nộp bài" not in report and f"/attempts/{attempt_id}/solutions" in report
    assert student.get(f"/attempts/{attempt_id}/solutions").status_code == 200
    assert "Kiểm tra 15 phút" in student.get("/tests/mine").text

    xlsx = teacher.get(f"/teacher/tests/{exam.id}/results.xlsx")
    sheet = load_workbook(io.BytesIO(xlsx.content)).active
    assert sheet["B4"].value == "Nguyễn An" and sheet["D4"].value == "Đã nộp" and sheet["E4"].value == 0


def test_closed_test_cannot_be_joined_or_started(teacher, make_user, db, bank):
    exam = create(teacher, db, bank)
    student = login(make_user, "hs@example.com")
    student.post(f"/k/{exam.access_token}/join", data={"code": exam.access_code})
    shift(db, exam.id, -60, -1)
    assert "đã đóng" in student.get(f"/k/{exam.access_token}").text
    student.post(f"/k/{exam.access_token}/start")
    assert db(lambda s: s.scalar(select(func.count()).select_from(ExamAttempt))) == 0


def test_shared_paper_redraw_and_lock_after_start(teacher, make_user, db, bank):
    exam = create(teacher, db, bank, mc=3, tf=1, same_paper="1")
    paper = db(lambda s: s.scalars(select(ExamQuestion.question_id).where(ExamQuestion.exam_id == exam.id)))
    assert len(paper) == 4
    assert "Đề chung (4 câu)" in teacher.get(f"/teacher/tests/{exam.id}").text
    teacher.post(f"/teacher/tests/{exam.id}/redraw")
    assert len(db(lambda s: s.scalars(select(ExamQuestion.question_id).where(ExamQuestion.exam_id == exam.id)))) == 4

    shift(db, exam.id, -1, 60)
    seen = []
    for email in ("a@example.com", "b@example.com"):
        s = login(make_user, email)
        s.post(f"/k/{exam.access_token}/join", data={"code": exam.access_code})
        attempt_id = int(s.post(f"/k/{exam.access_token}/start", follow_redirects=False).headers["location"][10:])
        snap = db(lambda ss, i=attempt_id: ss.get(ExamAttempt, i)).paper_snapshot
        seen.append(sorted(q["id"] for q in snap["questions"]))
    assert seen[0] == seen[1]  # everyone gets the same questions

    # Started: no more editing, redrawing or deleting.
    teacher.post(f"/teacher/tests/{exam.id}/redraw")
    r = teacher.post(f"/teacher/tests/{exam.id}", data=form_data(bank, db_now(db), mc=2, same_paper="1"))
    assert "Đã có học sinh làm bài" in r.text
    teacher.post(f"/teacher/tests/{exam.id}/delete")
    assert db(lambda s: s.get(Exam, exam.id)) is not None
    # A new code keeps enrolled students in.
    teacher.post(f"/teacher/tests/{exam.id}/code")
    assert db(lambda s: s.get(Exam, exam.id)).access_code != exam.access_code
    assert db(lambda s: s.scalar(select(func.count()).select_from(ExamEnrollment))) == 2


def test_edit_before_start_and_delete(teacher, db, bank):
    exam = create(teacher, db, bank, mc=3, tf=0)
    form = teacher.get(f"/teacher/tests/{exam.id}/edit").text
    assert 'value="Kiểm tra 15 phút lớp 11A2"' in form
    r = teacher.post(
        f"/teacher/tests/{exam.id}",
        data=form_data(bank, db_now(db), mc=6, tf=2, title="Kiểm tra 1 tiết"),
        follow_redirects=False,
    )
    assert r.status_code == 303
    exam = db(lambda s: s.get(Exam, exam.id))
    assert exam.title == "Kiểm tra 1 tiết" and exam.description == "6 trắc nghiệm + 2 đúng/sai"
    teacher.post(f"/teacher/tests/{exam.id}/delete")
    assert db(lambda s: s.get(Exam, exam.id)) is None
