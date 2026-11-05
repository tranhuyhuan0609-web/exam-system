"""AI import: upload an exam file → background worker → review drafts → publish to the question bank."""

import secrets
from pathlib import PurePath
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

from app import question_rules as rules
from app.ai.normalize import SUPPORTED_EXTENSIONS
from app.ai.pipeline import FLAG_LABELS, normalize_draft, page_key, pages_prefix
from app.config import get_settings
from app.deps import AdminUser, SessionDep
from app.domain.question_hash import hash_of_choices
from app.infrastructure.storage import StorageError, get_storage
from app.models import (
    Difficulty,
    Exam,
    ExamMode,
    ExamQuestion,
    ImportJob,
    JobStatus,
    Question,
    QuestionType,
    SourceType,
)
from app.routers.admin.common import clean_text, get_or_404, redirect
from app.scope import load_categories, resolve_scope, scope_of, scope_options, scope_paths
from app.templating import flash, templates

router = APIRouter()

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
TYPE_LABELS = {t.value: label for t, label in rules.TYPE_LABELS.items()}


def _publishable(draft: dict) -> list[str]:
    """Errors preventing a draft from becoming a bank question (empty list = OK)."""
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


def _drafts(job: ImportJob) -> list[dict]:
    return [normalize_draft(d) for d in (job.drafts or [])]


async def _job_or_404(session, job_id: int) -> ImportJob:
    return await get_or_404(session, ImportJob, job_id)


@router.get("/ai-imports")
async def list_jobs(request: Request, user: AdminUser, session: SessionDep, scope: str = ""):
    jobs = (await session.scalars(select(ImportJob).order_by(ImportJob.id.desc()).limit(50))).all()
    subjects = await load_categories(session)
    return templates.TemplateResponse(
        request,
        "admin/ai_imports.html",
        {
            "user": user,
            "jobs": jobs,
            "scope_options": scope_options(subjects),
            "scope_paths": scope_paths(subjects),
            "scope_of": scope_of,
            "selected_scope": scope,
            "ai_ready": bool(get_settings().gemini_api_key),
            "extensions": ", ".join(SUPPORTED_EXTENSIONS),
            "max_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        },
    )


@router.post("/ai-imports")
async def create_job(
    request: Request,
    user: AdminUser,
    session: SessionDep,
    scope: Annotated[str, Form()] = "",
    default_difficulty: Annotated[str, Form()] = Difficulty.UNDERSTANDING.value,
    file: Annotated[UploadFile | None, File()] = None,
):
    back = "/admin/ai-imports?" + urlencode({"scope": scope})
    target = await resolve_scope(session, scope)
    if target is None:
        flash(request, "Vui lòng chọn môn / chương / bài để lưu câu hỏi.", "error")
        return redirect(back)
    if default_difficulty not in Difficulty.__members__:
        default_difficulty = Difficulty.UNDERSTANDING.value
    name = (file.filename or "") if file else ""
    suffix = PurePath(name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        flash(request, "Hãy chọn file PDF, Word (.docx) hoặc ảnh (PNG/JPG).", "error")
        return redirect(back)
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if not data or len(data) > MAX_UPLOAD_BYTES:
        flash(request, f"File rỗng hoặc lớn hơn {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.", "error")
        return redirect(back)

    relative = f"raw/{secrets.token_hex(12)}{suffix}"
    await get_storage().put(relative, data, file.content_type or "application/octet-stream")
    job = ImportJob(
        created_by=user.id,
        original_filename=name[:300],
        stored_path=relative,
        status=JobStatus.QUEUED,
        stage="Đang chờ worker xử lý…",
        root_id=target.root_id,
        category_id=target.category_id,
        default_difficulty=Difficulty(default_difficulty),
    )
    session.add(job)
    await session.commit()
    flash(request, f"Đã tải lên “{name}”. AI đang xử lý, trang sẽ tự cập nhật.")
    return redirect(f"/admin/ai-imports/{job.id}")


@router.get("/ai-imports/{job_id}")
async def job_detail(request: Request, user: AdminUser, session: SessionDep, job_id: int):
    job = await _job_or_404(session, job_id)
    subjects = await load_categories(session)
    drafts = _drafts(job)
    # Flag drafts that already exist in the bank (exact match on the normalised content hash).
    hashes = [
        hash_of_choices(d.get("type", ""), d.get("content", ""), [{"content": c} for c in d.get("choices", [])])
        for d in drafts
    ]
    existing = set(
        (await session.scalars(select(Question.content_hash).where(Question.content_hash.in_(hashes)))).all()
    )
    for d, digest in zip(drafts, hashes, strict=True):
        if digest in existing and not d.get("question_id") and "duplicate" not in d["flags"]:
            d["flags"] = [*d["flags"], "duplicate"]
    drafts = [
        {
            **d,
            "index": i,
            "problems": _publishable(d),
            "flag_texts": [FLAG_LABELS.get(f, f) for f in d.get("flags", [])],
        }
        for i, d in enumerate(drafts)
    ]
    return templates.TemplateResponse(
        request,
        "admin/ai_import_detail.html",
        {
            "user": user,
            "job": job,
            "drafts": drafts,
            "ready_count": sum(not d["problems"] for d in drafts),
            "published_count": sum(bool(d.get("question_id")) for d in drafts),
            "scope_path": scope_paths(subjects).get(scope_of(job).key, ""),
            "pages": list(range(1, job.page_count + 1)),
            "type_labels": TYPE_LABELS,
            "letters": "ABCDEF",
            "default_title": PurePath(job.original_filename).stem[:300],
        },
    )


@router.get("/ai-imports/{job_id}/status")
async def job_status(session: SessionDep, job_id: int):
    job = await _job_or_404(session, job_id)
    return JSONResponse(
        {
            "status": job.status.value,
            "stage": job.stage,
            "progress_done": job.progress_done,
            "progress_total": job.progress_total,
            "error": job.error,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/ai-imports/{job_id}/pages/{page}.png")
async def job_page(session: SessionDep, job_id: int, page: int):
    """Original page images are admin-only (they may contain unpublished exams)."""
    await _job_or_404(session, job_id)
    try:
        data = await get_storage().get(page_key(job_id, page))
    except StorageError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không có trang này.") from exc
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.post("/ai-imports/{job_id}/drafts/{index}")
async def update_draft(request: Request, session: SessionDep, job_id: int, index: int):
    job = await _job_or_404(session, job_id)
    drafts = _drafts(job)
    if not 0 <= index < len(drafts) or drafts[index].get("question_id"):
        flash(request, "Không sửa được câu này.", "error")
        return redirect(f"/admin/ai-imports/{job_id}")
    form = await request.form()
    draft = drafts[index]
    if form.get("type") in TYPE_LABELS:
        draft["type"] = str(form.get("type"))
    draft["content"] = str(form.get("content", "")).strip()
    draft["choices"] = [str(c).strip() for c in form.getlist("choice_content")]
    correct = str(form.get("correct", ""))
    draft["correct"] = int(correct) if correct.isdigit() and int(correct) < len(draft["choices"]) else None
    draft["truths"] = [{"1": True, "0": False}.get(str(v)) for v in form.getlist("choice_truth")]
    draft["answers"] = rules.split_accepted(str(form.get("accepted_answers", "")))
    if draft["type"] == QuestionType.SHORT_ANSWER:
        draft["choices"] = []
    draft["explanation"] = str(form.get("explanation", "")).strip()
    if (difficulty := str(form.get("difficulty", ""))) in Difficulty.__members__:
        draft["difficulty"] = difficulty
    if form.get("remove_figure") == "on":
        draft["figure"] = None
    draft["flags"] = [f for f in draft.get("flags", []) if f not in ("no_match", "no_solution")]  # reviewed by a human
    draft["edited"] = True
    job.drafts = drafts
    await session.commit()
    problems = _publishable(draft)
    flash(request, f"Đã lưu câu {draft['number']}." + (f" Còn lỗi: {' '.join(problems)}" if problems else ""))
    return redirect(f"/admin/ai-imports/{job_id}#draft-{index}")


@router.post("/ai-imports/{job_id}/publish")
async def publish(request: Request, user: AdminUser, session: SessionDep, job_id: int):
    job = await _job_or_404(session, job_id)
    form = await request.form()
    selected = sorted({int(v) for v in form.getlist("selected") if str(v).isdigit()})
    drafts = _drafts(job)
    created: list[Question] = []
    created_at_index: list[int] = []
    for index in selected:
        if index >= len(drafts) or _publishable(drafts[index]):
            continue
        d = drafts[index]
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
            is_verified=True,  # an admin reviewed it on this screen
        )
        question.content_hash = hash_of_choices(d["type"], question.content, question.choices)
        created.append(question)
        created_at_index.append(index)
    if not created:
        flash(request, "Không có câu hợp lệ nào được chọn.", "error")
        return redirect(f"/admin/ai-imports/{job_id}")

    session.add_all(created)
    await session.flush()
    for index, question in zip(created_at_index, created, strict=True):
        drafts[index]["question_id"] = question.id
    job.drafts = drafts

    exam = None
    if form.get("create_exam") == "on":
        title = clean_text(str(form.get("exam_title", "")), 300) or PurePath(job.original_filename).stem[:300]
        minutes = str(form.get("duration_minutes", "")).strip()
        exam = Exam(
            root_id=job.root_id,
            category_id=job.category_id,
            title=title,
            exam_mode=ExamMode.PRACTICE,
            duration_seconds=(int(minutes) if minutes.isdigit() and 1 <= int(minutes) <= 600 else 15) * 60,
            is_published=form.get("publish_exam") == "on",
            created_by=user.id,
        )
        session.add(exam)
        await session.flush()
        session.add_all(
            ExamQuestion(exam_id=exam.id, question_id=q.id, position=pos) for pos, q in enumerate(created, start=1)
        )
    await session.commit()
    if exam:
        flash(request, f"Đã đưa {len(created)} câu vào ngân hàng và tạo đề “{exam.title}”.")
        return redirect(f"/admin/exams/{exam.id}")
    flash(request, f"Đã đưa {len(created)} câu vào ngân hàng câu hỏi.")
    return redirect(f"/admin/ai-imports/{job_id}")


@router.post("/ai-imports/{job_id}/retry")
async def retry(request: Request, session: SessionDep, job_id: int):
    job = await _job_or_404(session, job_id)
    if job.status != JobStatus.FAILED:
        flash(request, "Chỉ chạy lại được job bị lỗi.", "error")
    else:
        job.status, job.error, job.stage = JobStatus.QUEUED, None, "Đang chờ worker xử lý…"
        job.progress_done = job.progress_total = 0
        await session.commit()
        flash(request, "Đã xếp hàng chạy lại.")
    return redirect(f"/admin/ai-imports/{job_id}")


@router.post("/ai-imports/{job_id}/delete")
async def delete_job(request: Request, session: SessionDep, job_id: int):
    job = await _job_or_404(session, job_id)
    if job.status == JobStatus.PROCESSING:
        flash(request, "Job đang được xử lý, hãy đợi xong rồi xoá.", "error")
        return redirect(f"/admin/ai-imports/{job_id}")
    storage = get_storage()
    for d in job.drafts or []:  # figures of published questions stay; they are now used by the bank
        if d.get("figure") and not d.get("question_id"):
            await storage.delete(d["figure"])
    await storage.delete_prefix(pages_prefix(job_id))
    await storage.delete(job.stored_path)
    await session.delete(job)
    await session.commit()
    flash(request, f"Đã xoá job “{job.original_filename}”.")
    return redirect("/admin/ai-imports")
