"""Admin: library documents imported from Google Drive — review rights, publish/hide, allow downloads, sync."""

import subprocess
import sys

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.config import get_settings
from app.deps import AdminUser, SessionDep
from app.infrastructure.storage import get_bronze_storage, get_storage
from app.models import AccessTier, LibraryDocument, LibraryKind, LibraryStatus
from app.pipeline.use_cases.drive_sync import load_sources
from app.pipeline.use_cases.library_split import split_stored
from app.routers.admin.common import redirect
from app.routers.library import KIND_LABELS
from app.templating import flash, templates

router = APIRouter()
STATUS_LABELS = {"PENDING_REVIEW": "Chờ duyệt", "PUBLISHED": "Đã công khai (giáo viên Pro)", "HIDDEN": "Đã ẩn"}


@router.get("/library")
async def library_admin(
    request: Request, user: AdminUser, session: SessionDep, status: str = "PENDING_REVIEW", q: str = ""
):
    stmt = select(LibraryDocument)
    if status in STATUS_LABELS:
        stmt = stmt.where(LibraryDocument.status == LibraryStatus(status))
    if q.strip():
        stmt = stmt.where(LibraryDocument.title.ilike(f"%{q.strip()}%"))
    docs = (await session.scalars(stmt.order_by(LibraryDocument.id.desc()).limit(300))).all()
    counts = dict(
        (await session.execute(select(LibraryDocument.status, func.count()).group_by(LibraryDocument.status))).all()
    )
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "admin/library.html",
        {
            "user": user,
            "docs": docs,
            "status": status,
            "q": q,
            "counts": {s.value: n for s, n in counts.items()},
            "STATUS_LABELS": STATUS_LABELS,
            "KIND_LABELS": KIND_LABELS,
            "kinds": list(LibraryKind),
            "sources": load_sources(settings.drive_configs_dir),
            "has_key": bool(settings.google_drive_api_key),
            "back": f"/admin/library?status={status}",
        },
    )


def _back(form) -> str:
    back = str(form.get("back") or "")
    return back if back.startswith("/admin/library") else "/admin/library"


@router.post("/library/bulk")
async def library_bulk(request: Request, user: AdminUser, session: SessionDep):
    form = await request.form()
    ids = [int(i) for i in form.getlist("ids") if str(i).isdigit()]
    action = str(form.get("action") or "")
    docs = (await session.scalars(select(LibraryDocument).where(LibraryDocument.id.in_(ids)))).all()
    if action == "split":  # cut bundles into one document per exam
        cut = 0
        for doc in docs:
            cut += await split_stored(session, doc, get_bronze_storage(), get_storage(), get_settings().soffice_path)
        flash(request, f"Đã tách được {cut} đề." if cut else "Không tìm thấy nhiều đề trong các tài liệu đã chọn.")
        return redirect(_back(form))
    for doc in docs:
        if action == "publish":
            if not doc.storage_key:  # listed only (copyrighted book): there is no file to publish
                continue
            doc.status = LibraryStatus.PUBLISHED
        elif action == "hide":
            doc.status = LibraryStatus.HIDDEN
        elif action == "allow_download":
            doc.downloadable = True
        elif action == "view_only":
            doc.downloadable = False
        elif action.startswith("kind:") and action[5:] in LibraryKind.__members__:
            doc.kind = LibraryKind(action[5:])
            if doc.kind == LibraryKind.HSG:
                doc.access_tier = AccessTier.PRO
        elif action.startswith("tier:") and action[5:] in AccessTier.__members__:
            doc.access_tier = AccessTier(action[5:])
    await session.commit()
    flash(request, f"Đã cập nhật {len(docs)} tài liệu.")
    return redirect(_back(form))


@router.post("/library/sync/{source_id}")
async def library_sync(request: Request, user: AdminUser, source_id: str):
    sources = load_sources(get_settings().drive_configs_dir)
    if source_id not in sources:
        flash(request, "Không có nguồn này trong configs/drive.", "error")
        return redirect("/admin/library")
    if not get_settings().google_drive_api_key:
        flash(request, "Chưa có GOOGLE_DRIVE_API_KEY trong .env.", "error")
        return redirect("/admin/library")
    # Runs in its own process (long download); results appear on this page as files arrive.
    subprocess.Popen([sys.executable, "-m", "app.pipeline", "drive-sync", source_id])  # noqa: S603 - fixed argv
    flash(request, f"Đang đồng bộ “{sources[source_id].name}” từ Google Drive. Tải lại trang sau ít phút.")
    return redirect("/admin/library")
