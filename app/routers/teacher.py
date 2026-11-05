"""Teacher area (Pro plan): private scheduled tests, plus the student side of joining one by link + code."""

import io
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import func, select

from app.application.custom_quiz import LEVELS, SECONDS_PER_QUESTION, bank_counts
from app.application.paper import PART_ORDER, fixed_questions, question_count
from app.application.teacher_tests import (
    STATUS_LABELS,
    ScheduleError,
    ScheduleSpec,
    attempts_started,
    create_test,
    draw_shared_paper,
    enroll,
    is_enrolled,
    new_code,
    normalize_code,
    results,
    schedule_status,
    spec_from_exam,
    update_test,
)
from app.attempts import AttemptError, active_attempt, db_now, finalize_expired, start_or_resume
from app.deps import LoggedInUser, SessionDep, require_user
from app.models import (
    AttemptStatus,
    Category,
    Exam,
    ExamAttempt,
    ExamEnrollment,
    ExamVisibility,
    User,
    UserRole,
)
from app.routers.admin.common import redirect
from app.scope import breadcrumb, load_categories, scope_options, scope_paths
from app.templating import DISPLAY_TZ, flash, templates

router = APIRouter()


async def require_teacher(user: Annotated[User, Depends(require_user)]) -> User:
    if user.role not in (UserRole.TEACHER, UserRole.ADMIN):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Trang này dành cho giáo viên.")
    return user


TeacherUser = Annotated[User, Depends(require_teacher)]


async def _require_pro(request: Request, session, user: User) -> datetime:
    now = await db_now(session)
    if not user.has_pro(now):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cần gói Pro để tạo hoặc sửa bài kiểm tra.")
    return now


async def _own_test(session, user: User, exam_id: int) -> Exam:
    exam = await session.get(Exam, exam_id)
    if exam is None or exam.visibility != ExamVisibility.PRIVATE or exam.created_by != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy bài kiểm tra.")
    return exam


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip() or default)
    except ValueError:
        return default


def _local(value: Any) -> datetime | None:
    """datetime-local input (Vietnam time) -> aware datetime."""
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=DISPLAY_TZ) if value else None
    except ValueError:
        return None


async def _spec(session, form) -> ScheduleSpec:
    category = await session.get(Category, _int(form.get("category_id")))
    if category is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Hãy chọn mục lục.")
    return ScheduleSpec(
        title=str(form.get("title") or ""),
        category=category,
        counts={t: _int(form.get(f"count_{t}")) for t in PART_ORDER},
        levels=[str(lv) for lv in form.getlist("levels") if lv in LEVELS],
        starts_at=_local(form.get("starts_at")),
        ends_at=_local(form.get("ends_at")),
        minutes=_int(form.get("minutes")),
        same_paper=form.get("same_paper") == "1",
        shuffle=form.get("shuffle") == "1",
    )


def _form_values(form) -> dict[str, Any]:
    return {
        "title": form.get("title") or "",
        "category_id": _int(form.get("category_id")) or None,
        "counts": {t: _int(form.get(f"count_{t}")) for t in PART_ORDER},
        "levels": [str(lv) for lv in form.getlist("levels")],
        "starts_at": _local(form.get("starts_at")),
        "ends_at": _local(form.get("ends_at")),
        "minutes": _int(form.get("minutes")) or "",
        "same_paper": form.get("same_paper") == "1",
        "shuffle": form.get("shuffle") == "1",
    }


async def _form_page(request, session, user, values, exam=None, error=None):
    return templates.TemplateResponse(
        request,
        "teacher/test_form.html",
        {
            "user": user,
            "exam": exam,
            "values": values,
            "error": error,
            "scope_options": scope_options(await load_categories(session)),
            "levels": LEVELS,
            "parts": PART_ORDER,
            "pace": SECONDS_PER_QUESTION,
        },
        status_code=status.HTTP_400_BAD_REQUEST if error else status.HTTP_200_OK,
    )


# ---------------------------------------------------------------- teacher pages


@router.get("/teacher")
async def teacher_home(request: Request, user: TeacherUser, session: SessionDep):
    now = await db_now(session)
    tests = (
        await session.scalars(
            select(Exam)
            .where(Exam.visibility == ExamVisibility.PRIVATE, Exam.created_by == user.id)
            .order_by(Exam.available_from.desc(), Exam.id.desc())
        )
    ).all()
    counts = dict(
        (
            await session.execute(
                select(ExamEnrollment.exam_id, func.count())
                .where(ExamEnrollment.exam_id.in_([t.id for t in tests]))
                .group_by(ExamEnrollment.exam_id)
            )
        ).all()
    )
    paths = scope_paths(await load_categories(session))
    return templates.TemplateResponse(
        request,
        "teacher/index.html",
        {
            "user": user,
            "tests": tests,
            "joined": counts,
            "now": now,
            "has_pro": user.has_pro(now),
            "status_of": lambda e: schedule_status(e, now),
            "STATUS_LABELS": STATUS_LABELS,
            "scope_paths": paths,
        },
    )


@router.post("/teacher/upgrade")
async def request_upgrade(request: Request, user: TeacherUser, session: SessionDep):
    if user.role == UserRole.TEACHER:
        user.pro_requested_at = await db_now(session)
        await session.commit()
        flash(request, "Đã gửi yêu cầu nâng cấp Pro. Quản trị viên sẽ duyệt sớm.")
    return redirect("/teacher")


@router.get("/teacher/tests/new")
async def new_test(request: Request, user: TeacherUser, session: SessionDep, c: int | None = None):
    now = await _require_pro(request, session, user)
    starts = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)  # next full hour
    values = {
        "title": "",
        "category_id": c,
        "counts": {"MULTIPLE_CHOICE": 10},
        "levels": list(LEVELS),
        "starts_at": starts,
        "ends_at": starts + timedelta(hours=1),
        "minutes": 45,
        "same_paper": False,
        "shuffle": True,
    }
    return await _form_page(request, session, user, values)


@router.post("/teacher/tests")
async def create_test_route(request: Request, user: TeacherUser, session: SessionDep):
    now = await _require_pro(request, session, user)
    form = await request.form()
    try:
        exam = await create_test(session, user, await _spec(session, form), now)
    except ScheduleError as exc:  # raised before anything is written
        return await _form_page(request, session, user, _form_values(form), error=str(exc))
    await session.commit()
    flash(request, "Đã tạo bài kiểm tra. Gửi link và mã bên dưới cho học sinh.")
    return redirect(f"/teacher/tests/{exam.id}")


@router.get("/teacher/tests/{exam_id:int}")
async def test_detail(request: Request, user: TeacherUser, session: SessionDep, exam_id: int):
    exam = await _own_test(session, user, exam_id)
    now = await db_now(session)
    await finalize_expired(session, exam_id=exam.id, now=now)
    rows, stats = await results(session, exam)
    roots = await load_categories(session, exam.root_id)
    return templates.TemplateResponse(
        request,
        "teacher/test_detail.html",
        {
            "user": user,
            "exam": exam,
            "now": now,
            "status": schedule_status(exam, now),
            "STATUS_LABELS": STATUS_LABELS,
            "link": str(request.base_url).rstrip("/") + f"/k/{exam.access_token}",
            "question_count": await question_count(session, exam),
            "paper": await fixed_questions(session, exam) if exam.question_source.value == "FIXED" else [],
            "started": await attempts_started(session, exam),
            "rows": rows,
            "stats": stats,
            "trail": breadcrumb(roots, exam.category_id),
            "has_pro": user.has_pro(now),
        },
    )


@router.get("/teacher/tests/{exam_id:int}/edit")
async def edit_test(request: Request, user: TeacherUser, session: SessionDep, exam_id: int):
    await _require_pro(request, session, user)
    exam = await _own_test(session, user, exam_id)
    return await _form_page(request, session, user, spec_from_exam(exam), exam=exam)


@router.post("/teacher/tests/{exam_id:int}")
async def update_test_route(request: Request, user: TeacherUser, session: SessionDep, exam_id: int):
    now = await _require_pro(request, session, user)
    exam = await _own_test(session, user, exam_id)
    form = await request.form()
    try:
        await update_test(session, exam, await _spec(session, form), now)
    except ScheduleError as exc:  # raised before the exam is modified
        return await _form_page(request, session, user, _form_values(form), exam=exam, error=str(exc))
    await session.commit()
    flash(request, "Đã lưu bài kiểm tra.")
    return redirect(f"/teacher/tests/{exam_id}")


@router.post("/teacher/tests/{exam_id:int}/redraw")
async def redraw_test(request: Request, user: TeacherUser, session: SessionDep, exam_id: int):
    await _require_pro(request, session, user)
    exam = await _own_test(session, user, exam_id)
    if await attempts_started(session, exam):
        flash(request, "Đã có học sinh làm bài, không đổi đề được nữa.", "error")
    else:
        await draw_shared_paper(session, exam)
        await session.commit()
        flash(request, "Đã bốc đề mới.")
    return redirect(f"/teacher/tests/{exam_id}#de")


@router.post("/teacher/tests/{exam_id:int}/code")
async def regenerate_code(request: Request, user: TeacherUser, session: SessionDep, exam_id: int):
    exam = await _own_test(session, user, exam_id)
    exam.access_code = new_code()
    await session.commit()
    flash(request, f"Mã truy cập mới: {exam.access_code}. Học sinh đã vào bằng mã cũ vẫn làm được bài.")
    return redirect(f"/teacher/tests/{exam_id}")


@router.post("/teacher/tests/{exam_id:int}/delete")
async def delete_test(request: Request, user: TeacherUser, session: SessionDep, exam_id: int):
    exam = await _own_test(session, user, exam_id)
    if await attempts_started(session, exam):
        flash(request, "Đã có học sinh làm bài, không xoá được (kết quả cần được giữ lại).", "error")
        return redirect(f"/teacher/tests/{exam_id}")
    await session.delete(exam)
    await session.commit()
    flash(request, "Đã xoá bài kiểm tra.")
    return redirect("/teacher")


@router.get("/teacher/tests/{exam_id:int}/results.xlsx")
async def export_results(user: TeacherUser, session: SessionDep, exam_id: int):
    exam = await _own_test(session, user, exam_id)
    await finalize_expired(session, exam_id=exam.id)
    rows, _ = await results(session, exam)
    wb = Workbook()
    ws = wb.active
    ws.title = "Kết quả"
    ws.append([exam.title])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([])
    header = [
        "STT",
        "Họ và tên",
        "Email",
        "Trạng thái",
        "Điểm",
        "Số câu đúng",
        "Bắt đầu",
        "Nộp lúc",
        "Thời gian (phút)",
    ]
    ws.append(header)
    for cell in ws[3]:
        cell.font = Font(bold=True)

    def local(value):
        return value.astimezone(DISPLAY_TZ).replace(tzinfo=None) if value else None

    for number, row in enumerate(rows, start=1):
        a = row.attempt
        ws.append(
            [
                number,
                row.user.full_name,
                row.user.email,
                row.status,
                float(a.score) if a and a.score is not None and a.status == AttemptStatus.SUBMITTED else None,
                f"{a.correct_count}/{a.total_questions}" if a and a.status == AttemptStatus.SUBMITTED else None,
                local(a.started_at) if a else None,
                local(a.submitted_at) if a else None,
                row.minutes,
            ]
        )
    for column, width in zip("ABCDEFGHI", (6, 28, 32, 12, 8, 12, 18, 18, 16), strict=True):
        ws.column_dimensions[column].width = width
    for r in ws.iter_rows(min_row=4, min_col=7, max_col=8):
        for cell in r:
            cell.number_format = "hh:mm dd/mm/yyyy"
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="ket-qua-bai-{exam.id}.xlsx"'},
    )


@router.get("/api/categories/{category_id:int}/bank")
async def category_bank(user: TeacherUser, session: SessionDep, category_id: int):
    """Verified questions per type and level in a branch (teacher test form)."""
    category = await session.get(Category, category_id)
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy mục.")
    return JSONResponse(await bank_counts(session, category))


# ---------------------------------------------------------------- students: join by link + code


async def _test_by_token(session, token: str) -> Exam:
    exam = await session.scalar(
        select(Exam).where(Exam.access_token == token, Exam.visibility == ExamVisibility.PRIVATE)
    )
    if exam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link bài kiểm tra không đúng hoặc đã bị xoá.")
    return exam


@router.get("/k/{token}")
async def access_page(request: Request, user: LoggedInUser, session: SessionDep, token: str):
    exam = await _test_by_token(session, token)
    now = await db_now(session)
    await finalize_expired(session, user.id, exam.id, now)
    joined = exam.created_by == user.id or await is_enrolled(session, exam, user)
    attempt = await session.scalar(
        select(ExamAttempt)
        .where(ExamAttempt.exam_id == exam.id, ExamAttempt.user_id == user.id)
        .order_by(ExamAttempt.attempt_no.desc())
    )
    teacher = await session.get(User, exam.created_by) if exam.created_by else None
    return templates.TemplateResponse(
        request,
        "test_access.html",
        {
            "user": user,
            "exam": exam,
            "teacher": teacher,
            "joined": joined,
            "attempt": attempt,
            "running": await active_attempt(session, user.id, exam.id, now),
            "status": schedule_status(exam, now),
            "seconds_to_open": max(0, (exam.available_from - now).total_seconds()) if exam.available_from else 0,
            "question_count": await question_count(session, exam),
            "is_owner": exam.created_by == user.id,
        },
    )


@router.post("/k/{token}/join")
async def join_test(request: Request, user: LoggedInUser, session: SessionDep, token: str):
    exam = await _test_by_token(session, token)
    form = await request.form()
    if normalize_code(str(form.get("code") or "")) != exam.access_code:
        flash(request, "Mã truy cập không đúng.", "error")
        return redirect(f"/k/{token}")
    await enroll(session, exam, user)
    await session.commit()
    return redirect(f"/k/{token}")


@router.post("/k/{token}/start")
async def start_test(request: Request, user: LoggedInUser, session: SessionDep, token: str):
    exam = await _test_by_token(session, token)
    if exam.created_by == user.id:
        flash(request, "Đây là bài của bạn; hãy dùng tài khoản học sinh để làm thử.", "error")
        return redirect(f"/k/{token}")
    if not await is_enrolled(session, exam, user):
        flash(request, "Hãy nhập mã truy cập trước.", "error")
        return redirect(f"/k/{token}")
    try:
        attempt = await start_or_resume(session, user, exam)
    except AttemptError as exc:
        flash(request, str(exc), "error")
        return redirect(f"/k/{token}")
    return redirect(f"/attempts/{attempt.id}")


@router.get("/tests/mine")
async def my_tests(request: Request, user: LoggedInUser, session: SessionDep):
    """Private tests this student joined."""
    now = await db_now(session)
    await finalize_expired(session, user.id, now=now)
    tests = (
        await session.scalars(
            select(Exam)
            .join(ExamEnrollment, ExamEnrollment.exam_id == Exam.id)
            .where(ExamEnrollment.user_id == user.id)
            .order_by(Exam.available_from.desc())
        )
    ).all()
    attempts = {
        a.exam_id: a
        for a in (
            await session.scalars(
                select(ExamAttempt).where(
                    ExamAttempt.user_id == user.id, ExamAttempt.exam_id.in_([t.id for t in tests])
                )
            )
        ).all()
    }
    return templates.TemplateResponse(
        request,
        "tests_mine.html",
        {
            "user": user,
            "tests": tests,
            "attempts": attempts,
            "status_of": lambda e: schedule_status(e, now),
            "STATUS_LABELS": STATUS_LABELS,
        },
    )
