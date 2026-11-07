"""Difficulty checked by AI: sources file questions by their own sections, and their "Vận dụng cao" often holds
direct one-formula questions. A text model grades them on the Ministry's four levels; the grade replaces the
source's label. Each question is graded once (crawled_questions.difficulty_checked)."""

import json
import logging
from typing import Literal

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CrawledQuestion, CrawlItemStatus, Difficulty, Question

log = logging.getLogger(__name__)
GROUP = 8  # questions per call (Groq's free tier: 8,000 tokens a minute)
CHECKED_LEVELS = (Difficulty.APPLICATION, Difficulty.ADVANCED_APPLICATION)

PROMPT = """Bạn là giáo viên ra đề theo chuẩn của Bộ GD&ĐT. Xếp MỨC ĐỘ NHẬN THỨC của từng câu hỏi (JSON) dưới đây:
- RECOGNITION (Nhận biết): nhớ, nhận ra định nghĩa, công thức, khái niệm, sự kiện; không phải tính toán.
- UNDERSTANDING (Thông hiểu): hiểu, giải thích; thay số trực tiếp vào MỘT công thức, một bước suy luận.
- APPLICATION (Vận dụng): bài quen thuộc nhiều bước, phối hợp hai kiến thức trở lên, biến đổi rồi mới tính.
- ADVANCED_APPLICATION (Vận dụng cao): tình huống mới hoặc thực tế phức tạp, nhiều bước không theo mẫu, biện luận
  tham số, đánh giá, cần ý tưởng riêng.
Xếp theo việc HỌC SINH phải làm để giải, không theo độ dài câu hay nhãn của nguồn. Khi phân vân giữa hai mức,
chọn mức thấp hơn.
Trả về DUY NHẤT JSON: {"items": [{"ref": <ref>, "level": "<mức>"}]}"""


class _Grade(BaseModel):
    ref: int
    level: Literal["RECOGNITION", "UNDERSTANDING", "APPLICATION", "ADVANCED_APPLICATION"]


class _Grades(BaseModel):
    items: list[_Grade]


def _payload(question: Question) -> dict:
    return {
        "ref": question.id,
        "content": question.content[:1500],
        "choices": [c.get("content", "") for c in question.choices or []][:6],
        "solution": (question.explanation or "")[:800],
    }


async def _grade(models, questions: list[Question]) -> tuple[dict[int, str], str]:
    user = json.dumps([_payload(q) for q in questions], ensure_ascii=False)
    for model in models:
        try:
            if not await model.available():
                continue
            raw = await model.ask_json(PROMPT, user, len(questions))
            return {g.ref: g.level for g in _Grades.model_validate_json(raw).items}, f"{model.name}:{model.model}"
        except (ValidationError, ValueError) as exc:
            log.warning("Difficulty from %s:%s unusable: %s", model.name, model.model, exc)
        except Exception as exc:  # quota, network: the next model
            log.warning("Difficulty with %s:%s failed: %s", model.name, model.model, exc)
    return {}, ""


async def check_difficulty(session: AsyncSession, models, limit: int = 200) -> dict[str, int]:
    """Grade up to `limit` published crawled questions labelled Vận dụng / Vận dụng cao by their source."""
    stats = {"checked": 0, "changed": 0, "unanswered": 0}
    rows = (
        await session.execute(
            select(CrawledQuestion, Question)
            .join(Question, Question.id == CrawledQuestion.question_id)
            .where(
                CrawledQuestion.status == CrawlItemStatus.PUBLISHED,
                CrawledQuestion.difficulty_checked.is_(False),
                Question.difficulty.in_(CHECKED_LEVELS),
            )
            .order_by(Question.id)
            .limit(limit)
        )
    ).all()
    for start in range(0, len(rows), GROUP):
        group = rows[start : start + GROUP]
        grades, provider = await _grade(models, [q for _, q in group])
        if not grades:
            stats["unanswered"] += len(group)
            break  # every model busy: next sweep
        for item, question in group:
            level = grades.get(question.id)
            if level is None:
                stats["unanswered"] += 1
                continue
            item.difficulty_checked = True
            stats["checked"] += 1
            if level != question.difficulty.value:
                log.info("Difficulty of %s: %s -> %s (%s)", question.id, question.difficulty.value, level, provider)
                question.difficulty = item.difficulty = Difficulty(level)
                stats["changed"] += 1
        await session.commit()
    return stats
