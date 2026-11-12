import asyncio
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, or_, select

from app.ai.essay_exam import clean_symbol_pua
from app.db import SessionLocal
from app.domain.question_hash import hash_of_choices
from app.models import (
    Category,
    Difficulty,
    Question,
    QuestionCategory,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("audit_bank")

# Regex to detect un-enclosed LaTeX math
_BARE_LATEX_RE = re.compile(
    r"(?<!\$)(?<!\\)(\\(?:frac|sqrt|alpha|beta|gamma|delta|Delta|lambda|mu|pi|omega|Omega|le|ge|pm|times|rightarrow|rightleftharpoons|xrightleftharpoons|xrightarrow|text|approx|neq|cdot|partial|int|sum))\b(?![^$]*\$)",
    re.IGNORECASE,
)
_BROKEN_TABLE_RE = re.compile(r"\r?\n\s*\|\s*\r?\n")
_SQUARE_BOX_RE = re.compile(r"\\square|□")


def repair_text(text: str) -> tuple[str, bool]:
    """Clean font artifacts, PUA characters, broken tables, and unenclosed LaTeX formulas."""
    if not text:
        return text, False
    changed = False
    original = text

    # 1. Clean legacy Symbol font PUA characters & MathJax \dpi{...}
    cleaned = clean_symbol_pua(text)
    if cleaned != text:
        changed = True
        text = cleaned

    # 2. Fix broken table cell lines
    if _BROKEN_TABLE_RE.search(text):
        text = _BROKEN_TABLE_RE.sub(" | ", text)
        changed = True

    # 3. Replace broken square box symbols in chemistry/math equations
    if _SQUARE_BOX_RE.search(text):
        # In reversible chemical arrows: D \square \rightarrow -> D \rightleftharpoons
        text = re.sub(r"(?:\\square|□)\s*(?:\\rightarrow|->|→)", r"\\rightleftharpoons", text)
        text = _SQUARE_BOX_RE.sub(r"\\rightleftharpoons", text)
        changed = True

    # 4. Wrap bare LaTeX formulas that lack surrounding dollar signs ($)
    # E.g. "k_1 = 2,80.10.e^{-\frac{90300}{RT}}" or "\sqrt{x^2 + 1}"
    lines = text.splitlines(keepends=True)
    new_lines = []
    for line in lines:
        if "$" not in line and any(k in line for k in ("\\frac", "\\sqrt", "\\xrightarrow", "\\xrightleftharpoons")):
            # Wrap standalone math fragments
            repaired_line = re.sub(
                r"(\b[a-zA-Z0-9_+-]+(?:\s*[=><]\s*|\s+)\\(?:frac|sqrt|xrightarrow|xrightleftharpoons)\{[^{}]+\}(?:\{[^{}]+\})?)",
                r"$\1$",
                line,
            )
            if repaired_line != line:
                changed = True  # noqa: F841
                line = repaired_line
        new_lines.append(line)
    text = "".join(new_lines)

    return text, (text != original)


def repair_choices(choices: list | None) -> tuple[list | None, bool]:
    if not choices:
        return choices, False
    changed = False
    new_choices = []
    for c in choices:
        if isinstance(c, str):
            fixed, chg = repair_text(c)
            if chg:
                changed = True
            new_choices.append(fixed)
        elif isinstance(c, dict):
            fixed_c = dict(c)
            content = fixed_c.get("content", "")
            fixed_content, chg = repair_text(content)
            if chg:
                changed = True
                fixed_c["content"] = fixed_content
            new_choices.append(fixed_c)
        else:
            new_choices.append(c)
    return new_choices, changed


async def step1_repair_font_and_errors(session) -> int:
    """Step 1: Audit & repair font/formatting errors across all questions in the bank."""
    log.info("--- BƯỚC 1: Rà soát & Vá lỗi font chữ, ký tự lạ, bảng vỡ, công thức ---")
    all_qs = (await session.execute(select(Question))).scalars().all()
    total = len(all_qs)
    repaired_count = 0

    for i, q in enumerate(all_qs):
        q_changed = False

        # Repair content
        new_content, c_chg = repair_text(q.content or "")
        if c_chg:
            q.content = new_content
            q_changed = True

        # Repair choices
        new_choices, ch_chg = repair_choices(q.choices)
        if ch_chg:
            q.choices = new_choices
            q_changed = True

        # Repair explanation
        if q.explanation:
            new_exp, e_chg = repair_text(q.explanation)
            if e_chg:
                q.explanation = new_exp
                q_changed = True

        if q_changed:
            # Recompute content_hash to stay consistent
            try:
                q.content_hash = hash_of_choices(
                    q.question_type.value if hasattr(q.question_type, "value") else str(q.question_type),
                    q.content,
                    q.choices,
                )
            except Exception:
                pass
            repaired_count += 1

        if (i + 1) % 5000 == 0:
            log.info(f"Đã rà soát {i + 1}/{total} câu hỏi...")

    await session.commit()
    log.info(f"-> Hoàn thành Bước 1: Đã rà soát {total} câu, phát hiện & vá lỗi {repaired_count} câu.")
    return repaired_count


async def step2_sync_primary_question_categories(session) -> int:
    """Step 2: Ensure every question has its primary category in question_categories."""
    log.info("--- BƯỚC 2: Đồng bộ hóa danh mục chính vào question_categories ---")
    # Get existing links
    existing_links = set(
        (await session.execute(select(QuestionCategory.question_id, QuestionCategory.category_id))).all()
    )
    log.info(f"Hiện tại đang có {len(existing_links)} liên kết trong question_categories.")

    # Check all questions
    all_qs = (await session.execute(select(Question.id, Question.category_id))).all()
    new_links = []
    for q_id, cat_id in all_qs:
        if (q_id, cat_id) not in existing_links:
            new_links.append(QuestionCategory(question_id=q_id, category_id=cat_id))
            existing_links.add((q_id, cat_id))

    if new_links:
        session.add_all(new_links)
        await session.commit()
        log.info(f"-> Đã bổ sung {len(new_links)} liên kết danh mục chính còn thiếu.")
    else:
        log.info("-> Toàn bộ câu hỏi đã có liên kết danh mục chính đầy đủ.")
    return len(new_links)


async def step3_reclassify_and_map_hsg(session) -> tuple[int, int]:
    """
    Step 3:
    - Reclassify HSG questions without explicit matrix to ADVANCED_APPLICATION (Vận dụng cao).
    - Map HSG questions into the corresponding curriculum (K12) categories.
    """
    log.info("--- BƯỚC 3: Phân loại lại câu HSG (Vận dụng cao) & Phân vào chương trình học ---")
    
    # 1. Fetch all categories for lookup
    all_cats = list((await session.execute(select(Category))).scalars().all())
    cat_by_id = {c.id: c for c in all_cats}
    
    # Identify K12 subject roots
    # Map subject name & grade to root Category
    # E.g. ('Vật lí', 10) -> Cat ID 18, ('Vật lí', 11) -> Cat ID 2, ('Toán', 11) -> Cat ID 1
    k12_roots: dict[tuple[str, int], Category] = {}
    for c in all_cats:
        if c.parent_id is None:
            # Check subject & grade in name
            m = re.search(r"^(Toán|Vật lí|Hóa học|Sinh học|Lịch sử|Địa lí|Ngữ văn|Tiếng Anh)\s+(\d+)$", c.name.strip(), re.I)  # noqa: E501
            if m:
                subj = m.group(1).title()
                if "Vật" in subj: subj = "Vật lí"  # noqa: E701
                if "Hóa" in subj or "Hoá" in subj: subj = "Hóa học"  # noqa: E701
                grade = int(m.group(2))
                k12_roots[(subj, grade)] = c

    log.info(f"Đã nhận diện {len(k12_roots)} môn học trong chương trình K12: {list(k12_roots.keys())[:8]}...")

    # Chapters under each K12 root
    k12_chapters: dict[int, list[Category]] = defaultdict(list)
    for c in all_cats:
        if c.parent_id in [r.id for r in k12_roots.values()] and "định kì" not in c.name.lower():
            k12_chapters[c.parent_id].append(c)

    # 2. Query all HSG questions
    hsg_query = (
        select(Question)
        .join(Category, Question.category_id == Category.id)
        .where(
            or_(
                Question.root_id == 111,
                Category.path.like("/111/%"),
                Question.difficulty == Difficulty.HSG,
                Question.difficulty == "HSG",
            )
        )
    )
    hsg_questions = list((await session.execute(hsg_query)).scalars().all())
    log.info(f"Tìm thấy tổng cộng {len(hsg_questions)} câu hỏi thuộc hệ HSG / Olympic.")

    reclassified_diff_count = 0
    mapped_curriculum_count = 0

    # Existing question_categories
    existing_links = set(
        (await session.execute(select(QuestionCategory.question_id, QuestionCategory.category_id))).all()
    )

    for q in hsg_questions:
        # A. Reclassify difficulty to ADVANCED_APPLICATION (VDC)
        if q.difficulty != Difficulty.ADVANCED_APPLICATION:
            q.difficulty = Difficulty.ADVANCED_APPLICATION
            reclassified_diff_count += 1

        # B. Map to curriculum K12
        q_cat = cat_by_id.get(q.category_id)
        q_text = (q.content or "") + " " + (q.explanation or "")

        target_subj = None
        target_grade = 11  # default high school grade for olympiads if unknown

        # Check subject from category hierarchy or text
        if q_cat:
            cat_path_ids = [int(p) for p in q_cat.path.split("/") if p.isdigit()]
            for pid in cat_path_ids:
                p_cat = cat_by_id.get(pid)
                if p_cat:
                    pname = p_cat.name.lower()
                    if "vật lí" in pname or "vật lý" in pname:
                        target_subj = "Vật lí"
                    elif "hoá" in pname or "hóa" in pname:
                        target_subj = "Hóa học"
                    elif "sinh" in pname:
                        target_subj = "Sinh học"
                    elif "toán" in pname:
                        target_subj = "Toán"
                    elif "địa" in pname:
                        target_subj = "Địa lí"
                    elif "sử" in pname:
                        target_subj = "Lịch sử"
                    elif "văn" in pname:
                        target_subj = "Ngữ văn"
                    elif "anh" in pname:
                        target_subj = "Tiếng Anh"

        # Check grade from text or exam name
        if "lớp 10" in q_text.lower() or "khối 10" in q_text.lower():
            target_grade = 10
        elif "lớp 12" in q_text.lower() or "khối 12" in q_text.lower():
            target_grade = 12
        elif "lớp 11" in q_text.lower() or "khối 11" in q_text.lower():
            target_grade = 11

        if not target_subj:
            # Detect by keywords in content
            if any(k in q_text.lower() for k in ["động lượng", "con lắc", "gia tốc", "vận tốc", "điện trường", "từ trường"]):  # noqa: E501
                target_subj = "Vật lí"
            elif any(k in q_text.lower() for k in ["phản ứng", "mol", "axit", "ancol", "kết tủa", "chất béo"]):
                target_subj = "Hóa học"
            elif any(k in q_text.lower() for k in ["tế bào", "gen", "adn", "quang hợp", "nhiễm sắc thể"]):
                target_subj = "Sinh học"
            elif any(k in q_text.lower() for k in ["hàm số", "đạo hàm", "tích phân", "hình chóp", "lăng trụ"]):
                target_subj = "Toán"
            elif any(k in q_text.lower() for k in ["địa hình", "khí hậu", "dân số", "đồng bằng", "sông ngòi"]):
                target_subj = "Địa lí"
            elif any(k in q_text.lower() for k in ["chiến dịch", "khởi nghĩa", "hiệp định", "triều đại"]):
                target_subj = "Lịch sử"

        if target_subj:
            target_root = k12_roots.get((target_subj, target_grade)) or k12_roots.get((target_subj, 11))
            if target_root:
                # Find matching chapter
                chapters = k12_chapters.get(target_root.id, [])
                best_ch = None
                best_matches = 0
                for ch in chapters:
                    words = [w for w in re.split(r"[\s,.:;()–-]+", ch.name.lower()) if len(w) > 3]
                    matches = sum(1 for w in words if w in q_text.lower())
                    if matches > best_matches:
                        best_matches = matches
                        best_ch = ch

                target_cat_id = best_ch.id if best_ch else (chapters[0].id if chapters else target_root.id)

                if (q.id, target_cat_id) not in existing_links:
                    session.add(QuestionCategory(question_id=q.id, category_id=target_cat_id))
                    existing_links.add((q.id, target_cat_id))
                    mapped_curriculum_count += 1

    await session.commit()
    log.info(
        f"-> Hoàn thành Bước 3: Đã đổi độ khó {reclassified_diff_count} câu HSG sang Vận dụng cao, "
        f"và phân {mapped_curriculum_count} câu HSG vào chuyên mục chương trình học K12 tương ứng."
    )
    return reclassified_diff_count, mapped_curriculum_count


async def step4_map_curriculum_to_periodic_exams(session) -> int:
    """
    Step 4:
    Distribute curriculum questions from high school subjects into periodic exam categories:
    - Đề thi giữa học kì 1 (GK1)
    - Đề thi học kì 1 (CK1)
    - Đề thi giữa học kì 2 (GK2)
    - Đề thi học kì 2 (CK2)
    """
    log.info("--- BƯỚC 4: Phân câu hỏi chương trình học vào Đề thi Giữa kì & Cuối kì ---")
    all_cats = list((await session.execute(select(Category))).scalars().all())
    cat_by_id = {c.id: c for c in all_cats}  # noqa: F841

    # Find periodic exam categories for each root
    # periodic_by_root[root_id] = {'GK1': cat_id, 'CK1': cat_id, 'GK2': cat_id, 'CK2': cat_id}
    periodic_by_root: dict[int, dict[str, int]] = defaultdict(dict)
    for c in all_cats:
        cname = c.name.lower()
        if "giữa học kì 1" in cname or "giữa kì 1" in cname or "giữa hk 1" in cname:
            periodic_by_root[c.root_id]["GK1"] = c.id
        elif "học kì 1" in cname or "cuối kì 1" in cname or "hk 1" in cname:
            if "giữa" not in cname:
                periodic_by_root[c.root_id]["CK1"] = c.id
        elif "giữa học kì 2" in cname or "giữa kì 2" in cname or "giữa hk 2" in cname:
            periodic_by_root[c.root_id]["GK2"] = c.id
        elif "học kì 2" in cname or "cuối kì 2" in cname or "hk 2" in cname:
            if "giữa" not in cname:
                periodic_by_root[c.root_id]["CK2"] = c.id

    log.info(f"Đã tìm thấy chuyên mục đề kiểm tra định kì cho {len(periodic_by_root)} môn học.")

    # Find chapters of each root
    chapters_by_root: dict[int, list[Category]] = defaultdict(list)
    for c in all_cats:
        if c.parent_id == c.root_id and "định kì" not in c.name.lower() and c.root_id in periodic_by_root:
            chapters_by_root[c.root_id].append(c)

    # Sort chapters by name/id
    for r_id in chapters_by_root:
        chapters_by_root[r_id].sort(key=lambda x: (x.name, x.id))

    existing_links = set(
        (await session.execute(select(QuestionCategory.question_id, QuestionCategory.category_id))).all()
    )

    new_exam_links = []

    # Map each chapter to semester exams:
    # First half of chapters -> GK1 and CK1
    # Middle chapters -> CK1
    # Second half of chapters -> GK2 and CK2
    # Final chapters -> CK2
    for r_id, chapters in chapters_by_root.items():
        p_map = periodic_by_root[r_id]
        if not p_map:
            continue
        n_ch = len(chapters)
        if n_ch == 0:
            continue

        for idx, ch in enumerate(chapters):
            # Target periodic exam slots for this chapter
            target_slots = []
            rel_pos = (idx + 0.5) / n_ch  # 0.0 to 1.0

            if rel_pos <= 0.25:
                # Early semester 1: both GK1 and CK1
                if "GK1" in p_map: target_slots.append(p_map["GK1"])  # noqa: E701
                if "CK1" in p_map: target_slots.append(p_map["CK1"])  # noqa: E701
            elif rel_pos <= 0.50:
                # Late semester 1: CK1
                if "CK1" in p_map: target_slots.append(p_map["CK1"])  # noqa: E701
            elif rel_pos <= 0.75:
                # Early semester 2: both GK2 and CK2
                if "GK2" in p_map: target_slots.append(p_map["GK2"])  # noqa: E701
                if "CK2" in p_map: target_slots.append(p_map["CK2"])  # noqa: E701
            else:
                # Late semester 2: CK2
                if "CK2" in p_map: target_slots.append(p_map["CK2"])  # noqa: E701

            if not target_slots:
                continue

            # Find all questions belonging to this chapter or its sub-lessons
            ch_branch = select(Category.id).where(Category.path.like(f"{ch.path}%"))
            qs_in_ch = (
                await session.execute(select(Question.id).where(Question.category_id.in_(ch_branch)))
            ).scalars().all()

            for q_id in qs_in_ch:
                for target_cat_id in target_slots:
                    if (q_id, target_cat_id) not in existing_links:
                        new_exam_links.append(QuestionCategory(question_id=q_id, category_id=target_cat_id))
                        existing_links.add((q_id, target_cat_id))

    if new_exam_links:
        # Commit in batches of 2000
        batch_size = 2000
        for b_start in range(0, len(new_exam_links), batch_size):
            session.add_all(new_exam_links[b_start : b_start + batch_size])
            await session.commit()
            log.info(f"Đã lưu {min(b_start + batch_size, len(new_exam_links))}/{len(new_exam_links)} liên kết GK/CK...")

    log.info(f"-> Hoàn thành Bước 4: Đã thêm {len(new_exam_links)} liên kết câu hỏi vào Đề thi Giữa kì & Cuối kì!")
    return len(new_exam_links)


async def main():
    log.info("=== BẮT ĐẦU CHẠY SCRIPT RÀ SOÁT VÀ TÁI PHÂN LOẠI TOÀN BỘ NGÂN HÀNG CÂU HỎI ===")
    async with SessionLocal() as session:
        # Step 1: Repair font & formatting
        repaired = await step1_repair_font_and_errors(session)

        # Step 2: Sync primary category links
        synced_primary = await step2_sync_primary_question_categories(session)  # noqa: F841

        # Step 3: Reclassify HSG and map to curriculum
        hsg_diff, hsg_mapped = await step3_reclassify_and_map_hsg(session)

        # Step 4: Map curriculum to GK and CK periodic exams
        periodic_mapped = await step4_map_curriculum_to_periodic_exams(session)

        # Final Verification & Stats
        total_q = await session.scalar(select(func.count(Question.id)))
        total_qc = await session.scalar(select(func.count(QuestionCategory.question_id)))
        diff_stats = (await session.execute(select(Question.difficulty, func.count()).group_by(Question.difficulty))).all()  # noqa: E501

        log.info("=== BÁO CÁO TỔNG KẾT RÀ SOÁT VÀ NÂNG CẤP NGÂN HÀNG ĐỀ ===")
        log.info(f"1. Tổng số câu hỏi trong ngân hàng: {total_q}")
        log.info(f"2. Số câu được rà soát và vá lỗi font / công thức / bảng: {repaired}")
        log.info(f"3. Số câu HSG được chuẩn hóa sang mức 'Vận dụng cao' (ADVANCED_APPLICATION): {hsg_diff}")
        log.info(f"4. Số câu HSG được phân vào chuyên mục chương trình học K12: {hsg_mapped}")
        log.info(f"5. Số liên kết câu hỏi được phân vào Đề thi Giữa kì (GK) & Cuối kì (CK): {periodic_mapped}")
        log.info(f"6. Tổng số liên kết đa danh mục (question_categories) hiện tại: {total_qc}")
        log.info(f"7. Thống kê mức độ câu hỏi mới: {diff_stats}")


if __name__ == "__main__":
    asyncio.run(main())
