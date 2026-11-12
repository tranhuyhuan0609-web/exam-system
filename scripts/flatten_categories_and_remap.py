"""Flatten the Category tree by:
1. Reparenting all canonical KNTT chapters directly under their Subject roots (removing intermediate 'Kết nối tri thức').
2. Remapping and merging all questions from other textbooks ('Cánh diều', 'Chân trời sáng tạo') into their matching KNTT chapters.
3. Deleting all dummy intermediate book nodes ('Kết nối tri thức', 'Cánh diều', 'Chân trời sáng tạo').
4. Recalculating path, depth, and position for all categories in the database.
"""  # noqa: E501

import asyncio

from sqlalchemy import delete, select, update

from app.db import SessionLocal, engine
from app.domain.cross_curriculum_mapper import map_other_book_to_kntt
from app.models import Category, Exam, Question


async def flatten_and_remap():
    print("=" * 70)
    print("BẮT ĐẦU CHUẨN HÓA MỤC LỤC: GỠ BỎ TẦNG SÁCH & HỢP NHẤT VÀO KẾT NỐI TRI THỨC")
    print("=" * 70)

    async with SessionLocal() as session:
        # 1. Identify all subject roots
        subject_roots = (await session.scalars(select(Category).where(Category.parent_id.is_(None)))).all()
        print(f"Tìm thấy {len(subject_roots)} môn học / khối lớp gốc.")

        remapped_questions_count = 0
        deleted_categories_count = 0

        for s_root in subject_roots:
            # Find book categories under this subject root
            child_cats = (await session.scalars(select(Category).where(Category.parent_id == s_root.id))).all()
            kntt_cat = next((c for c in child_cats if "kết nối tri thức" in c.name.lower()), None)
            other_book_cats = [
                c for c in child_cats if "cánh diều" in c.name.lower() or "chân trời sáng tạo" in c.name.lower()
            ]

            kntt_chapters = []
            if kntt_cat:
                kntt_chapters = (await session.scalars(select(Category).where(Category.parent_id == kntt_cat.id))).all()
                # Reparent KNTT chapters directly to s_root
                for ch in kntt_chapters:
                    ch.parent_id = s_root.id
                await session.flush()
                print(f"-> Môn '{s_root.name}': Đã chuyển {len(kntt_chapters)} chương KNTT trực tiếp lên dưới môn.")

            # For other book categories: remap questions into matching KNTT chapters
            for b_cat in other_book_cats:
                b_chapters = (await session.scalars(select(Category).where(Category.parent_id == b_cat.id))).all()
                for b_ch in b_chapters:
                    b_ch_sub = (await session.scalars(select(Category).where(Category.parent_id == b_ch.id))).all()
                    b_ch_all_ids = [b_ch.id, *(c.id for c in b_ch_sub)]

                    # Find matching KNTT chapter
                    kntt_cands = [{"id": kc.id, "name": kc.name, "obj": kc} for kc in kntt_chapters]
                    matched = map_other_book_to_kntt(b_ch.name, kntt_cands)

                    if matched:
                        target_ch: Category = matched["obj"]
                        # Move all questions pointing to b_ch or its child lessons to target_ch
                        res = await session.execute(
                            update(Question)
                            .where(Question.category_id.in_(b_ch_all_ids))
                            .values(category_id=target_ch.id)
                        )
                        remapped_questions_count += res.rowcount
                        # Move any exams
                        await session.execute(
                            update(Exam)
                            .where(Exam.category_id.in_(b_ch_all_ids))
                            .values(category_id=target_ch.id)
                        )
                        print(f"   [Hợp nhất] '{b_ch.name}' ({b_cat.name}) -> '{target_ch.name}' (KNTT) [{res.rowcount} câu]")  # noqa: E501
                        # Delete the other book chapter and its child lessons
                        await session.execute(delete(Category).where(Category.id.in_(b_ch_all_ids)))
                        deleted_categories_count += len(b_ch_all_ids)
                    else:
                        # No direct match found: promote it directly to s_root so no questions are lost
                        b_ch.parent_id = s_root.id
                        print(f"   [Giữ lại] '{b_ch.name}' không có đối ứng KNTT -> chuyển trực tiếp dưới môn '{s_root.name}'")  # noqa: E501

                # Delete the other book category wrapper
                await session.execute(delete(Category).where(Category.id == b_cat.id))
                deleted_categories_count += 1

            # Delete the KNTT dummy category wrapper
            if kntt_cat:
                await session.execute(delete(Category).where(Category.id == kntt_cat.id))
                deleted_categories_count += 1

        await session.commit()
        print(f"-> Đã hợp nhất {remapped_questions_count} câu hỏi từ các sách khác về mục lục KNTT.")
        print(f"-> Đã xóa {deleted_categories_count} danh mục dummy/trùng lặp.")

        # 2. Fix Hóa học 10 KNTT chapters if mistitled
        hoa10 = await session.scalar(select(Category).where(Category.name == "Hóa học 10"))
        if hoa10:
            h10_chapters = (await session.scalars(select(Category).where(Category.parent_id == hoa10.id))).all()
            h10_map = {
                "este": "Chương 1. Cấu tạo nguyên tử",
                "cacbohidrat": "Chương 2. Bảng tuần hoàn các nguyên tố hóa học",
                "amin": "Chương 3. Liên kết hóa học",
                "polime": "Chương 4. Phản ứng oxi hóa - khử",
                "đại cương": "Chương 5. Năng lượng hóa học",
                "kim loại kiềm": "Chương 6. Tốc độ phản ứng hóa học",
                "crom": "Chương 7. Nguyên tố nhóm VIIA (Halogen)",
                "nhận biết": "Chương 8. Ôn tập học kì & Tổng hợp",
            }
            for ch in h10_chapters:
                for k, new_title in h10_map.items():
                    if k in ch.name.lower():
                        ch.name = new_title
                        break
            await session.commit()
            print("-> Đã chuẩn hóa tiêu đề các chương Hóa học 10 theo đúng chuẩn KNTT.")

        # 3. Recalculate path, depth, and position for all categories
        print("-> Đang cập nhật lại path, depth, position cho toàn bộ cây danh mục...")
        all_cats = (await session.scalars(select(Category))).all()
        by_id = {c.id: c for c in all_cats}  # noqa: F841
        children_by_parent: dict[int | None, list[Category]] = {}
        for c in all_cats:
            children_by_parent.setdefault(c.parent_id, []).append(c)

        def recompute(cat: Category, parent_path: str, cur_depth: int, pos: int):
            cat.path = f"{parent_path}{cat.id}/"
            cat.depth = cur_depth
            cat.position = pos
            kids = children_by_parent.get(cat.id, [])
            kids.sort(key=lambda k: (k.position, k.name))
            for idx, kid in enumerate(kids, start=1):
                recompute(kid, cat.path, cur_depth + 1, idx)

        roots = children_by_parent.get(None, [])
        roots.sort(key=lambda r: (r.position, r.name))
        for idx, r in enumerate(roots, start=1):
            recompute(r, "/", 0, idx)

        await session.commit()
        print("=" * 70)
        print("HOÀN TẤT CHUẨN HÓA CÂY MỤC LỤC THEO DUY NHẤT SÁCH KẾT NỐI TRI THỨC!")
        print("=" * 70)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(flatten_and_remap())

