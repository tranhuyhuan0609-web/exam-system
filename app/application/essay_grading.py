"""AI grading of essay answers, run by the background worker after an attempt is submitted.

The objective parts are graded instantly on submit; answered essays put the attempt in PENDING_AI. The
worker sends them to the model (with the model solution, or asks it to solve first), stores points and
feedback per question, and recomputes the score.
"""

import logging
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.ai.provider import AIProvider, EssayItem
from app.attempts import _max, _type, apply_grade
from app.db import SessionLocal
from app.infrastructure.storage import StorageError, get_storage
from app.models import ExamAttempt, GradingStatus, QuestionType

log = logging.getLogger(__name__)

MAX_TRIES = 3
QUARTER = Decimal("0.25")


def _round_points(value: float, maximum: Decimal) -> Decimal:
    points = (Decimal(str(value)) / QUARTER).quantize(Decimal(1), ROUND_HALF_UP) * QUARTER
    return min(maximum, max(Decimal(0), points))


async def _figure(image_url: str | None) -> bytes | None:
    if not image_url or "figures/" not in image_url:
        return None
    try:
        return await get_storage().get("figures/" + image_url.split("figures/", 1)[1])
    except StorageError:
        return None


async def grade_attempt_essays(attempt: ExamAttempt, provider: AIProvider) -> None:
    """Grade the ungraded essays of one attempt in place (raises on AI failure)."""
    grades = dict(attempt.essay_grades or {})
    items: list[EssayItem] = []
    question_ids: list[str] = []
    for q in attempt.paper_snapshot.get("questions", []):
        qid = str(q["id"])
        answer = (attempt.answers or {}).get(qid)
        if _type(q) != QuestionType.ESSAY or not answer or qid in grades:
            continue
        items.append(
            EssayItem(
                number=len(items) + 1,
                content=q["content"],
                reference_solution=q.get("explanation"),
                student_answer=answer,
                max_points=float(_max(q)),
                figure=await _figure(q.get("image_url")),
            )
        )
        question_ids.append(qid)
    if items:
        by_number = {g.number: g for g in await provider.grade_essays(items)}
        maxima = {str(q["id"]): _max(q) for q in attempt.paper_snapshot["questions"]}
        for item, qid in zip(items, question_ids, strict=True):
            result = by_number.get(item.number)
            if result is None:
                raise ValueError(f"The model did not grade essay {item.number}")
            grades[qid] = {
                "points": str(_round_points(result.points, maxima[qid])),
                "max_points": str(maxima[qid]),
                "feedback": result.feedback.strip(),
                "solution": result.reference_solution.strip(),
            }
    attempt.essay_grades = grades
    outcome = apply_grade(attempt)
    attempt.grading_status = GradingStatus.PENDING_AI if outcome.pending_essays else GradingStatus.COMPLETE


async def grade_pending_essays(provider: AIProvider, batch: int = 3) -> int:
    """Grade a few attempts waiting for essay grading. Returns how many were finished."""
    async with SessionLocal() as session:
        attempts = (
            await session.scalars(
                select(ExamAttempt)
                .where(ExamAttempt.grading_status == GradingStatus.PENDING_AI)
                .order_by(ExamAttempt.submitted_at)
                .with_for_update(skip_locked=True)
                .limit(batch)
            )
        ).all()
        finished = 0
        for attempt in attempts:
            try:
                await grade_attempt_essays(attempt, provider)
                finished += attempt.grading_status == GradingStatus.COMPLETE
            except Exception:
                log.exception("Essay grading failed for attempt %s", attempt.id)
                attempt.grading_tries += 1
                if attempt.grading_tries >= MAX_TRIES:
                    attempt.grading_status = GradingStatus.FAILED
        await session.commit()
        return finished
