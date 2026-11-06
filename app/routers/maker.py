"""Exam maker for teachers: a test from an uploaded matrix (ma trận đề). Every teacher may use it; free accounts a
few matrices a day (each one is read by an AI model)."""

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from starlette.datastructures import UploadFile

from app.application.custom_quiz import SECONDS_PER_QUESTION
from app.application.matrix_import import LEVEL_VALUES, TYPE_VALUES, MatrixError, MatrixRow, read_matrix
from app.application.paper import MatrixRule, availability
from app.application.teacher_tests import MatrixSpec, ScheduleError, create_from_matrix
from app.attempts import db_now
from app.deps import SessionDep
from app.models import Category, Exam, UserRole
from app.pipeline.adapters.ai_chain import DbUsage
from app.plans import FREE_MATRICES_PER_DAY
from app.routers.admin.common import redirect
from app.routers.teacher import TeacherUser, _int, _local
from app.scope import load_categories, scope_options
from app.templating import flash, templates

router = APIRouter()
MAX_UPLOAD = 15 * 1024 * 1024


async def _roots(session) -> list[Category]:
    return list(
        (await session.scalars(select(Category).where(Category.parent_id.is_(None)).order_by(Category.name))).all()
    )


async def _subject(session, value) -> Category:
    """A subject (root category) with its table of contents loaded (`kids`), for scope_options."""
    root = await session.get(Category, _int(value))
    if root is None or root.parent_id is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Hãy chọn môn.")
    return (await load_categories(session, root.id))[0]


async def _reads_left(session, user) -> int | None:
    """Matrices a free teacher may still have read today (None: no limit)."""
    if user.has_pro(await db_now(session)):
        return None
    return max(0, FREE_MATRICES_PER_DAY - await DbUsage().used_today(f"matrix:{user.id}"))


async def _models():
    from app.worker import chat_models, default_provider

    text, vision = chat_models()
    try:
        gemini = default_provider()
    except Exception:  # no Gemini key
        gemini = None
    return text, vision, gemini


@router.get("/teacher/make")
async def maker_home(request: Request, user: TeacherUser, session: SessionDep):
    return templates.TemplateResponse(
        request,
        "teacher/make.html",
        {"user": user, "roots": await _roots(session), "reads_left": await _reads_left(session, user)},
    )


async def _preview(request, session, user, root: Category, rows: list[MatrixRow], values: dict, error=None):
    probe = Exam(root_id=root.id, category_id=root.id)
    rules = [MatrixRule(r.scope_key, r.level, r.question_type, max(r.count, 1)) for r in rows]
    have = await availability(session, probe, [r for r in rules]) if rows else []
    return templates.TemplateResponse(
        request,
        "teacher/matrix_preview.html",
        {
            "user": user,
            "root": root,
            "rows": list(zip(rows, have, strict=True)),
            "options": scope_options([root]),
            "levels": LEVEL_VALUES,
            "types": TYPE_VALUES,
            "values": values,
            "error": error,
            "pace": SECONDS_PER_QUESTION,
        },
        status_code=status.HTTP_400_BAD_REQUEST if error else status.HTTP_200_OK,
    )


def _suggested_minutes(rows: list[MatrixRow]) -> int:
    seconds = sum(r.count * SECONDS_PER_QUESTION.get(r.question_type, 90) for r in rows)
    return max(5, -(-seconds // 300) * 5)


@router.post("/teacher/make/matrix")
async def upload_matrix(request: Request, user: TeacherUser, session: SessionDep):
    form = await request.form()
    root = await _subject(session, form.get("root_id"))
    upload = form.get("file")
    if not isinstance(upload, UploadFile) or not upload.filename:
        flash(request, "Hãy chọn file ma trận.", "error")
        return redirect("/teacher/make")
    left = await _reads_left(session, user)
    if left == 0:
        flash(
            request,
            f"Tài khoản miễn phí đọc {FREE_MATRICES_PER_DAY} ma trận mỗi ngày; nâng cấp Pro để không giới hạn.",
            "error",
        )
        return redirect("/teacher/make")
    data = await upload.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        flash(request, "File quá lớn (tối đa 15 MB).", "error")
        return redirect("/teacher/make")
    try:
        found = await read_matrix(root, upload.filename, data, *await _models())
    except MatrixError as exc:
        flash(request, str(exc), "error")
        return redirect("/teacher/make")
    await DbUsage().record(f"matrix:{user.id}", 1)
    now = await db_now(session)
    starts = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    minutes = found.minutes or _suggested_minutes(found.rows)
    values = {
        "title": found.title or f"Kiểm tra {root.name}",
        "starts_at": starts,
        "ends_at": starts + timedelta(minutes=max(60, minutes + 15)),
        "minutes": minutes,
        "same_paper": False,
        "shuffle": True,
        "public": False,
    }
    return await _preview(request, session, user, root, found.rows, values)


def _rows_from(form) -> list[MatrixRow]:
    rows = []
    for i in range(_int(form.get("row_count"))):
        if form.get(f"drop_{i}") == "1":
            continue
        level, kind = str(form.get(f"level_{i}") or ""), str(form.get(f"type_{i}") or "")
        if level not in LEVEL_VALUES or kind not in TYPE_VALUES:
            continue
        rows.append(
            MatrixRow(
                topic=str(form.get(f"topic_{i}") or "")[:300],
                scope_key=str(form.get(f"scope_{i}") or ""),
                level=level,
                question_type=kind,
                count=_int(form.get(f"count_{i}")),
            )
        )
    return rows


def _values(form) -> dict[str, Any]:
    return {
        "title": str(form.get("title") or ""),
        "starts_at": _local(form.get("starts_at")),
        "ends_at": _local(form.get("ends_at")),
        "minutes": _int(form.get("minutes")),
        "same_paper": form.get("same_paper") == "1",
        "shuffle": form.get("shuffle") == "1",
        "public": form.get("publish") == "public",
    }


@router.post("/teacher/make/matrix/create")
async def create_matrix_test(request: Request, user: TeacherUser, session: SessionDep):
    form = await request.form()
    root = await _subject(session, form.get("root_id"))
    rows, values = _rows_from(form), _values(form)
    allowed = {o.key for o in scope_options([root])}
    rows = [r if r.scope_key in allowed else MatrixRow(r.topic, "", r.level, r.question_type, r.count) for r in rows]
    spec = MatrixSpec(
        title=values["title"],
        root=root,
        rules=[r.rule() for r in rows],
        starts_at=values["starts_at"],
        ends_at=values["ends_at"],
        minutes=values["minutes"],
        same_paper=values["same_paper"],
        shuffle=values["shuffle"],
        public=values["public"],
    )
    if spec.public and user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Chỉ quản trị viên đăng đề công khai lên web.")
    try:
        exam = await create_from_matrix(session, user, spec, await db_now(session))
    except ScheduleError as exc:
        return await _preview(request, session, user, root, rows, values, error=str(exc))
    await session.commit()
    if spec.public:
        flash(request, "Đã đăng đề lên web: học sinh vào làm được ngay.")
        return redirect(f"/admin/exams/{exam.id}")
    flash(request, "Đã tạo bài kiểm tra theo ma trận. Gửi link và mã bên dưới cho học sinh.")
    return redirect(f"/teacher/tests/{exam.id}")
