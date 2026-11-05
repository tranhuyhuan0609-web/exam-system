"""Post-submit report: timing, accuracy by difficulty, weak topics.

Accuracy is measured in points (earned / possible), so a half-right true/false question or a partly
correct essay counts proportionally. A "topic" (chuyên đề) is the question's lesson, else its chapter,
else its subject; questions deleted from the bank since the attempt fall under "Khác".
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chapter, Difficulty, ExamAttempt, Lesson, Question, Subject

WEAK_THRESHOLD = 50  # percent
DIFFICULTY_ORDER = [Difficulty.RECOGNITION.value, Difficulty.UNDERSTANDING.value, Difficulty.APPLICATION.value]
DIFFICULTY_LABELS = {"RECOGNITION": "Nhận biết", "UNDERSTANDING": "Thông hiểu", "APPLICATION": "Vận dụng"}


@dataclass
class Bucket:
    key: str
    label: str
    earned: Decimal = Decimal(0)
    possible: Decimal = Decimal(0)
    questions: int = 0
    numbers: list[int] = field(default_factory=list)  # question numbers in the paper

    @property
    def percent(self) -> int:
        return round(100 * self.earned / self.possible) if self.possible else 0

    @property
    def weak(self) -> bool:
        return self.possible > 0 and self.percent < WEAK_THRESHOLD


@dataclass
class Report:
    time_spent: int
    seconds_per_question: int
    counts: dict[str, int]
    by_difficulty: list[Bucket]
    topics: list[Bucket]

    @property
    def weak_topics(self) -> list[Bucket]:
        return sorted((t for t in self.topics if t.weak), key=lambda t: (t.percent, t.label))


async def _topic_names(session: AsyncSession, question_ids: list[int]) -> dict[int, str]:
    rows = (
        await session.execute(
            select(Question.id, Lesson.name, Chapter.name, Subject.name)
            .join(Subject, Subject.id == Question.subject_id)
            .outerjoin(Chapter, Chapter.id == Question.chapter_id)
            .outerjoin(Lesson, Lesson.id == Question.lesson_id)
            .where(Question.id.in_(question_ids))
        )
    ).all()
    return {qid: lesson or chapter or subject for qid, lesson, chapter, subject in rows}


async def build_report(session: AsyncSession, attempt: ExamAttempt, rows: list[dict[str, Any]]) -> Report:
    """`rows` is attempts.review(attempt): one dict per question with status, points and max_points."""
    snapshot_questions = attempt.paper_snapshot.get("questions", [])
    ids = [q["id"] for q in snapshot_questions]
    topics_by_id = await _topic_names(session, ids)

    difficulty: dict[str, Bucket] = {}
    topics: dict[str, Bucket] = {}
    counts = {"correct": 0, "partial": 0, "wrong": 0, "unanswered": 0, "pending": 0}
    for q, row in zip(snapshot_questions, rows, strict=True):
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        level = q.get("difficulty") or Difficulty.UNDERSTANDING.value
        topic = topics_by_id.get(q["id"], "Khác")
        for buckets, key, label in ((difficulty, level, DIFFICULTY_LABELS.get(level, level)), (topics, topic, topic)):
            bucket = buckets.setdefault(key, Bucket(key, label))
            if row["status"] != "pending":  # essays still being graded are left out until graded
                bucket.earned += row["points"]
                bucket.possible += row["max_points"]
            bucket.questions += 1
            bucket.numbers.append(row["number"])

    time_spent = int((attempt.submitted_at - attempt.started_at).total_seconds()) if attempt.submitted_at else 0
    return Report(
        time_spent=time_spent,
        seconds_per_question=round(time_spent / len(rows)) if rows else 0,
        counts=counts,
        by_difficulty=[difficulty[k] for k in DIFFICULTY_ORDER if k in difficulty],
        topics=sorted(topics.values(), key=lambda t: (t.percent, t.label)),
    )
