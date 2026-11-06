"""Exam maker: a teacher uploads an exam matrix (Excel, Word, PDF, photo); AI maps it onto the table of contents;
the teacher checks the rows and creates a scheduled test drawn from the bank by that matrix."""

import asyncio
import io
import json

from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import select

from app.application import matrix_import
from app.application.categories import create_category
from app.main import app
from app.models import Difficulty, Exam, Question, QuestionType, UserRole
from app.plans import FREE_MATRICES_PER_DAY
from app.routers import maker


def _xlsx() -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.append(["Chủ đề", "Nhận biết TN", "Thông hiểu TN", "Vận dụng TL", "Tổng"])
    sheet.append(["Động học", 4, 2, 1, 7])
    sheet.append(["Động lực học", 3, None, None, 3])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


class FakeModel:
    name, model = "groq", "gpt-oss"

    def __init__(self, ids):
        self.ids, self.sent = ids, []

    async def available(self):
        return True

    async def _ask(self, system, user, items):
        self.sent.append(user)
        kinematics, dynamics = self.ids
        return json.dumps(
            {
                "title": "Kiểm tra giữa kì I",
                "minutes": 45,
                "rows": [
                    {
                        "topic": "Động học",
                        "category": f"c:{kinematics}",
                        "level": "RECOGNITION",
                        "type": "MULTIPLE_CHOICE",
                        "count": 4,
                    },
                    {
                        "topic": "Động học",
                        "category": f"c:{kinematics}",
                        "level": "UNDERSTANDING",
                        "type": "MULTIPLE_CHOICE",
                        "count": 2,
                    },
                    {
                        "topic": "Động lực học",
                        "category": f"c:{dynamics}",
                        "level": "RECOGNITION",
                        "type": "MULTIPLE_CHOICE",
                        "count": 3,
                    },
                    {"topic": "Tổng", "category": "c:999999", "level": "APPLICATION", "type": "ESSAY", "count": 0},
                ],
            }
        )


def _bank(db) -> tuple[int, int, int]:
    async def make(s):
        root = await create_category(s, "Vật lí 10", slug="vat-li-10")
        kinematics = await create_category(s, "Động học", parent=root)
        dynamics = await create_category(s, "Động lực học", parent=root)
        for category, level, n in (
            (kinematics, Difficulty.RECOGNITION, 5),
            (kinematics, Difficulty.UNDERSTANDING, 2),
            (dynamics, Difficulty.RECOGNITION, 2),
        ):
            for i in range(n):
                s.add(
                    Question(
                        root_id=root.id,
                        category_id=category.id,
                        difficulty=level,
                        question_type=QuestionType.MULTIPLE_CHOICE,
                        content=f"{category.name} {level} {i}",
                        choices=[{"id": c, "content": c, "is_correct": c == "a"} for c in "abcd"],
                    )
                )
        await s.flush()
        return root.id, kinematics.id, dynamics.id

    return db(make)


def _teacher(make_user):
    make_user("gv@example.com", role=UserRole.TEACHER)
    client = TestClient(app)
    client.post("/login", data={"email": "gv@example.com", "password": "password123"})
    return client


def test_tables_in_excel_are_read_as_text():
    text, images = asyncio.run(matrix_import.content_of("ma-tran.xlsx", _xlsx()))
    assert images == [] and "Động học | 4 | 2 | 1 | 7" in text and "Động lực học | 3" in text


def test_a_teacher_makes_a_test_from_an_uploaded_matrix(db, make_user, monkeypatch):
    root, kinematics, dynamics = _bank(db)
    model = FakeModel((kinematics, dynamics))

    async def models():
        return [model], [], None

    monkeypatch.setattr(maker, "_models", models)
    teacher = _teacher(make_user)
    assert "Tạo đề từ ma trận" in teacher.get("/teacher").text
    page = teacher.post(
        "/teacher/make/matrix",
        data={"root_id": root},
        files={"file": ("ma-tran.xlsx", _xlsx(), "application/octet-stream")},
    )
    assert page.status_code == 200 and "Kiểm tra giữa kì I" in page.text
    assert f"c:{dynamics}" in model.sent[0] and "Động học | 4 | 2 | 1 | 7" in model.sent[0]  # the TOC and the table
    assert page.text.count('name="topic_') == 3  # the "Tổng" row (0 questions) is dropped
    assert 'class="is-short"' in page.text  # 3 asked for "Động lực học", the bank has 2

    form = {
        "root_id": root,
        "row_count": 3,
        "title": "Kiểm tra giữa kì I",
        "starts_at": "2030-01-01T08:00",
        "ends_at": "2030-01-01T10:00",
        "minutes": 45,
        "same_paper": "1",
        "shuffle": "1",
    }
    for i, (scope, level, count) in enumerate(
        [(kinematics, "RECOGNITION", 4), (kinematics, "UNDERSTANDING", 2), (dynamics, "RECOGNITION", 3)]
    ):
        form |= {
            f"topic_{i}": "x",
            f"scope_{i}": f"c:{scope}",
            f"level_{i}": level,
            f"type_{i}": "MULTIPLE_CHOICE",
            f"count_{i}": count,
        }
    short = teacher.post("/teacher/make/matrix/create", data=form)
    assert short.status_code == 400 and "Dòng 3: ngân hàng chỉ có 2 câu" in short.text
    form["count_2"] = 2
    created = teacher.post("/teacher/make/matrix/create", data=form, follow_redirects=False)
    assert created.status_code == 303
    exam = db(lambda s: s.scalar(select(Exam)))
    assert exam.title == "Kiểm tra giữa kì I" and len(exam.matrix) == 3 and exam.root_id == root
    assert exam.description.startswith("Theo ma trận đề · 8")
    detail = teacher.get(created.headers["location"])
    assert detail.status_code == 200 and exam.access_code in detail.text


def test_free_teachers_have_a_few_matrices_a_day(db, make_user, monkeypatch):
    root, kinematics, dynamics = _bank(db)

    async def models():
        return [FakeModel((kinematics, dynamics))], [], None

    monkeypatch.setattr(maker, "_models", models)
    teacher = _teacher(make_user)
    for _ in range(FREE_MATRICES_PER_DAY):
        files = {"file": ("m.xlsx", _xlsx(), "application/octet-stream")}
        assert teacher.post("/teacher/make/matrix", data={"root_id": root}, files=files).status_code == 200
    files = {"file": ("m.xlsx", _xlsx(), "application/octet-stream")}
    blocked = teacher.post("/teacher/make/matrix", data={"root_id": root}, files=files)
    assert "mỗi ngày" in blocked.text and "Còn 0" not in blocked.text
    assert "Hôm nay còn 0 lượt" in teacher.get("/teacher/make").text
    wrong = teacher.post("/teacher/make/matrix", data={"root_id": root}, files={"file": ("m.txt", b"x", "text/plain")})
    assert wrong.status_code == 200  # back to the form with a message


def _rows_form(root, kinematics, **extra):
    return {
        "root_id": root,
        "row_count": 1,
        "topic_0": "Động học",
        "scope_0": f"c:{kinematics}",
        "level_0": "RECOGNITION",
        "type_0": "MULTIPLE_CHOICE",
        "count_0": 3,
        "title": "Luyện tập Động học",
        "minutes": 15,
        "shuffle": "1",
        **extra,
    }


def test_admins_publish_on_the_site_and_teachers_keep_their_tests_to_themselves(db, admin_client, make_user):
    root, kinematics, _ = _bank(db)
    public = admin_client.post(
        "/teacher/make/matrix/create", data=_rows_form(root, kinematics, publish="public"), follow_redirects=False
    )
    assert public.status_code == 303 and public.headers["location"].startswith("/admin/exams/")
    exam = db(lambda s: s.scalar(select(Exam)))
    assert (exam.visibility.value, exam.exam_mode.value, exam.is_published) == ("PUBLIC", "PRACTICE", True)
    assert exam.access_code is None and exam.available_until is None and exam.max_attempts is None

    teacher = _teacher(make_user)
    forbidden = teacher.post("/teacher/make/matrix/create", data=_rows_form(root, kinematics, publish="public"))
    assert forbidden.status_code == 403
    own = teacher.post(
        "/teacher/make/matrix/create",
        data=_rows_form(root, kinematics, starts_at="2030-01-01T08:00", ends_at="2030-01-01T09:00"),
        follow_redirects=False,
    )
    test_id = int(own.headers["location"].rsplit("/", 1)[1])
    make_user("gv2@example.com", role=UserRole.TEACHER)
    other = TestClient(app)
    other.post("/login", data={"email": "gv2@example.com", "password": "password123"})
    assert other.get(f"/teacher/tests/{test_id}").status_code == 404  # only its teacher sees it
    assert "Luyện tập Động học" not in other.get("/teacher").text
    assert "Luyện tập Động học" in teacher.get("/teacher").text
