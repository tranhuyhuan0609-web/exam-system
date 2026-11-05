"""Post-exam report: overview (rank, time vs allowed, pace), difficulty breakdown, topic mastery, slow questions.

Rates count questions answered fully correctly ("đúng 3/5 câu"); partly right true/false questions and
partly right essays are not counted as correct. Essays still being graded by the AI are left out until
they are graded. A topic (chuyên đề) is the question's lesson, else its chapter, else its subject;
questions deleted from the bank since the attempt fall under "Khác".
"""

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Difficulty, ExamAttempt, Question

WEAK_THRESHOLD = 50  # percent
SLOWEST_MAX = 5  # the slowest quarter of the paper, at most 5 questions
DIFFICULTY_ORDER = [d.value for d in Difficulty]  # declaration order = increasing difficulty
DIFFICULTY_LABELS = {
    "RECOGNITION": "Nhận biết",
    "UNDERSTANDING": "Thông hiểu",
    "APPLICATION": "Vận dụng",
    "ADVANCED_APPLICATION": "Vận dụng cao",
}
# (minimum score on the 10-point scale, label, css class)
RANKS = [
    (Decimal(9), "Xuất sắc", "rank-excellent"),
    (Decimal(8), "Giỏi", "rank-good"),
    (Decimal("6.5"), "Khá", "rank-fair"),
    (Decimal(5), "Trung bình", "rank-average"),
    (Decimal(0), "Yếu", "rank-weak"),
]


@dataclass
class Bucket:
    key: str
    label: str
    correct: int = 0
    graded: int = 0  # questions with a final result (pending essays excluded)
    numbers: list[int] = field(default_factory=list)  # question numbers in the paper

    @property
    def percent(self) -> int:
        return round(100 * self.correct / self.graded) if self.graded else 0

    @property
    def weak(self) -> bool:
        return self.graded > 0 and self.percent < WEAK_THRESHOLD


@dataclass
class Report:
    rank: str
    rank_class: str
    time_spent: int
    time_allowed: int
    seconds_per_question: int
    counts: dict[str, int]
    by_difficulty: list[Bucket]
    topics: list[Bucket]
    slowest: list[int]  # question numbers, slowest first (only when timing was recorded)

    @property
    def weak_topics(self) -> list[Bucket]:
        return sorted((t for t in self.topics if t.weak), key=lambda t: (t.percent, t.label))


def rank_for(score: Decimal | None) -> tuple[str, str]:
    score = score or Decimal(0)
    return next((label, css) for minimum, label, css in RANKS if score >= minimum)


async def _topic_names(session: AsyncSession, question_ids: list[int]) -> dict[int, str]:
    """Topic of each question: the name of the most specific category it is filed under."""
    rows = (
        await session.execute(
            select(Question.id, Category.name)
            .join(Category, Category.id == Question.category_id)
            .where(Question.id.in_(question_ids))
        )
    ).all()
    return dict(rows)


async def build_report(session: AsyncSession, attempt: ExamAttempt, rows: list[dict[str, Any]]) -> Report:
    """`rows` is attempts.review(attempt) with "seconds" added: one dict per question."""
    snapshot_questions = attempt.paper_snapshot.get("questions", [])
    topics_by_id = await _topic_names(session, [q["id"] for q in snapshot_questions])

    difficulty: dict[str, Bucket] = {}
    topics: dict[str, Bucket] = {}
    counts = {"correct": 0, "partial": 0, "wrong": 0, "unanswered": 0, "pending": 0}
    for q, row in zip(snapshot_questions, rows, strict=True):
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        level = q.get("difficulty") or Difficulty.UNDERSTANDING.value
        topic = topics_by_id.get(q["id"], "Khác")
        for buckets, key, label in ((difficulty, level, DIFFICULTY_LABELS.get(level, level)), (topics, topic, topic)):
            bucket = buckets.setdefault(key, Bucket(key, label))
            bucket.numbers.append(row["number"])
            if row["status"] != "pending":
                bucket.graded += 1
                bucket.correct += row["status"] == "correct"

    time_spent = int((attempt.submitted_at - attempt.started_at).total_seconds()) if attempt.submitted_at else 0
    timed = [r for r in rows if r.get("seconds")]
    slow_count = min(SLOWEST_MAX, math.ceil(len(rows) / 4))
    slowest = [r["number"] for r in sorted(timed, key=lambda r: -r["seconds"])[:slow_count]]
    rank, rank_class = rank_for(attempt.score)
    return Report(
        rank=rank,
        rank_class=rank_class,
        time_spent=time_spent,
        time_allowed=int((attempt.expires_at - attempt.started_at).total_seconds()),
        seconds_per_question=round(time_spent / len(rows)) if rows else 0,
        counts=counts,
        by_difficulty=[difficulty[k] for k in DIFFICULTY_ORDER if k in difficulty],
        topics=sorted(topics.values(), key=lambda t: (t.percent, t.label)),
        slowest=slowest,
    )
