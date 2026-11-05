import pytest
from sqlalchemy import select

from app.models import Category, Difficulty, Exam, ExamQuestion, Question, UserRole
from tests.factories import make_tree


@pytest.fixture
def tree(db):
    """Two subjects; the first has two chapters, chapter 1 has two lessons."""

    async def _make(s):
        t = await make_tree(s, {"Vật lí 11": {"Chương 1": {"Bài 1": {}, "Bài 2": {}}, "Chương 2": {}}, "Toán 11": {}})
        return {
            "physics": t["Vật lí 11"].id,
            "math": t["Toán 11"].id,
            "c1": t["Chương 1"].id,
            "c2": t["Chương 2"].id,
            "l1": t["Bài 1"].id,
            "l2": t["Bài 2"].id,
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


def make_question(db, root_id, category_id=None, difficulty=Difficulty.RECOGNITION):
    async def _make(s):
        q = Question(
            root_id=root_id,
            category_id=category_id or root_id,
            difficulty=difficulty,
            content="Nội dung",
            choices=[{"id": "a", "content": "x", "is_correct": True}, {"id": "b", "content": "y", "is_correct": False}],
        )
        s.add(q)
        await s.flush()
        return q.id

    return db(_make)


def children(db, parent_id):
    return [
        c.name
        for c in db(
            lambda s: s.scalars(select(Category).where(Category.parent_id == parent_id).order_by(Category.position))
        )
    ]


# ---------------------------------------------------------------- access


def test_student_is_forbidden_everywhere_in_admin(client, make_user):
    make_user("hs@example.com", role=UserRole.STUDENT)
    client.post("/login", data={"email": "hs@example.com", "password": "password123"})
    for path in ["/admin", "/admin/categories", "/admin/questions", "/admin/questions/new", "/admin/exams"]:
        assert client.get(path).status_code == 403, path
    assert client.post("/admin/categories", data={"name": "Hack"}).status_code == 403


# ---------------------------------------------------------------- category tree


def test_create_subject_generates_unique_vietnamese_slug(admin_client, db):
    admin_client.post("/admin/categories", data={"name": "Vật lí 11"})
    admin_client.post("/admin/categories", data={"name": "Vật lí 11"})
    slugs = db(lambda s: s.scalars(select(Category.slug).order_by(Category.id)))
    assert list(slugs) == ["vat-li-11", "vat-li-11-2"]


def test_any_depth_reorder_and_rename(admin_client, db, tree):
    admin_client.post("/admin/categories", data={"name": "Chương 3", "parent_id": str(tree["physics"])})
    assert children(db, tree["physics"]) == ["Chương 1", "Chương 2", "Chương 3"]
    new_id = db(lambda s: s.scalar(select(Category.id).where(Category.name == "Chương 3")))
    admin_client.post(f"/admin/categories/{new_id}/order", data={"direction": "up"})
    admin_client.post(f"/admin/categories/{new_id}", data={"name": "Chương 2b"})
    assert children(db, tree["physics"]) == ["Chương 1", "Chương 2b", "Chương 2"]

    # A fourth level (topic under a lesson), and reordering lessons.
    admin_client.post("/admin/categories", data={"name": "Con lắc lò xo", "parent_id": str(tree["l1"])})
    topic = db(lambda s: s.scalar(select(Category).where(Category.name == "Con lắc lò xo")))
    assert (topic.depth, topic.path) == (3, f"/{tree['physics']}/{tree['c1']}/{tree['l1']}/{topic.id}/")
    admin_client.post(f"/admin/categories/{tree['l1']}/order", data={"direction": "down"})
    assert children(db, tree["c1"]) == ["Bài 2", "Bài 1"]
    assert "Con lắc lò xo" in admin_client.get("/admin/categories").text


def test_move_branch_keeps_paths_consistent(admin_client, db, tree):
    admin_client.post("/admin/categories", data={"name": "Con lắc", "parent_id": str(tree["l1"])})
    admin_client.post(f"/admin/categories/{tree['l1']}/move", data={"parent_id": str(tree["c2"])})
    lesson = db(lambda s: s.get(Category, tree["l1"]))
    topic = db(lambda s: s.scalar(select(Category).where(Category.name == "Con lắc")))
    assert lesson.path == f"/{tree['physics']}/{tree['c2']}/{tree['l1']}/" and lesson.depth == 2
    assert topic.path == lesson.path + f"{topic.id}/" and topic.depth == 3
    # Cannot move a branch inside itself.
    r = admin_client.post(f"/admin/categories/{tree['c2']}/move", data={"parent_id": str(topic.id)})
    assert "chính nó" in r.text
    # Moving an empty branch to another subject is fine; one with questions is not.
    admin_client.post(f"/admin/categories/{tree['c2']}/move", data={"parent_id": str(tree["math"])})
    assert db(lambda s: s.get(Category, tree["l1"])).root_id == tree["math"]
    make_question(db, tree["physics"], tree["l2"])
    r = admin_client.post(f"/admin/categories/{tree['c1']}/move", data={"parent_id": str(tree["math"])})
    assert "chỉ chuyển được trong cùng môn" in r.text


def test_cannot_delete_a_branch_that_has_questions(admin_client, db, tree):
    make_question(db, tree["physics"], tree["l1"])
    for target in ("c1", "l1", "physics"):
        r = admin_client.post(f"/admin/categories/{tree[target]}/delete")
        assert "Không thể xoá" in r.text
    assert db(lambda s: s.get(Category, tree["c1"])) is not None
    # An unused branch can be deleted, with everything below it.
    admin_client.post("/admin/categories", data={"name": "Bài 9", "parent_id": str(tree["c2"])})
    admin_client.post(f"/admin/categories/{tree['c2']}/delete")
    assert db(lambda s: s.get(Category, tree["c2"])) is None
    assert db(lambda s: s.scalar(select(Category.id).where(Category.name == "Bài 9"))) is None


# ---------------------------------------------------------------- questions


def test_create_question_derives_chapter_and_subject_from_lesson(admin_client, db, tree):
    r = admin_client.post(
        "/admin/questions",
        data=question_form(f"c:{tree['l2']}", content=r"Tính $T = \dfrac{2\pi}{\omega}$", correct=2),
        follow_redirects=False,
    )
    assert r.status_code == 303
    q = db(lambda s: s.scalar(select(Question)))
    assert (q.root_id, q.category_id) == (tree["physics"], tree["l2"])
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
    q1 = make_question(db, tree["physics"], tree["l1"])
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
    q1 = make_question(db, tree["physics"], tree["l1"])
    q2 = make_question(db, tree["physics"], tree["l2"], Difficulty.APPLICATION)
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
    r = admin_client.post(f"/admin/exams/{exam_id}", data=exam_form(f"c:{tree['math']}", published=True))
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
    r = admin_client.post("/admin/exams", data=exam_form(f"c:{tree['physics']}", **overrides))
    assert r.status_code == 400
    assert message in r.text
