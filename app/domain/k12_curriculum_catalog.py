"""Comprehensive Master K-12 Curriculum Catalog (Lớp 1 đến Lớp 12 - SGK Kết nối tri thức)
with dedicated Midterm & Final Exam sections for every subject.
"""

import unicodedata
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.k12_curriculum_data import get_master_k12_catalog
from app.models import Category

# Standard Periodic Exam Chapter appended to every subject
PERIODIC_EXAM_CHAPTER = {
    "id": "c-exams",
    "title": "📝 Đề kiểm tra định kì",
    "desc": "Bộ đề kiểm tra giữa học kì 1, cuối học kì 1, giữa học kì 2 và cuối học kì 2 bám sát chương trình môn học.",
    "is_exam_group": True,
    "lessons": [
        {"id": "exam-mid1", "title": "Đề thi giữa học kì 1", "badge": "Giữa HK1", "is_exam": True},
        {"id": "exam-fin1", "title": "Đề thi học kì 1", "badge": "Học kì 1", "is_exam": True},
        {"id": "exam-mid2", "title": "Đề thi giữa học kì 2", "badge": "Giữa HK2", "is_exam": True},
        {"id": "exam-fin2", "title": "Đề thi học kì 2", "badge": "Học kì 2", "is_exam": True},
    ],
}

# Complete Master Curriculum Specification across Grades 1..12
K12_CATALOG: list[dict[str, Any]] = get_master_k12_catalog()


async def _recompute_paths(session: AsyncSession):
    nodes = list((await session.scalars(select(Category))).all())
    by_parent: dict[int | None, list[Category]] = {}
    for n in nodes:
        by_parent.setdefault(n.parent_id, []).append(n)

    async def _walk(parent_id: int | None, path: str, depth: int):
        for idx, child in enumerate(by_parent.get(parent_id, []), start=1):
            child_path = f"{path}{child.id}/"
            child.path = child_path
            child.depth = depth
            child.position = idx
            await _walk(child.id, child_path, depth + 1)

    for idx, root in enumerate(by_parent.get(None, []), start=1):
        root_path = f"/{root.id}/"
        root.path = root_path
        root.depth = 0
        root.position = idx
        await _walk(root.id, root_path, 1)

    await session.flush()


def _norm(s: str) -> str:
    return unicodedata.normalize("NFC", s or "").strip().lower()


async def seed_full_k12_curriculum(session: AsyncSession) -> dict[str, int]:
    """Idempotently seed the complete K-12 master curriculum with chapters, lessons,
    and periodic exam sections across all grades 1 to 12."""
    all_categories = list((await session.scalars(select(Category))).all())
    roots_by_name = {_norm(c.name): c for c in all_categories if c.parent_id is None}
    cats_by_parent_name: dict[tuple[int, str], Category] = {
        (c.parent_id, _norm(c.name)): c for c in all_categories if c.parent_id is not None
    }

    created_subjects = 0
    created_chapters = 0
    created_lessons = 0

    existing_slugs = {c.slug for c in all_categories if c.slug}
    for g_entry in K12_CATALOG:
        grade = g_entry["grade"]  # noqa: F841
        for subj in g_entry["subjects"]:
            s_name = subj["name"]
            s_slug = subj["slug"]
            s_norm = _norm(s_name)

            root = roots_by_name.get(s_norm)
            if not root:
                for c in all_categories:
                    if c.parent_id is None and c.slug == s_slug:
                        root = c
                        break
            if not root:
                target_slug = s_slug
                counter = 1
                while target_slug in existing_slugs:
                    target_slug = f"{s_slug}-{counter}"
                    counter += 1
                existing_slugs.add(target_slug)
                root = Category(name=s_name, slug=target_slug, parent_id=None, depth=0, path="/tmp/")
                session.add(root)
                await session.flush()
                roots_by_name[s_norm] = root
                created_subjects += 1

            chapters = [*subj.get("chapters", []), PERIODIC_EXAM_CHAPTER]
            for c_pos, ch in enumerate(chapters, start=1):  # noqa: B007
                ch_title = ch["title"]
                ch_norm = _norm(ch_title)
                ch_cat = cats_by_parent_name.get((root.id, ch_norm))
                if not ch_cat:
                    for (p_id, c_name), existing in cats_by_parent_name.items():
                        if p_id == root.id and (ch_norm in c_name or c_name in ch_norm):
                            ch_cat = existing
                            break

                if not ch_cat:
                    ch_cat = Category(
                        name=ch_title,
                        slug=None,
                        parent_id=root.id,
                        depth=1,
                        path=f"/{root.id}/tmp/",
                    )
                    session.add(ch_cat)
                    await session.flush()
                    cats_by_parent_name[(root.id, ch_norm)] = ch_cat
                    created_chapters += 1

                # If this is periodic exams, reparent any legacy direct exam categories under it
                if ch.get("is_exam_group"):
                    for (p_id, c_name), legacy_cat in list(cats_by_parent_name.items()):
                        if (
                            p_id == root.id
                            and any(kw in c_name for kw in ("đề thi giữa", "đề thi học kì", "đề kiểm tra"))
                            and legacy_cat.id != ch_cat.id
                        ):
                            legacy_cat.parent_id = ch_cat.id

                for l_pos, les in enumerate(ch.get("lessons", []), start=1):  # noqa: B007
                    l_title = les["title"]
                    l_norm = _norm(l_title)
                    l_cat = cats_by_parent_name.get((ch_cat.id, l_norm))
                    if not l_cat:
                        for (p_id, c_name), existing in cats_by_parent_name.items():
                            if p_id == ch_cat.id and (l_norm in c_name or c_name in l_norm):
                                l_cat = existing
                                break

                    if not l_cat:
                        l_cat = Category(
                            name=l_title,
                            slug=None,
                            parent_id=ch_cat.id,
                            depth=2,
                            path=f"/{root.id}/{ch_cat.id}/tmp/",
                        )
                        session.add(l_cat)
                        await session.flush()
                        cats_by_parent_name[(ch_cat.id, l_norm)] = l_cat
                        created_lessons += 1

    # Ensure EVERY curriculum subject in DB has the periodic exams chapter and all 4 standard exams
    for root in list(roots_by_name.values()):
        if any(kw in root.name.lower() for kw in ("ôn thi", "luyện thi", "quốc tế")):
            continue
        p_ch = cats_by_parent_name.get((root.id, _norm(PERIODIC_EXAM_CHAPTER["title"])))
        if not p_ch:
            p_ch = Category(
                name=PERIODIC_EXAM_CHAPTER["title"],
                slug=None,
                parent_id=root.id,
                depth=1,
                path=f"/{root.id}/tmp/",
            )
            session.add(p_ch)
            await session.flush()
            cats_by_parent_name[(root.id, _norm(PERIODIC_EXAM_CHAPTER["title"]))] = p_ch
            created_chapters += 1

        # Reparent any legacy direct exam categories under this chapter
        for (p_id, c_name), legacy_cat in list(cats_by_parent_name.items()):
            if (
                p_id == root.id
                and any(kw in c_name for kw in ("đề thi giữa", "đề thi học kì", "đề kiểm tra"))
                and legacy_cat.id != p_ch.id
            ):
                legacy_cat.parent_id = p_ch.id
                cats_by_parent_name[(p_ch.id, c_name)] = legacy_cat

        # Ensure all 4 standard periodic exams exist under this chapter
        for l_pos, les in enumerate(PERIODIC_EXAM_CHAPTER["lessons"], start=1):  # noqa: B007
            l_title = les["title"]
            l_norm = _norm(l_title)
            l_cat = cats_by_parent_name.get((p_ch.id, l_norm))
            if not l_cat:
                for (p_id, c_name), existing in cats_by_parent_name.items():
                    if p_id == p_ch.id and (l_norm in c_name or c_name in l_norm):
                        l_cat = existing
                        break
            if not l_cat:
                l_cat = Category(
                    name=l_title,
                    slug=None,
                    parent_id=p_ch.id,
                    depth=2,
                    path=f"/{root.id}/{p_ch.id}/tmp/",
                )
                session.add(l_cat)
                await session.flush()
                cats_by_parent_name[(p_ch.id, l_norm)] = l_cat
                created_lessons += 1

    await session.flush()
    await _recompute_paths(session)
    await session.commit()

    return {
        "created_subjects": created_subjects,
        "created_chapters": created_chapters,
        "created_lessons": created_lessons,
    }
