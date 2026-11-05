"""Build our table of contents from a source's: subject root › textbook series › chapter › lesson.

Existing nodes are reused when their number matches ("Chương 1: Dao động" = "Chương I. Dao động",
"Bài 3: …" = "Bài 3. …") or, without a number, when the normalised names match; only missing nodes are
created, with the source's wording. Admins can rename or move them afterwards: matching by number keeps
working.
"""

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.categories import create_category, reparent, unique_slug
from app.models import Category
from app.text import slugify

_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
_NUMBERED = re.compile(r"^\s*(chương|chủ đề|phần|bài)\s*([IVXLC]+|\d+)\b", re.I)


def _roman(value: str) -> int:
    total, prev = 0, 0
    for ch in reversed(value.upper()):
        n = _ROMAN[ch]
        total, prev = (total - n, prev) if n < prev else (total + n, n)
    return total


def number_key(name: str) -> tuple[str, int] | None:
    """("chương", 1) for "Chương I. Dao động" / "Chương 1: Dao động"; None when the name has no number."""
    m = _NUMBERED.match(unicodedata.normalize("NFC", name))
    if not m:
        return None
    kind, num = m.group(1).lower(), m.group(2)
    return kind, int(num) if num.isdigit() else _roman(num)


def _norm(name: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", unicodedata.normalize("NFC", name).casefold()).split())


async def _children(session: AsyncSession, parent: Category | None) -> list[Category]:
    cond = Category.parent_id == parent.id if parent else Category.parent_id.is_(None)
    return list((await session.scalars(select(Category).where(cond).order_by(Category.position))).all())


async def child_named(
    session: AsyncSession, parent: Category | None, name: str, create: bool = True
) -> Category | None:
    key = number_key(name)
    for child in await _children(session, parent):
        if (key is not None and number_key(child.name) == key) or _norm(child.name) == _norm(name):
            return child
    if not create:
        return None
    slug = await unique_slug(session, slugify(name)) if parent is None else None
    return await create_category(session, name.strip()[:300], parent, slug=slug)


async def ensure_path(session: AsyncSession, names: list[str]) -> Category:
    """Find or create root › … › leaf (empty names are skipped)."""
    node: Category | None = None
    for name in [n for n in names if n and n.strip()]:
        node = await child_named(session, node, name)
    if node is None:
        raise ValueError("Empty category path")
    return node


async def adopt_series(session: AsyncSession, root_name: str, series: str) -> int:
    """Move a subject's chapters that sit directly under the root into a series node (e.g. the KNTT chapters
    created before series existed). Lessons, questions and exams keep their ids. Returns how many moved."""
    root = await child_named(session, None, root_name, create=False)
    if root is None:
        return 0
    target = await child_named(session, root, series)
    moved = 0
    for child in await _children(session, root):
        if (
            child.id != target.id
            and number_key(child.name)
            and number_key(child.name)[0] in ("chương", "chủ đề", "phần")
        ):
            await reparent(session, child, target)
            moved += 1
    if moved:
        await session.flush()
        session.expire_all()  # reparent rewrites paths in bulk: loaded nodes must be re-read
    return moved
