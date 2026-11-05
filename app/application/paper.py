"""Choosing the questions of one attempt: a fixed list, or a random draw following the exam's matrix.

Matrix rule (stored in Exam.matrix as JSON):

    {"scope": "c:3", "difficulty": "RECOGNITION" | null, "type": "MULTIPLE_CHOICE" | null, "count": 10}

Only verified questions from the exam's subject are drawn. Questions the student already saw in earlier
attempts of the same exam are used last, so each retry gets as many new questions as the bank allows.
Papers are ordered by THPT section (multiple choice, true/false, short answer).
"""

import random
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Difficulty, Exam, ExamAttempt, ExamQuestion, Question, QuestionSource, QuestionType
from app.scope import Scope, resolve_scope, scope_of

SECTION_ORDER = {
    QuestionType.MULTIPLE_CHOICE: 0,
    QuestionType.TRUE_FALSE: 1,
    QuestionType.SHORT_ANSWER: 2,
    QuestionType.ESSAY: 3,
}
PART_ORDER = [t.value for t in sorted(SECTION_ORDER, key=SECTION_ORDER.get)]
MAX_RULES = 30
MAX_RULE_COUNT = 200


class PaperError(Exception):
    """The paper cannot be built (e.g. the bank has too few questions); the message is user-facing."""


@dataclass(frozen=True)
class MatrixRule:
    scope_key: str
    difficulty: str | None
    question_type: str | None
    count: int

    def to_json(self) -> dict[str, Any]:
        return {"scope": self.scope_key, "difficulty": self.difficulty, "type": self.question_type, "count": self.count}


def parse_matrix(raw: Any) -> list[MatrixRule]:
    rules = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        difficulty = item.get("difficulty") if item.get("difficulty") in Difficulty.__members__ else None
        qtype = item.get("type") if item.get("type") in QuestionType.__members__ else None
        try:
            count = int(item.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        rules.append(MatrixRule(str(item.get("scope") or ""), difficulty, qtype, count))
    return rules


def validate_rules(rules: list[MatrixRule]) -> list[str]:
    errors = []
    if not rules:
        errors.append("Ma trận cần ít nhất 1 dòng.")
    if len(rules) > MAX_RULES:
        errors.append(f"Ma trận tối đa {MAX_RULES} dòng.")
    for number, rule in enumerate(rules, start=1):
        if not 1 <= rule.count <= MAX_RULE_COUNT:
            errors.append(f"Dòng {number}: số câu phải từ 1 đến {MAX_RULE_COUNT}.")
    return errors


async def _rule_scope(session: AsyncSession, exam: Exam, rule: MatrixRule) -> Scope:
    scope = await resolve_scope(session, rule.scope_key) if rule.scope_key else scope_of(exam)
    if scope is None or scope.subject_id != exam.subject_id:
        raise PaperError("Phạm vi trong ma trận không còn tồn tại hoặc không thuộc môn của đề.")
    return scope


async def rule_candidates(
    session: AsyncSession, exam: Exam, rule: MatrixRule, parts: list[str] | None = None
) -> list[int]:
    scope = await _rule_scope(session, exam, rule)
    conditions = [Question.is_verified, Question.subject_id == exam.subject_id, *scope.conditions(Question)]
    if rule.difficulty:
        conditions.append(Question.difficulty == Difficulty(rule.difficulty))
    if rule.question_type:
        conditions.append(Question.question_type == QuestionType(rule.question_type))
    elif parts is not None:  # "any type" rule, restricted to the parts the student chose
        conditions.append(Question.question_type.in_([QuestionType(p) for p in parts]))
    return list((await session.scalars(select(Question.id).where(*conditions).order_by(Question.id))).all())


async def availability(session: AsyncSession, exam: Exam, rules: list[MatrixRule]) -> list[int | None]:
    """Matching questions per rule (ignoring overlap between rules); None when the scope is invalid."""
    out: list[int | None] = []
    for rule in rules:
        try:
            out.append(len(await rule_candidates(session, exam, rule)))
        except PaperError:
            out.append(None)
    return out


async def _seen_question_ids(session: AsyncSession, exam_id: int, user_id: int) -> set[int]:
    snapshots = (
        await session.scalars(
            select(ExamAttempt.paper_snapshot).where(ExamAttempt.exam_id == exam_id, ExamAttempt.user_id == user_id)
        )
    ).all()
    return {q["id"] for snap in snapshots for q in (snap or {}).get("questions", [])}


async def draw_matrix(
    session: AsyncSession,
    exam: Exam,
    rng: random.Random,
    seen: set[int] | None = None,
    parts: list[str] | None = None,
) -> list[Question]:
    rules = parse_matrix(exam.matrix)
    if errors := validate_rules(rules):
        raise PaperError(" ".join(errors))
    seen = seen or set()
    picked: list[int] = []
    for number, rule in enumerate(rules, start=1):
        if parts is not None and rule.question_type and rule.question_type not in parts:
            continue  # the student left this part out
        taken = set(picked)
        ids = [qid for qid in await rule_candidates(session, exam, rule, parts) if qid not in taken]
        fresh = [qid for qid in ids if qid not in seen]
        old = [qid for qid in ids if qid in seen]
        rng.shuffle(fresh)
        rng.shuffle(old)
        chosen = (fresh + old)[: rule.count]
        if len(chosen) < rule.count:
            raise PaperError(
                f"Dòng {number} của ma trận cần {rule.count} câu nhưng ngân hàng chỉ còn {len(ids)} câu phù hợp "
                "(đã duyệt, đúng phạm vi, mức độ và dạng câu)."
            )
        picked.extend(chosen)
    by_id = {q.id: q for q in (await session.scalars(select(Question).where(Question.id.in_(picked)))).all()}
    return [by_id[qid] for qid in picked]


async def fixed_questions(session: AsyncSession, exam: Exam) -> list[Question]:
    return list(
        (
            await session.scalars(
                select(Question)
                .join(ExamQuestion, ExamQuestion.question_id == Question.id)
                .where(ExamQuestion.exam_id == exam.id)
                .order_by(ExamQuestion.position, Question.id)
            )
        ).all()
    )


def arrange(questions: list[Question], shuffle: bool, rng: random.Random) -> list[Question]:
    """Group by THPT section; within a section keep the given order, or shuffle it."""
    sections: dict[int, list[Question]] = {}
    for q in questions:
        sections.setdefault(SECTION_ORDER.get(q.question_type, 9), []).append(q)
    ordered = []
    for key in sorted(sections):
        group = sections[key]
        if shuffle:
            rng.shuffle(group)
        ordered.extend(group)
    return ordered


async def questions_for_attempt(
    session: AsyncSession,
    exam: Exam,
    user_id: int,
    rng: random.Random | None = None,
    parts: list[str] | None = None,
) -> list[Question]:
    """Questions of a new attempt; `parts` (question types) limits the paper when the student chose so."""
    rng = rng or random.SystemRandom()
    if exam.question_source == QuestionSource.MATRIX:
        seen = await _seen_question_ids(session, exam.id, user_id)
        questions = await draw_matrix(session, exam, rng, seen, parts)
    else:
        questions = await fixed_questions(session, exam)
        if parts is not None:
            questions = [q for q in questions if q.question_type.value in parts]
    if not questions:
        raise PaperError("Đề này chưa có câu hỏi." if parts is None else "Các phần bạn chọn chưa có câu hỏi.")
    if exam.question_source == QuestionSource.FIXED and not exam.shuffle_questions:
        return questions  # exactly the order the admin arranged
    return arrange(questions, exam.shuffle_questions, rng)


def shuffled_choices(question: Question, shuffle: bool, rng: random.Random) -> list[dict[str, Any]]:
    """Options in the order this attempt shows them. Only multiple-choice options move (A/B/C/D);
    true/false statements keep their a)–d) order. Ids are kept, so grading is unaffected."""
    choices = [dict(c) for c in question.choices]
    if shuffle and question.question_type == QuestionType.MULTIPLE_CHOICE:
        rng.shuffle(choices)
    return choices


async def question_count(session: AsyncSession, exam: Exam) -> int:
    if exam.question_source == QuestionSource.MATRIX:
        return sum(rule.count for rule in parse_matrix(exam.matrix))
    return len(await fixed_questions(session, exam))


async def available_parts(session: AsyncSession, exam: Exam) -> dict[str, int]:
    """Question types the exam contains, with how many questions of each a full paper has (THPT order).

    Matrix rules without a type count under every type they could draw ("ANY" key)."""
    counts: dict[str, int] = {}
    if exam.question_source == QuestionSource.MATRIX:
        for rule in parse_matrix(exam.matrix):
            key = rule.question_type or "ANY"
            counts[key] = counts.get(key, 0) + rule.count
        if "ANY" in counts:  # an untyped rule may draw any type present in its scope
            for qtype in PART_ORDER:
                counts.setdefault(qtype, 0)
    else:
        for q in await fixed_questions(session, exam):
            counts[q.question_type.value] = counts.get(q.question_type.value, 0) + 1
    ordered = {t: counts[t] for t in PART_ORDER if t in counts}
    if "ANY" in counts:
        ordered["ANY"] = counts["ANY"]
    return ordered
