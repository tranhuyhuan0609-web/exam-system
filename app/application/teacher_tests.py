"""Teachers' private scheduled tests (Pro plan).

A test is an Exam with visibility PRIVATE and mode SCHEDULED: never listed anywhere, reached through
/k/<access_token>, and students must also type access_code. It opens at available_from and closes for
everyone at available_until; each student gets one attempt. Students only see their score and the
solutions once the test has closed, so answers cannot leak to classmates still working.

Questions come from the shared bank, configured like a custom quiz (category, counts per part, levels):
either a random paper per student (MATRIX) or one paper drawn now and shared by everyone (FIXED).
"""

import random
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.custom_quiz import LEVELS, QuizRequest, bank_counts, describe, validate
from app.application.paper import PART_ORDER, MatrixRule, arrange, draw_matrix, parse_matrix
from app.models import (
    AttemptStatus,
    Category,
    Exam,
    ExamAttempt,
    ExamEnrollment,
    ExamMode,
    ExamQuestion,
    ExamVisibility,
    QuestionSource,
    User,
)
from app.scope import scope_for

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: easy to read aloud and type
MAX_MINUTES = 300


class ScheduleError(Exception):
    """The test cannot be saved as asked; the message is user-facing."""


def new_token() -> str:
    return secrets.token_urlsafe(16)


def new_code(length: int = 6) -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def normalize_code(code: str) -> str:
    return "".join(code.split()).upper()


@dataclass
class ScheduleSpec:
    title: str
    category: Category
    counts: dict[str, int]
    levels: list[str]
    starts_at: datetime | None
    ends_at: datetime | None
    minutes: int
    same_paper: bool = False
    shuffle: bool = True
    errors: list[str] = field(default_factory=list)


async def check_spec(session: AsyncSession, spec: ScheduleSpec, now: datetime, *, is_new: bool) -> list[str]:
    errors = list(spec.errors)
    if not spec.title.strip() or len(spec.title) > 300:
        errors.append("Hãy nhập tên bài kiểm tra (tối đa 300 ký tự).")
    bank = await bank_counts(session, spec.category)
    errors += validate(QuizRequest(spec.counts, spec.levels), bank)
    if not 1 <= spec.minutes <= MAX_MINUTES:
        errors.append(f"Thời gian làm bài phải từ 1 đến {MAX_MINUTES} phút.")
    if spec.starts_at is None or spec.ends_at is None:
        errors.append("Hãy chọn giờ mở và giờ đóng bài.")
    else:
        if spec.ends_at <= spec.starts_at:
            errors.append("Giờ đóng phải sau giờ mở.")
        elif (spec.ends_at - spec.starts_at).total_seconds() < spec.minutes * 60:
            errors.append("Khoảng từ giờ mở đến giờ đóng ngắn hơn thời gian làm bài.")
        if is_new and spec.ends_at <= now:
            errors.append("Giờ đóng đã qua.")
    return errors


def _rules(spec: ScheduleSpec) -> list[MatrixRule]:
    levels = tuple(lv for lv in LEVELS if lv in spec.levels)
    if len(levels) == len(LEVELS):
        levels = ()
    key = scope_for(spec.category).key
    return [MatrixRule(key, None, t, n, levels) for t in PART_ORDER if (n := spec.counts.get(t, 0)) > 0]


def _apply(exam: Exam, spec: ScheduleSpec) -> None:
    exam.title = spec.title.strip()
    exam.root_id = spec.category.root_id
    exam.category_id = spec.category.id
    exam.description = describe({r.question_type: r.count for r in _rules(spec)})
    exam.matrix = [rule.to_json() for rule in _rules(spec)]
    exam.available_from = spec.starts_at
    exam.available_until = spec.ends_at
    exam.duration_seconds = spec.minutes * 60
    exam.shuffle_questions = spec.shuffle
    exam.shuffle_choices = spec.shuffle
    exam.question_source = QuestionSource.FIXED if spec.same_paper else QuestionSource.MATRIX


async def draw_shared_paper(session: AsyncSession, exam: Exam, rng: random.Random | None = None) -> None:
    """Draw the one paper every student of a FIXED test gets (replacing any earlier draw)."""
    rng = rng or random.SystemRandom()
    await session.execute(delete(ExamQuestion).where(ExamQuestion.exam_id == exam.id))
    questions = arrange(await draw_matrix(session, exam, rng), shuffle=False, rng=rng)
    for position, q in enumerate(questions, start=1):
        session.add(ExamQuestion(exam_id=exam.id, question_id=q.id, position=position))
    await session.flush()


async def create_test(session: AsyncSession, teacher: User, spec: ScheduleSpec, now: datetime) -> Exam:
    if errors := await check_spec(session, spec, now, is_new=True):
        raise ScheduleError(" ".join(errors))
    exam = Exam(
        exam_mode=ExamMode.SCHEDULED,
        visibility=ExamVisibility.PRIVATE,
        is_published=False,
        max_attempts=1,
        allow_custom_parts=False,
        created_by=teacher.id,
        access_token=new_token(),
        access_code=new_code(),
    )
    _apply(exam, spec)
    session.add(exam)
    await session.flush()
    if spec.same_paper:
        await draw_shared_paper(session, exam)
    return exam


@dataclass
class MatrixSpec:
    """A test from a matrix: one rule per row (scope, level, type, count), as read from a teacher's file."""

    title: str
    root: Category
    rules: list[MatrixRule]
    starts_at: datetime | None
    ends_at: datetime | None
    minutes: int
    same_paper: bool = False
    shuffle: bool = True
    public: bool = False  # admins: listed on the site, any student starts it at once (no schedule, link or code)


async def check_matrix(session: AsyncSession, spec: MatrixSpec, now: datetime) -> list[str]:
    from app.application.paper import availability, validate_rules

    errors = validate_rules(spec.rules)
    if not spec.title.strip() or len(spec.title) > 300:
        errors.append("Hãy nhập tên bài kiểm tra (tối đa 300 ký tự).")
    if any(not rule.scope_key for rule in spec.rules):
        errors.append("Có dòng chưa chọn mục lục.")
    elif spec.rules:
        probe = Exam(root_id=spec.root.id, category_id=spec.root.id)
        have_counts = await availability(session, probe, spec.rules)
        for number, (rule, have) in enumerate(zip(spec.rules, have_counts, strict=True), 1):
            if have is None:
                errors.append(f"Dòng {number}: mục lục không thuộc môn {spec.root.name}.")
            elif have < rule.count:
                errors.append(f"Dòng {number}: ngân hàng chỉ có {have} câu phù hợp (cần {rule.count}).")
    if not 1 <= spec.minutes <= MAX_MINUTES:
        errors.append(f"Thời gian làm bài phải từ 1 đến {MAX_MINUTES} phút.")
    if spec.public:
        return errors
    if spec.starts_at is None or spec.ends_at is None:
        errors.append("Hãy chọn giờ mở và giờ đóng bài.")
    elif spec.ends_at <= spec.starts_at:
        errors.append("Giờ đóng phải sau giờ mở.")
    elif (spec.ends_at - spec.starts_at).total_seconds() < spec.minutes * 60:
        errors.append("Khoảng từ giờ mở đến giờ đóng ngắn hơn thời gian làm bài.")
    elif spec.ends_at <= now:
        errors.append("Giờ đóng đã qua.")
    return errors


async def create_from_matrix(session: AsyncSession, teacher: User, spec: MatrixSpec, now: datetime) -> Exam:
    if errors := await check_matrix(session, spec, now):
        raise ScheduleError(" ".join(errors))
    exam = Exam(
        exam_mode=ExamMode.SCHEDULED,
        visibility=ExamVisibility.PRIVATE,
        is_published=False,
        max_attempts=1,
        allow_custom_parts=False,
        created_by=teacher.id,
        access_token=new_token(),
        access_code=new_code(),
        title=spec.title.strip(),
        root_id=spec.root.id,
        category_id=spec.root.id,
        matrix=[rule.to_json() for rule in spec.rules],
        available_from=spec.starts_at,
        available_until=spec.ends_at,
        duration_seconds=spec.minutes * 60,
        shuffle_questions=spec.shuffle,
        shuffle_choices=spec.shuffle,
        question_source=QuestionSource.FIXED if spec.same_paper else QuestionSource.MATRIX,
    )
    if spec.public:  # a practice exam on the site: no window, no link or code, attempts unlimited
        exam.exam_mode, exam.visibility, exam.is_published, exam.max_attempts = (
            ExamMode.PRACTICE,
            ExamVisibility.PUBLIC,
            True,
            None,
        )
        exam.access_token = exam.access_code = exam.available_from = exam.available_until = None
    totals: dict[str, int] = {}
    for rule in spec.rules:
        totals[rule.question_type] = totals.get(rule.question_type, 0) + rule.count
    exam.description = "Theo ma trận đề · " + describe(totals)
    session.add(exam)
    await session.flush()
    if spec.same_paper:
        await draw_shared_paper(session, exam)
    return exam


async def create_fixed_test(
    session: AsyncSession, teacher: User, spec: MatrixSpec, question_ids: list[int], now: datetime
) -> Exam:
    """A test of the given questions in that order (similar papers): private with link and code, or (admins)
    public on the site. `spec.rules` is unused."""
    errors = [
        e for e in await check_matrix(session, MatrixSpec(**{**spec.__dict__, "rules": []}), now) if "Ma trận" not in e
    ]
    if not question_ids:
        errors.append("Đề cần ít nhất 1 câu hỏi.")
    if errors:
        raise ScheduleError(" ".join(errors))
    exam = Exam(
        exam_mode=ExamMode.SCHEDULED,
        visibility=ExamVisibility.PRIVATE,
        is_published=False,
        max_attempts=1,
        allow_custom_parts=False,
        created_by=teacher.id,
        access_token=new_token(),
        access_code=new_code(),
        title=spec.title.strip(),
        root_id=spec.root.id,
        category_id=spec.root.id,
        available_from=spec.starts_at,
        available_until=spec.ends_at,
        duration_seconds=spec.minutes * 60,
        shuffle_questions=spec.shuffle,
        shuffle_choices=spec.shuffle,
        question_source=QuestionSource.FIXED,
        description=f"Đề tương tự · {len(question_ids)} câu",
    )
    if spec.public:
        exam.exam_mode, exam.visibility, exam.is_published, exam.max_attempts = (
            ExamMode.PRACTICE,
            ExamVisibility.PUBLIC,
            True,
            None,
        )
        exam.access_token = exam.access_code = exam.available_from = exam.available_until = None
    session.add(exam)
    await session.flush()
    for position, question_id in enumerate(question_ids, start=1):
        session.add(ExamQuestion(exam_id=exam.id, question_id=question_id, position=position))
    await session.flush()
    return exam


async def attempts_started(session: AsyncSession, exam: Exam) -> int:
    return await session.scalar(select(func.count()).select_from(ExamAttempt).where(ExamAttempt.exam_id == exam.id))


async def update_test(session: AsyncSession, exam: Exam, spec: ScheduleSpec, now: datetime) -> None:
    if await attempts_started(session, exam):
        raise ScheduleError("Đã có học sinh làm bài, không sửa được nữa.")
    if errors := await check_spec(session, spec, now, is_new=False):
        raise ScheduleError(" ".join(errors))
    old_paper = (exam.question_source, exam.matrix)
    _apply(exam, spec)
    await session.flush()
    if spec.same_paper and (exam.question_source, exam.matrix) != old_paper:
        await draw_shared_paper(session, exam)
    elif not spec.same_paper:
        await session.execute(delete(ExamQuestion).where(ExamQuestion.exam_id == exam.id))


def spec_from_exam(exam: Exam) -> dict[str, Any]:
    """Form values of an existing test."""
    rules = parse_matrix(exam.matrix)
    levels = list(rules[0].levels) if rules and rules[0].levels else list(LEVELS)
    return {
        "title": exam.title,
        "category_id": exam.category_id,
        "counts": {r.question_type: r.count for r in rules},
        "levels": levels,
        "starts_at": exam.available_from,
        "ends_at": exam.available_until,
        "minutes": exam.duration_seconds // 60,
        "same_paper": exam.question_source == QuestionSource.FIXED,
        "shuffle": exam.shuffle_questions,
    }


def schedule_status(exam: Exam, now: datetime) -> str:
    if exam.available_from and now < exam.available_from:
        return "UPCOMING"
    if exam.available_until and now >= exam.available_until:
        return "CLOSED"
    return "OPEN"


STATUS_LABELS = {"UPCOMING": "Sắp mở", "OPEN": "Đang mở", "CLOSED": "Đã đóng"}


def results_visible(exam: Exam, now: datetime) -> bool:
    """Students of a private test see scores and solutions only after it closes."""
    if exam.visibility != ExamVisibility.PRIVATE:
        return True
    return exam.available_until is not None and now >= exam.available_until


# ---------------------------------------------------------------- results


@dataclass
class ResultRow:
    user: User
    joined_at: datetime
    attempt: ExamAttempt | None

    @property
    def status(self) -> str:
        if self.attempt is None:
            return "Chưa làm"
        return "Đã nộp" if self.attempt.status == AttemptStatus.SUBMITTED else "Đang làm"

    @property
    def minutes(self) -> float | None:
        a = self.attempt
        if a is None or a.submitted_at is None:
            return None
        return round((a.submitted_at - a.started_at).total_seconds() / 60, 1)


@dataclass
class ResultStats:
    joined: int
    submitted: int
    average: Decimal | None
    highest: Decimal | None
    lowest: Decimal | None
    bins: list[int]  # students per score band [0,1), [1,2) ... [9,10]


async def results(session: AsyncSession, exam: Exam) -> tuple[list[ResultRow], ResultStats]:
    enrolled = (
        await session.execute(
            select(User, ExamEnrollment.joined_at)
            .join(ExamEnrollment, ExamEnrollment.user_id == User.id)
            .where(ExamEnrollment.exam_id == exam.id)
        )
    ).all()
    attempts = {
        a.user_id: a
        for a in (
            await session.scalars(
                select(ExamAttempt).where(ExamAttempt.exam_id == exam.id).order_by(ExamAttempt.attempt_no)
            )
        ).all()
    }
    rows = [ResultRow(user, joined, attempts.get(user.id)) for user, joined in enrolled]
    rows.sort(
        key=lambda r: (
            r.attempt is None or r.attempt.score is None,
            -(r.attempt.score or 0) if r.attempt else 0,
            r.user.full_name,
        )
    )
    scores = [r.attempt.score for r in rows if r.attempt and r.attempt.status == AttemptStatus.SUBMITTED]
    scores = [Decimal(s) for s in scores if s is not None]
    bins = [0] * 10
    for score in scores:
        bins[min(9, int(score))] += 1
    stats = ResultStats(
        joined=len(rows),
        submitted=len(scores),
        average=(sum(scores) / len(scores)).quantize(Decimal("0.01")) if scores else None,
        highest=max(scores, default=None),
        lowest=min(scores, default=None),
        bins=bins,
    )
    return rows, stats


async def enroll(session: AsyncSession, exam: Exam, user: User) -> None:
    if await session.get(ExamEnrollment, (exam.id, user.id)) is None:
        session.add(ExamEnrollment(exam_id=exam.id, user_id=user.id))
        await session.flush()


async def is_enrolled(session: AsyncSession, exam: Exam, user: User) -> bool:
    return await session.get(ExamEnrollment, (exam.id, user.id)) is not None
