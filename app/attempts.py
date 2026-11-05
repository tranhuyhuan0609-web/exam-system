"""Exam attempt lifecycle: start / resume, paper snapshot, and the student-safe view of a paper."""

from datetime import datetime, timedelta
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
