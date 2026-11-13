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
from app.ai.pipeline import FLAG_LABELS, page_key, pages_prefix
from app.application.ai_publish import create_exam, drafts_of, publish_drafts, publish_problems
from app.application.library_publish import ensure_job_library_document, publish_to_library
from app.config import get_settings
from app.deps import AdminUser, SessionDep
from app.domain.question_hash import hash_of_choices
from app.infrastructure.storage import StorageError, get_bronze_storage, get_storage
from app.models import (
    AccessTier,
    Category,
    Difficulty,
    ExamOriginType,
    ImportJob,
    JobStatus,
    KnowledgeArticle,
    KnowledgeUnit,
    Question,
    QuestionType,
)
from app.routers.admin.common import clean_text, get_or_404, redirect
from app.scope import load_categories, resolve_scope, scope_of, scope_options, scope_paths
from app.templating import flash, templates

router = APIRouter()

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
TYPE_LABELS = {t.value: label for t, label in rules.TYPE_LABELS.items()}


_publishable = publish_problems
_drafts = drafts_of


async def _job_or_404(session, job_id: int) -> ImportJob:
    return await get_or_404(session, ImportJob, job_id)


@router.get("/ai-imports")
async def list_jobs(request: Request, user: AdminUser, session: SessionDep, scope: str = ""):
    jobs = (
        await session.scalars(
            select(ImportJob)
            .order_by(
                (ImportJob.status == JobStatus.PROCESSING).desc(),
                (ImportJob.status == JobStatus.DONE).desc(),
                ImportJob.finished_at.desc().nulls_last(),
                ImportJob.id.desc(),
            )
            .limit(100)
        )
    ).all()
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
    is_pro: Annotated[str, Form()] = "",
    file: Annotated[UploadFile | None, File()] = None,
    solution_file: Annotated[UploadFile | None, File()] = None,
):
    back = "/admin/ai-imports?" + urlencode({"scope": scope})
    target = await resolve_scope(session, scope)
    if target is None:
        flash(request, "Vui lòng chọn môn / chương / bài để lưu câu hỏi.", "error")
        return redirect(back)
    if default_difficulty not in Difficulty.__members__:
        default_difficulty = Difficulty.UNDERSTANDING.value
    if target.root_id == 111 or "/111/" in target.path:
        if default_difficulty in (Difficulty.UNDERSTANDING.value, Difficulty.RECOGNITION.value, Difficulty.HSG.value):
            default_difficulty = Difficulty.ADVANCED_APPLICATION.value
    name = (file.filename or "") if file else ""
    suffix = PurePath(name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        flash(request, "Hãy chọn file PDF, Word (.docx, .doc), file nén (.zip, .rar) hoặc ảnh (PNG/JPG).", "error")
        return redirect(back)
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if not data or len(data) > MAX_UPLOAD_BYTES:
        flash(request, f"File rỗng hoặc lớn hơn {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.", "error")
        return redirect(back)

    # Xử lý file đáp án đi kèm nếu có
    sol_relative: str | None = None
    if solution_file and solution_file.filename:
        sol_name = solution_file.filename
        sol_suffix = PurePath(sol_name).suffix.lower()
        if sol_suffix in SUPPORTED_EXTENSIONS:
            sol_data = await solution_file.read(MAX_UPLOAD_BYTES + 1)
            if sol_data and len(sol_data) <= MAX_UPLOAD_BYTES:
                sol_token = secrets.token_hex(12)
                sol_relative = f"raw/{sol_token}{sol_suffix}"
                sol_mime = solution_file.content_type or "application/octet-stream"
                await get_storage().put(sol_relative, sol_data, sol_mime)

    category = await session.get(Category, target.category_id)
    pro_selected = is_pro.lower() in ("on", "1", "true") or (
        category is not None and category.default_tier == AccessTier.PRO
    )
    access_tier = AccessTier.PRO if pro_selected else AccessTier.FREE
    origin_type = ExamOriginType.ORIGINAL if is_pro.lower() in ("on", "1", "true") else ExamOriginType.PUBLIC_CRAWLED

    token = secrets.token_hex(12)
    relative = f"raw/{token}{suffix}"
    content_type = file.content_type or "application/octet-stream"
    await get_storage().put(relative, data, content_type)
    if access_tier == AccessTier.PRO:
        # Also archive the original file in the private PRO vault (`bronze-raw`)
        await get_bronze_storage().put(f"authored-exams/{token}{suffix}", data, content_type)
    job = ImportJob(
        created_by=user.id,
        original_filename=name[:300],
        stored_path=relative,
        solution_stored_path=sol_relative,
        status=JobStatus.QUEUED,
        stage="Đang chờ worker xử lý…",
        root_id=target.root_id,
        category_id=target.category_id,
        default_difficulty=Difficulty(default_difficulty),
        access_tier=access_tier,
        origin_type=origin_type,
    )
    session.add(job)
    await session.commit()
    msg = f"Đã tải lên “{name}”" + (f" kèm đáp án “{solution_file.filename}”." if sol_relative else ".")
    flash(request, f"{msg} Hệ thống đang bóc tách tự động, trang sẽ tự cập nhật.")
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
    draft["flags"] = [
        f for f in draft.get("flags", []) if f not in ("no_match", "no_solution", "missing_stem", "figure_failed")
    ]  # reviewed by a human
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
    if form.get("is_pro") == "on":
        job.access_tier = AccessTier.PRO
        job.origin_type = ExamOriginType.ORIGINAL
    selected = sorted({int(v) for v in form.getlist("selected") if str(v).isdigit()})
    created = await publish_drafts(session, job, selected, verified=True)  # an admin reviewed it on this screen
    if not created:
        flash(request, "Không có câu hợp lệ nào được chọn.", "error")
        return redirect(f"/admin/ai-imports/{job_id}")

    title = clean_text(str(form.get("exam_title", "")), 300) or PurePath(job.original_filename).stem[:300]

    # The two checkboxes of the publish form decide: create an exam from the questions, and make it public
    create_exam_flag = form.get("create_exam") == "on"  # an unticked checkbox is simply absent from the form
    exam = None
    if create_exam_flag:
        minutes = str(form.get("duration_minutes", "")).strip()
        exam_minutes = int(minutes) if minutes.isdigit() and 1 <= int(minutes) <= 600 else max(15, len(created) * 3)
        exam = await create_exam(
            session,
            job,
            created,
            title=title,
            minutes=exam_minutes,
            published=form.get("publish_exam") == "on",
            user_id=user.id,
            access_tier=job.access_tier,
            origin_type=job.origin_type,
        )

    doc = await ensure_job_library_document(session, job, title=title, exam_id=exam.id if exam else None)
    if exam:
        await publish_to_library(session, job, exam)  # typeset clean PDFs without headers/logos

    # If this import job contains structured VietJack-style Study Guide / Theory data:
    if job.knowledge_article_data:
        ku = await session.scalar(select(KnowledgeUnit).where(KnowledgeUnit.code == "MATH12-KNTT-C6-B2-D2"))
        if ku:
            for q in created:
                q.knowledge_unit_id = ku.id

            article = await session.scalar(select(KnowledgeArticle).where(KnowledgeArticle.knowledge_unit_id == ku.id))
            if not article:
                article = KnowledgeArticle(knowledge_unit_id=ku.id, title=job.knowledge_article_data.get("title", title))  # noqa: E501
                session.add(article)
            article.title = job.knowledge_article_data.get("title", title)
            article.method_guide_md = job.knowledge_article_data.get("method_guide_md")
            article.summary_formulas_md = job.knowledge_article_data.get("summary_formulas_md")
            article.notes_and_tips_md = job.knowledge_article_data.get("notes_and_tips_md")
            article.worked_examples_json = job.knowledge_article_data.get("worked_examples_json", [])
            article.branded_pdf_url = job.knowledge_article_data.get("branded_pdf_url")

    await session.commit()
    if job.knowledge_article_data:
        flash(request, f"🚀 Đã xuất bản thành công vào Ngân hàng Tài liệu & Ngân hàng Câu hỏi ({len(created)} câu)! Học sinh có thể làm bài tương tác trực tiếp tại: /tai-lieu/toan-12/xac-suat-co-dieu-kien/dang-2-cong-thuc-bayes")  # noqa: E501
        return redirect(f"/admin/ai-imports/{job_id}")
    if exam:
        folder_display = " › ".join(doc.folder_path) if doc and doc.folder_path else "Thư viện"
        flash(request, f"Đã đưa {len(created)} câu vào ngân hàng, xuất đề “{exam.title}” và lưu vào mục: {folder_display}.")  # noqa: E501
        return redirect(f"/admin/exams/{exam.id}")
    flash(request, f"Đã đưa {len(created)} câu vào ngân hàng câu hỏi và lưu đề vào Thư viện tài liệu.")
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
