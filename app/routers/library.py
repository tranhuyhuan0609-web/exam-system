"""Library of books and documents: browse by folder, read PDFs, download exam papers. Who may do what: app/plans.py."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import select

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
    LibraryKind.OTHER: "Khác",
}
INLINE = {"application/pdf", "image/png", "image/jpeg"}

LibraryUser = Annotated[User, Depends(require_user)]  # any account; reading limits for free ones


def _visible(user: User):
    if user.role == UserRole.ADMIN:  # admins also see documents waiting for review
        return LibraryDocument.status != LibraryStatus.HIDDEN
    return LibraryDocument.status == LibraryStatus.PUBLISHED


@router.get("/library")
async def library_home(request: Request, user: LibraryUser, session: SessionDep, folder: str = "", q: str = ""):
    docs = (await session.scalars(select(LibraryDocument).where(_visible(user)).order_by(LibraryDocument.title))).all()
    current = [part for part in folder.split("/") if part] if folder else []
    if q.strip():
        needle = q.strip().casefold()
        shown = [d for d in docs if needle in d.title.casefold()]
    else:
        shown = [d for d in docs if d.folder_path == current]
    subfolders: dict[str, int] = {}
    for d in docs:
        if d.folder_path[: len(current)] == current and len(d.folder_path) > len(current):
            name = d.folder_path[len(current)]
            subfolders[name] = subfolders.get(name, 0) + 1
    now = await db_now(session)
    pro = user.has_pro(now)
    limit = get_settings().free_docs_per_day
    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "user": user,
            "docs": shown,
            "subfolders": sorted(subfolders.items()),
            "current": current,
            "folder": "/".join(current),
            "q": q,
            "KIND_LABELS": KIND_LABELS,
            "total": len(docs),
            "pro": pro,
            "reads_left": None if pro else max(0, limit - await docs_read_today(session, user, now)),
            "read_limit": limit,
            "downloadable": {d.id for d in shown if can_download(user, d, now)},
        },
    )


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
