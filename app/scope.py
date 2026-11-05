"""Subject → Chapter → Lesson "scope" helpers.

Questions and exams attach to a subject, optionally narrowed to a chapter or lesson. Forms use a single
<select> whose values are encoded as "s:<id>", "c:<id>" or "l:<id>"; the server derives the full triple
from that key so the three foreign keys can never disagree.
"""

from dataclasses import dataclass

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Chapter, Lesson, Subject

_INDENT = " " * 4


@dataclass(frozen=True)
class Scope:
    subject_id: int
    chapter_id: int | None = None
    lesson_id: int | None = None

    @property
    def key(self) -> str:
        if self.lesson_id:
            return f"l:{self.lesson_id}"
        if self.chapter_id:
            return f"c:{self.chapter_id}"
        return f"s:{self.subject_id}"

    def conditions(self, model) -> list[ColumnElement[bool]]:
        """SQL filters selecting rows of `model` that fall inside this scope."""
        if self.lesson_id:
            return [model.lesson_id == self.lesson_id]
        if self.chapter_id:
            return [model.chapter_id == self.chapter_id]
        return [model.subject_id == self.subject_id]


@dataclass(frozen=True)
class ScopeOption:
    key: str
    label: str  # indented, for <option>
    path: str  # full "Subject › Chapter › Lesson", for display


def scope_of(obj) -> Scope:
    return Scope(obj.subject_id, obj.chapter_id, obj.lesson_id)


async def load_subject_tree(session: AsyncSession, subject_id: int | None = None) -> list[Subject]:
    stmt = select(Subject).options(selectinload(Subject.chapters).selectinload(Chapter.lessons)).order_by(Subject.name)
    if subject_id is not None:
        stmt = stmt.where(Subject.id == subject_id)
    return list((await session.scalars(stmt)).all())


def scope_options(subjects: list[Subject]) -> list[ScopeOption]:
    options = []
    for s in subjects:
        options.append(ScopeOption(f"s:{s.id}", s.name, s.name))
        for c in s.chapters:
            options.append(ScopeOption(f"c:{c.id}", _INDENT + c.name, f"{s.name} › {c.name}"))
            for lesson in c.lessons:
                options.append(
                    ScopeOption(f"l:{lesson.id}", _INDENT * 2 + lesson.name, f"{s.name} › {c.name} › {lesson.name}")
                )
    return options


def scope_paths(subjects: list[Subject]) -> dict[str, str]:
    return {o.key: o.path for o in scope_options(subjects)}


async def resolve_scope(session: AsyncSession, key: str | None) -> Scope | None:
    kind, _, raw = (key or "").partition(":")
    if not raw.isdigit():
        return None
    obj_id = int(raw)
    if kind == "s":
        return Scope(obj_id) if await session.get(Subject, obj_id) else None
    if kind == "c":
        chapter = await session.get(Chapter, obj_id)
        return Scope(chapter.subject_id, chapter.id) if chapter else None
    if kind == "l":
        lesson = await session.get(Lesson, obj_id)
        if lesson is None:
            return None
        chapter = await session.get(Chapter, lesson.chapter_id)
        return Scope(chapter.subject_id, chapter.id, lesson.id)
    return None
