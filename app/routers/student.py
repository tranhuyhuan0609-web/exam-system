from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.attempts import AttemptError, active_attempt, db_now, public_questions, remaining_seconds, start_or_resume
from app.deps import ApiUser, CurrentUser, LoggedInUser, SessionDep
from app.models import AttemptStatus, Exam, ExamAttempt, ExamMode, ExamQuestion, User, UserRole
from app.routers.admin.common import redirect
from app.scope import load_subject_tree, scope_of, scope_paths
from app.templating import flash, templates

router = APIRouter()

NO_STORE = {"Cache-Control": "no-store"}


def _visible_exams_filter(user: User | None):
    conditions = [Exam.exam_mode == ExamMode.PRACTICE]
    if not (user and user.role == UserRole.ADMIN):  # admins may preview drafts
        conditions.append(Exam.is_published)
    return conditions


async def _get_visible_exam(session, exam_id: int, user: User | None) -> Exam:
    exam = await session.scalar(select(Exam).where(Exam.id == exam_id, *_visible_exams_filter(user)))
    if exam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy đề thi.")
    return exam


async def _own_attempt(session, attempt_id: int, user: User) -> ExamAttempt:
    attempt = await session.get(ExamAttempt, attempt_id)
    if attempt is None or attempt.user_id != user.id:  # 404, not 403: don't reveal other students' attempts
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy lượt làm bài.")
    return attempt


@router.get("/")
async def home(request: Request, user: CurrentUser, session: SessionDep):
    published = Exam.is_published  # the home page lists published exams only, even for admins
    exams = (
        await session.scalars(select(Exam).where(Exam.exam_mode == ExamMode.PRACTICE, published).order_by(Exam.id))
    ).all()
    counts = dict(
        (await session.execute(select(ExamQuestion.exam_id, func.count()).group_by(ExamQuestion.exam_id))).all()
    )
    mine: dict[int, int] = {}
    active: dict[int, int] = {}
    if user:
        now = await db_now(session)
        mine = dict(
            (
                await session.execute(
                    select(ExamAttempt.exam_id, func.count())
                    .where(ExamAttempt.user_id == user.id)
                    .group_by(ExamAttempt.exam_id)
                )
            ).all()
        )
        active = dict(
            (
                await session.execute(
                    select(ExamAttempt.exam_id, ExamAttempt.id).where(
                        ExamAttempt.user_id == user.id,
                        ExamAttempt.status == AttemptStatus.IN_PROGRESS,
                        ExamAttempt.expires_at > now,
                    )
                )
            ).all()
        )

    subjects = await load_subject_tree(session)
    paths = scope_paths(subjects)
    by_subject: dict[int, list[Exam]] = defaultdict(list)
    for exam in exams:
        by_subject[exam.subject_id].append(exam)
    groups = [(s, by_subject[s.id]) for s in subjects if by_subject.get(s.id)]
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "user": user,
            "groups": groups,
            "question_counts": counts,
            "my_attempts": mine,
            "active": active,
            "scope_paths": paths,
            "scope_of": scope_of,
        },
    )


@router.get("/exams/{exam_id}")
async def exam_page(request: Request, user: CurrentUser, session: SessionDep, exam_id: int):
    exam = await _get_visible_exam(session, exam_id, user)
    question_count = await session.scalar(
        select(func.count()).select_from(ExamQuestion).where(ExamQuestion.exam_id == exam_id)
    )
    attempts, current, now = [], None, None
    if user:
        now = await db_now(session)
        attempts = (
            await session.scalars(
                select(ExamAttempt)
                .where(ExamAttempt.user_id == user.id, ExamAttempt.exam_id == exam_id)
                .order_by(ExamAttempt.attempt_no.desc())
            )
        ).all()
        current = await active_attempt(session, user.id, exam_id, now)
    used = len(attempts)
    return templates.TemplateResponse(
        request,
        "exam.html",
        {
            "user": user,
            "exam": exam,
            "question_count": question_count,
            "attempts": attempts,
            "current": current,
            "current_remaining": remaining_seconds(current, now) if current else 0,
            "now": now,
            "limit_reached": exam.max_attempts is not None and used >= exam.max_attempts and not current,
            "scope_path": scope_paths(await load_subject_tree(session, exam.subject_id)).get(scope_of(exam).key, ""),
        },
    )


@router.post("/exams/{exam_id}/start")
async def start_exam(request: Request, user: LoggedInUser, session: SessionDep, exam_id: int):
    exam = await _get_visible_exam(session, exam_id, user)
    try:
        attempt = await start_or_resume(session, user, exam)
    except AttemptError as exc:
        flash(request, str(exc), "error")
        return redirect(f"/exams/{exam_id}")
    return redirect(f"/attempts/{attempt.id}")


@router.get("/attempts/{attempt_id}")
async def attempt_page(request: Request, user: LoggedInUser, session: SessionDep, attempt_id: int):
    attempt = await _own_attempt(session, attempt_id, user)
    exam = await session.get(Exam, attempt.exam_id)
    return templates.TemplateResponse(
        request, "attempt.html", {"user": user, "attempt": attempt, "exam": exam}, headers=NO_STORE
    )


@router.get("/api/attempts/{attempt_id}/paper")
async def attempt_paper(user: ApiUser, session: SessionDep, attempt_id: int):
    attempt = await _own_attempt(session, attempt_id, user)
    exam = await session.get(Exam, attempt.exam_id)
    now = await db_now(session)
    return JSONResponse(
        {
            "attempt_id": attempt.id,
            "attempt_no": attempt.attempt_no,
            "exam_id": exam.id,
            "exam_title": exam.title,
            "status": attempt.status.value,
            "duration_seconds": exam.duration_seconds,
            "remaining_seconds": remaining_seconds(attempt, now),
            "expires_at": attempt.expires_at.isoformat(),
            "questions": public_questions(attempt.paper_snapshot),
            "answers": attempt.answers,
        },
        headers=NO_STORE,
    )


@router.get("/api/attempts/{attempt_id}/clock")
async def attempt_clock(user: ApiUser, session: SessionDep, attempt_id: int):
    """Cheap endpoint the page polls to re-sync its countdown with the server."""
    attempt = await _own_attempt(session, attempt_id, user)
    now = await db_now(session)
    return JSONResponse(
        {"status": attempt.status.value, "remaining_seconds": remaining_seconds(attempt, now)}, headers=NO_STORE
    )
