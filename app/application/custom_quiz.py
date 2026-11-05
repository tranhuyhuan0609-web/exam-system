"""Quizzes a student builds for themself from one category: how many questions of each type, which levels.

A custom quiz is stored as a personal matrix exam (visibility PERSONAL, owner = created_by), so it reuses
the whole attempt flow: frozen paper, auto-save, flags, report and solutions. Retrying it draws new
questions first, like any matrix exam.
"""

import math
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.paper import PART_ORDER, MatrixRule
from app.models import Category, Difficulty, Exam, ExamMode, ExamVisibility, Question, QuestionSource, User
from app.scope import scope_for

# Suggested time per question (seconds), roughly the THPT pace of each part.
SECONDS_PER_QUESTION = {
    "MULTIPLE_CHOICE": 90,
    "TRUE_FALSE": 180,
    "SHORT_ANSWER": 150,
    "ESSAY": 600,
}
MAX_QUESTIONS = 100
MAX_MINUTES = 180
LEVELS = [d.value for d in Difficulty]


class CustomQuizError(Exception):
    """The quiz cannot be built as asked; the message is user-facing."""


async def bank_counts(session: AsyncSession, category: Category) -> dict[str, dict[str, int]]:
    """Verified questions in the category's branch: {question type: {difficulty: count}}, THPT part order."""
    scope = scope_for(category)
    rows = (
        await session.execute(
            select(Question.question_type, Question.difficulty, func.count())
            .where(Question.is_verified, *scope.conditions(Question))
            .group_by(Question.question_type, Question.difficulty)
        )
    ).all()
    counts: dict[str, dict[str, int]] = {t: {} for t in PART_ORDER}
    for qtype, level, n in rows:
        counts[qtype.value][level.value] = n
    return counts


def suggested_minutes(counts: dict[str, int]) -> int:
    seconds = sum(SECONDS_PER_QUESTION.get(t, 90) * n for t, n in counts.items())
    return max(5, min(MAX_MINUTES, math.ceil(seconds / 60 / 5) * 5))  # rounded up to 5 minutes


@dataclass
class QuizRequest:
    counts: dict[str, int]  # question type -> how many
    levels: list[str]  # empty = every level
    minutes: int | None = None  # None = suggested


def validate(request: QuizRequest, bank: dict[str, dict[str, int]]) -> list[str]:
    errors = []
    levels = request.levels or LEVELS
    total = sum(request.counts.values())
    if total < 1:
        errors.append("Hãy chọn ít nhất 1 câu.")
    if total > MAX_QUESTIONS:
        errors.append(f"Một bài tối đa {MAX_QUESTIONS} câu.")
    for qtype, n in request.counts.items():
        have = sum(bank.get(qtype, {}).get(lv, 0) for lv in levels)
        if n < 0:
            errors.append("Số câu không hợp lệ.")
        elif n > have:
            errors.append(f"Ngân hàng chỉ có {have} câu {TYPE_NAMES.get(qtype, qtype)} phù hợp, bạn chọn {n}.")
    if request.minutes is not None and not 1 <= request.minutes <= MAX_MINUTES:
        errors.append(f"Thời gian phải từ 1 đến {MAX_MINUTES} phút.")
    return errors


TYPE_NAMES = {
    "MULTIPLE_CHOICE": "trắc nghiệm",
    "TRUE_FALSE": "đúng/sai",
    "SHORT_ANSWER": "trả lời ngắn",
    "ESSAY": "tự luận",
}


def describe(counts: dict[str, int]) -> str:
    return " + ".join(f"{n} {TYPE_NAMES[t]}" for t in PART_ORDER if (n := counts.get(t)))


async def create_custom_quiz(session: AsyncSession, user: User, category: Category, request: QuizRequest) -> Exam:
    bank = await bank_counts(session, category)
    if errors := validate(request, bank):
        raise CustomQuizError(" ".join(errors))
    counts = {t: n for t in PART_ORDER if (n := request.counts.get(t, 0)) > 0}
    levels = tuple(lv for lv in LEVELS if lv in request.levels)
    if len(levels) == len(LEVELS):
        levels = ()
    key = scope_for(category).key
    rules = [MatrixRule(key, None, qtype, n, levels) for qtype, n in counts.items()]
    minutes = request.minutes or suggested_minutes(counts)
    exam = Exam(
        root_id=category.root_id,
        category_id=category.id,
        title=f"Tự luyện: {category.name}",
        description=describe(counts),
        exam_mode=ExamMode.PRACTICE,
        visibility=ExamVisibility.PERSONAL,
        duration_seconds=minutes * 60,
        max_attempts=None,
        is_published=False,
        question_source=QuestionSource.MATRIX,
        matrix=[rule.to_json() for rule in rules],
        shuffle_questions=True,
        shuffle_choices=True,
        allow_custom_parts=False,
        created_by=user.id,
    )
    session.add(exam)
    await session.flush()
    return exam
