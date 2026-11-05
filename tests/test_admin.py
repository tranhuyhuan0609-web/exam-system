import pytest
from sqlalchemy import select

from app.models import Chapter, Difficulty, Exam, ExamQuestion, Lesson, Question, Subject, UserRole


@pytest.fixture
def tree(db):
    """Two subjects; the first has two chapters, chapter 1 has two lessons."""

    async def _make(s):
        physics = Subject(name="Vật lí 11", slug="vat-li-11")
        c1 = Chapter(name="Chương 1", position=1)
        c1.lessons = [Lesson(name="Bài 1", position=1), Lesson(name="Bài 2", position=2)]
        physics.chapters = [c1, Chapter(name="Chương 2", position=2)]
        math = Subject(name="Toán 11", slug="toan-11")
        s.add_all([physics, math])
        await s.flush()
        return {
            "physics": physics.id,
            "math": math.id,
            "c1": c1.id,
            "c2": physics.chapters[1].id,
            "l1": c1.lessons[0].id,
            "l2": c1.lessons[1].id,
        }

    return db(_make)


def question_form(scope, content="Câu hỏi?", choices=("A", "B", "C", "D"), correct=0, ids=None, **extra):
    data = {
        "scope": scope,
        "difficulty": "RECOGNITION",
        "content": content,
        "explanation": "Lời giải",
        "choice_content": list(choices),
        "choice_id": list(ids or [""] * len(choices)),
        **extra,
    }
    if correct is not None:
        data["correct"] = str(correct)
    return data


def make_question(db, subject_id, chapter_id=None, lesson_id=None, difficulty=Difficulty.RECOGNITION):
    async def _make(s):
        q = Question(
            subject_id=subject_id,
            chapter_id=chapter_id,
            lesson_id=lesson_id,
            difficulty=difficulty,
            content="Nội dung",
            choices=[{"id": "a", "content": "x", "is_correct": True}, {"id": "b", "content": "y", "is_correct": False}],
        )
        s.add(q)
        await s.flush()
        return q.id

    return db(_make)


# ---------------------------------------------------------------- access


def test_student_is_forbidden_everywhere_in_admin(client, make_user):
    make_user("hs@example.com", role=UserRole.STUDENT)
    client.post("/login", data={"email": "hs@example.com", "password": "password123"})
    for path in ["/admin", "/admin/subjects", "/admin/questions", "/admin/questions/new", "/admin/exams"]:
        assert client.get(path).status_code == 403, path
    assert client.post("/admin/subjects", data={"name": "Hack"}).status_code == 403


# ---------------------------------------------------------------- subjects / chapters / lessons


def test_create_subject_generates_unique_vietnamese_slug(admin_client, db):
    admin_client.post("/admin/subjects", data={"name": "Vật lí 11"})
    admin_client.post("/admin/subjects", data={"name": "Vật lí 11"})
    slugs = db(lambda s: s.scalars(select(Subject.slug).order_by(Subject.id)))
    assert list(slugs) == ["vat-li-11", "vat-li-11-2"]


def test_chapters_and_lessons_can_be_added_reordered_and_renamed(admin_client, db, tree):
    admin_client.post(f"/admin/subjects/{tree['physics']}/chapters", data={"name": "Chương 3"})
    chapters = db(
        lambda s: s.scalars(select(Chapter).where(Chapter.subject_id == tree["physics"]).order_by(Chapter.position))
    )
    names = [c.name for c in chapters]
    assert names == ["Chương 1", "Chương 2", "Chương 3"]

    new_id = chapters[2].id
    admin_client.post(f"/admin/chapters/{new_id}/move", data={"direction": "up"})
    admin_client.post(f"/admin/chapters/{new_id}", data={"name": "Chương 2b"})
    names = db(
        lambda s: s.scalars(
            select(Chapter.name).where(Chapter.subject_id == tree["physics"]).order_by(Chapter.position)
        )
    )
    assert list(names) == ["Chương 1", "Chương 2b", "Chương 2"]

    admin_client.post(f"/admin/chapters/{tree['c1']}/lessons", data={"name": "Bài 3"})
    admin_client.post(f"/admin/lessons/{tree['l1']}/move", data={"direction": "down"})
    lessons = db(
        lambda s: s.scalars(select(Lesson.name).where(Lesson.chapter_id == tree["c1"]).order_by(Lesson.position))
    )
    assert list(lessons) == ["Bài 2", "Bài 1", "Bài 3"]


def test_cannot_delete_chapter_or_subject_that_has_questions(admin_client, db, tree):
    make_question(db, tree["physics"], tree["c1"], tree["l1"])

    r = admin_client.post(f"/admin/chapters/{tree['c1']}/delete")
    assert "Không thể xoá chương" in r.text
    r = admin_client.post(f"/admin/lessons/{tree['l1']}/delete")
    assert "Không thể xoá bài" in r.text
    r = admin_client.post(f"/admin/subjects/{tree['physics']}/delete")
    assert "Không thể xoá" in r.text
    assert db(lambda s: s.get(Chapter, tree["c1"])) is not None

    # An unused chapter can be deleted, and its lessons go with it.
    admin_client.post(f"/admin/chapters/{tree['c2']}/delete")
    assert db(lambda s: s.get(Chapter, tree["c2"])) is None


# ---------------------------------------------------------------- questions


def test_create_question_derives_chapter_and_subject_from_lesson(admin_client, db, tree):
    r = admin_client.post(
        "/admin/questions",
        data=question_form(f"l:{tree['l2']}", content=r"Tính $T = \dfrac{2\pi}{\omega}$", correct=2),
        follow_redirects=False,
    )
    assert r.status_code == 303
    q = db(lambda s: s.scalar(select(Question)))
    assert (q.subject_id, q.chapter_id, q.lesson_id) == (tree["physics"], tree["c1"], tree["l2"])
    assert q.content == r"Tính $T = \dfrac{2\pi}{\omega}$"
    assert [c["is_correct"] for c in q.choices] == [False, False, True, False]
    ids = [c["id"] for c in q.choices]
    assert all(ids) and len(set(ids)) == 4


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"correct": None}, "Chưa có đáp án đúng"),
        ({"choices": ("Chỉ một",)}, "từ 2 đến 6 lựa chọn"),
        ({"choices": ("A", " ", "C")}, "Có lựa chọn bị trống"),
        ({"content": "   "}, "Thiếu nội dung câu hỏi"),
        ({"scope": "l:999999"}, "Vui lòng chọn môn"),
    ],
)
def test_question_validation(admin_client, db, tree, overrides, message):
    data = question_form(overrides.pop("scope", f"c:{tree['c1']}"), **overrides)
    r = admin_client.post("/admin/questions", data=data)
    assert r.status_code == 400
    assert message in r.text
    assert db(lambda s: s.scalar(select(Question.id))) is None


def test_editing_question_keeps_existing_choice_ids(admin_client, db, tree):
    qid = make_question(db, tree["physics"], tree["c1"])
    r = admin_client.post(
        f"/admin/questions/{qid}",
        data=question_form(f"c:{tree['c1']}", choices=("x", "y", "z"), ids=["a", "b", ""], correct=1),
        follow_redirects=False,
    )
    assert r.status_code == 303
    q = db(lambda s: s.get(Question, qid))
    assert [c["id"] for c in q.choices][:2] == ["a", "b"]
    assert q.choices[2]["id"] not in ("", "a", "b")
    assert [c["is_correct"] for c in q.choices] == [False, True, False]


def test_question_list_filters_by_scope_and_renders_latex_source_escaped(admin_client, db, tree):
    q1 = make_question(db, tree["physics"], tree["c1"], tree["l1"])
    make_question(db, tree["math"])

    async def set_content(s):
        (await s.get(Question, q1)).content = "<script>alert(1)</script> $x^2$"

    db(set_content)
    page = admin_client.get(f"/admin/questions?scope=l:{tree['l1']}")
    assert page.status_code == 200
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text


# ---------------------------------------------------------------- exams


def exam_form(scope, title="Đề luyện tập", duration="15", max_attempts="", published=False):
    data = {"title": title, "scope": scope, "duration_minutes": duration, "max_attempts": max_attempts}
    if published:
        data["is_published"] = "on"
    return data


def test_exam_lifecycle(admin_client, db, tree):
    q1 = make_question(db, tree["physics"], tree["c1"], tree["l1"])
    q2 = make_question(db, tree["physics"], tree["c1"], tree["l2"], Difficulty.APPLICATION)
    other_subject_q = make_question(db, tree["math"])

    r = admin_client.post("/admin/exams", data=exam_form(f"c:{tree['c1']}"), follow_redirects=False)
    assert r.status_code == 303
    exam_id = int(r.headers["location"].rsplit("/", 1)[1])
    exam = db(lambda s: s.get(Exam, exam_id))
    assert (exam.duration_seconds, exam.max_attempts, exam.is_published) == (900, None, False)

    # Cannot publish an empty exam.
    r = admin_client.post(f"/admin/exams/{exam_id}", data=exam_form(f"c:{tree['c1']}", published=True))
    assert r.status_code == 400
    assert "ít nhất 1 câu hỏi" in r.text

    # Questions from another subject are ignored.
    admin_client.post(
        f"/admin/exams/{exam_id}/questions", data={"question_id": [str(q1), str(q2), str(other_subject_q)]}
    )
    order = db(
        lambda s: s.scalars(
            select(ExamQuestion.question_id).where(ExamQuestion.exam_id == exam_id).order_by(ExamQuestion.position)
        )
    )
    assert list(order) == [q1, q2]

    admin_client.post(f"/admin/exams/{exam_id}/questions/{q2}/move", data={"direction": "up"})
    order = db(
        lambda s: s.scalars(
            select(ExamQuestion.question_id).where(ExamQuestion.exam_id == exam_id).order_by(ExamQuestion.position)
        )
    )
    assert list(order) == [q2, q1]

    r = admin_client.post(
        f"/admin/exams/{exam_id}", data=exam_form(f"c:{tree['c1']}", max_attempts="3", published=True)
    )
    assert r.status_code == 200
    exam = db(lambda s: s.get(Exam, exam_id))
    assert (exam.max_attempts, exam.is_published) == (3, True)

    # Moving an exam that has questions to another subject is rejected.
    r = admin_client.post(f"/admin/exams/{exam_id}", data=exam_form(f"s:{tree['math']}", published=True))
    assert "không thể chuyển sang môn khác" in r.text

    # A question used in an exam cannot be deleted.
    r = admin_client.post(f"/admin/questions/{q1}/delete")
    assert "đang được dùng trong đề" in r.text
    assert db(lambda s: s.get(Question, q1)) is not None

    detail = admin_client.get(f"/admin/exams/{exam_id}")
    assert detail.status_code == 200
    assert "Câu hỏi trong đề (2)" in detail.text


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"duration": "0"}, "từ 1 đến 600 phút"),
        ({"duration": "abc"}, "từ 1 đến 600 phút"),
        ({"max_attempts": "0"}, "Số lần làm tối đa"),
        ({"title": " "}, "Tên đề không hợp lệ"),
    ],
)
def test_exam_validation(admin_client, tree, overrides, message):
    r = admin_client.post("/admin/exams", data=exam_form(f"s:{tree['physics']}", **overrides))
    assert r.status_code == 400
    assert message in r.text
