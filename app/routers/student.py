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
from app.application.paper import PART_ORDER, available_parts, preview_questions, question_count
from app.application.report import build_report
from app.application.teacher_tests import results_visible
from app.attempts import (
    AttemptClosed,
    AttemptError,
    active_attempt,
    build_snapshot,
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
from app.grades_data import PRACTICE_BOXES
from app.models import (
    AccessTier,
    AttemptStatus,
    Category,
    Difficulty,
    Exam,
    ExamAttempt,
    ExamMode,
    ExamTarget,
    ExamVisibility,
    GradingStatus,
    KnowledgeUnit,
    Question,
    QuestionCategory,
    User,
    UserRole,
)
from app.routers.admin.common import redirect
from app.scope import breadcrumb, load_categories, scope_of, scope_paths, walk
from app.sections_data import HOT_FOREIGN, HOT_LESSONS, HOT_MATERIALS, SECTIONS
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
@router.get("/section/{section_id}")
async def home(
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    c: int | None = None,
    section: str | None = None,
    section_id: str | None = None,
    level: str | None = None,
):
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
    cat_links = (
        select(Question.id.label("qid"), Question.category_id.label("cid"))
        .where(Question.is_verified, Question.category_id.is_not(None))
        .union(
            select(QuestionCategory.question_id.label("qid"), QuestionCategory.category_id.label("cid"))
            .join(Question, QuestionCategory.question_id == Question.id)
            .where(Question.is_verified)
        )
        .subquery()
    )
    for category_id, n in (
        await session.execute(
            select(cat_links.c.cid, func.count(cat_links.c.qid.distinct()))
            .group_by(cat_links.c.cid)
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
    now = await db_now(session) if user else None
    pro = bool(user and now and user.has_pro(now))
    if selected and question_totals.get(selected.id):
        quiz = {
            "bank": await bank_counts(session, selected, include_pro=pro),
            "levels": LEVELS,
            "seconds": SECONDS_PER_QUESTION,
        }

    # Calculate total attempts across all students to rank hot exams
    total_attempts: dict[int, int] = dict(
        (
            await session.execute(select(ExamAttempt.exam_id, func.count(ExamAttempt.id)).group_by(ExamAttempt.exam_id))
        ).all()
    )

    # Sort exams so hot, most attempted exams come first
    hot_exams = sorted(shown, key=lambda e: total_attempts.get(e.id, 0), reverse=True)

    active_section_key = section or section_id
    current_section = SECTIONS.get(active_section_key) if active_section_key else None

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "sections": SECTIONS,
            "practice_boxes": PRACTICE_BOXES,
            "hot_exams": hot_exams,
            "hot_materials": HOT_MATERIALS,
            "hot_lessons": HOT_LESSONS,
            "hot_foreign": HOT_FOREIGN,
            "total_attempts": total_attempts,
            "current_section": current_section,
            "active_section_key": active_section_key,
            "current_level": level,
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
    pro = False
    if user:
        now = await db_now(session)
        pro = user.has_pro(now)
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
    is_pro_exam = exam.access_tier == AccessTier.PRO
    preview_items = []
    if is_pro_exam and not pro:
        preview_qs = await preview_questions(session, exam)
        preview_items = public_questions(build_snapshot(preview_qs, shuffle_choices=False))
    return templates.TemplateResponse(
        request,
        "exam.html",
        {
            "user": user,
            "pro": pro,
            "exam": exam,
            "is_pro_exam": is_pro_exam,
            "preview_items": preview_items,
            "locked_questions_count": max(0, total_questions - len(preview_items)),
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


@router.get("/api/exams/{exam_id}/preview")
async def exam_preview_api(user: CurrentUser, session: SessionDep, exam_id: int):
    """Freemium JSON preview API: for PRO exams, Free/anonymous users receive only the first N preview questions."""
    exam = await _get_visible_exam(session, exam_id, user)
    total = await question_count(session, exam)
    now = await db_now(session) if user else None
    pro = bool(user and now and user.has_pro(now))
    is_pro_exam = exam.access_tier == AccessTier.PRO
    if is_pro_exam and not pro:
        qs = await preview_questions(session, exam)
        items = public_questions(build_snapshot(qs, shuffle_choices=False))
        return JSONResponse(
            {
                "exam_id": exam.id,
                "title": exam.title,
                "access_tier": exam.access_tier.value,
                "origin_type": exam.origin_type.value,
                "preview_only": True,
                "preview_question_count": len(items),
                "total_questions": total,
                "locked_questions_count": max(0, total - len(items)),
                "questions": items,
                "upgrade_url": "/plans",
                "upgrade_message": (
                    f"Bạn đang xem thử {len(items)}/{total} câu đầu tiên của đề độc quyền PRO. "
                    "Nâng cấp tài khoản PRO để mở khóa toàn bộ câu hỏi và làm bài bấm giờ."
                ),
            },
            headers=NO_STORE,
        )
    qs = await preview_questions(session, exam, limit=total)
    items = public_questions(build_snapshot(qs, shuffle_choices=False))
    return JSONResponse(
        {
            "exam_id": exam.id,
            "title": exam.title,
            "access_tier": exam.access_tier.value,
            "origin_type": exam.origin_type.value,
            "preview_only": False,
            "preview_question_count": len(items),
            "total_questions": total,
            "locked_questions_count": 0,
            "questions": items,
        },
        headers=NO_STORE,
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
        now = await db_now(session)
        exam = await create_custom_quiz(session, user, category, quiz, include_pro=user.has_pro(now))
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
    pro = user.has_pro(now)
    if not pro:
        # Freemium Funnel A: On FREE exams, blur step-by-step AI solutions on the last 5 ADVANCED_APPLICATION
        # (Vận dụng cao) questions, or on all questions when `exam.is_solution_pro_only` is True.
        vdc_numbers = [
            r["number"]
            for r in rows
            if r.get("difficulty") == Difficulty.ADVANCED_APPLICATION.value
            and (r.get("explanation") or (r.get("essay") and r["essay"].get("solution")))
        ]
        locked_numbers = (
            {r["number"] for r in rows}
            if getattr(exam, "is_solution_pro_only", False)
            else set(vdc_numbers[-5:])
        )
        for r in rows:
            sol = r.get("explanation") or (r.get("essay") or {}).get("solution")
            if sol and r["number"] in locked_numbers:
                r["solution_pro_locked"] = True
                r["explanation"] = None
                if r.get("essay") and "solution" in r["essay"]:
                    r["essay"] = {**r["essay"], "solution": None}
    report = await build_report(session, attempt, rows)
    return {
        "user": user,
        "pro": pro,
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


@router.get("/section/tai-lieu/level/{level_id}/chinh-khoa")
@router.get("/section/tai-lieu/level/{level_id}")
async def materials_level_curriculum(
    request: Request,
    user: CurrentUser,
    level_id: str,
):
    tl_section = SECTIONS.get("tai-lieu")
    if not tl_section:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy chuyên mục Tài liệu.")

    level = next((l for l in tl_section.get("levels", []) if l["id"] == level_id), None)
    if not level:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Không tìm thấy cấp học: {level_id}")

    return templates.TemplateResponse(
        request,
        "materials_level_detail.html",
        {
            "user": user,
            "section": tl_section,
            "level": level,
            "sections": SECTIONS,
        },
    )


@router.get("/grade/{grade_id}")
async def grade_detail(request: Request, user: CurrentUser, grade_id: str):
    box = next((b for b in PRACTICE_BOXES if b["id"] == grade_id), None)
    if not box:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy thông tin khối lớp.")
    return templates.TemplateResponse(
        request,
        "grade_detail.html",
        {
            "box": box,
            "user": user,
            "sections": SECTIONS,
        },
    )


@router.get("/grade/{grade_id}/subject/{subject_slug}")
@router.get("/grade/{grade_id}/subject/{subject_slug}/{section_mode}")
async def grade_subject_detail(
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    grade_id: str,
    subject_slug: str,
    section_mode: str | None = None,
):
    if section_mode and section_mode not in ("study", "practice"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy trang yêu cầu.")

    from app.curriculum_data import get_curriculum

    box = next((b for b in PRACTICE_BOXES if b["id"] == grade_id), None)
    if not box:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy thông tin khối lớp.")

    row = next((r for r in box["rows"] if r.get("slug") == subject_slug), None)
    subj_name = row["name"] if row else subject_slug.replace("-", " ").title()

    curriculum = get_curriculum(grade_id, box["title"], subj_name, subject_slug)

    # Load published exams to match with chapters/subject
    exams = (
        await session.scalars(
            select(Exam)
            .where(Exam.exam_mode == ExamMode.PRACTICE, Exam.visibility == ExamVisibility.PUBLIC, Exam.is_published)
            .order_by(Exam.id)
        )
    ).all()
    counts = {exam.id: await question_count(session, exam) for exam in exams}
    mine: dict[int, int] = {}
    active: dict[int, int] = {}
    best: dict[int, Any] = {}
    if user:
        now = await db_now(session)
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

    # Match exams to chapters
    ch_exams: dict[str, list[Exam]] = defaultdict(list)
    for ch in curriculum["chapters"]:
        ch_id = ch["id"]
        ch_title = ch["title"].lower()
        db_cat_id = ch.get("db_cat_id")
        for exam in exams:
            # Match by direct db_cat_id if category is ancestor
            category = nodes.get(exam.category_id)
            ancestors = {int(i) for i in category.path.strip("/").split("/")} if category else set()
            if db_cat_id and db_cat_id in ancestors:
                ch_exams[ch_id].append(exam)
            elif (
                any(word in exam.title.lower() for word in ["dao động", "chương 1"] if "chương 1" in ch_title)
                and "chương 1" in ch_title
                and not ch_exams[ch_id]
            ):
                ch_exams[ch_id].append(exam)
            elif (
                any(
                    word in exam.title.lower()
                    for word in ["khí lí tưởng", "sóng"]
                    if "sóng" in ch_title or "khí" in ch_title
                )
                and not ch_exams[ch_id]
            ):
                ch_exams[ch_id].append(exam)

    return templates.TemplateResponse(
        request,
        "grade_subject.html",
        {
            "curriculum": curriculum,
            "box": box,
            "user": user,
            "ch_exams": ch_exams,
            "question_counts": counts,
            "my_attempts": mine,
            "active": active,
            "best": best,
            "scope_paths": paths,
            "scope_of": scope_of,
            "section_mode": section_mode,
            "subject_slug": subject_slug,
            "grade_id": grade_id,
        },
    )
