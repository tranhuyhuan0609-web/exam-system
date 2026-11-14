"""Module bóc tách Chuyên đề Lý thuyết (VietJack style) + 4 Phần Bài tập + Đáp án nội tuyến."""

import re
from typing import Any


def split_study_guide_and_questions(full_text: str) -> dict[str, Any]:
    """
    Tách tự động:
    1. Khối Lý thuyết / Phương pháp giải (nếu đầu file có 'Phương pháp:', 'Các công thức cần nhớ:') -> knowledge_article
    2. Khối Bài tập chia theo 4 phần chuẩn GDPT 2018 (Tự luận, Phần I ABCD, Phần II Đúng/Sai, Phần III Trả lời ngắn)
       hoặc Đề HSG kèm phần 'Đáp án' ở nửa cuối file (như HSG-29-2.pdf).
    """
    # 1. Tách phần Lý thuyết ở đầu tài liệu (nếu có)
    theory_split = re.split(
        r"\n\s*(?=BÀI TẬP TỰ LUẬN|BÀI TẬP TRẮC NGHIỆM|PHẦN I\.|Câu\s+(?:I|1)[\.\:]|Bài\s+1[\.\:])",
        full_text,
        maxsplit=1,
    )
    knowledge_article: dict[str, str] | None = None
    if len(theory_split) > 1 and any(k in theory_split[0] for k in ("Phương pháp", "Công thức", "Dạng ", "Lý thuyết")):
        t_raw = theory_split[0].strip()
        q_raw = theory_split[1].strip()
        method_m = re.search(r"Phương pháp\s*:(.*?)(?=Chú ý|Các công thức|$)", t_raw, re.S | re.I)
        formula_m = re.search(r"Các công thức cần nhớ\s*:(.*?)(?=Chú ý 2|$)", t_raw, re.S | re.I)
        notes_m = re.search(r"(Chú ý.*)", t_raw, re.S | re.I)
        knowledge_article = {
            "raw_theory": t_raw,
            "method_guide_md": method_m.group(1).strip() if method_m else t_raw,
            "summary_formulas_md": formula_m.group(1).strip() if formula_m else "",
            "notes_and_tips_md": notes_m.group(1).strip() if notes_m else "",
        }
    else:
        q_raw = full_text

    # 2. Tách phần "Đáp án / Hướng dẫn chấm" nếu nằm chung ở cuối file (như HSG-29-2.pdf)
    ans_split = re.split(r"\n\s*(?:ĐÁP ÁN|HƯỚNG DẪN CHẤM|BÀI GIẢI)\s*\n", q_raw, maxsplit=1, flags=re.IGNORECASE)
    exam_part = ans_split[0].strip()
    sol_part = ans_split[1].strip() if len(ans_split) > 1 else ""
    inline_solutions = _map_solutions_by_number(sol_part) if sol_part else {}

    # 3. Kiểm tra xem đề chia theo 3 Phần mới (PHẦN I, PHẦN II, PHẦN III) hay đề HSG/Cổ điển
    if "PHẦN I." in exam_part or "PHẦN II." in exam_part:
        questions = _parse_gdpt2018_sections(exam_part)
    else:
        questions = _parse_general_questions(exam_part, inline_solutions)

    return {
        "knowledge_article": knowledge_article,
        "questions": questions,
    }


def _map_solutions_by_number(sol_text: str) -> dict[str, str]:
    pattern = re.compile(r"(?:^|\n)\s*(?:Câu|Bài)\s+([IVX]+|\d+)[\.\:]", re.I)
    parts = pattern.split(sol_text)
    return {parts[i].strip(): parts[i + 1].strip() for i in range(1, len(parts), 2)}


def _parse_general_questions(text: str, solutions_map: dict[str, str]) -> list[dict[str, Any]]:
    pattern = re.compile(
        r"(?:^|\n)\s*((?:Câu|Bài)\s+([IVX]+|\d+)[\.\:]?\s*(?:\(\d+(?:[,\.]\d+)?\s*điểm\))?\s*[\–\-\:]?\s*[^\n]*)",
        re.IGNORECASE,
    )
    parts = pattern.split(text)
    items = []
    for i in range(1, len(parts), 3):
        header, q_num, body = parts[i].strip(), parts[i + 1].strip(), parts[i + 2].strip()
        body = re.split(r"[-—_]*\s*HẾT\s*[-—_]*|Họ và tên thí sinh", body, flags=re.I)[0].strip()
        items.append(
            {
                "number": q_num,
                "question_type": "ESSAY",
                "header": header,
                "content": body,
                "solution": solutions_map.get(q_num, ""),
            }
        )
    return items


def _parse_gdpt2018_sections(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    p1 = re.split(r"\n\s*(?:BÀI TẬP TRẮC NGHIỆM\s*\n\s*)?PHẦN I\.", text, maxsplit=1)
    essay_raw, rest1 = p1[0], (p1[1] if len(p1) > 1 else "")
    p2 = re.split(r"\n\s*PHẦN II\.", rest1, maxsplit=1)
    mcq_raw, rest2 = p2[0], (p2[1] if len(p2) > 1 else "")
    p3 = re.split(r"\n\s*PHẦN III\.", rest2, maxsplit=1)
    tf_raw, sa_raw = p3[0], (p3[1] if len(p3) > 1 else "")

    for m in re.finditer(r"Bài tập\s+(\d+)\s*:\s*(.*?)(?=\n\s*Bài tập\s+\d+\s*:|$)", essay_raw, re.S):
        items.append({"question_type": "ESSAY", "number": m.group(1), "content": m.group(2).strip()})
    for m in re.finditer(r"Câu\s+(\d+)\s*:\s*(.*?)(?=\n\s*Câu\s+\d+\s*:|$)", mcq_raw, re.S):
        items.append({"question_type": "SINGLE_CHOICE", "number": m.group(1), "content": m.group(2).strip()})
    for m in re.finditer(r"Câu\s+(\d+)\s*:\s*(.*?)(?=\n\s*Câu\s+\d+\s*:|$)", tf_raw, re.S):
        items.append({"question_type": "TRUE_FALSE_4", "number": m.group(1), "content": m.group(2).strip()})
    for m in re.finditer(r"Câu\s+(\d+)\s*:\s*(.*?)(?=\n\s*Câu\s+\d+\s*:|-*HẾT-*|$)", sa_raw, re.S):
        items.append({"question_type": "SHORT_ANSWER", "number": m.group(1), "content": m.group(2).strip()})
    return items
