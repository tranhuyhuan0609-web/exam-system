"""Students build their own quiz from a category: counts per part, levels, time."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Difficulty, Exam, ExamAttempt, ExamVisibility, Question, QuestionType
from tests.factories import make_tree

R, U, A = Difficulty.RECOGNITION, Difficulty.UNDERSTANDING, Difficulty.APPLICATION


@pytest.fixture
def bank(db):
    """Vật lí 11 → Chương 1 → Bài 1 / Bài 2. MC: 4 R in Bài 1, 3 U in Bài 2; TF: 2 U; SA: 1 A; 1 unverified."""

    async def _make(s):
        t = await make_tree(s, {"Vật lí 11": {"Chương 1": {"Bài 1": {}, "Bài 2": {}}, "Chương 2": {}}})

        def q(n, level, category, qtype=QuestionType.MULTIPLE_CHOICE, verified=True):
            choices = (
                [{"id": f"{n}{x}", "content": x, "is_correct": x == "A"} for x in "ABCD"]
                if qtype == QuestionType.MULTIPLE_CHOICE
                else [{"id": f"{n}{k}", "content": k, "is_correct": True} for k in "abcd"]
                if qtype == QuestionType.TRUE_FALSE
                else []
            )
            return Question(
                root_id=t["Vật lí 11"].id,
                category_id=category.id,
                difficulty=level,
                question_type=qtype,
                content=f"Q{n}",
                choices=choices,
                accepted_answers=["1"] if qtype == QuestionType.SHORT_ANSWER else None,
                is_verified=verified,
            )

        s.add_all(
            [q(i, R, t["Bài 1"]) for i in range(4)]
            + [q(10 + i, U, t["Bài 2"]) for i in range(3)]
            + [q(20 + i, U, t["Chương 1"], QuestionType.TRUE_FALSE) for i in range(2)]
            + [q(30, A, t["Bài 2"], QuestionType.SHORT_ANSWER)]
            + [q(40, R, t["Bài 1"], verified=False)]
        )
        return {name: c.id for name, c in t.items()}

    return db(_make)


def student(make_user, email="hs@example.com"):
    make_user(email)
    c = TestClient(app)
    c.post("/login", data={"email": email, "password": "password123"})
    return c


def build(c, category_id, mc=0, tf=0, sa=0, levels=None, minutes=""):
    data = {
        "category_id": str(category_id),
        "count_MULTIPLE_CHOICE": str(mc),
        "count_TRUE_FALSE": str(tf),
        "count_SHORT_ANSWER": str(sa),
        "count_ESSAY": "0",
        "minutes": str(minutes),
    }
    if levels is not None:
        data["levels"] = levels
    return c.post("/practice", data=data, follow_redirects=False)


def test_home_shows_builder_with_bank_counts(client, make_user, bank):
    page = client.get(f"/?c={bank['Chương 1']}").text
    assert 'id="tu-tao"' in page and "Đăng nhập để tự tạo bài" in page
    page = student(make_user).get(f"/?c={bank['Chương 1']}").text
    assert "/ 7 câu" in page and "/ 2 câu" in page  # 7 verified MC in the chapter, 2 true/false
    # Categories with questions but no exam are still in the table of contents.
    assert f"/?c={bank['Bài 2']}" in client.get("/").text
    assert 'id="tu-tao"' not in client.get(f"/?c={bank['Chương 2']}").text  # empty branch: no builder


def test_build_and_take_a_custom_quiz(make_user, db, bank):
    c = student(make_user)
    r = build(c, bank["Chương 1"], mc=5, tf=1, sa=1)
    assert r.status_code == 303 and r.headers["location"].startswith("/attempts/")
    attempt_id = int(r.headers["location"].rsplit("/", 1)[1])
    attempt = db(lambda s: s.get(ExamAttempt, attempt_id))
    exam = db(lambda s: s.get(Exam, attempt.exam_id))
    assert exam.visibility == ExamVisibility.PERSONAL and not exam.is_published
    assert exam.description == "5 trắc nghiệm + 1 đúng/sai + 1 trả lời ngắn"
    assert exam.duration_seconds == 15 * 60  # 5×90 + 180 + 150 s = 13 min, rounded up to 15
    types = [q["type"] for q in attempt.paper_snapshot["questions"]]
    assert types == ["MULTIPLE_CHOICE"] * 5 + ["TRUE_FALSE", "SHORT_ANSWER"]  # THPT part order
    assert "Q40" not in str(attempt.paper_snapshot)  # unverified questions are never drawn

    assert "Tiếp tục làm bài" in c.get("/practice/mine").text
    # The attempt works like any other: submit, report, retry.
    c.post(f"/api/attempts/{attempt_id}/submit", json={"answers": {}})
    assert c.get(f"/attempts/{attempt_id}/result").status_code == 200
    mine = c.get("/practice/mine").text
    assert "Tự luyện: Chương 1" in mine and "Đã làm 1 lần" in mine
    r = c.post(f"/exams/{exam.id}/start", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] != f"/attempts/{attempt_id}"


def test_levels_and_custom_time(make_user, db, bank):
    c = student(make_user)
    r = build(c, bank["Chương 1"], mc=3, levels=["UNDERSTANDING"], minutes=7)
    attempt = db(lambda s: s.get(ExamAttempt, int(r.headers["location"].rsplit("/", 1)[1])))
    ids = [q["id"] for q in attempt.paper_snapshot["questions"]]
    levels = db(lambda s: s.scalars(select(Question.difficulty).where(Question.id.in_(ids))))
    assert set(levels) == {U}
    assert (attempt.expires_at - attempt.started_at).total_seconds() == 7 * 60
    # Only 3 understanding MC exist.
    r = build(c, bank["Chương 1"], mc=4, levels=["UNDERSTANDING"])
    assert r.headers["location"].endswith("#tu-tao")
    assert "chỉ có 3 câu trắc nghiệm" in c.get(r.headers["location"]).text


def test_branch_scope_and_validation(make_user, db, bank):
    c = student(make_user)
    # Bài 1 has only recognition MC.
    assert "chỉ có 4 câu trắc nghiệm" in c.get(build(c, bank["Bài 1"], mc=5).headers["location"]).text
    assert "ít nhất 1 câu" in c.get(build(c, bank["Bài 1"]).headers["location"]).text
    r = build(c, bank["Bài 1"], mc=4)
    attempt = db(lambda s: s.get(ExamAttempt, int(r.headers["location"].rsplit("/", 1)[1])))
    assert sorted(q["content"] for q in attempt.paper_snapshot["questions"]) == ["Q0", "Q1", "Q2", "Q3"]
    # A failed build leaves nothing behind.
    assert db(lambda s: s.scalars(select(Exam.id))) == [attempt.exam_id]


def test_custom_quiz_is_private_to_its_owner(make_user, admin_client, db, bank):
    owner = student(make_user)
    r = build(owner, bank["Chương 1"], mc=2)
    exam_id = db(lambda s: s.scalar(select(Exam.id)))
    other = student(make_user, "khac@example.com")
    assert other.get(f"/exams/{exam_id}").status_code == 404
    assert other.post(f"/exams/{exam_id}/start").status_code == 404
    assert other.post(f"/practice/{exam_id}/delete").status_code == 404
    assert other.get(r.headers["location"]).status_code == 404  # the attempt as well
    assert "Tự luyện" not in other.get("/practice/mine").text
    # Not on the public home page nor in the admin exam list.
    assert "Tự luyện" not in other.get("/").text
    assert "Tự luyện" not in admin_client.get("/admin/exams").text
    assert admin_client.get(f"/exams/{exam_id}").status_code == 404
    # The owner can delete it, attempts included.
    owner.post(f"/practice/{exam_id}/delete")
    assert db(lambda s: s.scalars(select(ExamAttempt.id))) == []
    assert db(lambda s: s.scalars(select(Exam.id))) == []


def test_login_required_to_build(client, bank):
    r = build(client, bank["Chương 1"], mc=2)
    assert r.status_code in (303, 401) and "/login" in r.headers.get("location", "/login")
