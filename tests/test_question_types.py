from decimal import Decimal

import pytest
from sqlalchemy import select

from app import question_rules as rules
from app.application.categories import create_category
from app.attempts import clean_answers, grade
from app.models import Exam, ExamQuestion, Question, QuestionType
from app.question_import import parse_file, parse_text

# ---------------------------------------------------------------- rules


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1.50", "1,5"), ("01,5", "1,5"), (" 30 ", "30"), ("-0,0", "0"), ("+3", "3"), ("−2,5", "-2,5"), ("abc", "abc")],
)
def test_normalize_short_answer(raw, expected):
    assert rules.normalize_short_answer(raw) == expected


def test_short_answer_matching_and_number_extraction():
    assert rules.short_answer_matches("1.5", ["1,50"])
    assert rules.short_answer_matches("30", ["29", "30"])
    assert not rules.short_answer_matches("", ["0"])
    assert not rules.short_answer_matches(None, ["0"])
    assert rules.extract_number("30 cm") == "30"
    assert rules.extract_number("≈ 1,25.10^5 Pa") == "1,25"
    assert rules.extract_number("không có số") is None
    assert rules.split_accepted("1,25; 1,3 |1.3") == ["1,25", "1,3", "1.3"]


@pytest.mark.parametrize(
    "text", ["Đ S Đ S", "ĐSĐS", "a) Đúng b) Sai c) Đúng d) Sai", "a-Đ, b-S, c-Đ, d-S", "D S D S", "T F T F"]
)
def test_parse_truths_accepts_common_formats(text):
    assert rules.parse_truths(text) == [True, False, True, False]


def test_true_false_points_follow_thpt_table():
    assert [rules.true_false_points(n) for n in range(5)] == [
        Decimal("0"),
        Decimal("0.1"),
        Decimal("0.25"),
        Decimal("0.5"),
        Decimal("1"),
    ]


def test_validate_per_type():
    assert rules.validate("TRUE_FALSE", "Hỏi", ["a", "b", "c"], truths=[True, False, True])[0].startswith(
        "Câu Đúng/Sai cần đúng 4 ý"
    )
    assert "Đúng hoặc Sai" in " ".join(rules.validate("TRUE_FALSE", "Hỏi", ["a", "b", "c", "d"], truths=[True, None]))
    assert rules.validate("TRUE_FALSE", "Hỏi", ["a", "b", "c", "d"], truths=[True, False, False, True]) == []
    assert "cần đáp án" in rules.validate("SHORT_ANSWER", "Hỏi", [], accepted=[])[0]
    assert rules.validate("SHORT_ANSWER", "Hỏi", [], accepted=["30"]) == []


# ---------------------------------------------------------------- grading


SNAPSHOT = {
    "questions": [
        {
            "id": 1,
            "type": "MULTIPLE_CHOICE",
            "choices": [{"id": "a", "is_correct": True}, {"id": "b", "is_correct": False}],
        },
        {
            "id": 2,
            "type": "TRUE_FALSE",
            "choices": [
                {"id": "s1", "is_correct": True},
                {"id": "s2", "is_correct": False},
                {"id": "s3", "is_correct": True},
                {"id": "s4", "is_correct": False},
            ],
        },
        {"id": 3, "type": "SHORT_ANSWER", "choices": [], "accepted_answers": ["1,5"]},
    ]
}


def test_grading_mixed_paper_uses_thpt_points_scaled_to_ten():
    answers = {"1": "a", "2": {"s1": True, "s2": False, "s3": False, "s4": False}, "3": "1.50"}
    result = grade(SNAPSHOT, answers)
    # 0.25 (MC) + 0.5 (3/4 statements) + 0.25 (short answer) = 1.0 of 1.5
    assert (result.points, result.max_points, result.correct_count) == (Decimal("1.0"), Decimal("1.5"), 2)
    assert result.score == Decimal("6.67")
    perfect = {"1": "a", "2": {"s1": True, "s2": False, "s3": True, "s4": False}, "3": "1,5"}
    assert grade(SNAPSHOT, perfect).score == Decimal("10.00")
    assert grade(SNAPSHOT, {}).score == Decimal("0.00")


def test_clean_answers_checks_shape_per_type():
    dirty = {
        "1": {"a": True},  # MC must be a choice id
        "2": {"s1": True, "s2": "yes", "zz": False},  # non-bool and unknown statements dropped
        "3": "  42 " + "9" * 50,  # trimmed and capped
        "99": "a",
    }
    cleaned = clean_answers(SNAPSHOT, dirty)
    assert cleaned == {"2": {"s1": True}, "3": ("42 " + "9" * 50)[: rules.SHORT_ANSWER_MAX_LEN]}


# ---------------------------------------------------------------- file import


def test_text_import_detects_true_false_and_short_answer():
    text = (
        "Câu 1: Phát biểu nào đúng?\n"
        "a) Ý một\tb) Ý hai\n"
        "c) Ý ba\n"
        "d) Ý bốn\n"
        "Đáp án: a) Đúng b) Sai c) Sai d) Đúng\n"
        "Câu 2: Tính độ sâu (m).\n"
        "Đáp án: 30\n"
        "Câu 3: Trắc nghiệm\nA. x\nB. y\nĐáp án: B\n"
    )
    tf, sa, mc = parse_text(text)
    assert (tf.type, tf.choices, tf.truths, tf.errors) == (
        "TRUE_FALSE",
        ["Ý một", "Ý hai", "Ý ba", "Ý bốn"],
        [True, False, False, True],
        [],
    )
    assert (sa.type, sa.answers, sa.choices) == ("SHORT_ANSWER", ["30"], [])
    assert (mc.type, mc.correct) == ("MULTIPLE_CHOICE", 1)
    # Validation runs through parse_file.
    (bad,) = parse_file("de.txt", "Câu 1: Thiếu đáp án ngắn".encode())
    assert any("cần đáp án" in e for e in bad.errors)


# ---------------------------------------------------------------- admin form + student flow


@pytest.fixture
def subject_scope(db):
    async def _make(s):
        subject = await create_category(s, "Vật lí 12", slug="vat-li-12")
        return subject.id

    return db(_make)


def test_admin_creates_true_false_and_short_answer_questions(admin_client, db, subject_scope):
    scope = f"c:{subject_scope}"
    r = admin_client.post(
        "/admin/questions",
        data={
            "scope": scope,
            "question_type": "TRUE_FALSE",
            "difficulty": "UNDERSTANDING",
            "content": "Xét khí lí tưởng",
            "choice_content": ["Ý a", "Ý b", "Ý c", "Ý d"],
            "choice_id": ["", "", "", ""],
            "choice_truth": ["1", "0", "0", "1"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    r = admin_client.post(
        "/admin/questions",
        data={
            "scope": scope,
            "question_type": "SHORT_ANSWER",
            "difficulty": "APPLICATION",
            "content": "Độ sâu là bao nhiêu mét?",
            "accepted_answers": "30; 30,0",
            "choice_content": ["leftover row is ignored"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    tf, sa = db(lambda s: s.scalars(select(Question).order_by(Question.id)))
    assert tf.question_type == QuestionType.TRUE_FALSE
    assert [c["is_correct"] for c in tf.choices] == [True, False, False, True]
    assert (sa.question_type, sa.choices, sa.accepted_answers) == (QuestionType.SHORT_ANSWER, [], ["30", "30,0"])

    # Missing truth values / answers are rejected.
    r = admin_client.post(
        "/admin/questions",
        data={
            "scope": scope,
            "question_type": "TRUE_FALSE",
            "difficulty": "RECOGNITION",
            "content": "x",
            "choice_content": ["a", "b", "c", "d"],
            "choice_truth": ["1", "", "0", "1"],
        },
    )
    assert r.status_code == 400 and "Đúng hoặc Sai" in r.text
    r = admin_client.post(
        "/admin/questions",
        data={"scope": scope, "question_type": "SHORT_ANSWER", "difficulty": "RECOGNITION", "content": "x"},
    )
    assert r.status_code == 400 and "cần đáp án" in r.text

    edit = admin_client.get(f"/admin/questions/{tf.id}/edit")
    assert 'data-type="TRUE_FALSE"' in edit.text


def test_student_takes_mixed_exam(client, make_user, db, subject_scope):
    async def _make(s):
        questions = [
            Question(
                root_id=subject_scope,
                category_id=subject_scope,
                difficulty="RECOGNITION",
                question_type=QuestionType.MULTIPLE_CHOICE,
                content="MC",
                choices=[
                    {"id": "a", "content": "đúng", "is_correct": True},
                    {"id": "b", "content": "sai", "is_correct": False},
                ],
            ),
            Question(
                root_id=subject_scope,
                category_id=subject_scope,
                difficulty="UNDERSTANDING",
                question_type=QuestionType.TRUE_FALSE,
                content="TF",
                choices=[{"id": f"s{i}", "content": f"ý {i}", "is_correct": i % 2 == 1} for i in range(1, 5)],
                explanation="GIAI_TF",
            ),
            Question(
                root_id=subject_scope,
                category_id=subject_scope,
                difficulty="APPLICATION",
                question_type=QuestionType.SHORT_ANSWER,
                content="SA",
                choices=[],
                accepted_answers=["30"],
            ),
        ]
        s.add_all(questions)
        exam = Exam(
            root_id=subject_scope,
            category_id=subject_scope,
            title="Đề THPT mini",
            duration_seconds=900,
            is_published=True,
        )
        s.add(exam)
        await s.flush()
        s.add_all(ExamQuestion(exam_id=exam.id, question_id=q.id, position=i) for i, q in enumerate(questions, 1))
        return exam.id, [q.id for q in questions]

    exam_id, (mc, tf, sa) = db(_make)
    make_user("hs@example.com")
    client.post("/login", data={"email": "hs@example.com", "password": "password123"})
    attempt_id = int(
        client.post(f"/exams/{exam_id}/start", follow_redirects=False).headers["location"].rsplit("/", 1)[1]
    )

    paper = client.get(f"/api/attempts/{attempt_id}/paper")
    assert [q["type"] for q in paper.json()["questions"]] == ["MULTIPLE_CHOICE", "TRUE_FALSE", "SHORT_ANSWER"]
    assert "is_correct" not in paper.text and "accepted_answers" not in paper.text and "GIAI_TF" not in paper.text

    data = client.post(
        f"/api/attempts/{attempt_id}/submit",
        json={"answers": {str(mc): "a", str(tf): {"s1": True, "s2": False, "s3": True, "s4": True}, str(sa): "30,0"}},
    ).json()
    # 0.25 + 0.5 (3 of 4 statements) + 0.25 = 1.0 / 1.5
    assert (data["score"], data["correct_count"]) == ("6.67", 2)

    result = client.get(f"/attempts/{attempt_id}/solutions")
    assert "Đúng một phần" in result.text
    assert "0.5/1 điểm" in result.text
    assert "GIAI_TF" in result.text
