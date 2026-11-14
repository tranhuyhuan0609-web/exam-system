"""Category ("scope") helpers for the table-of-contents tree.

Questions, exams and AI imports belong to one category (any level) and to its root (the subject). Forms use
a single <select> whose values are "c:<category id>"; the server derives the root from the category, so the
two foreign keys can never disagree. A scope covers its whole branch: filtering by "Chương 1" includes the
questions of every lesson below it.
"""

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, ExamTarget

_INDENT = "    "


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
        """SQL filters selecting rows of `model` that fall anywhere inside this branch, checking both direct and many-to-many category links."""  # noqa: E501
        from sqlalchemy import or_

        from app.models import Category, Question, QuestionCategory

        if model == Question:
            if self.path.count("/") == 2:  # a root: every row of the subject
                branch_cats = select(Category.id).where(Category.path.like(f"/{self.root_id}/%"))
                return [
                    or_(
                        model.root_id == self.root_id,
                        model.id.in_(
                            select(QuestionCategory.question_id).where(QuestionCategory.category_id.in_(branch_cats))
                        ),
                    )
                ]
            branch = select(Category.id).where(Category.path.like(f"{self.path}%"))
            return [
                or_(
                    model.category_id.in_(branch),
                    model.id.in_(select(QuestionCategory.question_id).where(QuestionCategory.category_id.in_(branch))),
                )
            ]

        if self.path.count("/") == 2:  # a root: every row of the subject
            return [model.root_id == self.root_id]
        branch = select(Category.id).where(Category.path.like(f"{self.path}%"))
        return [model.category_id.in_(branch)]


@dataclass(frozen=True)
class TargetScope:
    code: str
    name: str
    group_category: str  # "CURRICULUM" | "MAJOR_EXAM" | "OLYMPIAD"
    target_id: int

    @property
    def key(self) -> str:
        return f"target:{self.code}"

    @property
    def subject_id(self) -> int | None:
        return None

    def conditions(self, model) -> list[ColumnElement[bool]]:
        """SQL filters selecting questions belonging to this target via inheritance, tagging, or difficulty."""
        from app.models import (
            AccessTier,
            Difficulty,
            Question,
            QuestionTargetTag,
            TargetCurriculumMapping,
        )

        if model != Question:
            return []

        clauses: list[ColumnElement[bool]] = []
        standard_diffs = [
            Difficulty.RECOGNITION,
            Difficulty.UNDERSTANDING,
            Difficulty.APPLICATION,
            Difficulty.ADVANCED_APPLICATION,
        ]

        # 1. Automatic curriculum mapping
        mapped_units_subq = select(TargetCurriculumMapping.knowledge_unit_id).where(
            TargetCurriculumMapping.target_id == self.target_id
        )
        clauses.append(model.knowledge_unit_id.in_(mapped_units_subq) & model.difficulty.in_(standard_diffs))

        # 2. Tagged in question_target_tags
        tagged_q_ids_subq = select(QuestionTargetTag.question_id).where(QuestionTargetTag.target_id == self.target_id)
        if self.code in ("THPTQG", "VAO_10", "SGK_KNTT"):
            clauses.append(model.id.in_(tagged_q_ids_subq) & model.difficulty.in_(standard_diffs))
        else:
            clauses.append(model.id.in_(tagged_q_ids_subq))

        # 3. Legacy root categories mapping
        if self.code == "THPTQG":
            clauses.append(model.root_id.in_([81, 85, 139, 161, 179]))
        elif self.code in ("VAO_10", "VAO_10_CHUYEN"):
            clauses.append(model.root_id.in_([187, 194, 348]))
        elif self.group_category == "OLYMPIAD" or self.code in ("HSG_TINH", "HSG_QG", "OLYMPIAD_CHUYEN"):
            clauses.append(model.root_id == 111)

        # 4. Thinking skill (TSA, HSA, BCA, HNUE)
        if self.code in ("DGTD_TSA", "DGNL_HSA", "DGNL_VNUHCM", "DGNL_BCA", "DGNL_HNUE"):
            clauses.append(model.is_thinking_skill.is_(True) & model.difficulty.in_(standard_diffs))

        # 5. Olympiad / HSG (HSG gồm các câu phần VD, VDC và HSG)
        if self.group_category == "OLYMPIAD" or self.code in ("HSG_TINH", "HSG_QG", "OLYMPIAD_CHUYEN"):
            clauses.append(
                model.difficulty.in_([Difficulty.HSG, Difficulty.ADVANCED_APPLICATION])
                | (
                    (model.difficulty == Difficulty.APPLICATION)
                    & (
                        model.is_thinking_skill.is_(True)
                        | (model.access_tier == AccessTier.PRO)
                        | model.id.in_(tagged_q_ids_subq)
                    )  # noqa: E501
                )
            )
        elif self.code == "VAO_10_CHUYEN":
            clauses.append(
                model.difficulty.in_([Difficulty.APPLICATION, Difficulty.ADVANCED_APPLICATION, Difficulty.HSG])
            )

        return [or_(*clauses)] if clauses else []


@dataclass(frozen=True)
class SectionScope:
    section: str  # "curriculum" | "major_exam" | "olympiad"

    @property
    def key(self) -> str:
        return f"section:{self.section}"

    @property
    def subject_id(self) -> int | None:
        return None

    def conditions(self, model) -> list[ColumnElement[bool]]:
        """SQL filter selecting all questions belonging to this top-level pillar."""
        from app.models import (
            AccessTier,
            Difficulty,
            ExamTarget,
            Question,
            QuestionTargetTag,
            TargetCurriculumMapping,
        )

        if model != Question:
            return []

        if self.section == "curriculum":
            curr_root_ids = [
                1,
                2,
                3,
                18,
                91,
                116,
                221,
                240,
                243,
                270,
                353,
                355,
                357,
                362,
                372,
                388,
                395,
                436,
                439,
                443,
                446,
                448,
                469,
                472,
                520,
                564,
            ]  # noqa: E501
            return [model.root_id.in_(curr_root_ids)]

        if self.section == "major_exam":
            major_root_ids = [81, 85, 139, 161, 179, 187, 194, 348]
            target_subq = (
                select(QuestionTargetTag.question_id).join(ExamTarget).where(ExamTarget.group_category == "MAJOR_EXAM")
            )  # noqa: E501
            map_subq = (
                select(TargetCurriculumMapping.knowledge_unit_id)
                .join(ExamTarget)
                .where(ExamTarget.group_category == "MAJOR_EXAM")
            )  # noqa: E501
            standard_diffs = [
                Difficulty.RECOGNITION,
                Difficulty.UNDERSTANDING,
                Difficulty.APPLICATION,
                Difficulty.ADVANCED_APPLICATION,
            ]
            return [
                or_(
                    model.root_id.in_(major_root_ids),
                    model.id.in_(target_subq),
                    (model.knowledge_unit_id.in_(map_subq) & model.difficulty.in_(standard_diffs)),
                    model.is_thinking_skill.is_(True),
                )
            ]

        if self.section == "olympiad":
            hsg_target_subq = (
                select(QuestionTargetTag.question_id).join(ExamTarget).where(ExamTarget.group_category == "OLYMPIAD")
            )  # noqa: E501
            return [
                or_(
                    model.root_id == 111,
                    model.difficulty.in_([Difficulty.HSG, Difficulty.ADVANCED_APPLICATION]),
                    model.id.in_(hsg_target_subq),
                    (
                        (model.difficulty == Difficulty.APPLICATION)
                        & (model.is_thinking_skill.is_(True) | (model.access_tier == AccessTier.PRO))
                    ),  # noqa: E501
                )
            ]

        return []


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


@dataclass(frozen=True)
class ScopeGroup:
    label: str
    options: list[ScopeOption]


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


async def resolve_scope(session: AsyncSession, key: str | None) -> Scope | TargetScope | SectionScope | None:
    if not key:
        return None
    kind, _, raw = key.partition(":")
    if kind == "c" and raw.isdigit():
        category = await session.get(Category, int(raw))
        return scope_for(category) if category else None
    if kind == "target" and raw.strip():
        code = raw.strip().upper()
        target = await session.scalar(select(ExamTarget).where(ExamTarget.code == code))
        if target:
            return TargetScope(
                code=target.code,
                name=target.name,
                group_category=str(target.group_category),
                target_id=target.id,
            )
    if kind == "section" and raw.strip():
        return SectionScope(section=raw.strip())
    return None


def is_curriculum_root_name(name: str) -> bool:
    nl = name.lower()
    return not any(kw in nl for kw in ("ôn thi", "luyện thi", "hsg", "quốc tế", "open trivia"))


def _grade_sort_key(node: Category):
    m = re.search(r"\b(\d+)\b", node.name)
    grade = int(m.group(1)) if m else 99
    return (grade, node.name)


async def load_curriculum_categories(session: AsyncSession) -> list[Category]:
    """Return only standard K-12 curriculum categories (excluding exam & Olympiad roots), sorted cleanly by grade."""
    all_roots = await load_categories(session)
    curr_roots = [r for r in all_roots if is_curriculum_root_name(r.name)]
    return sorted(curr_roots, key=_grade_sort_key)


async def get_section_scope_data(session: AsyncSession) -> dict[str, Any]:
    """Prepare cascading 2-level options for the question bank filter:
    Level 1: section ('all', 'curriculum', 'major_exam', 'olympiad')
    Level 2: scope options belonging strictly to that selected section.
    """
    curr_roots = await load_curriculum_categories(session)
    curr_opts = [{"key": "", "label": "— Tất cả môn học SGK KNTT —", "path": "Tất cả SGK"}] + [
        {"key": o.key, "label": o.label, "path": o.path} for o in scope_options(curr_roots)
    ]

    targets = list((await session.scalars(select(ExamTarget).order_by(ExamTarget.id))).all())

    major_exam_opts = [
        {"key": "section:major_exam", "label": "— Tất cả kỳ thi quan trọng —", "path": "Kỳ thi quan trọng"}
    ] + [
        {"key": f"target:{t.code}", "label": t.name, "path": t.name}
        for t in targets
        if str(t.group_category) == "MAJOR_EXAM"
    ]

    olympiad_opts = [
        {
            "key": "section:olympiad",
            "label": "— Tất cả Học sinh giỏi & Chuyên (VD + VDC) —",
            "path": "Học sinh giỏi & Chuyên",
        }  # noqa: E501
    ] + [
        {"key": f"target:{t.code}", "label": t.name, "path": t.name}
        for t in targets
        if str(t.group_category) == "OLYMPIAD"
    ]

    all_opts = [{"key": "", "label": "— Tất cả câu hỏi trong ngân hàng —", "path": "Tất cả ngân hàng"}]

    section_options = {
        "all": all_opts,
        "curriculum": curr_opts,
        "major_exam": major_exam_opts,
        "olympiad": olympiad_opts,
    }

    all_paths: dict[str, str] = {}
    for opts in section_options.values():
        for o in opts:
            if o["key"]:
                all_paths[o["key"]] = o["path"]

    return {
        "section_options": section_options,
        "all_paths": all_paths,
    }


async def get_admin_scope_groups(session: AsyncSession, roots: list[Category]) -> list[ScopeGroup]:
    """Admin question bank TOC partitioned into 3 structured parts:
    1. Bám sát chương trình học (Lớp 1..12 KNTT)
    2. Kỳ thi quan trọng (THPTQG, TSA, HSA, Vào 10, BCA...)
    3. Bồi dưỡng Học sinh giỏi (HSG & Chuyên - VD + VDC)
    """
    curr_roots = [r for r in roots if is_curriculum_root_name(r.name)]
    curriculum_opts = scope_options(sorted(curr_roots, key=_grade_sort_key))
    targets = list((await session.scalars(select(ExamTarget).order_by(ExamTarget.id))).all())

    major_exam_opts = [
        ScopeOption(
            key=f"target:{t.code}",
            label=t.name,
            path=t.name,
            depth=0,
        )
        for t in targets
        if str(t.group_category) == "MAJOR_EXAM"
    ]

    olympiad_opts = [
        ScopeOption(
            key=f"target:{t.code}",
            label=t.name,
            path=t.name,
            depth=0,
        )
        for t in targets
        if str(t.group_category) == "OLYMPIAD"
    ]

    return [
        ScopeGroup(
            label="📘 PHẦN 1: BÁM SÁT CHƯƠNG TRÌNH HỌC (LỚP 1 ĐẾN 12 - KẾT NỐI TRI THỨC)",
            options=curriculum_opts,
        ),
        ScopeGroup(
            label="🎯 PHẦN 2: KỲ THI QUAN TRỌNG (THPTQG • ĐGTD • ĐGNL • VÀO 10)",
            options=major_exam_opts,
        ),
        ScopeGroup(
            label="🏆 PHẦN 3: BỒI DƯỠNG HỌC SINH GIỎI (HSG & CHUYÊN - VD + VDC)",
            options=olympiad_opts,
        ),
    ]


async def get_all_scope_paths(session: AsyncSession, roots: list[Category]) -> dict[str, str]:
    paths = scope_paths(roots)
    targets = (await session.scalars(select(ExamTarget))).all()
    for t in targets:
        paths[f"target:{t.code}"] = t.name
    paths["section:major_exam"] = "Kỳ thi quan trọng"
    paths["section:olympiad"] = "Học sinh giỏi & Chuyên"
    return paths


async def scope_by_id(session: AsyncSession, category_id: int) -> Scope | None:
    category = await session.get(Category, category_id)
    return scope_for(category) if category else None


def normalize_chapter_key(name: str) -> str:
    s = name.lower().strip().replace("hòa", "hoà")
    if "đề kiểm tra" in s or "định kì" in s:
        return "de_kiem_tra"
    m = re.match(r"^(chương\s*(\d+|[ivxldcm]+)|chủ\s*đề\s*(\d+))\b", s)
    if m:
        num_str = m.group(2) or m.group(3)
        roman_map = {
            "i": 1,
            "ii": 2,
            "iii": 3,
            "iv": 4,
            "v": 5,
            "vi": 6,
            "vii": 7,
            "viii": 8,
            "ix": 9,
            "x": 10,
            "xi": 11,
            "xii": 12,
        }  # noqa: E501
        num = roman_map.get(num_str.lower(), int(num_str) if num_str.isdigit() else num_str)
        return f"ch_{num}"
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", s))


def normalize_lesson_name(name: str) -> str:
    s = name.lower().strip().replace("hòa", "hoà")
    m = re.match(r"^(bài\s*(\d+))\b", s)
    if m:
        return f"bai_{m.group(2)}"
    m_unit = re.match(r"^(unit\s*(\d+))\b", s)
    if m_unit:
        rest = s[m_unit.end() :].strip()
        for part in ["reading", "writing", "listening", "speaking", "vocabulary", "grammar", "phonetics"]:
            if part in rest:
                return f"unit_{m_unit.group(2)}_{part}"
        return f"unit_{m_unit.group(2)}"
    m_cd = re.match(r"^(chủ\s*đề\s*(\d+))\b", s)
    if m_cd:
        return f"chu_de_{m_cd.group(2)}"
    m_exam = re.search(r"(giữa\s*học\s*kì\s*[12]|học\s*kì\s*[12])", s)
    if m_exam:
        return f"exam_{re.sub(r'\s+', '', m_exam.group(1))}"
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", s))


async def get_drilldown_catalog(session: AsyncSession) -> dict[str, Any]:
    """Hierarchical master catalog for sequential 5-step admin question filtering:
    - Section (Phần 1, Phần 2, Phần 3)
    - If Phần 1: Grade (Lớp 1..12) -> Subject (Môn) -> Chapter (Chương) -> Lesson (Bài học / Đề thi định kỳ)
    - If Phần 2: Exam (Kỳ thi) -> Subject (Môn)
    - If Phần 3: Tier (Cấp độ HSG) -> Subject (Môn) -> Difficulty (Mức độ)
    """
    from sqlalchemy import func

    from app.models import Question

    all_categories = list((await session.scalars(select(Category).order_by(Category.position, Category.id))).all())

    q_counts: dict[int, int] = dict(
        (
            await session.execute(
                select(Question.category_id, func.count(Question.id))
                .where(Question.category_id.is_not(None))
                .group_by(Question.category_id)
            )
        ).all()
    )

    by_parent: dict[int | None, list[Category]] = {}
    for c in all_categories:
        by_parent.setdefault(c.parent_id, []).append(c)

    curr_roots = [c for c in by_parent.get(None, []) if is_curriculum_root_name(c.name)]

    grades_dict: dict[int, dict[str, Any]] = {}
    for g in range(1, 13):
        grades_dict[g] = {
            "grade": g,
            "name": f"Lớp {g}",
            "subjects": [],
        }

    for root in curr_roots:
        m = re.search(r"\b(\d+)\b", root.name)
        g = int(m.group(1)) if m else None
        if not g or g not in grades_dict:
            continue

        raw_chapters = by_parent.get(root.id, [])
        ch_grouped: dict[str, list[tuple[Category, list[Category], int]]] = {}
        for ch in raw_chapters:
            ckey = normalize_chapter_key(ch.name)
            ch_lessons = by_parent.get(ch.id, [])
            tot_q = q_counts.get(ch.id, 0) + sum(q_counts.get(l.id, 0) for l in ch_lessons)  # noqa: E741
            ch_grouped.setdefault(ckey, []).append((ch, ch_lessons, tot_q))

        chapters_data = []
        for ckey, entries in ch_grouped.items():  # noqa: B007
            # Pick representative chapter (highest total questions, most lessons, lowest id)
            entries.sort(key=lambda e: (-e[2], -len(e[1]), e[0].id))
            best_ch, best_lessons, best_q = entries[0]

            is_exam = "đề kiểm tra" in best_ch.name.lower() or "định kì" in best_ch.name.lower()

            all_raw_lessons = list(best_lessons)
            for other_ch, other_lessons, other_q in entries[1:]:  # noqa: B007
                for l in other_lessons:  # noqa: E741
                    if q_counts.get(l.id, 0) > 0 and l not in all_raw_lessons:
                        all_raw_lessons.append(l)

            les_grouped: dict[str, list[Category]] = {}
            for l in all_raw_lessons:  # noqa: E741
                lkey = normalize_lesson_name(l.name)
                les_grouped.setdefault(lkey, []).append(l)

            lessons_data = []
            seen_clean_names = set()
            for lkey, l_entries in les_grouped.items():  # noqa: B007
                l_entries.sort(key=lambda l: (-q_counts.get(l.id, 0), l.position or 999, l.id))  # noqa: E741
                chosen = l_entries[0]
                clean_n = re.sub(r"\s+", " ", chosen.name.lower().replace("hòa", "hoà").strip())
                if clean_n in seen_clean_names:
                    continue
                seen_clean_names.add(clean_n)
                lessons_data.append({"id": chosen.id, "name": chosen.name})

            def _les_order_key(item):
                m_num = re.search(r"(\d+)", item["name"])
                return int(m_num.group(1)) if m_num else 9999

            lessons_data.sort(key=_les_order_key)

            chapters_data.append(
                {
                    "id": best_ch.id,
                    "name": best_ch.name,
                    "is_exam": is_exam,
                    "lessons": lessons_data,
                }
            )

        def _ch_order_key(item):
            if item.get("is_exam"):
                return 9999
            m_num = re.search(r"(\d+)", item["name"])
            return int(m_num.group(1)) if m_num else 999

        chapters_data.sort(key=_ch_order_key)

        grades_dict[g]["subjects"].append(
            {
                "id": root.id,
                "name": root.name,
                "chapters": chapters_data,
            }
        )

    subj_order = [
        "toán",
        "tiếng việt",
        "ngữ văn",
        "tiếng anh",
        "khoa học tự nhiên",
        "vật lí",
        "hoá học",
        "hóa học",
        "sinh học",
        "lịch sử",
        "địa lí",
        "tin học",
        "công nghệ",
        "giáo dục kinh tế",
    ]
    for g_data in grades_dict.values():

        def _subj_sort_key(s):
            n = s["name"].lower()
            for idx, kw in enumerate(subj_order):
                if kw in n:
                    return idx
            return 99

        g_data["subjects"].sort(key=_subj_sort_key)

    targets = list((await session.scalars(select(ExamTarget).order_by(ExamTarget.id))).all())
    major_targets = [t for t in targets if str(t.group_category) == "MAJOR_EXAM"]

    standard_major_subjects = [
        {"code": "TOAN", "name": "Toán"},
        {"code": "VAT_LI", "name": "Vật lí"},
        {"code": "HOA_HOC", "name": "Hóa học"},
        {"code": "SINH_HOC", "name": "Sinh học"},
        {"code": "TIENG_ANH", "name": "Tiếng Anh"},
        {"code": "NGU_VAN", "name": "Ngữ văn"},
        {"code": "LICH_SU", "name": "Lịch sử"},
        {"code": "DIA_LI", "name": "Địa lí"},
        {"code": "KHTN", "name": "Khoa học tự nhiên"},
    ]
    major_exam_list = [
        {
            "code": t.code,
            "name": t.name,
            "subjects": standard_major_subjects,
        }
        for t in major_targets
    ]

    olympiad_targets = [t for t in targets if str(t.group_category) == "OLYMPIAD"]
    olympiad_subjects = [
        {"code": "TOAN", "name": "Toán"},
        {"code": "VAT_LI", "name": "Vật lí"},
        {"code": "HOA_HOC", "name": "Hóa học"},
        {"code": "SINH_HOC", "name": "Sinh học"},
        {"code": "TIN_HOC", "name": "Tin học"},
        {"code": "TIENG_ANH", "name": "Tiếng Anh"},
        {"code": "NGU_VAN", "name": "Ngữ văn"},
        {"code": "LICH_SU", "name": "Lịch sử"},
        {"code": "DIA_LI", "name": "Địa lí"},
    ]
    olympiad_list = [
        {
            "code": t.code,
            "name": t.name,
            "subjects": olympiad_subjects,
        }
        for t in olympiad_targets
    ]

    return {
        "grades": grades_dict,
        "major_exams": major_exam_list,
        "olympiads": olympiad_list,
    }


async def build_drilldown_conditions(
    session: AsyncSession,
    section: str,
    grade: str | int | None,
    subject_id: str | int | None,
    chapter_id: str | int | None,
    lesson_id: str | int | None,
    target: str | None,
    exam_subject: str | None,
) -> tuple[list[ColumnElement[bool]], str, dict[str, Any]]:
    """Build exact SQL conditions, breadcrumb label, and resolved active IDs for cascading drill-down."""
    from sqlalchemy import or_

    from app.models import Category, KnowledgeUnit, Question, QuestionCategory

    conditions: list[ColumnElement[bool]] = []
    breadcrumb_parts: list[str] = []
    resolved = {
        "section": section or "all",
        "grade": str(grade or ""),
        "subject_id": str(subject_id or ""),
        "chapter_id": str(chapter_id or ""),
        "lesson_id": str(lesson_id or ""),
        "target": target or "",
        "exam_subject": exam_subject or "",
    }

    # Helper: resolve curriculum category by ID
    all_categories = list((await session.scalars(select(Category))).all())
    cat_by_id = {c.id: c for c in all_categories}

    # 1. CURRICULUM DRILL-DOWN
    if section == "curriculum":
        breadcrumb_parts.append("Phần 1: Bám sát CT học (Lớp 1 - 12)")
        if lesson_id and str(lesson_id).isdigit() and int(lesson_id) in cat_by_id:
            les_cat = cat_by_id[int(lesson_id)]
            ch_cat = cat_by_id.get(les_cat.parent_id) if les_cat.parent_id else None
            root_cat = (
                cat_by_id.get(ch_cat.parent_id)
                if ch_cat and ch_cat.parent_id
                else (cat_by_id.get(les_cat.root_id) if les_cat.root_id else None)
            )  # noqa: E501

            conditions.append(
                or_(
                    Question.category_id == les_cat.id,
                    Question.id.in_(
                        select(QuestionCategory.question_id).where(QuestionCategory.category_id == les_cat.id)
                    ),
                )
            )
            if root_cat:
                m = re.search(r"\b(\d+)\b", root_cat.name)
                resolved["grade"] = str(int(m.group(1))) if m else ""
                resolved["subject_id"] = str(root_cat.id)
                breadcrumb_parts.append(f"Lớp {resolved['grade']}")
                breadcrumb_parts.append(root_cat.name)
            if ch_cat:
                resolved["chapter_id"] = str(ch_cat.id)
                breadcrumb_parts.append(ch_cat.name)
            breadcrumb_parts.append(les_cat.name)

        elif chapter_id and str(chapter_id).isdigit() and int(chapter_id) in cat_by_id:
            ch_cat = cat_by_id[int(chapter_id)]
            root_cat = cat_by_id.get(ch_cat.parent_id) or cat_by_id.get(ch_cat.root_id)
            branch = select(Category.id).where(Category.path.like(f"{ch_cat.path}%"))
            conditions.append(
                or_(
                    Question.category_id.in_(branch),
                    Question.id.in_(
                        select(QuestionCategory.question_id).where(QuestionCategory.category_id.in_(branch))
                    ),
                )
            )
            if root_cat:
                m = re.search(r"\b(\d+)\b", root_cat.name)
                resolved["grade"] = str(int(m.group(1))) if m else ""
                resolved["subject_id"] = str(root_cat.id)
                breadcrumb_parts.append(f"Lớp {resolved['grade']}")
                breadcrumb_parts.append(root_cat.name)
            breadcrumb_parts.append(ch_cat.name)

        elif subject_id and str(subject_id).isdigit() and int(subject_id) in cat_by_id:
            root_cat = cat_by_id[int(subject_id)]
            branch_cats = select(Category.id).where(Category.path.like(f"/{root_cat.id}/%"))
            conditions.append(
                or_(
                    Question.root_id == root_cat.id,
                    Question.id.in_(
                        select(QuestionCategory.question_id).where(QuestionCategory.category_id.in_(branch_cats))
                    ),
                )
            )
            m = re.search(r"\b(\d+)\b", root_cat.name)
            resolved["grade"] = str(int(m.group(1))) if m else ""
            breadcrumb_parts.append(f"Lớp {resolved['grade']}")
            breadcrumb_parts.append(root_cat.name)

        elif grade and str(grade).isdigit():
            g_num = int(grade)
            g_roots = [
                c.id
                for c in all_categories
                if c.parent_id is None
                and is_curriculum_root_name(c.name)
                and (m := re.search(r"\b(\d+)\b", c.name))
                and int(m.group(1)) == g_num  # noqa: E501
            ]
            conditions.append(Question.root_id.in_(g_roots))
            breadcrumb_parts.append(f"Lớp {g_num}")

        else:
            curr_roots = [c.id for c in all_categories if c.parent_id is None and is_curriculum_root_name(c.name)]
            conditions.append(Question.root_id.in_(curr_roots))

    # 2. MAJOR EXAMS
    elif section == "major_exam":
        breadcrumb_parts.append("Phần 2: Kỳ thi quan trọng")
        if target:
            t_scope = await resolve_scope(session, f"target:{target}")
            if t_scope:
                conditions += t_scope.conditions(Question)
                breadcrumb_parts.append(t_scope.name)
        else:
            sec_scope = SectionScope("major_exam")
            conditions += sec_scope.conditions(Question)

        if exam_subject:
            subj_kw = {
                "TOAN": ("toán", "math"),
                "VAT_LI": ("vật lí", "vật lý", "physics"),
                "HOA_HOC": ("hoá", "hóa", "chemistry"),
                "SINH_HOC": ("sinh", "biology"),
                "TIENG_ANH": ("tiếng anh", "english"),
                "NGU_VAN": ("văn", "ngữ văn", "literature"),
                "LICH_SU": ("sử", "lịch sử", "history"),
                "DIA_LI": ("địa", "địa lí", "geography"),
                "KHTN": ("khoa học tự nhiên", "khtn"),
            }.get(exam_subject.upper(), ())
            if subj_kw:
                match_roots = [
                    c.id for c in all_categories if c.parent_id is None and any(k in c.name.lower() for k in subj_kw)
                ]
                match_ku = select(KnowledgeUnit.id).where(
                    or_(*(KnowledgeUnit.subject.ilike(f"%{k}%") for k in subj_kw))
                )
                conditions.append(
                    or_(
                        Question.root_id.in_(match_roots),
                        Question.knowledge_unit_id.in_(match_ku),
                    )
                )
                breadcrumb_parts.append(exam_subject.upper())

    # 3. OLYMPIAD & HSG
    elif section == "olympiad":
        breadcrumb_parts.append("Phần 3: Bồi dưỡng HSG")
        if target:
            t_scope = await resolve_scope(session, f"target:{target}")
            if t_scope:
                conditions += t_scope.conditions(Question)
                breadcrumb_parts.append(t_scope.name)
        else:
            sec_scope = SectionScope("olympiad")
            conditions += sec_scope.conditions(Question)

        if exam_subject:
            subj_kw = {
                "TOAN": ("toán", "math"),
                "VAT_LI": ("vật lí", "vật lý", "physics"),
                "HOA_HOC": ("hoá", "hóa", "chemistry"),
                "SINH_HOC": ("sinh", "biology"),
                "TIN_HOC": ("tin", "tin học", "informatics"),
                "TIENG_ANH": ("tiếng anh", "english"),
                "NGU_VAN": ("văn", "ngữ văn", "literature"),
                "LICH_SU": ("sử", "lịch sử", "history"),
                "DIA_LI": ("địa", "địa lí", "geography"),
            }.get(exam_subject.upper(), ())
            if subj_kw:
                match_roots = [c.id for c in all_categories if any(k in c.name.lower() for k in subj_kw)]
                match_ku = select(KnowledgeUnit.id).where(
                    or_(*(KnowledgeUnit.subject.ilike(f"%{k}%") for k in subj_kw))
                )
                conditions.append(
                    or_(
                        Question.category_id.in_(match_roots),
                        Question.root_id.in_(match_roots),
                        Question.knowledge_unit_id.in_(match_ku),
                    )
                )
                breadcrumb_parts.append(exam_subject.upper())

    else:
        breadcrumb_parts.append("Tất cả ngân hàng câu hỏi")

    breadcrumb_str = " › ".join(breadcrumb_parts)
    return conditions, breadcrumb_str, resolved
