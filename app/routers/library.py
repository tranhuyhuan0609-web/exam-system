"""Library of books and documents: browse by folder, read PDFs, download exam papers. Who may do what: app/plans.py."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy import func, select

from app.application.library_publish import create_exam_from_library_doc
from app.application.pdf_preview import PREVIEW_MAX_PAGES, ensure_document_preview, stamp_user_watermark
from app.attempts import db_now
from app.config import get_settings
from app.deps import CurrentUser, SessionDep, require_user
from app.infrastructure.storage import get_bronze_storage, get_storage
from app.models import (
    AccessTier,
    KnowledgeArticle,
    KnowledgeUnit,
    LibraryDocument,
    LibraryKind,
    LibraryStatus,
    Question,
    QuestionType,
    User,
    UserRole,
)
from app.plans import can_download, docs_read_today, open_document
from app.templating import flash, templates

router = APIRouter()

KIND_LABELS = {
    LibraryKind.BOOK: "Sách tham khảo",
    LibraryKind.TOPIC: "Chuyên đề / tài liệu",
    LibraryKind.EXAM: "Đề thi",
    LibraryKind.HSG: "Học sinh giỏi",
    LibraryKind.PAPER: "Bài báo / nghiên cứu",
    LibraryKind.OTHER: "Khác",
}
INLINE = {"application/pdf", "image/png", "image/jpeg"}

PAGE_SIZE = 100
LibraryUser = Annotated[User, Depends(require_user)]  # any account; reading limits for free ones


# Top-level Library sections and their display icons
SECTION_ICONS = {
    "THPTQG": "🎓",
    "Học sinh giỏi": "🏆",
    "ĐGTD & ĐGNL": "⚡",
    "Đề thi Học kỳ": "📑",
    "Sách & Chuyên đề": "📘",
}
SECTION_ORDER = ["THPTQG", "Học sinh giỏi", "ĐGTD & ĐGNL", "Đề thi Học kỳ", "Sách & Chuyên đề"]


def _visible(user: User):
    if user.role == UserRole.ADMIN:  # admins also see documents waiting for review, and works without a file yet
        return LibraryDocument.status != LibraryStatus.HIDDEN
    # Read here, in our reader: a work whose file we do not hold is not shown (no links out to other sites)
    return (LibraryDocument.status == LibraryStatus.PUBLISHED) & (LibraryDocument.storage_key != "")


@router.get("/library")
async def library_home(
    request: Request,
    user: LibraryUser,
    session: SessionDep,
    section: str = "",
    folder: str = "",
    q: str = "",
    page: int = 1,
):
    """Sections first, then one folder (or the search results) at a time, in pages."""
    visible = _visible(user)
    current = [part for part in folder.split("/") if part] if folder else []
    page = max(1, page)
    where = [visible]
    
    sections = []
    if not q.strip() and not current:  # Front page: show main exam sections (THPTQG, HSG, ĐGTD...)
        top_name = LibraryDocument.folder_path[0].astext
        top_counts = dict(
            (
                await session.execute(
                    select(top_name, func.count())
                    .where(visible, func.jsonb_array_length(LibraryDocument.folder_path) > 0)
                    .group_by(top_name)
                )
            ).all()
        )
        for name in SECTION_ORDER:
            icon = SECTION_ICONS.get(name, "📁")
            n = top_counts.get(name, 0)
            sections.append((name, f"{icon} {name}", n))
        for name, n in top_counts.items():
            if name not in SECTION_ORDER and n > 0:
                sections.append((name, f"📁 {name}", n))

    query = select(LibraryDocument).where(*where)
    if q.strip():
        needle = q.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        query = query.where(LibraryDocument.title.ilike(f"%{needle}%"))
    elif current:
        depth = len(current)
        inside = [LibraryDocument.folder_path[i].astext == part for i, part in enumerate(current)]
        query = query.where(*inside)
    else:
        query = query.where(LibraryDocument.folder_path == [])

    shown = []
    if not sections:
        shown = (
            await session.scalars(
                query.order_by(LibraryDocument.title).limit(PAGE_SIZE + 1).offset((page - 1) * PAGE_SIZE)
            )
        ).all()
    more, shown = len(shown) > PAGE_SIZE, shown[:PAGE_SIZE]
    subfolders = []
    if not sections:
        depth = len(current)
        name = LibraryDocument.folder_path[depth].astext
        inside = [LibraryDocument.folder_path[i].astext == part for i, part in enumerate(current)]
        subfolders = (
            await session.execute(
                select(name, func.count())
                .where(*where, func.jsonb_array_length(LibraryDocument.folder_path) > depth, *inside)
                .group_by(name)
                .order_by(name)
            )
        ).all()
    pairs = await _pairs(session, user, shown)
    total = await session.scalar(select(func.count()).select_from(LibraryDocument).where(visible))
    now = await db_now(session)
    pro = user.has_pro(now)
    limit = get_settings().free_docs_per_day
    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "user": user,
            "docs": shown,
            "sections": sections,
            "section": "",
            "section_label": "",
            "subfolders": [tuple(row) for row in subfolders],
            "current": current,
            "folder": "/".join(current),
            "q": q,
            "page": page,
            "more": more,
            "KIND_LABELS": KIND_LABELS,
            "total": total,
            "pro": pro,
            "reads_left": None if pro else max(0, limit - await docs_read_today(session, user, now)),
            "read_limit": limit,
            "downloadable": {d.id for d in shown if can_download(user, d, now)},
            "pairs": pairs,
        },
    )


async def _pairs(session, user: User, docs) -> dict[int, tuple[str, int]]:
    """A paper and its answer copy point at each other (same exam): doc id -> (label of the other, its id)."""
    exam_ids = {d.exam_id for d in docs if d.exam_id}
    if not exam_ids:
        return {}
    copies = (
        await session.scalars(select(LibraryDocument).where(LibraryDocument.exam_id.in_(exam_ids), _visible(user)))
    ).all()
    by_exam: dict[int, dict[str, LibraryDocument]] = {}
    for copy in copies:
        side = "answers" if (copy.source_file_id or "").endswith(":loi-giai") else "paper"
        by_exam.setdefault(copy.exam_id, {})[side] = copy
    pairs = {}
    for both in by_exam.values():
        if "paper" in both and "answers" in both:
            pairs[both["paper"].id] = ("📝 Đáp án", both["answers"].id)
            pairs[both["answers"].id] = ("📄 Đề", both["paper"].id)
    return pairs


async def _document(session, user: User, document_id: int) -> LibraryDocument:
    doc = await session.get(LibraryDocument, document_id)
    if doc is None or doc.status == LibraryStatus.HIDDEN or not doc.storage_key:  # no key: listed only
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy tài liệu.")
    if doc.status != LibraryStatus.PUBLISHED and user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy tài liệu.")
    return doc


def _filename(doc: LibraryDocument) -> str:
    ext = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}.get(doc.mime, "")
    if not ext and "." in doc.storage_key:
        ext = "." + doc.storage_key.rsplit(".", 1)[1]
    return doc.title + ext


@router.get("/library/{document_id:int}/preview")
async def preview_document_page(
    request: Request, user: LibraryUser, session: SessionDep, document_id: int
):
    """Freemium HTML reader page: shows the 2-page preview PDF inline for Free users with a Pro upgrade CTA."""
    doc = await _document(session, user, document_id)
    now = await db_now(session)
    pro = user.has_pro(now)
    is_pro_doc = doc.access_tier == AccessTier.PRO or doc.kind == LibraryKind.HSG
    total_pages = None
    if is_pro_doc and not pro and doc.mime == "application/pdf":
        sliced = await ensure_document_preview(session, doc, get_bronze_storage(), get_storage())
        if sliced:
            _, total_pages = sliced
    return templates.TemplateResponse(
        request,
        "library_preview.html",
        {
            "user": user,
            "doc": doc,
            "pro": pro,
            "is_pro_doc": is_pro_doc,
            "preview_pages": PREVIEW_MAX_PAGES,
            "total_pages": total_pages,
            "can_download": can_download(user, doc, now),
        },
    )


@router.get("/library/{document_id:int}/preview.pdf")
async def preview_document_pdf(user: LibraryUser, session: SessionDep, document_id: int):
    """Publicly accessible 2-page watermarked preview PDF for PRO / HSG documents."""
    doc = await _document(session, user, document_id)
    if doc.mime != "application/pdf":
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Tài liệu không phải định dạng PDF.")
    sliced = await ensure_document_preview(session, doc, get_bronze_storage(), get_storage())
    if sliced is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tạo được bản xem thử.")
    preview_bytes, total_pages = sliced
    return Response(
        preview_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote('Xem-thu-' + _filename(doc))}",
            "Cache-Control": "public, max-age=3600",
            "X-Preview-Only": "true",
            "X-Preview-Pages": str(min(PREVIEW_MAX_PAGES, total_pages)),
            "X-Total-Pages": str(total_pages),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/library/{document_id:int}/presigned-url")
async def presigned_document_url(user: LibraryUser, session: SessionDep, document_id: int):
    """Issue a 60-second presigned URL from the private vault (`bronze-raw`) for active PRO subscribers."""
    doc = await _document(session, user, document_id)
    now = await db_now(session)
    is_pro_doc = doc.access_tier == AccessTier.PRO or doc.kind == LibraryKind.HSG
    if is_pro_doc and not user.has_pro(now):
        return JSONResponse(
            {
                "detail": "Tài liệu chuyên sâu thuộc gói PRO. Bạn được xem thử 2 trang đầu miễn phí.",
                "access_tier": "PRO",
                "preview_url": f"/library/{doc.id}/preview.pdf",
                "upgrade_url": "/plans",
            },
            status_code=status.HTTP_403_FORBIDDEN,
        )
    bronze = get_bronze_storage()
    signed = bronze.presigned_url(doc.storage_key, expires_seconds=60)
    return JSONResponse(
        {
            "url": signed or f"/library/{doc.id}/view",
            "expires_in": 60,
            "access_tier": doc.access_tier.value,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/library/{document_id:int}/view")
async def view_document(user: LibraryUser, session: SessionDep, document_id: int):
    """Read in the browser (PDF, images). For PRO documents, Free accounts receive only the 2-page preview PDF."""
    doc = await _document(session, user, document_id)
    if doc.mime not in INLINE:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Định dạng này chưa xem trực tuyến được.")
    now = await db_now(session)
    is_pro_doc = doc.access_tier == AccessTier.PRO or doc.kind == LibraryKind.HSG
    if is_pro_doc and not user.has_pro(now):
        if doc.mime == "application/pdf":
            sliced = await ensure_document_preview(session, doc, get_bronze_storage(), get_storage())
            if sliced is not None:
                preview_bytes, total_pages = sliced
                return Response(
                    preview_bytes,
                    media_type="application/pdf",
                    headers={
                        "Content-Disposition": f"inline; filename*=UTF-8''{quote('Xem-thu-' + _filename(doc))}",
                        "Cache-Control": "private, no-store",
                        "X-Preview-Only": "true",
                        "X-Preview-Pages": str(min(PREVIEW_MAX_PAGES, total_pages)),
                        "X-Total-Pages": str(total_pages),
                        "X-Content-Type-Options": "nosniff",
                    },
                )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Tài liệu chuyên sâu này thuộc gói PRO. Hãy nâng cấp tài khoản PRO để xem toàn bộ nội dung.",
        )
    if reason := await open_document(session, user, doc, now):
        raise HTTPException(status.HTTP_403_FORBIDDEN, reason)
    storage = get_bronze_storage()
    filename_encoded = quote(_filename(doc))

    # Watermarked PRO PDFs need processing; all other documents stream directly to conserve RAM
    if is_pro_doc and doc.mime == "application/pdf":
        data = await storage.get(doc.storage_key)
        data = stamp_user_watermark(data, f"PRO: {user.email}")
        return Response(
            data,
            media_type=doc.mime,
            headers={
                "Content-Disposition": f"inline; filename*=UTF-8''{filename_encoded}",
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return StreamingResponse(
        storage.iter_stream(doc.storage_key),
        media_type=doc.mime,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{filename_encoded}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/library/{document_id:int}/download")
async def download_document(user: LibraryUser, session: SessionDep, document_id: int):
    doc = await _document(session, user, document_id)
    if not can_download(user, doc, await db_now(session)):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Chỉ giáo viên gói Pro được tải đề thi; sách và tài liệu chỉ xem trực tuyến."
        )
    storage = get_bronze_storage()
    filename_encoded = quote(_filename(doc))
    return StreamingResponse(
        storage.iter_stream(doc.storage_key),
        media_type=doc.mime,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename_encoded}"},
    )


@router.post("/library/{document_id:int}/create-exam")
@router.get("/library/{document_id:int}/create-exam")
async def create_exam_from_document_route(
    request: Request, user: LibraryUser, session: SessionDep, document_id: int
):
    """Student creates / opens a practice exam directly from a document in the Library (/library)."""
    doc = await _document(session, user, document_id)
    now = await db_now(session)
    is_pro_doc = doc.access_tier == AccessTier.PRO or doc.kind == LibraryKind.HSG
    if is_pro_doc and not user.has_pro(now):
        flash(request, "Tài liệu chuyên sâu này thuộc gói PRO. Hãy nâng cấp tài khoản PRO để tạo đề luyện tập.", "error")
        return RedirectResponse(f"/library/{doc.id}/preview", status_code=status.HTTP_303_SEE_OTHER)

    exam = await create_exam_from_library_doc(session, doc, user_id=user.id)
    if exam is None:
        flash(request, "Tài liệu này chưa có bộ câu hỏi sẵn sàng để tạo đề luyện tập.", "error")
        return RedirectResponse("/library", status_code=status.HTTP_303_SEE_OTHER)

    await session.commit()
    flash(request, f"Đã mở đề luyện tập “{exam.title}”.")
    return RedirectResponse(f"/exams/{exam.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/tai-lieu/{subject_slug}/{chapter_slug}/{lesson_slug}")
async def view_study_article(
    request: Request,
    session: SessionDep,
    subject_slug: str,
    chapter_slug: str,
    lesson_slug: str,
    user: CurrentUser = None,
):
    """VietJack-style Study Guide & Interactive Practice page for Problem Types / Lessons."""
    ku = await session.scalar(
        select(KnowledgeUnit).where(
            (KnowledgeUnit.slug == lesson_slug) | (KnowledgeUnit.code.ilike(f"%{lesson_slug}%"))
        )
    )
    if not ku and lesson_slug == "dang-2-cong-thuc-bayes":
        ku = await session.scalar(select(KnowledgeUnit).where(KnowledgeUnit.code == "MATH12-KNTT-C6-B2-D2"))

    if not ku:
        raise HTTPException(status_code=404, detail="Bài học hoặc dạng tài liệu không tồn tại.")

    article = await session.scalar(
        select(KnowledgeArticle).where(KnowledgeArticle.knowledge_unit_id == ku.id)
    )

    questions = list((await session.scalars(
        select(Question).where(Question.knowledge_unit_id == ku.id).order_by(Question.id)
    )).all())

    essay_questions = [q for q in questions if q.question_type == QuestionType.ESSAY]
    mcq_questions = [q for q in questions if q.question_type == QuestionType.MULTIPLE_CHOICE]
    tf_questions = [q for q in questions if q.question_type == QuestionType.TRUE_FALSE]
    sa_questions = [q for q in questions if q.question_type == QuestionType.SHORT_ANSWER]

    all_units = list((await session.scalars(
        select(KnowledgeUnit)
        .where(KnowledgeUnit.grade == ku.grade, KnowledgeUnit.subject == ku.subject)
        .order_by(KnowledgeUnit.sort_order, KnowledgeUnit.id)
    )).all())

    chapters = [u for u in all_units if u.unit_type == "CHAPTER"]
    by_parent: dict[int, list[KnowledgeUnit]] = {}
    for u in all_units:
        if u.parent_id:
            by_parent.setdefault(u.parent_id, []).append(u)

    toc_tree = []
    for ch in chapters:
        ch_lessons = by_parent.get(ch.id, [])
        lessons_tree = []
        for les in ch_lessons:
            les_pts = by_parent.get(les.id, [])
            lessons_tree.append({
                "unit": les,
                "problem_types": les_pts,
                "is_active": les.id == ku.id or any(pt.id == ku.id for pt in les_pts),
            })
        toc_tree.append({
            "chapter": ch,
            "lessons": lessons_tree,
            "is_active": ch.id == ku.id or any(l["is_active"] for l in lessons_tree),
        })

    return templates.TemplateResponse(
        request,
        "study_article.html",
        {
            "user": user,
            "unit": ku,
            "article": article,
            "toc_tree": toc_tree,
            "essay_questions": essay_questions,
            "mcq_questions": mcq_questions,
            "tf_questions": tf_questions,
            "sa_questions": sa_questions,
            "total_questions": len(questions),
            "subject_name": f"{'Toán' if ku.subject == 'MATH' else ku.subject} {ku.grade}",
        },
    )
