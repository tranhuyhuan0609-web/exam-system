"""Plans and what each allows.

| who              | practice exams | library: read | library: download                 | teacher tests |
|------------------|----------------|---------------|-----------------------------------|---------------|
| student, free    | N per day      | N per day     | no                                | -             |
| student, Pro     | unlimited      | unlimited     | no                                | -             |
| teacher, free    | N per day      | N per day     | no                                | no            |
| teacher, Pro     | unlimited      | unlimited     | exam papers only (not books)      | yes           |
| admin            | everything                                                                        |

Taking a teacher's private test never counts against the daily limit: the teacher assigned it.
A day is a calendar day in Vietnam (UTC+7).
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Exam, ExamAttempt, ExamVisibility, LibraryDocument, LibraryKind, LibraryView, User, UserRole

VN = timezone(timedelta(hours=7))
FREE_MATRICES_PER_DAY = 5  # exam matrices a free teacher may have read by AI each day (/teacher/make)
DOWNLOADABLE_KINDS = {LibraryKind.EXAM, LibraryKind.HSG}  # exam papers; books and topic notes are read online


def vn_day(now: datetime) -> date:
    return now.astimezone(VN).date()


def day_start(now: datetime) -> datetime:
    return datetime.combine(vn_day(now), datetime.min.time(), VN)


def unlimited(user: User, now: datetime) -> bool:
    return user.has_pro(now)


async def exams_used_today(session: AsyncSession, user: User, now: datetime) -> int:
    return await session.scalar(
        select(func.count())
        .select_from(ExamAttempt)
        .join(Exam, Exam.id == ExamAttempt.exam_id)
        .where(
            ExamAttempt.user_id == user.id,
            ExamAttempt.started_at >= day_start(now),
            Exam.visibility != ExamVisibility.PRIVATE,
        )
    )


async def check_exam_quota(session: AsyncSession, user: User, exam: Exam, now: datetime) -> str | None:
    """None when the user may start a new attempt, else the reason shown to them."""
    if exam.visibility == ExamVisibility.PRIVATE or unlimited(user, now):
        return None
    limit = get_settings().free_exams_per_day
    if await exams_used_today(session, user, now) >= limit:
        return f"Tài khoản miễn phí được làm {limit} đề mỗi ngày. Nâng cấp Pro để luyện đề không giới hạn."
    return None


async def docs_read_today(session: AsyncSession, user: User, now: datetime) -> int:
    return await session.scalar(
        select(func.count()).where(LibraryView.user_id == user.id, LibraryView.day == vn_day(now))
    )


async def open_document(session: AsyncSession, user: User, doc: LibraryDocument, now: datetime) -> str | None:
    """Count a read (opening the same document again the same day is free). None = allowed."""
    if unlimited(user, now):
        return None
    day = vn_day(now)
    seen = await session.scalar(
        select(LibraryView.id).where(
            LibraryView.user_id == user.id, LibraryView.document_id == doc.id, LibraryView.day == day
        )
    )
    if seen:
        return None
    limit = get_settings().free_docs_per_day
    if await docs_read_today(session, user, now) >= limit:
        return f"Tài khoản miễn phí được xem {limit} tài liệu mỗi ngày. Nâng cấp Pro để xem không giới hạn."
    await session.execute(
        insert(LibraryView).values(user_id=user.id, document_id=doc.id, day=day).on_conflict_do_nothing()
    )
    await session.commit()
    return None


def can_download(user: User, doc: LibraryDocument, now: datetime) -> bool:
    if user.role == UserRole.ADMIN:
        return True
    return user.role == UserRole.TEACHER and user.has_pro(now) and doc.downloadable and doc.kind in DOWNLOADABLE_KINDS
