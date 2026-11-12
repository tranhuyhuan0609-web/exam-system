"""Reclassify all 16,326 questions in the central question bank:
1. Classify cognitive difficulty (`RECOGNITION`, `UNDERSTANDING`, `APPLICATION`, `ADVANCED_APPLICATION`, `HSG`).
2. Anchor each question to its SGK Kết nối tri thức knowledge unit (`knowledge_unit_id`).
3. Detect thinking skills (`is_thinking_skill = True` for TSA/HSA).
4. Set access tier (`PRO` for HSG, `FREE` for standard curriculum).
5. Populate `question_target_tags` connecting to the 7 ExamTargets.
"""

import asyncio
import re
import unicodedata
from collections import Counter
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.knowledge_linker import (
    SUBJECT_CODE_MAP,
    infer_knowledge_metadata,
    seed_knowledge_architecture,
)
from app.db import SessionLocal, engine
from app.domain.difficulty import classify_difficulty
from app.models import (
    AccessTier,
    Category,
    Difficulty,
    ExamTarget,
    KnowledgeUnit,
    Question,
    QuestionTargetTag,
)


def _detect_subject_and_grade(
    root: Category | None, cat: Category | None, content: str
) -> tuple[str, int]:
    """Detect subject (PHYSICS, MATH, CHEMISTRY, BIOLOGY, ENGLISH, etc.) and grade (9, 10, 11, 12)."""
    text = f"{root.name if root else ''} {root.slug if root else ''} {cat.name if cat else ''}".lower()

    grade = 12
    m_grade = re.search(r"\b(?:lớp\s*|toán\s*|lí\s*|hóa\s*|sinh\s*|tiếng anh\s*|địa\s*|sử\s*|tin\s*)(\d+)\b", text)
    if m_grade:
        g = int(m_grade.group(1))
        if g in (9, 10, 11, 12):
            grade = g
    elif "vào 10" in text or "tuyển sinh 10" in text:
        grade = 9
    elif "thpt" in text:
        grade = 12

    # Subject detection
    subject = "PHYSICS"
    if any(w in text for w in ("toán", "math", "giải tích", "hình học", "đại số")):
        subject = "MATH"
    elif any(w in text for w in ("vật lí", "vật lý", "physics", "dao động", "sóng cơ")):
        subject = "PHYSICS"
    elif any(w in text for w in ("hóa", "hoá", "chemistry", "este", "amin", "kim loại", "peptit")):
        subject = "CHEMISTRY"
    elif any(w in text for w in ("sinh", "biology", "di truyền", "tế bào", "sinh thái")):
        subject = "BIOLOGY"
    elif any(w in text for w in ("tiếng anh", "english", "ngoại ngữ")):
        subject = "ENGLISH"
    elif any(w in text for w in ("lịch sử", "history", "sử")):
        subject = "HISTORY"
    elif any(w in text for w in ("địa lí", "địa lý", "geography")):
        subject = "GEOGRAPHY"
    elif any(w in text for w in ("tin học", "informatics")):
        subject = "INFORMATICS"
    else:
        # Infer from question content
        c_low = content.lower()
        if any(w in c_low for w in ("kim loại", "axit", "ancol", "este", "este", "phản ứng", "dung dịch", "mol/l", "fe", "cu", "al", "h2so4", "naoh")):
            subject = "CHEMISTRY"
        elif any(w in c_low for w in ("dao động", "con lắc", "chu kì", "bước sóng", "tần số", "điện áp", "điện trở", "quang phổ", "tụ điện")):
            subject = "PHYSICS"
        elif any(w in c_low for w in ("hàm số", "nguyên hàm", "tích phân", "đồng biến", "nghịch biến", "tiệm cận", "đồ thị", "phương trình")):
            subject = "MATH"
        elif any(w in c_low for w in ("gen", "nhiễm sắc thể", "adn", "arn", "đột biến", "quần thể")):
            subject = "BIOLOGY"
        elif any(w in c_low for w in ("verb", "tense", "grammar", "synonym", "antonym", "the", "and", "is", "are")):
            subject = "ENGLISH"

    return subject, grade


async def run_reclassification():
    print("=" * 70)
    print("BẮT ĐẦU PHÂN LOẠI LẠI TOÀN BỘ NGÂN HÀNG CÂU HỎI (16,326 CÂU)")
    print(f"Thời gian: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    async with SessionLocal() as session:
        # 1. Ensure Knowledge Architecture & Exam Targets are seeded
        print("-> Đang kiểm tra và đồng bộ cây mục tiêu kỳ thi và kiến thức SGK...")
        await seed_knowledge_architecture(session)
        await session.commit()

        # Load all ExamTargets
        targets_by_code = {
            t.code: t for t in (await session.scalars(select(ExamTarget))).all()
        }

        # Load all KnowledgeUnits
        all_units = (await session.scalars(select(KnowledgeUnit))).all()
        units_by_code = {u.code: u for u in all_units if u.code}
        # Group units by (subject, grade)
        units_by_subj_grade: dict[tuple[str, int], list[KnowledgeUnit]] = {}
        for u in all_units:
            units_by_subj_grade.setdefault((u.subject, u.grade), []).append(u)

        # Load categories
        cats = {c.id: c for c in (await session.scalars(select(Category))).all()}

        # 2. Query all questions
        total_q = await session.scalar(select(func.count()).select_from(Question))
        print(f"-> Tổng số câu hỏi cần phân loại: {total_q}")

        # Statistics before
        before_stats = dict(
            (await session.execute(
                select(Question.difficulty, func.count()).group_by(Question.difficulty)
            )).all()
        )
        print("-> Phân bố độ khó HIỆN TẠI:")
        for d, cnt in before_stats.items():
            print(f"   * {d}: {cnt} ({cnt / total_q * 100:.1f}%)")

        BATCH_SIZE = 1000
        offset = 0
        updated_diff = Counter()
        thinking_count = 0
        hsg_count = 0
        tags_inserted = 0

        while offset < total_q:
            chunk = (
                await session.scalars(
                    select(Question).order_by(Question.id).offset(offset).limit(BATCH_SIZE)
                )
            ).all()
            if not chunk:
                break

            q_ids = [q.id for q in chunk]
            # Clear existing QuestionTargetTags for this batch to re-sync cleanly
            await session.execute(
                delete(QuestionTargetTag).where(QuestionTargetTag.question_id.in_(q_ids))
            )

            new_tags: list[QuestionTargetTag] = []

            for q in chunk:
                root = cats.get(q.root_id)
                cat = cats.get(q.category_id)
                is_hsg_source = bool(
                    (root and "hsg" in root.name.lower())
                    or (cat and "hsg" in cat.name.lower())
                )

                content_norm = unicodedata.normalize("NFC", q.content or "").strip().lower()
                choices_list = [
                    str(c.get("content", "")) for c in (q.choices or [])
                ] if isinstance(q.choices, list) else None

                # 1. Cognitive Difficulty
                if is_hsg_source:
                    diff_str = "HSG"
                else:
                    diff_str = classify_difficulty(
                        q.content,
                        choices=choices_list,
                        question_type=q.question_type.value if q.question_type else "",
                    )
                new_difficulty = Difficulty(diff_str)
                q.difficulty = new_difficulty
                updated_diff[new_difficulty.value] += 1

                # 2. Thinking Skill
                has_diagram = (
                    "đồ thị" in content_norm
                    or "thí nghiệm" in content_norm
                    or "bảng số liệu" in content_norm
                    or "bảng biến thiên" in content_norm
                    or "tsa" in content_norm
                    or "hsa" in content_norm
                    or bool(q.image_path)
                )
                is_thinking = has_diagram and new_difficulty in (
                    Difficulty.APPLICATION,
                    Difficulty.ADVANCED_APPLICATION,
                )
                q.is_thinking_skill = is_thinking
                if is_thinking:
                    thinking_count += 1

                # 3. Access Tier
                if new_difficulty == Difficulty.HSG or is_hsg_source:
                    q.access_tier = AccessTier.PRO
                    hsg_count += 1
                else:
                    q.access_tier = AccessTier.FREE

                # 4. Detect Subject & Grade and Anchor to KnowledgeUnit
                subj, grade = _detect_subject_and_grade(root, cat, q.content)
                meta = infer_knowledge_metadata(q.content, new_difficulty, grade=grade, subject=subj)

                target_unit = None
                if meta.get("kntt_unit_code"):
                    target_unit = units_by_code.get(meta["kntt_unit_code"])
                if target_unit is None:
                    # Match by (subject, grade)
                    avail = units_by_subj_grade.get((subj, grade))
                    if not avail:
                        # Fallback to Math or Physics 12
                        avail = units_by_subj_grade.get(("PHYSICS", 12)) or all_units
                    if avail:
                        target_unit = avail[0]

                if target_unit is not None:
                    q.knowledge_unit_id = target_unit.id

                # 5. Populate QuestionTargetTags
                suitable = ["SGK_KNTT"]
                if new_difficulty == Difficulty.HSG:
                    suitable = ["HSG_TINH", "HSG_QG"]
                else:
                    if grade in (11, 12):
                        suitable.append("THPTQG")
                    elif grade == 9:
                        suitable.append("VAO_10")
                    if is_thinking and grade in (10, 11, 12):
                        suitable.append("DGTD_TSA")
                        suitable.append("DGNL_HSA")

                for t_code in suitable:
                    target_obj = targets_by_code.get(t_code)
                    if target_obj:
                        new_tags.append(
                            QuestionTargetTag(question_id=q.id, target_id=target_obj.id)
                        )

            if new_tags:
                session.add_all(new_tags)
                tags_inserted += len(new_tags)

            await session.commit()
            offset += len(chunk)
            print(f"   -> Đã xử lý {offset}/{total_q} câu hỏi ({(offset / total_q) * 100:.1f}%)...")

        print("=" * 70)
        print("HOÀN TẤT PHÂN LOẠI LẠI TOÀN BỘ NGÂN HÀNG CÂU HỎI!")
        print("=" * 70)
        print("KẾT QUẢ PHÂN BỐ MỚI:")
        for d, cnt in updated_diff.most_common():
            pct = cnt / total_q * 100
            print(f"  - {d:20s}: {cnt:5d} câu ({pct:5.1f}%)")
        print(f"  - Đánh giá tư duy (TSA/HSA) : {thinking_count:5d} câu")
        print(f"  - Học sinh giỏi / Chuyên [PRO]: {hsg_count:5d} câu")
        print(f"  - Liên kết kỳ thi (tags)      : {tags_inserted:5d} tags")
        print("=" * 70)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_reclassification())

