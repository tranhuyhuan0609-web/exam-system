"""Category ("scope") helpers for the table-of-contents tree.

Questions, exams and AI imports belong to one category (any level) and to its root (the subject). Forms use
a single <select> whose values are "c:<category id>"; the server derives the root from the category, so the
two foreign keys can never disagree. A scope covers its whole branch: filtering by "Chương 1" includes the
questions of every lesson below it.
"""

from dataclasses import dataclass

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category

_INDENT = " " * 4


@dataclass(frozen=True)
class Scope:
    category_id: int
    root_id: int
    path: str  # "/1/5/12/"

    @property
    def key(self) -> str:
        return f"c:{self.category_id}"

    @property
    def subject_id(self) -> int:  # the subject is the root category
        return self.root_id

    def conditions(self, model) -> list[ColumnElement[bool]]:
        """SQL filters selecting rows of `model` that fall anywhere inside this branch."""
        if self.path.count("/") == 2:  # a root: every row of the subject
            return [model.root_id == self.root_id]
        branch = select(Category.id).where(Category.path.like(f"{self.path}%"))
        return [model.category_id.in_(branch)]


@dataclass(frozen=True)
class ScopeRef:
    """What templates need from an object's category: its form key."""

    category_id: int

    @property
    def key(self) -> str:
        return f"c:{self.category_id}"


@dataclass(frozen=True)
class ScopeOption:
    key: str
    label: str  # indented, for <option>
    path: str  # full "Vật lí 11 › Chương 1 › Bài 1", for display
    depth: int


def scope_of(obj) -> ScopeRef:
    return ScopeRef(obj.category_id)


def scope_for(category: Category) -> Scope:
    return Scope(category.id, category.root_id, category.path)


async def load_categories(session: AsyncSession, root_id: int | None = None) -> list[Category]:
    """Every category (or one subject's), parents before children, siblings by position.

    Each node gets a transient `kids` list, so templates can walk the tree recursively.
    """
    stmt = select(Category)
    if root_id is not None:
        stmt = stmt.where(Category.path.like(f"/{root_id}/%"))
    nodes = list((await session.scalars(stmt)).all())
    by_id = {n.id: n for n in nodes}
    for n in nodes:
        n.kids = []
    roots = []
    for n in sorted(nodes, key=lambda n: (n.depth, n.position, n.name)):
        parent = by_id.get(n.parent_id)
        (parent.kids if parent else roots).append(n)
    return roots


def walk(roots: list[Category]):
    """Depth-first: (node, ancestors) pairs in display order."""

    def visit(node, ancestors):
        yield node, ancestors
        for kid in node.kids:
            yield from visit(kid, [*ancestors, node])

    for root in roots:
        yield from visit(root, [])


def scope_options(roots: list[Category]) -> list[ScopeOption]:
    return [
        ScopeOption(
            key=f"c:{node.id}",
            label=_INDENT * len(ancestors) + node.name,
            path=" › ".join([*(a.name for a in ancestors), node.name]),
            depth=len(ancestors),
        )
        for node, ancestors in walk(roots)
    ]


def scope_paths(roots: list[Category]) -> dict[str, str]:
    return {o.key: o.path for o in scope_options(roots)}


def breadcrumb(roots: list[Category], category_id: int) -> list[Category]:
    """Ancestors of a category plus the category itself, root first."""
    for node, ancestors in walk(roots):
        if node.id == category_id:
            return [*ancestors, node]
    return []


async def resolve_scope(session: AsyncSession, key: str | None) -> Scope | None:
    kind, _, raw = (key or "").partition(":")
    if kind != "c" or not raw.isdigit():
        return None
    category = await session.get(Category, int(raw))
    return scope_for(category) if category else None


async def scope_by_id(session: AsyncSession, category_id: int) -> Scope | None:
    category = await session.get(Category, category_id)
    return scope_for(category) if category else None
