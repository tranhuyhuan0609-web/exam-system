"""Editing the table-of-contents tree: create, rename, reorder, move to another branch, delete.

Invariants kept here: `path` = parent's path + own id, `depth` = number of ancestors, and every question /
exam / AI job has `root_id` equal to the root of its category.
"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Exam, ImportJob, Question


class CategoryError(Exception):
    """A tree edit is not allowed; the message is shown to the admin."""


async def create_category(
    session: AsyncSession, name: str, parent: Category | None = None, slug: str | None = None
) -> Category:
    last = await session.scalar(
        select(func.max(Category.position)).where(
            Category.parent_id == parent.id if parent else Category.parent_id.is_(None)
        )
    )
    category = Category(
        name=name,
        parent_id=parent.id if parent else None,
        slug=slug if parent is None else None,
        position=(last or 0) + 1,
        depth=parent.depth + 1 if parent else 0,
        path="",
    )
    session.add(category)
    await session.flush()  # the id is part of the path
    category.path = f"{parent.path if parent else '/'}{category.id}/"
    return category


def branch(category: Category):
    return select(Category.id).where(Category.path.like(f"{category.path}%"))


async def branch_usage(session: AsyncSession, category: Category) -> tuple[int, int, int]:
    """(questions, exams, AI jobs) attached anywhere in this branch."""
    ids = branch(category)

    async def count(model) -> int:
        return await session.scalar(select(func.count()).select_from(model).where(model.category_id.in_(ids))) or 0

    return await count(Question), await count(Exam), await count(ImportJob)


async def siblings(session: AsyncSession, category: Category) -> list[Category]:
    condition = Category.parent_id == category.parent_id if category.parent_id else Category.parent_id.is_(None)
    return list(
        (await session.scalars(select(Category).where(condition).order_by(Category.position, Category.id))).all()
    )


async def move_order(session: AsyncSession, category: Category, direction: str) -> None:
    items = await siblings(session, category)
    i = items.index(category)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(items):
        items[i], items[j] = items[j], items[i]
    for position, item in enumerate(items, start=1):
        item.position = position


async def reparent(session: AsyncSession, category: Category, new_parent: Category | None) -> None:
    """Move a branch under another category (or make it a subject when new_parent is None)."""
    if new_parent is not None and new_parent.path.startswith(category.path):
        raise CategoryError("Không thể chuyển một mục vào chính nó hoặc vào mục con của nó.")
    if (new_parent.id if new_parent else None) == category.parent_id:
        return
    old_root = category.root_id
    new_root = new_parent.root_id if new_parent else category.id
    if new_root != old_root and any(await branch_usage(session, category)):
        raise CategoryError(
            "Mục này (hoặc mục con) đang có câu hỏi / đề thi nên chỉ chuyển được trong cùng môn. "
            "Hãy chuyển các câu hỏi đi trước."
        )
    old_prefix, old_depth = category.path, category.depth
    new_prefix = f"{new_parent.path if new_parent else '/'}{category.id}/"
    depth_change = (new_parent.depth + 1 if new_parent else 0) - old_depth
    last = await session.scalar(
        select(func.max(Category.position)).where(
            Category.parent_id == new_parent.id if new_parent else Category.parent_id.is_(None)
        )
    )
    category.parent_id = new_parent.id if new_parent else None
    category.position = (last or 0) + 1
    await session.flush()
    await session.execute(
        update(Category)
        .where(Category.path.like(f"{old_prefix}%"))
        .values(
            path=func.concat(new_prefix, func.substr(Category.path, len(old_prefix) + 1)),
            depth=Category.depth + depth_change,
        )
        .execution_options(synchronize_session=False)
    )
    await session.refresh(category)


async def delete_category(session: AsyncSession, category: Category) -> None:
    questions, exams, jobs = await branch_usage(session, category)
    if questions or exams or jobs:
        raise CategoryError(
            f"Không thể xoá: mục này và các mục con còn {questions} câu hỏi, {exams} đề thi, {jobs} lần AI nhập đề."
        )
    nodes = (
        await session.scalars(
            select(Category).where(Category.path.like(f"{category.path}%")).order_by(Category.depth.desc())
        )
    ).all()
    for node in nodes:  # children first (the parent FK is RESTRICT)
        await session.delete(node)
        await session.flush()
