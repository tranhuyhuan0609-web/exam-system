"""Exam attempt lifecycle: start / resume, paper snapshot, student-safe paper view, submit and grading."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AttemptStatus, Exam, ExamAttempt, ExamQuestion, Question, User


class AttemptError(Exception):
    """Starting an attempt is not allowed; the message is shown to the student."""


async def db_now(session: AsyncSession) -> datetime:
    # The database clock is the single source of truth for exam timing (app servers may drift).
    return await session.scalar(select(func.now()))


def remaining_seconds(attempt: ExamAttempt, now: datetime) -> float:
    if attempt.status != AttemptStatus.IN_PROGRESS:
        return 0.0
    return max(0.0, (attempt.expires_at - now).total_seconds())


def build_snapshot(questions: list[Question]) -> dict[str, Any]:
    """Frozen copy of the paper, including answers, kept server-side only."""
    return {
        "questions": [
            {
                "id": q.id,
                "difficulty": q.difficulty.value,
                "content": q.content,
                "choices": [
                    {"id": c["id"], "content": c["content"], "is_correct": bool(c["is_correct"])} for c in q.choices
                ],
                "explanation": q.explanation,
            }
            for q in questions
        ]
    }


def public_questions(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Whitelist the fields a student may see. Never add is_correct / explanation here."""
    return [
        {
            "id": q["id"],
            "number": number,
            "content": q["content"],
            "choices": [{"id": c["id"], "content": c["content"]} for c in q["choices"]],
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

    questions = list(
        (
            await session.scalars(
                select(Question)
                .join(ExamQuestion, ExamQuestion.question_id == Question.id)
                .where(ExamQuestion.exam_id == exam.id)
                .order_by(ExamQuestion.position, Question.id)
            )
        ).all()
    )
    if not questions:
        raise AttemptError("Đề này chưa có câu hỏi.")

    attempt = ExamAttempt(
        user_id=user.id,
        exam_id=exam.id,
        attempt_no=last_no + 1,
        status=AttemptStatus.IN_PROGRESS,
        started_at=now,
        expires_at=now + timedelta(seconds=exam.duration_seconds),
        answers={},
        paper_snapshot=build_snapshot(questions),
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
    correct_count: int
    total: int
    score: Decimal


def clean_answers(snapshot: dict[str, Any], answers: Any) -> dict[str, str]:
    """Keep only {question_id: choice_id} pairs that exist in this paper."""
    if not isinstance(answers, dict):
        return {}
    valid = {str(q["id"]): {c["id"] for c in q["choices"]} for q in snapshot["questions"]}
    cleaned = {}
    for key, choice in answers.items():
        qid = str(key)
        if qid in valid and choice in valid[qid]:
            cleaned[qid] = choice
    return cleaned


def grade(snapshot: dict[str, Any], answers: dict[str, str]) -> Grade:
    questions = snapshot["questions"]
    correct = sum(
        1
        for q in questions
        if (chosen := answers.get(str(q["id"]))) and any(c["id"] == chosen and c["is_correct"] for c in q["choices"])
    )
    total = len(questions)
    score = (MAX_SCORE * correct / total).quantize(Decimal("0.01"), ROUND_HALF_UP) if total else Decimal(0)
    return Grade(correct, total, score)


def _finish(attempt: ExamAttempt, answers: dict[str, str], submitted_at: datetime) -> None:
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
    session: AsyncSession, user_id: int, exam_id: int | None = None, now: datetime | None = None, commit: bool = True
) -> int:
    """Close this student's attempts whose time ran out without a submit (tab closed, offline...).

    Phase 2 adds a background worker that does this for everyone; until then it runs lazily
    whenever the student looks at their attempts.
    """
    now = now or await db_now(session)
    stmt = (
        select(ExamAttempt)
        .where(
            ExamAttempt.user_id == user_id,
            ExamAttempt.status == AttemptStatus.IN_PROGRESS,
            ExamAttempt.expires_at + SUBMIT_GRACE < now,
        )
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if exam_id is not None:
        stmt = stmt.where(ExamAttempt.exam_id == exam_id)
    expired = (await session.scalars(stmt)).all()
    for attempt in expired:
        _finish(attempt, clean_answers(attempt.paper_snapshot, attempt.answers), attempt.expires_at)
    if expired and commit:
        await session.commit()
    return len(expired)


def review(attempt: ExamAttempt) -> list[dict[str, Any]]:
    """Per-question breakdown for the result page (answers and explanations are shown after submit)."""
    rows = []
    for number, q in enumerate(attempt.paper_snapshot["questions"], start=1):
        chosen = attempt.answers.get(str(q["id"]))
        correct_id = next((c["id"] for c in q["choices"] if c["is_correct"]), None)
        rows.append(
            {
                "number": number,
                "content": q["content"],
                "choices": q["choices"],
                "explanation": q.get("explanation"),
                "chosen": chosen,
                "correct_id": correct_id,
                "status": "unanswered" if chosen is None else ("correct" if chosen == correct_id else "wrong"),
            }
        )
    return rows
