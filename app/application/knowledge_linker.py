"""Single-Source-of-Truth Multi-dimensional Knowledge Unit (SGK Kết nối tri thức)
& Exam Target (THPTQG, ĐGTD TSA, ĐGNL HSA, Vào 10, HSG Tỉnh / Quốc gia) Linking Engine.

Two parallel linking mechanisms operate on a single `questions` table without duplicating rows:
1. Full Curriculum Inheritance (`TargetCurriculumMapping`):
   - Questions anchored to a Grade 12 (or core Grade 11) `KnowledgeUnit` with difficulty in
     `[RECOGNITION, UNDERSTANDING, APPLICATION, ADVANCED_APPLICATION]` (`NB, TH, VD, VDC`)
     automatically appear in `SGK_KNTT` and `THPTQG` (while `HSG` questions are excluded from `THPTQG`).
   - Questions anchored to a Grade 9 `KnowledgeUnit` automatically appear in `VAO_10`.
2. Selective Intersection (`QuestionTargetTag`, `is_thinking_skill`, `Difficulty.HSG`):
   - `DGTD_TSA` / `DGNL_HSA`: selects questions tagged in `question_target_tags` OR with `is_thinking_skill=True`.
   - `HSG_TINH` / `HSG_QG`: selects questions tagged in `question_target_tags` OR with `difficulty = Difficulty.HSG`.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.curriculum_data import CURRICULUM_DATA
from app.models import (
    AccessTier,
    Category,
    Difficulty,
    ExamTarget,
    KnowledgeUnit,
    Question,
    QuestionTargetTag,
    TargetCurriculumMapping,
    TargetGroupCategory,
)

# Short Vietnamese abbreviations <-> canonical Difficulty enum values
DIFFICULTY_SHORT_TO_ENUM: dict[str, Difficulty] = {
    "NB": Difficulty.RECOGNITION,
    "RECOGNITION": Difficulty.RECOGNITION,
    "TH": Difficulty.UNDERSTANDING,
    "UNDERSTANDING": Difficulty.UNDERSTANDING,
    "VD": Difficulty.APPLICATION,
    "APPLICATION": Difficulty.APPLICATION,
    "VDC": Difficulty.ADVANCED_APPLICATION,
    "ADVANCED_APPLICATION": Difficulty.ADVANCED_APPLICATION,
    "HSG": Difficulty.HSG,
}

STANDARD_CURRICULUM_DIFFICULTIES: list[str] = [
    Difficulty.RECOGNITION.value,
    Difficulty.UNDERSTANDING.value,
    Difficulty.APPLICATION.value,
    Difficulty.ADVANCED_APPLICATION.value,
]

ALL_DIFFICULTIES_WITH_HSG: list[str] = [
    *STANDARD_CURRICULUM_DIFFICULTIES,
    Difficulty.HSG.value,
]

SUBJECT_CODE_MAP: dict[str, tuple[str, str]] = {
    "vat-li": ("PHYSICS", "PHY"),
    "toan": ("MATH", "MATH"),
    "hoa-hoc": ("CHEMISTRY", "CHEM"),
    "sinh-hoc": ("BIOLOGY", "BIO"),
    "tieng-anh": ("ENGLISH", "ENG"),
    "ngu-van": ("LITERATURE", "LIT"),
    "lich-su": ("HISTORY", "HIST"),
    "dia-li": ("GEOGRAPHY", "GEO"),
    "tin-hoc": ("INFORMATICS", "INF"),
    "gdcd": ("CIVICS", "CIV"),
    "ktpl": ("CIVICS", "CIV"),
}

SUBJECT_LABELS_VI: dict[str, str] = {
    "PHYSICS": "Vật lí",
    "MATH": "Toán học",
    "CHEMISTRY": "Hóa học",
    "BIOLOGY": "Sinh học",
    "ENGLISH": "Tiếng Anh",
    "LITERATURE": "Ngữ văn",
    "HISTORY": "Lịch sử",
    "GEOGRAPHY": "Địa lí",
    "INFORMATICS": "Tin học",
    "CIVICS": "GD Kinh tế & Pháp luật",
}

EXAM_TARGET_DEFS: list[dict[str, Any]] = [
    {
        "code": "SGK_KNTT",
        "group_category": TargetGroupCategory.CURRICULUM,
        "name": "Bám sát Chương trình SGK (Lớp 1 – 12 Kết nối tri thức)",
        "access_tier": AccessTier.FREE,
    },
    {
        "code": "THPTQG",
        "group_category": TargetGroupCategory.MAJOR_EXAM,
        "name": "Kỳ thi Tốt nghiệp THPT Quốc gia",
        "access_tier": AccessTier.FREE,
    },
    {
        "code": "DGTD_TSA",
        "group_category": TargetGroupCategory.MAJOR_EXAM,
        "name": "Kỳ thi Đánh giá tư duy (ĐHBK Hà Nội - TSA)",
        "access_tier": AccessTier.FREE,
    },
    {
        "code": "DGNL_HSA",
        "group_category": TargetGroupCategory.MAJOR_EXAM,
        "name": "Kỳ thi Đánh giá năng lực (ĐHQG Hà Nội - HSA)",
        "access_tier": AccessTier.FREE,
    },
    {
        "code": "DGNL_VNUHCM",
        "group_category": TargetGroupCategory.MAJOR_EXAM,
        "name": "Kỳ thi Đánh giá năng lực (ĐHQG TP.HCM)",
        "access_tier": AccessTier.FREE,
    },
    {
        "code": "VAO_10",
        "group_category": TargetGroupCategory.MAJOR_EXAM,
        "name": "Tuyển sinh vào Lớp 10 THPT Công lập",
        "access_tier": AccessTier.FREE,
    },
    {
        "code": "VAO_10_CHUYEN",
        "group_category": TargetGroupCategory.MAJOR_EXAM,
        "name": "Kỳ thi Tuyển sinh vào Lớp 10 Chuyên",
        "access_tier": AccessTier.FREE,
    },
    {
        "code": "DGNL_BCA",
        "group_category": TargetGroupCategory.MAJOR_EXAM,
        "name": "Kỳ thi Đánh giá năng lực Bộ Công An",
        "access_tier": AccessTier.FREE,
    },
    {
        "code": "DGNL_HNUE",
        "group_category": TargetGroupCategory.MAJOR_EXAM,
        "name": "Kỳ thi Đánh giá năng lực ĐH Sư Phạm",
        "access_tier": AccessTier.FREE,
    },
    {
        "code": "HSG_TINH",
        "group_category": TargetGroupCategory.OLYMPIAD,
        "name": "Bồi dưỡng Học sinh giỏi Tỉnh / Thành phố",
        "access_tier": AccessTier.PRO,
    },
    {
        "code": "HSG_QG",
        "group_category": TargetGroupCategory.OLYMPIAD,
        "name": "Bồi dưỡng Học sinh giỏi Quốc gia & Chọn đội tuyển",
        "access_tier": AccessTier.PRO,
    },
    {
        "code": "OLYMPIAD_CHUYEN",
        "group_category": TargetGroupCategory.OLYMPIAD,
        "name": "Chuyên đề Bồi dưỡng Chuyên sâu & Olympic",
        "access_tier": AccessTier.PRO,
    },
]

_THINKING_SKILL_RE = re.compile(
    r"\b(đánh giá tư duy|tư duy khoa học|mô hình hóa|thí nghiệm|bảng số liệu|đồ thị biểu diễn|"
    r"phân tích dữ liệu|suy luận|TSA|HSA|đọc hiểu khoa học|nghiên cứu trường hợp)\b",
    re.I,
)

_HSG_KEYWORDS_RE = re.compile(
    r"\b(học sinh giỏi|olympic|chuyên|quốc gia|bất đẳng thức jensen|bất đẳng thức karamata|"
    r"phương trình hàm|hệ vật cơ học|vật rắn quay|lưỡng chất cầu|vi phân|tích phân đường|"
    r"cơ học lượng tử|tương đối tính|cực trị hình học không gian|số học olympic)\b",
    re.I,
)


def normalize_difficulty_list(values: list[str] | None) -> list[Difficulty]:
    """Convert a list of difficulty codes (either ['NB','TH','VD','VDC'] or enum names) to Difficulty enums."""
    if not values:
        return [Difficulty(d) for d in STANDARD_CURRICULUM_DIFFICULTIES]
    result: list[Difficulty] = []
    for v in values:
        key = (v or "").strip().upper()
        mapped = DIFFICULTY_SHORT_TO_ENUM.get(key)
        if mapped and mapped not in result:
            result.append(mapped)
    return result or [Difficulty(d) for d in STANDARD_CURRICULUM_DIFFICULTIES]


def normalize_difficulty_value(value: str | Difficulty | None, default: Difficulty = Difficulty.UNDERSTANDING) -> Difficulty:
    if isinstance(value, Difficulty):
        return value
    if not value:
        return default
    return DIFFICULTY_SHORT_TO_ENUM.get(value.strip().upper(), default)


def _subject_from_curriculum_key(curr_key: str) -> tuple[str, str, int] | None:
    """Parse keys like 'lop-12_vat-li-lop-12' into ('PHYSICS', 'PHY', 12)."""
    m = re.match(r"^lop-(\d+)_([a-z0-9-]+?)(?:-lop-\d+)?$", curr_key)
    if not m:
        return None
    grade = int(m.group(1))
    if grade not in (9, 10, 11, 12):
        return None
    slug_part = m.group(2)
    for prefix, (subj_name, subj_short) in SUBJECT_CODE_MAP.items():
        if slug_part.startswith(prefix):
            return subj_name, subj_short, grade
    return None


def infer_knowledge_metadata(
    content: str,
    difficulty: str | Difficulty | None = None,
    *,
    grade: int | None = 12,
    subject: str | None = None,
) -> dict[str, Any]:
    """Infer multi-dimensional classification (`difficulty`, `is_thinking_skill`, `suitable_targets`, `kntt_unit_code`, `topic_name`)
    from question text and difficulty."""
    text = unicodedata.normalize("NFC", content or "").strip()
    norm = text.lower()

    resolved_diff = normalize_difficulty_value(difficulty, Difficulty.UNDERSTANDING)
    if _HSG_KEYWORDS_RE.search(norm):
        resolved_diff = Difficulty.HSG

    is_thinking = bool(_THINKING_SKILL_RE.search(norm)) or (
        resolved_diff in (Difficulty.APPLICATION, Difficulty.ADVANCED_APPLICATION, Difficulty.HSG)
        and ("đồ thị" in norm or "thí nghiệm" in norm or "bảng" in norm or "mô hình" in norm)
    )

    eff_grade = grade or 12
    targets: list[str] = ["SGK_KNTT"]

    # Phân loại đa kỳ thi: một câu hỏi có thể phục vụ nhiều mục tiêu cùng lúc
    if resolved_diff in (Difficulty.ADVANCED_APPLICATION, Difficulty.HSG):
        # Câu vận dụng cao hoặc HSG: dùng cho ôn thi HSG, câu phân hóa 9-10 THPTQG và Đánh giá tư duy
        targets.extend(["THPTQG", "HSG_TINH", "DGTD_TSA", "DGNL_HSA"])
        if _HSG_KEYWORDS_RE.search(norm) or "quốc gia" in norm or "olympic" in norm:
            targets.append("HSG_QG")
    elif resolved_diff == Difficulty.APPLICATION:
        targets.append("THPTQG")
        if is_thinking or eff_grade in (10, 11, 12):
            targets.append("DGTD_TSA")
            targets.append("DGNL_HSA")
        if "nâng cao" in norm or "chuyên" in norm:
            targets.append("HSG_TINH")
    else:
        # Nhận biết, thông hiểu
        if eff_grade in (11, 12):
            targets.append("THPTQG")
        elif eff_grade == 9:
            targets.append("VAO_10")
        if is_thinking:
            targets.append("DGTD_TSA")

    if eff_grade == 9 and "VAO_10" not in targets:
        targets.append("VAO_10")
        if resolved_diff in (Difficulty.ADVANCED_APPLICATION, Difficulty.HSG):
            targets.append("VAO_10_CHUYEN")

    # Loại bỏ trùng lặp giữ nguyên thứ tự
    targets = list(dict.fromkeys(targets))

    # Nhận diện chuyên đề (topic_name) và mã bài học SGK (kntt_unit_code)
    unit_code: str | None = None
    topic_name: str | None = None

    if any(w in norm for w in ("dao động điều hòa", "con lắc lò xo", "con lắc đơn", "chu kì dao động", "biên độ", "độ cứng k", "pha dao động")):
        unit_code = "PHY11-KNTT-C1-B1"
        topic_name = "Dao động điều hòa"
    elif any(w in norm for w in ("sóng cơ", "giao thoa sóng", "sóng dừng", "bước sóng", "sóng âm", "mức cường độ âm")):
        unit_code = "PHY11-KNTT-C2-B1"
        topic_name = "Sóng cơ & Sóng âm"
    elif any(w in norm for w in ("nhiệt độ", "nội năng", "nhiệt lượng", "nhiệt dung riêng", "chuyển thể", "khí lí tưởng", "đẳng nhiệt", "đẳng áp", "đẳng tích", "áp suất khí", "piston")):
        unit_code = "PHY12-KNTT-C2-B1"
        topic_name = "Nhiệt học & Khí lí tưởng"
    elif any(w in norm for w in ("vật rắn", "khớp nối", "thanh mảnh", "mô men", "mômen", "trục quay", "giàn thanh", "hệ cơ")):
        unit_code = f"PHY{eff_grade}-KNTT-C1-B3" if eff_grade in (10, 11) else "PHY10-KNTT-C4-B1"
        topic_name = "Cơ học vật rắn"
    elif any(w in norm for w in ("cơ học chất điểm", "ném xiên", "ném ngang", "lực cản", "độ cao h", "vận tốc ban đầu", "stokes")):
        unit_code = "PHY10-KNTT-C3-B1"
        topic_name = "Cơ học chất điểm"
    elif any(w in norm for w in ("sao chổi", "thiên thể", "hấp dẫn", "mặt trời", "parabol", "quỹ đạo", "cận nhật")):
        unit_code = "PHY10-KNTT-C3-B4"
        topic_name = "Lực hấp dẫn & Thiên thể"
    elif any(w in norm for w in ("từ trường", "cảm ứng từ", "từ thông", "lực từ", "dòng điện cảm ứng")):
        unit_code = "PHY12-KNTT-C3-B1"
        topic_name = "Từ trường & Cảm ứng điện từ"
    elif any(w in norm for w in ("hạt nhân", "phóng xạ", "độ hụt khối", "năng lượng liên kết", "phân hạch")):
        unit_code = "PHY12-KNTT-C4-B1"
        topic_name = "Vật lí hạt nhân"
    elif any(w in norm for w in ("quang hợp", "hô hấp tế bào", "sắc tố quang hợp", "chu trình canvin")):
        unit_code = "BIO11-KNTT-C1-B1"
        topic_name = "Quang hợp & Hô hấp"
    elif any(w in norm for w in ("adn", "arn", "gen", "nhiễm sắc thể", "đột biến", "phiên mã", "dịch mã", "menđen")):
        unit_code = "BIO12-KNTT-C1-B1"
        topic_name = "Di truyền học"
    elif any(w in norm for w in ("hệ sinh thái", "quần xã", "quần thể", "chuỗi thức ăn", "lưới thức ăn", "sinh quyển")):
        unit_code = "BIO12-KNTT-C3-B1"
        topic_name = "Sinh thái học"
    elif any(w in norm for w in ("đồng biến", "nghịch biến", "cực trị của hàm số", "tiệm cận", "đạo hàm", "bảng biến thiên")):
        unit_code = "MATH12-KNTT-C1-B1"
        topic_name = "Ứng dụng đạo hàm & Khảo sát hàm số"
    elif any(w in norm for w in ("nguyên hàm", "tích phân", "diện tích hình phẳng")):
        unit_code = "MATH12-KNTT-C4-B1"
        topic_name = "Nguyên hàm & Tích phân"
    elif any(w in norm for w in ("hình chóp", "lăng trụ", "khoảng cách trong không gian", "góc giữa hai mặt phẳng")):
        unit_code = "MATH11-KNTT-C4-B1"
        topic_name = "Hình học không gian"

    return {
        "difficulty": resolved_diff.value,
        "is_thinking_skill": is_thinking,
        "suitable_targets": targets,
        "kntt_unit_code": unit_code,
        "topic_name": topic_name,
    }


async def seed_knowledge_architecture(session: AsyncSession) -> dict[str, int]:
    """Idempotently seed `exam_targets`, `knowledge_units` (from CURRICULUM_DATA for Grades 9-12),
    and `target_curriculum_mappings` (connecting SGK_KNTT, THPTQG, and VAO_10 to KnowledgeUnits)."""
    # 1. Upsert ExamTargets
    existing_targets = {
        t.code: t for t in (await session.scalars(select(ExamTarget))).all()
    }
    for tdef in EXAM_TARGET_DEFS:
        target = existing_targets.get(tdef["code"])
        if target is None:
            target = ExamTarget(
                code=tdef["code"],
                group_category=tdef["group_category"],
                name=tdef["name"],
                access_tier=tdef["access_tier"],
            )
            session.add(target)
            existing_targets[tdef["code"]] = target
        else:
            target.group_category = tdef["group_category"]
            target.name = tdef["name"]
            target.access_tier = tdef["access_tier"]
    await session.flush()

    # 2. Load existing KnowledgeUnits & Categories
    existing_units = {
        u.code: u for u in (await session.scalars(select(KnowledgeUnit))).all() if u.code
    }
    categories = (await session.scalars(select(Category))).all()
    cat_by_slug = {c.slug: c for c in categories if c.slug}

    # 3. Seed KnowledgeUnits from CURRICULUM_DATA for Grades 9..12
    created_units: list[KnowledgeUnit] = []
    for curr_key, info in CURRICULUM_DATA.items():
        parsed = _subject_from_curriculum_key(curr_key)
        if not parsed:
            continue
        subject_code, subj_short, grade = parsed
        chapters = info.get("chapters", [])
        for c_idx, ch in enumerate(chapters, start=1):
            ch_id_raw = str(ch.get("id", f"c{c_idx}")).upper()
            ch_code = f"{subj_short}{grade}-KNTT-{ch_id_raw}"
            ch_unit = existing_units.get(ch_code)
            matched_cat = cat_by_slug.get(curr_key) or cat_by_slug.get(f"{curr_key}-{ch_id_raw.lower()}")
            if ch_unit is None:
                ch_unit = KnowledgeUnit(
                    parent_id=None,
                    category_id=matched_cat.id if matched_cat else None,
                    subject=subject_code,
                    grade=grade,
                    unit_type="CHAPTER",
                    code=ch_code,
                    name=ch.get("title", f"Chương {c_idx}"),
                    sort_order=c_idx,
                )
                session.add(ch_unit)
                await session.flush()
                existing_units[ch_code] = ch_unit
                created_units.append(ch_unit)

            for l_idx, lesson in enumerate(ch.get("lessons", []), start=1):
                if lesson.get("is_exam"):
                    continue
                l_id_raw = str(lesson.get("id", f"b{l_idx}")).upper()
                l_code = f"{ch_code}-{l_id_raw}"
                l_unit = existing_units.get(l_code)
                if l_unit is None:
                    l_unit = KnowledgeUnit(
                        parent_id=ch_unit.id,
                        category_id=ch_unit.category_id,
                        subject=subject_code,
                        grade=grade,
                        unit_type="LESSON",
                        code=l_code,
                        name=lesson.get("title", f"Bài {l_idx}"),
                        sort_order=l_idx,
                    )
                    session.add(l_unit)
                    existing_units[l_code] = l_unit
                    created_units.append(l_unit)

    # Ensure canonical Physics 12 Oscillation lessons exist explicitly (e.g. PHY12-KNTT-C1-B2)
    canonical_extras = [
        ("PHYSICS", 12, "CHAPTER", "PHY12-KNTT-C1", None, "Chương 1: Dao động & Vật lí nhiệt", 1),
        ("PHYSICS", 12, "LESSON", "PHY12-KNTT-C1-B1", "PHY12-KNTT-C1", "Bài 1: Dao động điều hòa & Sự chuyển thể", 1),
        ("PHYSICS", 12, "LESSON", "PHY12-KNTT-C1-B2", "PHY12-KNTT-C1", "Bài 2: Phương trình dao động điều hòa & Con lắc lò xo", 2),
        ("MATH", 12, "CHAPTER", "MATH12-KNTT-C1", None, "Chương 1: Ứng dụng đạo hàm để khảo sát và vẽ đồ thị hàm số", 1),
        ("MATH", 12, "LESSON", "MATH12-KNTT-C1-B1", "MATH12-KNTT-C1", "Bài 1: Tính đơn điệu và cực trị của hàm số", 1),
    ]
    await session.flush()
    for subj, grd, utype, code, parent_code, name, s_order in canonical_extras:
        if code not in existing_units:
            p_unit = existing_units.get(parent_code) if parent_code else None
            u = KnowledgeUnit(
                parent_id=p_unit.id if p_unit else None,
                subject=subj,
                grade=grd,
                unit_type=utype,
                code=code,
                name=name,
                sort_order=s_order,
            )
            session.add(u)
            await session.flush()
            existing_units[code] = u

    await session.flush()

    # 4. Seed TargetCurriculumMappings (Mechanism 1: Automatic Curriculum Inheritance)
    existing_mappings = {
        (m.target_id, m.knowledge_unit_id): m
        for m in (await session.scalars(select(TargetCurriculumMapping))).all()
    }
    sgk_target = existing_targets["SGK_KNTT"]
    thptqg_target = existing_targets["THPTQG"]
    vao10_target = existing_targets["VAO_10"]

    mappings_added = 0
    for unit in existing_units.values():
        # Every SGK unit maps to SGK_KNTT (NB, TH, VD, VDC)
        if (sgk_target.id, unit.id) not in existing_mappings:
            session.add(
                TargetCurriculumMapping(
                    target_id=sgk_target.id,
                    knowledge_unit_id=unit.id,
                    allowed_difficulties=list(STANDARD_CURRICULUM_DIFFICULTIES),
                )
            )
            mappings_added += 1

        # Grade 12 (100%) and Grade 11 (core chapters C1, C2) automatically inherit into THPTQG (excluding HSG)
        is_grade11_core = unit.grade == 11 and bool(
            unit.code and ("-C1" in unit.code or "-C2" in unit.code)
        )
        if unit.grade == 12 or is_grade11_core:
            if (thptqg_target.id, unit.id) not in existing_mappings:
                session.add(
                    TargetCurriculumMapping(
                        target_id=thptqg_target.id,
                        knowledge_unit_id=unit.id,
                        allowed_difficulties=list(STANDARD_CURRICULUM_DIFFICULTIES),
                    )
                )
                mappings_added += 1

        # Grade 9 automatically inherits into VAO_10 (excluding HSG)
        if unit.grade == 9:
            if (vao10_target.id, unit.id) not in existing_mappings:
                session.add(
                    TargetCurriculumMapping(
                        target_id=vao10_target.id,
                        knowledge_unit_id=unit.id,
                        allowed_difficulties=list(STANDARD_CURRICULUM_DIFFICULTIES),
                    )
                )
                mappings_added += 1

    await session.flush()

    # 5. Backfill unlinked questions in the bank to a default KnowledgeUnit based on content/category
    unlinked_questions = (
        await session.scalars(select(Question).where(Question.knowledge_unit_id.is_(None)))
    ).all()
    default_phy12 = existing_units.get("PHY12-KNTT-C1-B2") or existing_units.get("PHY12-KNTT-C1")
    default_math12 = existing_units.get("MATH12-KNTT-C1-B1") or existing_units.get("MATH12-KNTT-C1")
    for q in unlinked_questions:
        meta = infer_knowledge_metadata(q.content, q.difficulty)
        code = meta.get("kntt_unit_code")
        unit = existing_units.get(code) if code else None
        if unit is None:
            unit = default_phy12 or default_math12
        if unit is not None:
            q.knowledge_unit_id = unit.id
        if meta.get("is_thinking_skill") and not q.is_thinking_skill:
            q.is_thinking_skill = True

    await session.flush()
    return {
        "targets": len(existing_targets),
        "knowledge_units": len(existing_units),
        "mappings_added": mappings_added,
    }


async def resolve_knowledge_unit(
    session: AsyncSession,
    *,
    code: str | None = None,
    category_id: int | None = None,
    content: str | None = None,
) -> KnowledgeUnit | None:
    """Resolve a KnowledgeUnit by explicit `code`, `category_id`, or heuristic from `content`."""
    if code:
        unit = await session.scalar(select(KnowledgeUnit).where(KnowledgeUnit.code == code.strip().upper()))
        if unit is not None:
            return unit
    if category_id:
        unit = await session.scalar(
            select(KnowledgeUnit)
            .where(KnowledgeUnit.category_id == category_id)
            .order_by(KnowledgeUnit.id)
        )
        if unit is not None:
            return unit
    if content:
        meta = infer_knowledge_metadata(content)
        inferred_code = meta.get("kntt_unit_code")
        if inferred_code:
            unit = await session.scalar(select(KnowledgeUnit).where(KnowledgeUnit.code == inferred_code))
            if unit is not None:
                return unit
    return await session.scalar(select(KnowledgeUnit).order_by(KnowledgeUnit.id))


async def sync_question_targets(
    session: AsyncSession,
    question: Question,
    suitable_targets: list[str] | None,
) -> list[str]:
    """Attach `QuestionTargetTag` rows for the given target codes idempotently."""
    if not suitable_targets or not question.id:
        return []
    norm_codes = [c.strip().upper() for c in suitable_targets if c and c.strip()]
    if not norm_codes:
        return []
    targets = (
        await session.scalars(select(ExamTarget).where(ExamTarget.code.in_(norm_codes)))
    ).all()
    if not targets:
        return []
    existing_target_ids = set(
        (
            await session.scalars(
                select(QuestionTargetTag.target_id).where(QuestionTargetTag.question_id == question.id)
            )
        ).all()
    )
    attached: list[str] = []
    for t in targets:
        if t.id not in existing_target_ids:
            session.add(QuestionTargetTag(question_id=question.id, target_id=t.id))
            existing_target_ids.add(t.id)
        attached.append(t.code)
    await session.flush()
    return attached


async def _expand_unit_ids(session: AsyncSession, root_unit_id: int) -> list[int]:
    """Return `[root_unit_id, *child_ids, *grandchild_ids]` so filtering by a Chapter includes its Lessons."""
    children = (
        await session.scalars(select(KnowledgeUnit.id).where(KnowledgeUnit.parent_id == root_unit_id))
    ).all()
    if not children:
        return [root_unit_id]
    grandchildren = (
        await session.scalars(select(KnowledgeUnit.id).where(KnowledgeUnit.parent_id.in_(children)))
    ).all()
    return [root_unit_id, *children, *grandchildren]


async def questions_for_target(
    session: AsyncSession,
    target_code: str,
    *,
    subject: str | None = None,
    grade: int | None = None,
    knowledge_unit_id: int | None = None,
    knowledge_unit_code: str | None = None,
    difficulty: Difficulty | str | None = None,
    only_verified: bool = False,
    limit: int | None = None,
) -> list[Question]:
    """Single-Source-of-Truth query returning all `Question` rows belonging to `target_code` via:
    - Mechanism 1: Automatic Curriculum Inheritance (`TargetCurriculumMapping` + `allowed_difficulties`)
    - Mechanism 2: Selective Intersection (`QuestionTargetTag`, `is_thinking_skill`, or `Difficulty.HSG`)
    """
    code = target_code.strip().upper()
    target = await session.scalar(select(ExamTarget).where(ExamTarget.code == code))
    if target is None:
        await seed_knowledge_architecture(session)
        target = await session.scalar(select(ExamTarget).where(ExamTarget.code == code))
    if target is None:
        return []

    unit_filter_ids: list[int] | None = None
    if knowledge_unit_code and not knowledge_unit_id:
        u = await session.scalar(
            select(KnowledgeUnit).where(KnowledgeUnit.code == knowledge_unit_code.strip().upper())
        )
        if u is not None:
            knowledge_unit_id = u.id
    if knowledge_unit_id is not None:
        unit_filter_ids = await _expand_unit_ids(session, knowledge_unit_id)

    # 1. Mechanism 1: Curriculum Mappings for this target
    mappings = (
        await session.scalars(
            select(TargetCurriculumMapping).where(TargetCurriculumMapping.target_id == target.id)
        )
    ).all()

    clauses = []
    if mappings:
        # Group mapped unit_ids by their allowed_difficulties tuple
        by_diffs: dict[tuple[Difficulty, ...], list[int]] = {}
        for m in mappings:
            allowed = tuple(normalize_difficulty_list(m.allowed_difficulties))
            by_diffs.setdefault(allowed, []).append(m.knowledge_unit_id)
        for allowed_tuple, unit_ids in by_diffs.items():
            clauses.append(
                Question.knowledge_unit_id.in_(unit_ids) & Question.difficulty.in_(list(allowed_tuple))
            )

    # 2. Mechanism 2a: Explicit N-N tags in `question_target_tags`
    tagged_q_ids_subq = select(QuestionTargetTag.question_id).where(QuestionTargetTag.target_id == target.id)
    if code in ("THPTQG", "VAO_10", "SGK_KNTT"):
        # Exclude Olympiad HSG questions from standard curriculum / THPTQG / Vào 10 exams even if tagged broadly
        clauses.append(
            Question.id.in_(tagged_q_ids_subq) & Question.difficulty.in_(
                [Difficulty(d) for d in STANDARD_CURRICULUM_DIFFICULTIES]
            )
        )
    else:
        clauses.append(Question.id.in_(tagged_q_ids_subq))

    # 3. Mechanism 2b: Automatic intersection for TSA / HSA / VNUHCM / BCA / HNUE (`is_thinking_skill == True`)
    if code in ("DGTD_TSA", "DGNL_HSA", "DGNL_VNUHCM", "DGNL_BCA", "DGNL_HNUE"):
        clauses.append(
            Question.is_thinking_skill.is_(True)
            & Question.difficulty.in_([Difficulty(d) for d in STANDARD_CURRICULUM_DIFFICULTIES])
        )

    # 4. Mechanism 2c: Automatic intersection for Olympiad & HSG (HSG gồm câu VD, VDC và HSG)
    is_olympiad = target.group_category == TargetGroupCategory.OLYMPIAD or code in ("HSG_TINH", "HSG_QG", "OLYMPIAD_CHUYEN")
    if is_olympiad:
        clauses.append(
            Question.difficulty.in_([Difficulty.HSG, Difficulty.ADVANCED_APPLICATION])
            | (
                (Question.difficulty == Difficulty.APPLICATION)
                & (Question.is_thinking_skill.is_(True) | (Question.access_tier == AccessTier.PRO) | Question.id.in_(tagged_q_ids_subq))
            )
        )

    # 5. Mechanism 2d: Vào 10 Chuyên (lọc câu Lớp 9 mức VD, VDC, HSG)
    if code == "VAO_10_CHUYEN":
        clauses.append(
            Question.difficulty.in_([Difficulty.APPLICATION, Difficulty.ADVANCED_APPLICATION, Difficulty.HSG])
        )

    if not clauses:
        return []

    stmt: Select[tuple[Question]] = (
        select(Question)
        .outerjoin(KnowledgeUnit, Question.knowledge_unit_id == KnowledgeUnit.id)
        .where(or_(*clauses))
    )

    if subject:
        stmt = stmt.where(KnowledgeUnit.subject == subject.strip().upper())
    if grade is not None:
        stmt = stmt.where(KnowledgeUnit.grade == grade)
    if unit_filter_ids is not None:
        stmt = stmt.where(Question.knowledge_unit_id.in_(unit_filter_ids))
    if difficulty is not None:
        stmt = stmt.where(Question.difficulty == normalize_difficulty_value(difficulty))
    if only_verified:
        stmt = stmt.where(Question.is_verified.is_(True))

    stmt = stmt.order_by(Question.id.desc())
    if limit is not None:
        stmt = stmt.limit(limit)

    return list((await session.scalars(stmt)).all())


async def hsg_questions_for_unit(
    session: AsyncSession,
    *,
    knowledge_unit_id: int | None = None,
    knowledge_unit_code: str | None = None,
) -> list[Question]:
    """Return all Olympiad / HSG questions belonging to a SGK chapter/lesson (and its child lessons)."""
    return await questions_for_target(
        session,
        "HSG_TINH",
        knowledge_unit_id=knowledge_unit_id,
        knowledge_unit_code=knowledge_unit_code,
    )


async def target_question_counts(session: AsyncSession) -> dict[str, int]:
    """Return question counts for each of the 7 ExamTargets."""
    counts: dict[str, int] = {}
    for tdef in EXAM_TARGET_DEFS:
        qs = await questions_for_target(session, tdef["code"])
        counts[tdef["code"]] = len(qs)
    return counts

