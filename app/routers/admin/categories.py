"""Admin: the table-of-contents tree (subjects and any number of levels below them)."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from sqlalchemy import func, select

from app.application.categories import (
    CategoryError,
    create_category,
    delete_category,
    move_order,
    reparent,
    unique_slug,
)
from app.deps import AdminUser, SessionDep
from app.models import AccessTier, Category, Exam, Question
from app.routers.admin.common import clean_text, get_or_404, redirect
from app.scope import load_categories, scope_options, walk
from app.templating import flash, templates
from app.text import slugify

router = APIRouter()

TextForm = Annotated[str, Form()]


def _back(category_id: int | None = None) -> str:
    return f"/admin/categories#cat-{category_id}" if category_id else "/admin/categories"


@router.get("/subjects")
async def old_subjects_page():
    return redirect("/admin/categories")


@router.get("/categories")
async def categories_page(request: Request, user: AdminUser, session: SessionDep):
    roots = await load_categories(session)
    direct_questions = dict(
        (await session.execute(select(Question.category_id, func.count()).group_by(Question.category_id))).all()
    )
    direct_exams = dict(
        (await session.execute(select(Exam.category_id, func.count()).group_by(Exam.category_id))).all()
    )
    # Branch totals: a node counts everything filed under it or under its descendants.
    totals: dict[int, list[int]] = {}
    for node, ancestors in walk(roots):
        for owner in [node, *ancestors]:
            entry = totals.setdefault(owner.id, [0, 0])
            entry[0] += direct_questions.get(node.id, 0)
            entry[1] += direct_exams.get(node.id, 0)
    return templates.TemplateResponse(
        request,
        "admin/categories.html",
        {
            "user": user,
            "roots": roots,
            "totals": totals,
            "options": scope_options(roots),
        },
    )


@router.post("/categories")
async def create(
    request: Request,
    session: SessionDep,
    name: TextForm,
    parent_id: TextForm = "",
    slug: TextForm = "",
    default_tier: TextForm = "",
):
    clean = clean_text(name, 300)
    if not clean:
        flash(request, "Tên mục không hợp lệ (1–300 ký tự).", "error")
        return redirect(_back())
    parent = await get_or_404(session, Category, int(parent_id)) if parent_id.isdigit() else None
    tier = AccessTier(default_tier) if default_tier in AccessTier.__members__ else None
    category = await create_category(
        session,
        clean,
        parent,
        slug=None if parent else await unique_slug(session, slugify(slug or clean)),
        default_tier=tier,
    )
    await session.commit()
    flash(request, f"Đã thêm “{clean}”" + (f" vào “{parent.name}”." if parent else " (môn học mới)."))
    return redirect(_back(category.id))


@router.post("/categories/{category_id:int}")
async def rename(
    request: Request,
    session: SessionDep,
    category_id: int,
    name: TextForm,
    description: TextForm = "",
    default_tier: TextForm = "",
):
    category = await get_or_404(session, Category, category_id)
    clean = clean_text(name, 300)
    if not clean:
        flash(request, "Tên mục không hợp lệ (1–300 ký tự).", "error")
        return redirect(_back(category_id))
    category.name = clean
    category.description = description.strip() or category.description
    if default_tier in AccessTier.__members__:
        category.default_tier = AccessTier(default_tier)
    if category.parent_id is None:
        category.slug = await unique_slug(session, slugify(clean), exclude_id=category.id)
    await session.commit()
    flash(request, "Đã đổi tên.")
    return redirect(_back(category_id))


@router.post("/categories/{category_id:int}/order")
async def order(session: SessionDep, category_id: int, direction: TextForm):
    category = await get_or_404(session, Category, category_id)
    await move_order(session, category, direction)
    await session.commit()
    return redirect(_back(category_id))


@router.post("/categories/{category_id:int}/move")
async def move(request: Request, session: SessionDep, category_id: int, parent_id: TextForm = ""):
    category = await get_or_404(session, Category, category_id)
    new_parent = await get_or_404(session, Category, int(parent_id)) if parent_id.isdigit() else None
    try:
        await reparent(session, category, new_parent)
    except CategoryError as exc:
        flash(request, str(exc), "error")
        return redirect(_back(category_id))
    if new_parent is None and not category.slug:
        category.slug = await unique_slug(session, slugify(category.name), exclude_id=category.id)
    elif new_parent is not None:
        category.slug = None
    await session.commit()
    flash(request, f"Đã chuyển “{category.name}” " + (f"vào “{new_parent.name}”." if new_parent else "thành môn học."))
    return redirect(_back(category_id))


@router.post("/categories/{category_id:int}/delete")
async def delete(request: Request, session: SessionDep, category_id: int):
    category = await get_or_404(session, Category, category_id)
    parent_id = category.parent_id
    try:
        await delete_category(session, category)
    except CategoryError as exc:
        flash(request, str(exc), "error")
        return redirect(_back(category_id))
    await session.commit()
    flash(request, f"Đã xoá “{category.name}” và các mục con.")
    return redirect(_back(parent_id))
