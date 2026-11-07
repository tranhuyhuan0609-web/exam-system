import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app import question_rules as rules
from app.ai.schemas import EssayGrade, Solution
from app.application.categories import create_category
from app.application.essay_grading import grade_pending_essays
from app.models import Category, Exam, ExamAttempt, ExamQuestion, GradingStatus, Question, QuestionType


class FakeGrader:
    def __init__(self, points=0.8, fail=False):
        self.points, self.fail, self.calls = points, fail, []

    async def grade_essays(self, items):
        self.calls.append(items)
        if self.fail:
            raise RuntimeError("model down")
        return [
            EssayGrade(
                number=i.number, reference_solution="LOI_GIAI_AI", feedback="Em làm đúng hướng", points=self.points
            )
            for i in items
        ]


@pytest.fixture
def mixed_exam(db):
    """Fixed exam: 2 multiple choice, 1 true/false, 1 short answer, 1 essay (2 points). 20 minutes."""

    async def _make(s):
        subject = await create_category(s, "Vật lí 12", slug="vat-li-12")

        def q(kind, n, **extra):
            return Question(
                root_id=subject.id,
                category_id=subject.id,
                difficulty="UNDERSTANDING",
                question_type=kind,
                content=f"{kind.value} {n}",
                **extra,
            )

        questions = [
            q(
                QuestionType.MULTIPLE_CHOICE,
                1,
                choices=[
                    {"id": "a", "content": "x", "is_correct": True},
                    {"id": "b", "content": "y", "is_correct": False},
                ],
            ),
            q(
                QuestionType.MULTIPLE_CHOICE,
                2,
                choices=[
                    {"id": "c", "content": "x", "is_correct": True},
                    {"id": "d", "content": "y", "is_correct": False},
                ],
            ),
            q(
                QuestionType.TRUE_FALSE,
                1,
                choices=[{"id": f"s{i}", "content": "ý", "is_correct": True} for i in range(4)],
            ),
            q(QuestionType.SHORT_ANSWER, 1, choices=[], accepted_answers=["30"]),
            q(QuestionType.ESSAY, 1, choices=[], points=Decimal("2"), explanation="LOI_GIAI_MAU"),
        ]
        s.add_all(questions)
        exam = Exam(
            root_id=subject.id, category_id=subject.id, title="Đề đủ 4 phần", duration_seconds=1200, is_published=True
        )
        s.add(exam)
        await s.flush()
        s.add_all(ExamQuestion(exam_id=exam.id, question_id=x.id, position=i) for i, x in enumerate(questions, 1))
        return exam.id, {x.question_type.value + str(x.content[-1]): x.id for x in questions}

    return db(_make)


@pytest.fixture
def student(client, make_user):
    make_user("hs@example.com")
    client.post("/login", data={"email": "hs@example.com", "password": "password123"})
    return client


def start(client, exam_id, parts=None):
    data = {"custom": "1", "parts": parts} if parts is not None else {}
    r = client.post(f"/exams/{exam_id}/start", data=data, follow_redirects=False)
    return r, (
        int(r.headers["location"].rsplit("/", 1)[1]) if r.headers.get("location", "").startswith("/attempts/") else None
    )


# ---------------------------------------------------------------- rules


def test_essay_rules():
    assert rules.validate("ESSAY", "Giải bài toán", []) == []
    assert rules.validate("ESSAY", "", []) == ["Thiếu nội dung câu hỏi."]
    assert rules.max_points("ESSAY") == Decimal("1") and rules.max_points("ESSAY", "2.5") == Decimal("2.5")
    assert rules.parse_essay_points("1,5") == (Decimal("1.50"), None)
    assert rules.parse_essay_points("") == (None, None)
    assert rules.parse_essay_points("50")[1] and rules.parse_essay_points("abc")[1]


# ---------------------------------------------------------------- student chooses the parts


def test_exam_page_offers_part_presets(student, mixed_exam):
    exam_id, _ = mixed_exam
    page = student.get(f"/exams/{exam_id}").text
    for label in ("Chỉ trắc nghiệm", "TN + Đúng/Sai", "TN + ĐS + Trả lời ngắn", "Đủ các phần", "Tự luận"):
        assert label in page


def test_choosing_only_multiple_choice_builds_a_shorter_paper(student, db, mixed_exam):
    exam_id, ids = mixed_exam
    _, attempt_id = start(student, exam_id, ["MULTIPLE_CHOICE"])
    paper = student.get(f"/api/attempts/{attempt_id}/paper").json()
    assert [q["type"] for q in paper["questions"]] == ["MULTIPLE_CHOICE", "MULTIPLE_CHOICE"]
    attempt = db(lambda s: s.get(ExamAttempt, attempt_id))
    assert attempt.paper_snapshot["parts"] == ["MULTIPLE_CHOICE"]
    # 2 of 5 questions -> 2/5 of 20 minutes = 8 minutes.
    assert attempt.expires_at - attempt.started_at == timedelta(minutes=8)
    data = student.post(
        f"/api/attempts/{attempt_id}/submit", json={"answers": {str(ids["MULTIPLE_CHOICE1"]): "a"}}
    ).json()
    assert data["score"] == "5.00"  # 1 of 2 multiple-choice questions, scaled to 10


def test_parts_validation_and_admin_switch(student, db, mixed_exam):
    exam_id, _ = mixed_exam
    r, attempt_id = start(student, exam_id, [])
    assert attempt_id is None and "chọn ít nhất một phần" in student.get(r.headers["location"]).text
    db(lambda s: s.execute(update(Exam).values(allow_custom_parts=False)))
    _, attempt_id = start(student, exam_id, ["MULTIPLE_CHOICE"])
    assert len(student.get(f"/api/attempts/{attempt_id}/paper").json()["questions"]) == 5  # full exam


# ---------------------------------------------------------------- essays and AI grading


def test_essay_answer_is_graded_by_the_ai_worker(student, db, mixed_exam):
    exam_id, ids = mixed_exam
    _, attempt_id = start(student, exam_id)
    paper = student.get(f"/api/attempts/{attempt_id}/paper")
    essay = next(q for q in paper.json()["questions"] if q["type"] == "ESSAY")
    assert essay["choices"] == [] and "LOI_GIAI_MAU" not in paper.text

    answers = {str(ids["MULTIPLE_CHOICE1"]): "a", str(ids["ESSAY1"]): "Áp dụng $pV = const$ được $V_2 = 8$ lít"}
    student.patch(f"/api/attempts/{attempt_id}/answers", json={"changes": answers})  # essay text is auto-saved
    data = student.post(f"/api/attempts/{attempt_id}/submit", json={"answers": answers}).json()
    attempt = db(lambda s: s.get(ExamAttempt, attempt_id))
    assert attempt.grading_status == GradingStatus.PENDING_AI
    # Provisional: 0.25 of 0.25+0.25+1+0.25+2 = 3.75 points -> 0.67 / 10.
    assert data["score"] == "0.67"
    page = student.get(f"/attempts/{attempt_id}/result").text
    assert "Hệ thống AI đang chấm" in page and "tạm tính" in page

    grader = FakeGrader(points=1.6)  # rounded to 1.5
    assert asyncio.run(grade_pending_essays(grader)) == 1
    (item,) = grader.calls[0]
    assert (item.reference_solution, item.max_points) == ("LOI_GIAI_MAU", 2.0)
    attempt = db(lambda s: s.get(ExamAttempt, attempt_id))
    assert attempt.grading_status == GradingStatus.COMPLETE
    assert attempt.essay_grades[str(ids["ESSAY1"])]["points"] == "1.50"
    assert attempt.score == Decimal("4.67")  # (0.25 + 1.5) / 3.75 * 10
    assert "Hệ thống AI đang chấm" not in student.get(f"/attempts/{attempt_id}/result").text
    page = student.get(f"/attempts/{attempt_id}/solutions").text
    assert "Em làm đúng hướng" in page and "Nhận xét của AI · 1.5/2 điểm" in page and "AI đang chấm" not in page
    assert student.get(f"/api/attempts/{attempt_id}/grading").json()["grading_status"] == "COMPLETE"


def test_ai_grade_is_clamped_and_failures_give_up_after_three_tries(student, db, mixed_exam):
    exam_id, ids = mixed_exam
    _, attempt_id = start(student, exam_id)
    student.post(f"/api/attempts/{attempt_id}/submit", json={"answers": {str(ids["ESSAY1"]): "bài làm"}})
    for _ in range(3):
        asyncio.run(grade_pending_essays(FakeGrader(fail=True)))
    attempt = db(lambda s: s.get(ExamAttempt, attempt_id))
    assert (attempt.grading_status, attempt.grading_tries) == (GradingStatus.FAILED, 3)

    db(lambda s: s.execute(update(ExamAttempt).values(grading_status=GradingStatus.PENDING_AI, grading_tries=0)))
    asyncio.run(grade_pending_essays(FakeGrader(points=99)))
    assert db(lambda s: s.get(ExamAttempt, attempt_id)).essay_grades[str(ids["ESSAY1"])]["points"] == "2.00"


def test_unanswered_essay_needs_no_ai(student, db, mixed_exam):
    exam_id, _ = mixed_exam
    _, attempt_id = start(student, exam_id)
    student.post(f"/api/attempts/{attempt_id}/submit", json={"answers": {}})
    assert db(lambda s: s.get(ExamAttempt, attempt_id)).grading_status == GradingStatus.COMPLETE


# ---------------------------------------------------------------- admin


def test_admin_creates_essay_question_and_ai_solves_it(admin_client, db, monkeypatch, mixed_exam):
    subject_id = db(lambda s: s.scalar(select(Category.id).where(Category.parent_id.is_(None))))
    r = admin_client.post(
        "/admin/questions",
        data={
            "scope": f"c:{subject_id}",
            "question_type": "ESSAY",
            "difficulty": "APPLICATION",
            "content": "Tính độ sâu của bể nước",
            "points": "1,5",
            "explanation": "Lời giải mẫu",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    q = db(lambda s: s.scalar(select(Question).where(Question.content == "Tính độ sâu của bể nước")))
    assert (q.question_type, q.points, q.choices) == (QuestionType.ESSAY, Decimal("1.50"), [])

    class FakeGemini:
        def __init__(self, *args, **kwargs):
            pass

        async def solve(self, questions, figures):
            assert questions[0].type == "ESSAY" and questions[0].choices == []
            return [
                Solution(
                    number=1,
                    step_by_step_solution="$h = 30$ m",
                    final_value="30 m",
                    matches_an_option=True,
                    correct_label=None,
                    short_answer=None,
                    difficulty="APPLICATION",
                )
            ]

    monkeypatch.setattr("app.ai.provider.GeminiProvider", FakeGemini)
    r = admin_client.post(
        "/admin/questions/ai-solve", json={"type": "ESSAY", "content": "Tính độ sâu", "choices": ["x"]}
    )
    assert r.status_code == 200
    assert r.json()["explanation"] == "$h = 30$ m" and r.json()["difficulty"] == "APPLICATION"
    assert admin_client.post("/admin/questions/ai-solve", json={"type": "ESSAY", "content": " "}).status_code == 400
