from collections import defaultdict
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.application.paper import available_parts, question_count
from app.application.report import build_report
from app.attempts import (
    AttemptClosed,
    AttemptError,
    active_attempt,
    db_now,
    finalize_expired,
    public_questions,
    remaining_seconds,
    review,
    save_answers,
    start_or_resume,
    submit,
)
from app.deps import ApiUser, CurrentUser, LoggedInUser, SessionDep
from app.models import AttemptStatus, Exam, ExamAttempt, ExamMode, GradingStatus, User, UserRole
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


async def _best_scores(session, user_id: int, exam_id: int | None = None) -> dict[int, Any]:
    stmt = (
        select(ExamAttempt.exam_id, func.max(ExamAttempt.score))
        .where(ExamAttempt.user_id == user_id, ExamAttempt.status == AttemptStatus.SUBMITTED)
        .group_by(ExamAttempt.exam_id)
    )
    if exam_id is not None:
        stmt = stmt.where(ExamAttempt.exam_id == exam_id)
    return dict((await session.execute(stmt)).all())


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
    counts = {exam.id: await question_count(session, exam) for exam in exams}
    mine: dict[int, int] = {}
    active: dict[int, int] = {}
    best: dict[int, Any] = {}
    if user:
        now = await db_now(session)
        await finalize_expired(session, user.id, now=now)
        best = await _best_scores(session, user.id)
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
            "best": best,
            "scope_paths": paths,
            "scope_of": scope_of,
        },
    )


@router.get("/exams/{exam_id}")
async def exam_page(request: Request, user: CurrentUser, session: SessionDep, exam_id: int):
    exam = await _get_visible_exam(session, exam_id, user)
    total_questions = await question_count(session, exam)
    attempts, current, now = [], None, None
    if user:
        now = await db_now(session)
        await finalize_expired(session, user.id, exam_id, now)
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
            "question_count": total_questions,
            "attempts": attempts,
            "current": current,
            "current_remaining": remaining_seconds(current, now) if current else 0,
            "now": now,
            "limit_reached": exam.max_attempts is not None and used >= exam.max_attempts and not current,
            "best_score": max((a.score for a in attempts if a.score is not None), default=None),
            "parts": await available_parts(session, exam),
            "scope_path": scope_paths(await load_subject_tree(session, exam.subject_id)).get(scope_of(exam).key, ""),
        },
    )


@router.post("/exams/{exam_id}/start")
async def start_exam(request: Request, user: LoggedInUser, session: SessionDep, exam_id: int):
    exam = await _get_visible_exam(session, exam_id, user)
    form = await request.form()
    # "custom" is present when the student used the part picker; otherwise the full exam is started.
    parts = [str(p) for p in form.getlist("parts")] if form.get("custom") == "1" else None
    try:
        attempt = await start_or_resume(session, user, exam, parts)
    except AttemptError as exc:
        flash(request, str(exc), "error")
        return redirect(f"/exams/{exam_id}")
    return redirect(f"/attempts/{attempt.id}")


@router.get("/attempts/{attempt_id}")
async def attempt_page(request: Request, user: LoggedInUser, session: SessionDep, attempt_id: int):
    attempt = await _own_attempt(session, attempt_id, user)
    await finalize_expired(session, user.id, attempt.exam_id)
    await session.refresh(attempt)
    if attempt.status == AttemptStatus.SUBMITTED:
        return redirect(f"/attempts/{attempt_id}/result")
    exam = await session.get(Exam, attempt.exam_id)
    return templates.TemplateResponse(
        request, "attempt.html", {"user": user, "attempt": attempt, "exam": exam}, headers=NO_STORE
    )


@router.get("/attempts/{attempt_id}/result")
async def attempt_result(request: Request, user: LoggedInUser, session: SessionDep, attempt_id: int):
    attempt = await _own_attempt(session, attempt_id, user)
    await finalize_expired(session, user.id, attempt.exam_id)
    await session.refresh(attempt)
    if attempt.status != AttemptStatus.SUBMITTED:  # still running: answers must stay hidden
        return redirect(f"/attempts/{attempt_id}")
    exam = await session.get(Exam, attempt.exam_id)
    used = await session.scalar(
        select(func.count())
        .select_from(ExamAttempt)
        .where(ExamAttempt.user_id == user.id, ExamAttempt.exam_id == exam.id)
    )
    rows = review(attempt)
    flagged = set(attempt.flags or [])
    for row, q in zip(rows, attempt.paper_snapshot["questions"], strict=True):
        row["flagged"] = str(q["id"]) in flagged
    report = await build_report(session, attempt, rows)
    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "user": user,
            "attempt": attempt,
            "exam": exam,
            "rows": rows,
            "report": report,
            "flagged_count": len(flagged),
            "unanswered": sum(r["status"] == "unanswered" for r in rows),
            "time_spent": int((attempt.submitted_at - attempt.started_at).total_seconds()),
            "grading_pending": attempt.grading_status == GradingStatus.PENDING_AI,
            "grading_failed": attempt.grading_status == GradingStatus.FAILED,
            "chosen_parts": (attempt.paper_snapshot or {}).get("parts"),
            "can_retry": exam.is_published and (exam.max_attempts is None or used < exam.max_attempts),
        },
        headers=NO_STORE,
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
            "flags": attempt.flags or [],
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


@router.post("/api/attempts/{attempt_id}/submit")
async def submit_attempt(
    user: ApiUser,
    session: SessionDep,
    attempt_id: int,
    answers: Annotated[dict[str, Any], Body(embed=True)],
    flags: Annotated[list[str] | None, Body(embed=True)] = None,
):
    """Grade the attempt. JSON body {"answers": {question_id: ...}, "flags": [question_id]}; safe to call twice."""
    await _own_attempt(session, attempt_id, user)
    attempt = await submit(session, attempt_id, answers, flags)
    return JSONResponse(
        {
            "status": attempt.status.value,
            "score": str(attempt.score),
            "correct_count": attempt.correct_count,
            "total_questions": attempt.total_questions,
            "result_url": f"/attempts/{attempt_id}/result",
        },
        headers=NO_STORE,
    )


@router.patch("/api/attempts/{attempt_id}/answers")
async def autosave_answers(
    user: ApiUser,
    session: SessionDep,
    attempt_id: int,
    changes: Annotated[dict[str, Any], Body(embed=True)],
    flags: Annotated[list[str] | None, Body(embed=True)] = None,
):
    """Auto-save. JSON body {"changes": {question_id: value | null}}; only the changed questions are sent."""
    await _own_attempt(session, attempt_id, user)
    try:
        attempt, now = await save_answers(session, attempt_id, changes, flags)
    except AttemptClosed as exc:
        return JSONResponse(
            {"status": exc.status, "result_url": f"/attempts/{attempt_id}/result"},
            status_code=status.HTTP_409_CONFLICT,
            headers=NO_STORE,
        )
    return JSONResponse(
        {
            "status": attempt.status.value,
            "saved": len(attempt.answers),
            "remaining_seconds": remaining_seconds(attempt, now),
            "saved_at": now.isoformat(),
        },
        headers=NO_STORE,
    )


@router.get("/api/attempts/{attempt_id}/grading")
async def attempt_grading(user: ApiUser, session: SessionDep, attempt_id: int):
    """Polled by the result page while the AI grades essays."""
    attempt = await _own_attempt(session, attempt_id, user)
    return JSONResponse(
        {"grading_status": attempt.grading_status.value, "score": str(attempt.score) if attempt.score else None},
        headers=NO_STORE,
    )
