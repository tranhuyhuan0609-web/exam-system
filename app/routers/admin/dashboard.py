from fastapi import Request
from sqlalchemy import func, select

from app.deps import AdminUser, SessionDep
from app.models import Category, Exam, Question, User
from app.templating import templates


async def dashboard(request: Request, user: AdminUser, session: SessionDep):
    role_counts = dict((await session.execute(select(User.role, func.count()).group_by(User.role))).all())
    stats = {
        "subjects": await session.scalar(
            select(func.count()).select_from(Category).where(Category.parent_id.is_(None))
        ),
        "questions": await session.scalar(select(func.count()).select_from(Question)),
        "exams": await session.scalar(select(func.count()).select_from(Exam)),
        "published": await session.scalar(select(func.count()).select_from(Exam).where(Exam.is_published)),
    }
    return templates.TemplateResponse(
        request, "admin/index.html", {"user": user, "role_counts": role_counts, "stats": stats}
    )
