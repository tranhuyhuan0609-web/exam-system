"""Library of books and documents: browse by folder, read PDFs, download exam papers. Who may do what: app/plans.py."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import func, select

from app.attempts import db_now
from app.config import get_settings
from app.deps import SessionDep, require_user
from app.infrastructure.storage import get_bronze_storage
from app.models import LibraryDocument, LibraryKind, LibraryStatus, User, UserRole
from app.plans import can_download, docs_read_today, open_document
from app.templating import templates

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


# The library's sections: what a document is, then its folders (grade, subject, field …) inside.
SECTIONS = {
    "sach": ("📘 Sách", (LibraryKind.BOOK,)),
    "de-thi": ("📝 Đề thi", (LibraryKind.EXAM, LibraryKind.HSG)),
    "chuyen-de": ("📒 Chuyên đề", (LibraryKind.TOPIC,)),
    "bai-bao": ("🔬 Bài báo & nghiên cứu", (LibraryKind.PAPER,)),
    "khac": ("🗂️ Khác", (LibraryKind.OTHER,)),
}


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
    visible, current = _visible(user), [part for part in folder.split("/") if part] if folder else []
    page, section = max(1, page), section if section in SECTIONS else ""
    where = [visible]
    if section:
        where.append(LibraryDocument.kind.in_(SECTIONS[section][1]))
    sections = []
    if not section and not q.strip() and not current:  # the library's front page: its sections
        counts = dict(
            (
                await session.execute(
                    select(LibraryDocument.kind, func.count()).where(visible).group_by(LibraryDocument.kind)
                )
            ).all()
        )
        for slug, (label, kinds) in SECTIONS.items():
            if n := sum(counts.get(kind, 0) for kind in kinds):
                sections.append((slug, label, n))
    query = select(LibraryDocument).where(*where)
    if q.strip():
        needle = q.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        query = query.where(LibraryDocument.title.ilike(f"%{needle}%"))
    else:
        query = query.where(LibraryDocument.folder_path == current)
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
            "section": section,
            "section_label": SECTIONS[section][0] if section else "",
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


@router.get("/library/{document_id:int}/view")
async def view_document(user: LibraryUser, session: SessionDep, document_id: int):
    """Read in the browser (PDF, images). Other formats can only be downloaded, when allowed."""
    doc = await _document(session, user, document_id)
    if doc.mime not in INLINE:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Định dạng này chưa xem trực tuyến được.")
    if reason := await open_document(session, user, doc, await db_now(session)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, reason)
    data = await get_bronze_storage().get(doc.storage_key)
    return Response(
        data,
        media_type=doc.mime,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(_filename(doc))}",
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
    data = await get_bronze_storage().get(doc.storage_key)
    return Response(
        data,
        media_type=doc.mime,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(_filename(doc))}"},
    )
