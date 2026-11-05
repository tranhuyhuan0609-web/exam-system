"""Exam attempt lifecycle: start / resume, paper snapshot, student-safe paper view, submit and grading."""

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.paper import PaperError, questions_for_attempt, shuffled_choices
from app.infrastructure.storage import media_url
from app.models import AttemptStatus, Exam, ExamAttempt, Question, QuestionType, User
from app.question_rules import (
    SHORT_ANSWER_MAX_LEN,
    max_points,
    short_answer_matches,
    true_false_points,
)


class AttemptError(Exception):
    """Starting an attempt is not allowed; the message is shown to the student."""


async def db_now(session: AsyncSession) -> datetime:
    # The database clock is the single source of truth for exam timing (app servers may drift).
    return await session.scalar(select(func.now()))


def remaining_seconds(attempt: ExamAttempt, now: datetime) -> float:
    if attempt.status != AttemptStatus.IN_PROGRESS:
        return 0.0
    return max(0.0, (attempt.expires_at - now).total_seconds())


def build_snapshot(
    questions: list[Question], shuffle_choices: bool = False, rng: random.Random | None = None
) -> dict[str, Any]:
    """Frozen copy of the paper as this attempt shows it (question and option order, answers), server-side only."""
    rng = rng or random.SystemRandom()
    return {
        "questions": [
            {
                "id": q.id,
                "type": q.question_type.value,
                "difficulty": q.difficulty.value,
                "content": q.content,
                "choices": [
                    {"id": c["id"], "content": c["content"], "is_correct": bool(c["is_correct"])}
                    for c in shuffled_choices(q, shuffle_choices, rng)
                ],
                "accepted_answers": list(q.accepted_answers or []),
                "explanation": q.explanation,
                "image_url": media_url(q.image_path),
            }
            for q in questions
        ]
    }


def _type(q: dict[str, Any]) -> str:
    return q.get("type") or QuestionType.MULTIPLE_CHOICE.value  # snapshots taken before question types existed


def public_questions(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Whitelist the fields a student may see. Never add is_correct / accepted_answers / explanation here."""
    return [
        {
            "id": q["id"],
            "number": number,
            "type": _type(q),
            "content": q["content"],
            "choices": [{"id": c["id"], "content": c["content"]} for c in q["choices"]],
            "image_url": q.get("image_url"),
        }
        for number, q in enumerate(snapshot["questions"], start=1)
    ]


async def active_attempt(session: AsyncSession, user_id: int, exam_id: int, now: datetime) -> ExamAttempt | None:
    return await session.scalar(
        select(ExamAttempt)
        .where(
            ExamAttempt.user_id == user_id,
            ExamAttempt.exam_id == exam_id,
            ExamAttempt.status == AttemptStatus.IN_PROGRESS,
            ExamAttempt.expires_at > now,
        )
        .order_by(ExamAttempt.id.desc())
        .limit(1)
    )


async def start_or_resume(session: AsyncSession, user: User, exam: Exam) -> ExamAttempt:
    # Serialise concurrent starts by the same student for the same exam (double clicks, two tabs).
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:user_id, :exam_id)"), {"user_id": user.id, "exam_id": exam.id}
    )
    now = await db_now(session)

    if existing := await active_attempt(session, user.id, exam.id, now):
        return existing
    # No commit here: committing would release the advisory lock before the new attempt exists.
    await finalize_expired(session, user.id, exam.id, now, commit=False)

    used, last_no = (
        await session.execute(
            select(func.count(), func.coalesce(func.max(ExamAttempt.attempt_no), 0)).where(
                ExamAttempt.user_id == user.id, ExamAttempt.exam_id == exam.id
            )
        )
    ).one()
    if exam.max_attempts is not None and used >= exam.max_attempts:
        raise AttemptError(f"Bạn đã dùng hết {exam.max_attempts} lượt làm đề này.")

    try:
        questions = await questions_for_attempt(session, exam, user.id)
    except PaperError as exc:
        raise AttemptError(str(exc)) from exc

    attempt = ExamAttempt(
        user_id=user.id,
        exam_id=exam.id,
        attempt_no=last_no + 1,
        status=AttemptStatus.IN_PROGRESS,
        started_at=now,
        expires_at=now + timedelta(seconds=exam.duration_seconds),
        answers={},
        paper_snapshot=build_snapshot(questions, exam.shuffle_choices),
        total_questions=len(questions),
    )
    session.add(attempt)
    await session.commit()
    return attempt


# ---------------------------------------------------------------- submit & grading

# Accept submissions this long after expires_at to absorb network latency on the final auto-submit.
SUBMIT_GRACE = timedelta(seconds=30)
MAX_SCORE = Decimal(10)


@dataclass(frozen=True)
class Grade:
    correct_count: int  # questions answered fully correctly
    total: int
    score: Decimal  # on a 10-point scale
    points: Decimal  # THPT points earned
    max_points: Decimal


def clean_answers(snapshot: dict[str, Any], answers: Any) -> dict[str, Any]:
    """Keep only well-formed answers to questions in this paper.

    MULTIPLE_CHOICE {qid: choice_id} · TRUE_FALSE {qid: {statement_id: bool}} · SHORT_ANSWER {qid: "text"}
    """
    if not isinstance(answers, dict):
        return {}
    by_id = {str(q["id"]): q for q in snapshot["questions"]}
    cleaned: dict[str, Any] = {}
    for key, value in answers.items():
        q = by_id.get(str(key))
        if q is None:
            continue
        kind = _type(q)
        ids = {c["id"] for c in q["choices"]}
        if kind == QuestionType.TRUE_FALSE:
            if isinstance(value, dict):
                picked = {sid: v for sid, v in value.items() if sid in ids and isinstance(v, bool)}
                if picked:
                    cleaned[str(key)] = picked
        elif kind == QuestionType.SHORT_ANSWER:
            if isinstance(value, str) and value.strip():
                cleaned[str(key)] = value.strip()[:SHORT_ANSWER_MAX_LEN]
        elif isinstance(value, str) and value in ids:
            cleaned[str(key)] = value
    return cleaned


def question_result(q: dict[str, Any], answer: Any) -> tuple[Decimal, str]:
    """(points earned, status) where status is correct | partial | wrong | unanswered."""
    kind = _type(q)
    if answer is None:
        return Decimal(0), "unanswered"
    if kind == QuestionType.TRUE_FALSE:
        right = sum(1 for c in q["choices"] if c["id"] in answer and answer[c["id"]] == c["is_correct"])
        points = true_false_points(right, len(q["choices"]))
        status = "correct" if right == len(q["choices"]) else ("partial" if right else "wrong")
        return points, status
    if kind == QuestionType.SHORT_ANSWER:
        ok = short_answer_matches(answer, q.get("accepted_answers") or [])
        return (max_points(kind) if ok else Decimal(0)), ("correct" if ok else "wrong")
    ok = any(c["id"] == answer and c["is_correct"] for c in q["choices"])
    return (max_points(kind) if ok else Decimal(0)), ("correct" if ok else "wrong")


def grade(snapshot: dict[str, Any], answers: dict[str, Any]) -> Grade:
    questions = snapshot["questions"]
    earned, possible, correct = Decimal(0), Decimal(0), 0
    for q in questions:
        points, status = question_result(q, answers.get(str(q["id"])))
        earned += points
        possible += max_points(_type(q))
        correct += status == "correct"
    score = (MAX_SCORE * earned / possible).quantize(Decimal("0.01"), ROUND_HALF_UP) if possible else Decimal(0)
    return Grade(correct, len(questions), score, earned, possible)


def _finish(attempt: ExamAttempt, answers: dict[str, Any], submitted_at: datetime) -> None:
    result = grade(attempt.paper_snapshot, answers)
    attempt.answers = answers
    attempt.status = AttemptStatus.SUBMITTED
    attempt.submitted_at = submitted_at
    attempt.correct_count = result.correct_count
    attempt.total_questions = result.total
    attempt.score = result.score


async def submit(session: AsyncSession, attempt_id: int, answers: Any) -> ExamAttempt:
    """Grade and close an attempt. Idempotent: a second submit returns the first result unchanged."""
    # populate_existing: re-read the row after acquiring the lock; a concurrent submit may have just finished.
    attempt = await session.scalar(
        select(ExamAttempt)
        .where(ExamAttempt.id == attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if attempt.status == AttemptStatus.SUBMITTED:
        await session.commit()  # release the row lock (commit keeps loaded attributes; rollback would expire them)
        return attempt
    now = await db_now(session)
    if now <= attempt.expires_at + SUBMIT_GRACE:
        final_answers = clean_answers(attempt.paper_snapshot, answers)
    else:
        # Too late: only what the server already had at the deadline counts (phase 2 auto-save fills this).
        final_answers = clean_answers(attempt.paper_snapshot, attempt.answers)
    _finish(attempt, final_answers, min(now, attempt.expires_at))
    await session.commit()
    return attempt


async def finalize_expired(
    session: AsyncSession,
    user_id: int | None = None,
    exam_id: int | None = None,
    now: datetime | None = None,
    commit: bool = True,
    limit: int | None = None,
) -> int:
    """Grade attempts whose time ran out without a submit (tab closed, offline...), using the auto-saved answers.

    Runs for one student lazily when they open their pages, and for everyone from the background worker
    (user_id=None). SKIP LOCKED lets both run at once without blocking or double grading.
    """
    now = now or await db_now(session)
    stmt = (
        select(ExamAttempt)
        .where(ExamAttempt.status == AttemptStatus.IN_PROGRESS, ExamAttempt.expires_at + SUBMIT_GRACE < now)
        .order_by(ExamAttempt.expires_at)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if user_id is not None:
        stmt = stmt.where(ExamAttempt.user_id == user_id)
    if exam_id is not None:
        stmt = stmt.where(ExamAttempt.exam_id == exam_id)
    if limit is not None:
        stmt = stmt.limit(limit)
    expired = (await session.scalars(stmt)).all()
    for attempt in expired:
        _finish(attempt, clean_answers(attempt.paper_snapshot, attempt.answers), attempt.expires_at)
    if expired and commit:
        await session.commit()
    return len(expired)


class AttemptClosed(Exception):
    """Answers can no longer be changed (submitted, or past the deadline)."""

    def __init__(self, status: str):
        self.status = status


async def save_answers(session: AsyncSession, attempt_id: int, changes: Any) -> tuple[ExamAttempt, datetime]:
    """Auto-save: merge {question_id: value | None} into the attempt; None clears an answer.

    Invalid entries are ignored. The row lock serialises saves from several tabs; the client sends one
    request at a time, so saves from one tab are applied in order.
    """
    attempt = await session.scalar(
        select(ExamAttempt)
        .where(ExamAttempt.id == attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = await db_now(session)
    if attempt.status != AttemptStatus.IN_PROGRESS or now > attempt.expires_at + SUBMIT_GRACE:
        await session.commit()
        raise AttemptClosed(attempt.status.value if attempt.status == AttemptStatus.SUBMITTED else "EXPIRED")
    answers = dict(attempt.answers or {})
    if isinstance(changes, dict):
        for key, value in changes.items():
            qid = str(key)
            if value is None:
                answers.pop(qid, None)
                continue
            cleaned = clean_answers(attempt.paper_snapshot, {qid: value})
            if qid in cleaned:
                answers[qid] = cleaned[qid]
    attempt.answers = answers  # reassign so SQLAlchemy sees the JSONB change
    await session.commit()
    return attempt, now


def review(attempt: ExamAttempt) -> list[dict[str, Any]]:
    """Per-question breakdown for the result page (answers and explanations are shown after submit)."""
    rows = []
    for number, q in enumerate(attempt.paper_snapshot["questions"], start=1):
        answer = attempt.answers.get(str(q["id"]))
        points, status = question_result(q, answer)
        kind = _type(q)
        rows.append(
            {
                "number": number,
                "type": kind,
                "content": q["content"],
                "choices": q["choices"],
                "explanation": q.get("explanation"),
                "image_url": q.get("image_url"),
                "accepted_answers": q.get("accepted_answers") or [],
                "answer": answer,  # choice id, {statement id: bool} or text depending on type
                "correct_id": next((c["id"] for c in q["choices"] if c["is_correct"]), None),
                "status": status,
                "points": points,
                "max_points": max_points(kind),
            }
        )
    return rows
