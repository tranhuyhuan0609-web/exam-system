"""Exam maker for teachers: a test from an uploaded matrix (ma trận đề). Every teacher may use it; free accounts a
few matrices a day (each one is read by an AI model)."""

import secrets
from datetime import timedelta
from pathlib import PurePath
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select
from starlette.datastructures import UploadFile

from app import question_rules as rules
from app.ai.normalize import SUPPORTED_EXTENSIONS
from app.application.custom_quiz import SECONDS_PER_QUESTION
from app.application.matrix_import import LEVEL_VALUES, TYPE_VALUES, MatrixError, MatrixRow, read_matrix
from app.application.paper import MatrixRule, availability
from app.application.similar import MAX_VERSIONS, problems
from app.application.teacher_tests import MatrixSpec, ScheduleError, create_fixed_test, create_from_matrix
from app.attempts import db_now
from app.deps import SessionDep
from app.domain.question_hash import hash_of_choices
from app.infrastructure.storage import get_storage
from app.models import (
    Category,
    Difficulty,
    Exam,
    ImportJob,
    JobStatus,
    Question,
    QuestionType,
    SourceType,
    UserRole,
)
from app.pipeline.adapters.ai_chain import DbUsage
from app.plans import FREE_MATRICES_PER_DAY
from app.routers.admin.common import redirect
from app.routers.teacher import TeacherUser, _int, _local
from app.scope import load_categories, scope_options
from app.templating import DISPLAY_TZ, flash, templates

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
        {
            "user": user,
            "roots": await _roots(session),
            "reads_left": await _reads_left(session, user),
            "similar_left": await _similar_left(session, user),
            "jobs": (
                await session.scalars(
                    select(ImportJob)
                    .where(ImportJob.created_by == user.id, ImportJob.purpose == "SIMILAR")
                    .order_by(ImportJob.id.desc())
                    .limit(20)
                )
            ).all(),
            "max_versions": MAX_VERSIONS,
        },
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


# ---------------------------------------------------------------- similar papers from a teacher's paper

FREE_SIMILAR_PER_DAY = 3  # papers a free teacher may have rewritten each day


async def _similar_left(session, user) -> int | None:
    now = await db_now(session)
    if user.has_pro(now):
        return None
    start = now.astimezone(DISPLAY_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    used = await session.scalar(
        select(func.count())
        .select_from(ImportJob)
        .where(ImportJob.created_by == user.id, ImportJob.purpose == "SIMILAR", ImportJob.created_at >= start)
    )
    return max(0, FREE_SIMILAR_PER_DAY - used)


async def _own_job(session, user, job_id: int) -> ImportJob:
    job = await session.get(ImportJob, job_id)
    if job is None or job.purpose != "SIMILAR" or (job.created_by != user.id and user.role != UserRole.ADMIN):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy đề.")
    return job


@router.post("/teacher/make/similar")
async def upload_paper(request: Request, user: TeacherUser, session: SessionDep):
    form = await request.form()
    root = await _subject(session, form.get("root_id"))
    upload = form.get("file")
    name = upload.filename if isinstance(upload, UploadFile) else ""
    suffix = PurePath(name or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        flash(request, "Hãy chọn file đề PDF, Word (.docx) hoặc ảnh (PNG/JPG).", "error")
        return redirect("/teacher/make")
    if await _similar_left(session, user) == 0:
        flash(request, f"Tài khoản miễn phí tạo đề tương tự từ {FREE_SIMILAR_PER_DAY} file mỗi ngày.", "error")
        return redirect("/teacher/make")
    data = await upload.read(MAX_UPLOAD + 1)
    if not data or len(data) > MAX_UPLOAD:
        flash(request, "File rỗng hoặc lớn hơn 15 MB.", "error")
        return redirect("/teacher/make")
    relative = f"raw/{secrets.token_hex(12)}{suffix}"
    await get_storage().put(relative, data, upload.content_type or "application/octet-stream")
    job = ImportJob(
        created_by=user.id,
        original_filename=name[:300],
        stored_path=relative,
        status=JobStatus.QUEUED,
        stage="Đang chờ AI đọc đề…",
        root_id=root.id,
        category_id=root.id,
        default_difficulty=Difficulty.UNDERSTANDING,
        purpose="SIMILAR",
        versions=max(1, min(_int(form.get("versions"), 1), MAX_VERSIONS)),
    )
    session.add(job)
    await session.commit()
    flash(request, f"Đã tải lên “{name}”. AI đang đọc đề và viết đề tương tự, trang sẽ tự cập nhật.")
    return redirect(f"/teacher/make/similar/{job.id}")


@router.get("/teacher/make/similar/{job_id:int}")
async def similar_review(request: Request, user: TeacherUser, session: SessionDep, job_id: int):
    job = await _own_job(session, user, job_id)
    root = (await load_categories(session, job.root_id))[0]
    now = await db_now(session)
    starts = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    values = {
        "title": PurePath(job.original_filename).stem[:200],
        "starts_at": starts,
        "ends_at": starts + timedelta(hours=1),
        "minutes": 45,
        "shuffle": True,
        "public": False,
    }
    return _review_page(request, user, job, root, values)


def _review_page(request, user, job, root, values, error=None):
    versions = sorted({d["version"] for d in job.variants})
    return templates.TemplateResponse(
        request,
        "teacher/similar_review.html",
        {
            "user": user,
            "job": job,
            "root": root,
            "variants": list(enumerate(job.variants)),
            "versions": versions,
            "values": values,
            "error": error,
        },
        status_code=status.HTTP_400_BAD_REQUEST if error else status.HTTP_200_OK,
    )


def _edited(form, k: int, draft: dict) -> dict:
    """The teacher's edits of one new question."""
    d = dict(draft)
    d["content"] = str(form.get(f"q{k}_content") or "").strip()
    d["explanation"] = str(form.get(f"q{k}_explanation") or "").strip()
    if d["type"] in ("MULTIPLE_CHOICE", "TRUE_FALSE"):
        d["choices"] = [str(form.get(f"q{k}_choice{j}") or "").strip() for j in range(len(draft.get("choices") or []))]
    if d["type"] == "MULTIPLE_CHOICE":
        d["correct"] = _int(form.get(f"q{k}_correct"), -1)
        d["correct"] = d["correct"] if 0 <= d["correct"] < len(d["choices"]) else None
    if d["type"] == "TRUE_FALSE":
        d["truths"] = [form.get(f"q{k}_truth{j}") == "1" for j in range(len(d["choices"]))]
    if d["type"] == "SHORT_ANSWER":
        d["answers"] = [a for a in [str(form.get(f"q{k}_answer") or "").strip()] if a]
    return d


def _question(job: ImportJob, d: dict) -> Question:
    q = Question(
        root_id=job.root_id,
        category_id=job.category_id,
        difficulty=Difficulty(d.get("difficulty") or job.default_difficulty.value),
        question_type=QuestionType(d["type"]),
        content=d["content"],
        choices=rules.build_choices(d["type"], d["choices"], d.get("correct"), d.get("truths")),
        accepted_answers=d["answers"] if d["type"] == "SHORT_ANSWER" else None,
        explanation=d.get("explanation") or None,
        image_path=d.get("figure"),
        source_type=SourceType.AI_UPLOAD,
        is_verified=False,  # the teacher's own: never drawn into anyone else's papers
    )
    q.content_hash = hash_of_choices(d["type"], q.content, q.choices)
    return q


@router.post("/teacher/make/similar/{job_id:int}/create")
async def similar_create(request: Request, user: TeacherUser, session: SessionDep, job_id: int):
    job = await _own_job(session, user, job_id)
    if job.status != JobStatus.DONE:
        raise HTTPException(status.HTTP_409_CONFLICT, "AI chưa viết xong đề.")
    form = await request.form()
    root = (await load_categories(session, job.root_id))[0]
    values = _values(form)
    if values["public"] and user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Chỉ quản trị viên đăng đề công khai lên web.")
    chosen = {_int(v) for v in form.getlist("version")}
    drafts = [_edited(form, k, d) for k, d in enumerate(job.variants)]
    kept = [d for k, d in enumerate(drafts) if form.get(f"q{k}_drop") != "1" and d["version"] in chosen]
    errors = [f"Đề {d['version']}, câu gốc {d.get('of')}: {'; '.join(p)}" for d in kept if (p := problems(d))]
    job.variants = drafts  # the edits are kept, also when something is still wrong
    if not chosen:
        errors.append("Hãy chọn ít nhất một đề để giao.")
    if errors:
        await session.commit()
        return _review_page(request, user, job, root, values, error=" ".join(errors[:8]))
    created = []
    now = await db_now(session)
    try:
        for version in sorted(chosen):
            questions = [_question(job, d) for d in kept if d["version"] == version]
            session.add_all(questions)
            await session.flush()
            title = values["title"] if len(chosen) == 1 else f"{values['title']} – Đề {version}"
            spec = MatrixSpec(
                title=title[:300],
                root=root,
                rules=[],
                starts_at=values["starts_at"],
                ends_at=values["ends_at"],
                minutes=values["minutes"],
                shuffle=values["shuffle"],
                public=values["public"],
            )
            created.append(await create_fixed_test(session, user, spec, [q.id for q in questions], now))
    except ScheduleError as exc:
        await session.rollback()
        job = await _own_job(session, user, job_id)
        return _review_page(request, user, job, root, values, error=str(exc))
    await session.commit()
    if values["public"]:
        flash(request, f"Đã đăng {len(created)} đề lên web.")
        return redirect(f"/admin/exams/{created[0].id}")
    flash(request, f"Đã tạo {len(created)} bài kiểm tra. Gửi link và mã của từng bài cho học sinh.")
    return redirect("/teacher" if len(created) > 1 else f"/teacher/tests/{created[0].id}")
