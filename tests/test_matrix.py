import asyncio
import random

import pytest
from sqlalchemy import select, update

from app.application.paper import PaperError, arrange, draw_matrix, parse_matrix, validate_rules
from app.db import SessionLocal
from app.models import (
    Difficulty,
    Exam,
    ExamAttempt,
    Question,
    QuestionSource,
    QuestionType,
)
from tests.factories import make_tree

R, U, A = Difficulty.RECOGNITION, Difficulty.UNDERSTANDING, Difficulty.APPLICATION


@pytest.fixture
def bank(db):
    """Chapter 1: 6 recognition MC, 4 understanding MC, 2 true/false, 2 short answer (+1 unverified MC)."""

    async def _make(s):
        t = await make_tree(s, {"Vật lí 11": {"Chương 1": {}, "Chương 2": {}}})
        subject, c1, c2 = t["Vật lí 11"], t["Chương 1"], t["Chương 2"]

        def mc(n, difficulty, chapter=c1, verified=True):
            return Question(
                root_id=subject.id,
                category_id=chapter.id,
                difficulty=difficulty,
                content=f"MC {n}",
                choices=[{"id": f"{n}{x}", "content": x, "is_correct": x == "A"} for x in "ABCD"],
                is_verified=verified,
            )

        questions = [mc(i, R) for i in range(6)] + [mc(10 + i, U) for i in range(4)]
        questions.append(mc(99, R, verified=False))
        questions.append(mc(50, R, chapter=c2))
        questions += [
            Question(
                root_id=subject.id,
                category_id=c1.id,
                difficulty=U,
                question_type=QuestionType.TRUE_FALSE,
                content=f"TF {i}",
                choices=[{"id": f"t{i}{k}", "content": k, "is_correct": True} for k in "abcd"],
            )
            for i in range(2)
        ]
        questions += [
            Question(
                root_id=subject.id,
                category_id=c1.id,
                difficulty=A,
                question_type=QuestionType.SHORT_ANSWER,
                content=f"SA {i}",
                choices=[],
                accepted_answers=["1"],
            )
            for i in range(2)
        ]
        s.add_all(questions)
        exam = Exam(
            root_id=subject.id,
            category_id=c1.id,
            title="Đề ma trận",
            duration_seconds=900,
            is_published=True,
            question_source=QuestionSource.MATRIX,
            shuffle_choices=True,
            matrix=[
                {"scope": f"c:{c1.id}", "difficulty": "RECOGNITION", "type": "MULTIPLE_CHOICE", "count": 3},
                {"scope": "", "difficulty": "UNDERSTANDING", "type": "MULTIPLE_CHOICE", "count": 2},
                {"scope": "", "difficulty": None, "type": "SHORT_ANSWER", "count": 1},
                {"scope": "", "difficulty": None, "type": "TRUE_FALSE", "count": 1},
            ],
        )
        s.add(exam)
        await s.flush()
        return {"exam": exam.id, "subject": subject.id, "c1": c1.id, "c2": c2.id}

    return db(_make)


def draw(exam_id, seed=1, seen=None):
    async def _run():
        async with SessionLocal() as s:
            exam = await s.get(Exam, exam_id)
            return await draw_matrix(s, exam, random.Random(seed), seen)

    return asyncio.run(_run())


def test_matrix_draw_follows_every_rule_with_verified_unique_questions(bank):
    questions = draw(bank["exam"])
    assert len(questions) == 7 and len({q.id for q in questions}) == 7
    contents = [q.content for q in questions]
    assert sum(c.startswith("MC ") and int(c[3:]) < 6 for c in contents) == 3  # recognition, chapter 1
    assert sum(c.startswith("MC 1") and len(c) == 5 for c in contents) == 2  # understanding (MC 10..13)
    assert "MC 99" not in contents and "MC 50" not in contents  # unverified / other chapter excluded
    assert all(q.is_verified for q in questions)


def test_arrange_orders_by_thpt_section():
    def q(kind):
        return Question(question_type=kind, content=kind.value, choices=[])

    mixed = [q(QuestionType.SHORT_ANSWER), q(QuestionType.TRUE_FALSE), q(QuestionType.MULTIPLE_CHOICE)]
    ordered = arrange(mixed, shuffle=True, rng=random.Random(0))
    assert [x.question_type for x in ordered] == [
        QuestionType.MULTIPLE_CHOICE,
        QuestionType.TRUE_FALSE,
        QuestionType.SHORT_ANSWER,
    ]


def test_matrix_reports_which_rule_cannot_be_filled(bank, db):
    db(
        lambda s: s.execute(
            update(Exam).values(
                matrix=[{"scope": "", "difficulty": "APPLICATION", "type": "MULTIPLE_CHOICE", "count": 5}]
            )
        )
    )
    with pytest.raises(PaperError, match="Dòng 1 .* cần 5 câu .* chỉ còn 0"):
        draw(bank["exam"])
    assert validate_rules(parse_matrix([{"count": 0}]))[0].startswith("Dòng 1")
    assert validate_rules([]) == ["Ma trận cần ít nhất 1 dòng."]


def test_seen_questions_are_used_last(bank):
    first = {q.id for q in draw(bank["exam"], seed=1)}
    second = draw(bank["exam"], seed=2, seen=first)
    recognition = [q for q in second if q.content.startswith("MC ") and int(q.content[3:]) < 6]
    # 6 recognition questions, 3 used before: the retry gets the other 3.
    assert not {q.id for q in recognition} & first


def test_student_attempts_get_drawn_paper_frozen_per_attempt(client, make_user, db, bank):
    make_user("hs@example.com")
    client.post("/login", data={"email": "hs@example.com", "password": "password123"})
    page = client.get(f"/exams/{bank['exam']}")
    assert ">7<" in page.text  # question count comes from the matrix

    loc = client.post(f"/exams/{bank['exam']}/start", follow_redirects=False).headers["location"]
    attempt_id = int(loc.rsplit("/", 1)[1])
    paper = client.get(f"/api/attempts/{attempt_id}/paper").json()
    assert [q["type"] for q in paper["questions"]] == ["MULTIPLE_CHOICE"] * 5 + ["TRUE_FALSE", "SHORT_ANSWER"]
    # F5: the same paper comes back.
    assert client.get(f"/api/attempts/{attempt_id}/paper").json()["questions"] == paper["questions"]

    # Shuffled options keep their ids, so the correct option is still graded as correct.
    answers = {}
    for q in paper["questions"]:
        if q["type"] == "MULTIPLE_CHOICE":
            answers[str(q["id"])] = next(c["id"] for c in q["choices"] if c["id"].endswith("A"))
    result = client.post(f"/api/attempts/{attempt_id}/submit", json={"answers": answers}).json()
    assert result["correct_count"] == 5


def test_option_shuffling_actually_happens(bank, db):
    from app.application.paper import shuffled_choices

    question = Question(
        question_type=QuestionType.MULTIPLE_CHOICE,
        choices=[{"id": x, "content": x, "is_correct": x == "a"} for x in "abcdef"],
    )
    orders = {tuple(c["id"] for c in shuffled_choices(question, True, random.Random(seed))) for seed in range(10)}
    assert len(orders) > 1
    assert [c["id"] for c in shuffled_choices(question, False, random.Random(0))] == list("abcdef")
    tf = Question(question_type=QuestionType.TRUE_FALSE, choices=question.choices)
    assert [c["id"] for c in shuffled_choices(tf, True, random.Random(0))] == list("abcdef")  # a)–d) stay


def test_admin_saves_matrix_and_publishing_requires_a_fillable_matrix(admin_client, db, bank):
    exam_id = bank["exam"]
    r = admin_client.post(
        f"/admin/exams/{exam_id}/matrix",
        data={
            "rule_scope": [f"c:{bank['c1']}", ""],
            "rule_difficulty": ["RECOGNITION", ""],
            "rule_type": ["MULTIPLE_CHOICE", "SHORT_ANSWER"],
            "rule_count": ["4", "2"],
        },
    )
    assert "mỗi lượt làm sẽ bốc 6 câu" in r.text
    exam = db(lambda s: s.get(Exam, exam_id))
    assert [rule["count"] for rule in exam.matrix] == [4, 2]

    # Published exam: a matrix that cannot be filled is rejected and not saved.
    r = admin_client.post(
        f"/admin/exams/{exam_id}/matrix",
        data={
            "rule_scope": [""],
            "rule_difficulty": ["APPLICATION"],
            "rule_type": ["MULTIPLE_CHOICE"],
            "rule_count": ["3"],
        },
    )
    assert r.status_code == 400 and "không bốc đủ câu" in r.text
    assert [rule["count"] for rule in db(lambda s: s.get(Exam, exam_id)).matrix] == [4, 2]

    # Scope from another subject is refused.
    r = admin_client.post(
        f"/admin/exams/{exam_id}/matrix",
        data={"rule_scope": ["c:999"], "rule_difficulty": [""], "rule_type": [""], "rule_count": ["1"]},
    )
    assert "phạm vi phải thuộc môn của đề" in r.text

    page = admin_client.get(f"/admin/exams/{exam_id}")
    assert "Ma trận đề (6 câu mỗi lượt)" in page.text and "Có 6 câu" in page.text


def test_retry_draws_new_questions_for_the_same_student(client, make_user, db, bank):
    make_user("hs@example.com")
    client.post("/login", data={"email": "hs@example.com", "password": "password123"})
    ids = []
    for _ in range(2):
        loc = client.post(f"/exams/{bank['exam']}/start", follow_redirects=False).headers["location"]
        attempt_id = int(loc.rsplit("/", 1)[1])
        paper = client.get(f"/api/attempts/{attempt_id}/paper").json()
        ids.append({q["id"] for q in paper["questions"] if q["type"] == "MULTIPLE_CHOICE"})
        client.post(f"/api/attempts/{attempt_id}/submit", json={"answers": {}})
    assert len(db(lambda s: s.scalars(select(ExamAttempt)))) == 2
    # 10 MC questions in the bank, 5 per paper: the second paper avoids every MC question of the first.
    assert not ids[0] & ids[1]
