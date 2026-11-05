from collections import defaultdict
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.application.custom_quiz import (
    LEVELS,
    SECONDS_PER_QUESTION,
    CustomQuizError,
    QuizRequest,
    bank_counts,
    create_custom_quiz,
)
from app.application.paper import PART_ORDER, available_parts, question_count
from app.application.report import build_report
from app.application.teacher_tests import results_visible
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
from app.models import (
    AttemptStatus,
    Category,
    Exam,
    ExamAttempt,
    ExamMode,
    ExamVisibility,
    GradingStatus,
    Question,
    User,
    UserRole,
)
from app.routers.admin.common import redirect
from app.scope import breadcrumb, load_categories, scope_of, scope_paths, walk
from app.templating import flash, templates

router = APIRouter()

NO_STORE = {"Cache-Control": "no-store"}


def _can_see(exam: Exam | None, user: User | None) -> bool:
    if exam is None or exam.exam_mode != ExamMode.PRACTICE:
        return False
    if exam.visibility == ExamVisibility.PERSONAL:  # a student's own quiz: nobody else, not even admins
        return user is not None and exam.created_by == user.id
    return exam.is_published or (user is not None and user.role == UserRole.ADMIN)  # admins preview drafts


async def _get_visible_exam(session, exam_id: int, user: User | None) -> Exam:
    exam = await session.get(Exam, exam_id)
    if not _can_see(exam, user):
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
async def home(request: Request, user: CurrentUser, session: SessionDep, c: int | None = None):
    """Exam list with a table-of-contents sidebar; `c` selects a category (its whole branch)."""
    exams = (
        await session.scalars(
            select(Exam)
            .where(  # published public exams only, even for admins
                Exam.exam_mode == ExamMode.PRACTICE, Exam.visibility == ExamVisibility.PUBLIC, Exam.is_published
            )
            .order_by(Exam.id)
        )
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

    roots = await load_categories(session)
    paths = scope_paths(roots)
    nodes = {node.id: node for node, _ in walk(roots)}
    # Published exams per category, counting each exam in every ancestor of its category too.
    exam_counts: dict[int, int] = defaultdict(int)
    # Verified questions per branch too: a category without exams can still host a custom quiz.
    question_totals: dict[int, int] = defaultdict(int)
    for category_id, n in (
        await session.execute(
            select(Question.category_id, func.count()).where(Question.is_verified).group_by(Question.category_id)
        )
    ).all():
        if category := nodes.get(category_id):
            for cid in category.path.strip("/").split("/"):
                question_totals[int(cid)] += n
    branch_of: dict[int, set[int]] = {}
    for exam in exams:
        category = nodes.get(exam.category_id)
        ancestors = {int(i) for i in category.path.strip("/").split("/")} if category else set()
        branch_of[exam.id] = ancestors
        for cid in ancestors:
            exam_counts[cid] += 1
    selected = nodes.get(c) if c else None
    trail = breadcrumb(roots, selected.id) if selected else []
    shown = [e for e in exams if selected is None or selected.id in branch_of[e.id]]
    by_root: dict[int, list[Exam]] = defaultdict(list)
    for exam in shown:
        by_root[exam.root_id].append(exam)
    groups = [(r, by_root[r.id]) for r in roots if by_root.get(r.id)]
    quiz = None
    if selected and question_totals.get(selected.id):
        quiz = {"bank": await bank_counts(session, selected), "levels": LEVELS, "seconds": SECONDS_PER_QUESTION}
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "user": user,
            "groups": groups,
            "roots": roots,
            "exam_counts": exam_counts,
            "question_totals": question_totals,
            "toc_ids": set(exam_counts) | set(question_totals),
            "quiz": quiz,
            "selected": selected,
            "trail": trail,
            "open_ids": {n.id for n in trail},
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
            "trail": breadcrumb(await load_categories(session, exam.root_id), exam.category_id),
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


# ---------------------------------------------------------------- custom quizzes


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip() or default)
    except ValueError:
        return default


@router.post("/practice")
async def create_practice(request: Request, user: LoggedInUser, session: SessionDep):
    """Build a personal quiz from one category and start it right away."""
    form = await request.form()
    category = await session.get(Category, _int(form.get("category_id")))
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy mục.")
    back = f"/?c={category.id}#tu-tao"  # read before a rollback expires the instance
    quiz = QuizRequest(
        counts={t: _int(form.get(f"count_{t}")) for t in PART_ORDER},
        levels=[str(lv) for lv in form.getlist("levels") if lv in LEVELS],
        minutes=_int(form.get("minutes")) or None,
    )
    try:
        exam = await create_custom_quiz(session, user, category, quiz)
        attempt = await start_or_resume(session, user, exam)
    except (CustomQuizError, AttemptError) as exc:
        await session.rollback()
        flash(request, str(exc), "error")
        return redirect(back)
    return redirect(f"/attempts/{attempt.id}")


@router.get("/practice/mine")
async def my_practice(request: Request, user: LoggedInUser, session: SessionDep):
    """The quizzes this student built, newest first, with their attempts."""
    await finalize_expired(session, user.id)
    exams = (
        await session.scalars(
            select(Exam)
            .where(Exam.visibility == ExamVisibility.PERSONAL, Exam.created_by == user.id)
            .order_by(Exam.id.desc())
        )
    ).all()
    ids = [e.id for e in exams]
    stats = {
        exam_id: (n, best)
        for exam_id, n, best in (
            await session.execute(
                select(ExamAttempt.exam_id, func.count(), func.max(ExamAttempt.score))
                .where(ExamAttempt.user_id == user.id, ExamAttempt.exam_id.in_(ids))
                .group_by(ExamAttempt.exam_id)
            )
        ).all()
    }
    now = await db_now(session)
    active = dict(
        (
            await session.execute(
                select(ExamAttempt.exam_id, ExamAttempt.id).where(
                    ExamAttempt.user_id == user.id,
                    ExamAttempt.exam_id.in_(ids),
                    ExamAttempt.status == AttemptStatus.IN_PROGRESS,
                    ExamAttempt.expires_at > now,
                )
            )
        ).all()
    )
    submitted = {
        exam_id: attempt_id
        for exam_id, attempt_id in (
            await session.execute(
                select(ExamAttempt.exam_id, func.max(ExamAttempt.id))
                .where(
                    ExamAttempt.user_id == user.id,
                    ExamAttempt.exam_id.in_(ids),
                    ExamAttempt.status == AttemptStatus.SUBMITTED,
                )
                .group_by(ExamAttempt.exam_id)
            )
        ).all()
    }
    roots = await load_categories(session)
    return templates.TemplateResponse(
        request,
        "practice_mine.html",
        {
            "user": user,
            "exams": exams,
            "stats": stats,
            "active": active,
            "submitted": submitted,
            "counts": {e.id: await question_count(session, e) for e in exams},
            "scope_paths": scope_paths(roots),
            "scope_of": scope_of,
        },
    )


@router.post("/practice/{exam_id}/delete")
async def delete_practice(request: Request, user: LoggedInUser, session: SessionDep, exam_id: int):
    exam = await session.get(Exam, exam_id)
    if exam is None or exam.visibility != ExamVisibility.PERSONAL or exam.created_by != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy bài.")
    await session.delete(exam)
    await session.commit()
    flash(request, "Đã xoá bài tự tạo.")
    return redirect("/practice/mine")


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


async def _result_context(session, user: User, attempt_id: int) -> dict[str, Any] | None:
    """Data shared by the report dashboard and the solutions page; None while the attempt is running."""
    attempt = await _own_attempt(session, attempt_id, user)
    await finalize_expired(session, user.id, attempt.exam_id)
    await session.refresh(attempt)
    if attempt.status != AttemptStatus.SUBMITTED:  # still running: answers must stay hidden
        return None
    exam = await session.get(Exam, attempt.exam_id)
    now = await db_now(session)
    if not results_visible(exam, now):  # private test still open: no score or answers yet
        return {"user": user, "attempt": attempt, "exam": exam, "locked": True}
    used = await session.scalar(
        select(func.count())
        .select_from(ExamAttempt)
        .where(ExamAttempt.user_id == user.id, ExamAttempt.exam_id == exam.id)
    )
    rows = review(attempt)
    flagged = set(attempt.flags or [])
    times = attempt.question_times or {}
    for row, q in zip(rows, attempt.paper_snapshot["questions"], strict=True):
        row["flagged"] = str(q["id"]) in flagged
        row["seconds"] = times.get(str(q["id"]))
    report = await build_report(session, attempt, rows)
    return {
        "user": user,
        "attempt": attempt,
        "exam": exam,
        "rows": rows,
        "report": report,
        "slowest": set(report.slowest),
        "flagged_count": len(flagged),
        "grading_pending": attempt.grading_status == GradingStatus.PENDING_AI,
        "grading_failed": attempt.grading_status == GradingStatus.FAILED,
        "chosen_parts": (attempt.paper_snapshot or {}).get("parts"),
        "can_retry": _can_see(exam, user)
        and (exam.is_published or exam.visibility == ExamVisibility.PERSONAL)
        and (exam.max_attempts is None or used < exam.max_attempts),
    }


@router.get("/attempts/{attempt_id}/result")
async def attempt_result(request: Request, user: LoggedInUser, session: SessionDep, attempt_id: int):
    """Report dashboard shown right after submitting."""
    context = await _result_context(session, user, attempt_id)
    if context is None:
        return redirect(f"/attempts/{attempt_id}")
    if context.get("locked"):
        return templates.TemplateResponse(request, "result_locked.html", context, headers=NO_STORE)
    return templates.TemplateResponse(request, "result.html", context, headers=NO_STORE)


@router.get("/attempts/{attempt_id}/solutions")
async def attempt_solutions(request: Request, user: LoggedInUser, session: SessionDep, attempt_id: int):
    """Detailed solutions, opened from the dashboard: answers, answer keys and worked solutions."""
    context = await _result_context(session, user, attempt_id)
    if context is None:
        return redirect(f"/attempts/{attempt_id}")
    if context.get("locked"):
        return redirect(f"/attempts/{attempt_id}/result")
    return templates.TemplateResponse(request, "solutions.html", context, headers=NO_STORE)


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
            "times": attempt.question_times or {},
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
    times: Annotated[dict[str, Any] | None, Body(embed=True)] = None,
):
    """Grade the attempt. JSON body {"answers": {question_id: ...}, "flags": [question_id]}; safe to call twice."""
    await _own_attempt(session, attempt_id, user)
    attempt = await submit(session, attempt_id, answers, flags, times)
    exam = await session.get(Exam, attempt.exam_id)
    if not results_visible(exam, await db_now(session)):
        return JSONResponse(
            {"status": attempt.status.value, "result_url": f"/attempts/{attempt_id}/result"}, headers=NO_STORE
        )
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
    times: Annotated[dict[str, Any] | None, Body(embed=True)] = None,
):
    """Auto-save. JSON body {"changes": {question_id: value | null}}; only the changed questions are sent."""
    await _own_attempt(session, attempt_id, user)
    try:
        attempt, now = await save_answers(session, attempt_id, changes, flags, times)
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
    exam = await session.get(Exam, attempt.exam_id)
    hidden = not results_visible(exam, await db_now(session))
    return JSONResponse(
        {
            "grading_status": attempt.grading_status.value,
            "score": str(attempt.score) if attempt.score and not hidden else None,
        },
        headers=NO_STORE,
    )
