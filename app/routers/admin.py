from fastapi import APIRouter, Request
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.deps import AdminUser, SessionDep
from app.models import Chapter, Subject, User
from app.templating import templates

router = APIRouter(prefix="/admin")


@router.get("")
async def dashboard(request: Request, user: AdminUser, session: SessionDep):
    subjects = (
        await session.scalars(
            select(Subject).options(selectinload(Subject.chapters).selectinload(Chapter.lessons)).order_by(Subject.name)
        )
    ).all()
    role_counts = dict((await session.execute(select(User.role, func.count()).group_by(User.role))).all())
    return templates.TemplateResponse(
        request,
        "admin/index.html",
        {"user": user, "subjects": subjects, "role_counts": role_counts},
    )
