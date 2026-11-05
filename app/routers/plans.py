"""Plans page: what free and Pro accounts may do, today's usage, and the request to upgrade (an admin approves)."""

from fastapi import APIRouter, Request

from app.attempts import db_now
from app.config import get_settings
from app.deps import LoggedInUser, SessionDep
from app.models import UserRole
from app.plans import docs_read_today, exams_used_today
from app.routers.admin.common import redirect
from app.templating import flash, templates

router = APIRouter()


@router.get("/plans")
async def plans_page(request: Request, user: LoggedInUser, session: SessionDep):
    now = await db_now(session)
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "plans.html",
        {
            "user": user,
            "pro": user.has_pro(now),
            "exams_used": await exams_used_today(session, user, now),
            "docs_read": await docs_read_today(session, user, now),
            "exam_limit": settings.free_exams_per_day,
            "doc_limit": settings.free_docs_per_day,
        },
    )


@router.post("/plans/request")
async def request_pro(request: Request, user: LoggedInUser, session: SessionDep):
    if user.role == UserRole.ADMIN:
        return redirect("/plans")
    if user.pro_requested_at is None:
        user.pro_requested_at = await db_now(session)
        await session.commit()
    flash(request, "Đã gửi yêu cầu nâng cấp Pro. Quản trị viên sẽ duyệt sớm.")
    return redirect("/plans")
