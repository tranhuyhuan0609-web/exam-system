"""Users: teachers' Pro plan (granted by hand until online payment exists) and account roles."""

from datetime import timedelta

from fastapi import APIRouter, Request
from sqlalchemy import case, select

from app.attempts import db_now
from app.deps import AdminUser, SessionDep
from app.models import User, UserRole
from app.routers.admin.common import get_or_404, redirect
from app.security import verify_csrf_token
from app.templating import flash, templates

router = APIRouter()


def _back(form) -> str:
    back = str(form.get("back") or "")
    return back if back.startswith("/admin/users") else "/admin/users"  # never redirect off-site


FILTERS = {
    "requests": "Chờ duyệt Pro",
    "teachers": "Giáo viên",
    "students": "Học sinh",
    "all": "Tất cả",
}


@router.get("/users")
async def list_users(request: Request, user: AdminUser, session: SessionDep, filter: str = "requests", q: str = ""):
    now = await db_now(session)
    stmt = select(User)
    if filter == "requests":
        stmt = stmt.where(
            User.role != UserRole.ADMIN,
            User.pro_requested_at.is_not(None),
            (User.pro_until.is_(None)) | (User.pro_until < User.pro_requested_at),
        )
    elif filter == "teachers":
        stmt = stmt.where(User.role == UserRole.TEACHER)
    elif filter == "students":
        stmt = stmt.where(User.role == UserRole.STUDENT)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(User.email.like(like) | User.full_name.ilike(like))
    order = case((User.pro_requested_at.is_(None), 1), else_=0)
    users = (await session.scalars(stmt.order_by(order, User.pro_requested_at.desc(), User.id.desc()).limit(200))).all()
    return templates.TemplateResponse(
        request,
        "admin/users.html",
        {"user": user, "users": users, "now": now, "filter": filter, "filters": FILTERS, "q": q},
    )


@router.post("/users/{user_id:int}/pro")
async def set_pro(request: Request, user: AdminUser, session: SessionDep, user_id: int):
    target = await get_or_404(session, User, user_id)
    form = await request.form()
    back = _back(form)
    submitted_token = str(form.get("csrf_token") or "")
    if submitted_token and not verify_csrf_token(request, submitted_token):
        flash(request, "Lỗi bảo mật: CSRF token không khớp.", "error")
        return redirect(back)
    days = int(str(form.get("days") or "0")) if str(form.get("days") or "0").isdigit() else 0
    if target.role == UserRole.ADMIN:
        flash(request, "Quản trị viên đã có toàn quyền, không cần gói Pro.", "error")
        return redirect(back)
    now = await db_now(session)
    if days > 0:
        start = max(now, target.pro_until) if target.pro_until else now  # extending keeps the remaining days
        target.pro_until = start + timedelta(days=days)
        flash(request, f"Đã bật Pro cho {target.full_name} thêm {days} ngày.")
    else:
        target.pro_until = None
        flash(request, f"Đã tắt Pro của {target.full_name}.")
    target.pro_requested_at = None
    await session.commit()
    return redirect(back)


@router.post("/users/{user_id:int}/role")
async def set_role(request: Request, user: AdminUser, session: SessionDep, user_id: int):
    target = await get_or_404(session, User, user_id)
    form = await request.form()
    back = _back(form)
    submitted_token = str(form.get("csrf_token") or "")
    if submitted_token and not verify_csrf_token(request, submitted_token):
        flash(request, "Lỗi bảo mật: CSRF token không khớp.", "error")
        return redirect(back)
    role = str(form.get("role") or "")
    if target.role == UserRole.ADMIN or role not in (UserRole.STUDENT, UserRole.TEACHER):
        flash(request, "Không đổi được vai trò này.", "error")
        return redirect(back)
    target.role = UserRole(role)
    await session.commit()
    flash(request, f"{target.full_name} giờ là {'giáo viên' if role == UserRole.TEACHER else 'học sinh'}.")
    return redirect(back)
