"""Turn AI import drafts into bank questions (and optionally an exam). Used by the admin review screen and by
the automatic publication of library imports (app/application/library_publish.py)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import question_rules as rules
from app.ai.pipeline import normalize_draft
from app.domain.question_hash import hash_of_choices
from app.models import Difficulty, Exam, ExamMode, ExamQuestion, ImportJob, Question, QuestionType, SourceType


def drafts_of(job: ImportJob) -> list[dict]:
    return [normalize_draft(d) for d in (job.drafts or [])]


def publish_problems(draft: dict) -> list[str]:
    """Errors preventing a draft from becoming a bank question (empty list = OK)."""
    from app.ai.pipeline import FLAG_LABELS

    if draft.get("question_id"):
        return ["Đã đưa vào ngân hàng."]
    if draft.get("type") not in QuestionType.__members__:
        return [FLAG_LABELS["unsupported_type"]]
    return rules.validate(
        draft["type"],
        draft.get("content", ""),
        list(draft.get("choices", [])),
        draft.get("correct"),
        list(draft.get("truths") or []),
        list(draft.get("answers") or []),
    )


def _question(job: ImportJob, d: dict, verified: bool) -> Question:
    question = Question(
        root_id=job.root_id,
        category_id=job.category_id,
        difficulty=Difficulty(d.get("difficulty") or job.default_difficulty.value),
        question_type=QuestionType(d["type"]),
        content=d["content"],
        choices=rules.build_choices(d["type"], d["choices"], d.get("correct"), d.get("truths")),
        accepted_answers=d["answers"] if d["type"] == QuestionType.SHORT_ANSWER else None,
        explanation=d.get("explanation") or None,
        image_path=d.get("figure"),
        source_type=SourceType.AI_UPLOAD,
        is_verified=verified,
    )
    question.content_hash = hash_of_choices(d["type"], question.content, question.choices)
    return question


async def publish_drafts(
    session: AsyncSession, job: ImportJob, indexes: list[int], *, verified: bool, reuse_duplicates: bool = False
) -> list[Question]:
    """Bank questions for the chosen drafts, in order. With `reuse_duplicates`, a draft whose exact copy is
    already in the bank (another code of the same official paper, a re-synced file) points to that question
    instead of adding a second copy."""
    drafts = drafts_of(job)
    chosen = [i for i in indexes if i < len(drafts) and not publish_problems(drafts[i])]
    fresh = {i: _question(job, drafts[i], verified) for i in chosen}
    existing: dict[str, Question] = {}
    if reuse_duplicates and fresh:
        found = await session.scalars(
            select(Question).where(Question.content_hash.in_([q.content_hash for q in fresh.values()]))
        )
        existing = {q.content_hash: q for q in found}
    questions = []
    for i in chosen:
        question = existing.get(fresh[i].content_hash) or fresh[i]
        if question is fresh[i]:
            session.add(question)
            existing[question.content_hash] = question  # the same question twice in one file: one copy
        questions.append(question)
    await session.flush()
    for i, question in zip(chosen, questions, strict=True):
        drafts[i]["question_id"] = question.id
    job.drafts = drafts
    return questions


async def create_exam(
    session: AsyncSession,
    job: ImportJob,
    questions: list[Question],
    *,
    title: str,
    minutes: int,
    published: bool,
    user_id: int | None,
) -> Exam:
    exam = Exam(
        root_id=job.root_id,
        category_id=job.category_id,
        title=title[:300],
        exam_mode=ExamMode.PRACTICE,
        duration_seconds=minutes * 60,
        is_published=published,
        created_by=user_id,
    )
    session.add(exam)
    await session.flush()
    unique = list({q.id: q for q in questions}.values())  # a question reused twice appears once
    session.add_all(
        ExamQuestion(exam_id=exam.id, question_id=q.id, position=pos) for pos, q in enumerate(unique, start=1)
    )
    await session.flush()
    return exam
