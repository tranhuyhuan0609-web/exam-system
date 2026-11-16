"""Unit tests covering complex Vietnamese exam PDF/DOCX extraction patterns."""

from __future__ import annotations

import io
from pathlib import Path

import docx

from app.modules.parsers.internal.document_converter import (
    _normalize_ocr_line,
    extract_docx_hdc_questions,
    extract_docx_plain_text,
)
from app.modules.parsers.internal.pdf_extractor import (
    _normalize_line_text,
    extract_pdf_exam_structure,
)
from tests.pdf_fixtures import A4, PdfSketch


def test_tcvn3_decoding_preserves_utf8_vietnamese() -> None:
    assert _normalize_line_text("ÑEÀ KIEÅM TRA 12") == "ĐỀ KIỂM TRA 12"
    # Must NOT corrupt normal UTF-8 lines that happen to contain uppercase "Ý" or "Ê"
    utf8_line = "PHẦN II. Câu trắc nghiệm đúng sai (3 điểm). Ý a), b), c), d) ở mỗi câu."
    assert _normalize_line_text(utf8_line) == utf8_line


def test_ocr_prefix_normalization() -> None:
    assert _normalize_ocr_line("Chu 4 (4 điểm):") == "Câu 4 (4 điểm):"
    assert _normalize_ocr_line("Cu 5 (3 diém): Cho một ca nước") == "Câu 5 (3 điểm): Cho một ca nước"
    assert _normalize_ocr_line("Caw $ G3 diém): Test") == "Câu 5 (3 điểm): Test"


def test_hsg_roman_numerals_and_explicit_points() -> None:
    sketch = PdfSketch()
    p1 = sketch.new_page(*A4)
    p1.text(48, 30, "KY THI CHON HOC SINH GIOI LOP 10", size=12)
    p1.text(48, 130, "Câu I. (4,0 điểm) CƠ HỌC CHẤT ĐIỂM", size=11)
    p1.text(48, 150, "1. Bỏ qua sức cản không khí.", size=10)
    p1.text(48, 168, "a. Tính vận tốc chạm đất.", size=10)
    p1.text(48, 186, "b. Tính bán kính chính khúc.", size=10)
    p1.text(48, 220, "Câu II (4,0 điểm) CƠ HỌC VẬT RẮN", size=11)
    p1.text(48, 240, "Một thanh cứng đồng chất có khối lượng m.", size=10)

    p2 = sketch.new_page(*A4)
    p2.text(48, 48, "Câu III. (4,0 điểm) NHIỆT HỌC", size=11)
    p2.text(48, 68, "Một bình kín chia làm 2 ngăn.", size=10)
    p2.text(48, 110, "Câu IV (4,0 điểm) THIÊN THỂ", size=11)
    p2.text(48, 130, "Xét sao chổi P chuyển động parabol.", size=10)
    p2.text(48, 170, "Câu V. (4,0 điểm) PHƯƠNG ÁN THỰC HÀNH", size=11)
    p2.text(48, 190, "Thiết lập phương án đo gia tốc trọng trường.", size=10)

    art = extract_pdf_exam_structure(sketch.tobytes())
    qs = [q for s in art.sections for q in s.questions]
    assert len(qs) == 5
    assert [q.original_question_label for q in qs] == ["Câu I", "Câu II", "Câu III", "Câu IV", "Câu V"]
    assert all(q.item_type == "ESSAY" for q in qs)
    assert [q.points for q in qs] == [4.0, 4.0, 4.0, 4.0, 4.0]


def test_gdpt2025_three_part_section_reset_and_essay_scientist_guard() -> None:
    sketch = PdfSketch()
    p1 = sketch.new_page(*A4)
    p1.text(48, 36, "PHẦN I. Câu trắc nghiệm nhiều phương án lựa chọn", size=11)
    p1.text(48, 60, "Câu 1: Nhiệt độ tăng thì động năng phân tử", size=10)
    p1.text(48, 78, "A. tăng.   B. giảm.   C. không đổi.   D. bằng 0.", size=10)
    p1.text(48, 110, "PHẦN II. Câu trắc nghiệm đúng sai", size=11)
    p1.text(48, 132, "Câu 1: Một bình hình trụ có pít-tông:", size=10)
    p1.text(48, 150, "a) Khí biến đổi đẳng nhiệt.", size=10)
    p1.text(48, 166, "b) Áp suất tăng 2 lần.", size=10)
    p1.text(48, 182, "c) Thể tích giảm 2 lần.", size=10)
    p1.text(48, 198, "d) Lực tác dụng bằng 250 N.", size=10)
    p1.text(48, 224, "PHẦN III. Câu trắc nghiệm trả lời ngắn", size=11)
    p1.text(48, 246, "Câu 1: Nhiệt độ cuối cùng bằng bao nhiêu độ C?", size=10)
    p1.text(48, 280, "Câu 2. (2,0 điểm): Giải Nobel trao cho A. Hodgkin, A. Huxley và C. Eccles.", size=10)
    p1.text(48, 298, "1. Xác định điện thế nghỉ.", size=10)

    art = extract_pdf_exam_structure(sketch.tobytes())
    qs = [q for s in art.sections for q in s.questions]
    assert len(qs) == 4
    # Strictly increasing global question_no even though source restarts at Câu 1 in Parts II and III
    assert [q.question_no for q in qs] == [1, 2, 3, 4]
    assert [q.item_type for q in qs] == ["SINGLE_CHOICE", "CLUSTER_TRUE_FALSE", "SHORT_NUMERIC", "ESSAY"]


def test_inline_detailed_solutions_pair_without_duplication() -> None:
    sketch = PdfSketch()
    p1 = sketch.new_page(*A4)
    p1.text(48, 40, "Câu 1: (5,0 điểm) Một nêm trượt trên mặt phẳng ngang.", size=10)
    p1.text(48, 60, "Tính gia tốc của nêm.", size=10)
    p1.text(48, 85, "Câu 2: (5,0 điểm) Một vật trượt không ma sát.", size=10)
    p1.text(48, 105, "Tính vận tốc tại chân dốc.", size=10)
    p1.text(48, 140, "Đáp án", size=11)
    p1.text(48, 160, "Câu 1: Gia tốc a = g*sin(alpha).", size=10)
    p1.text(48, 185, "Câu 2: Áp dụng 2. a = 5 m/s^2 => v = 4 m/s.", size=10)

    art = extract_pdf_exam_structure(sketch.tobytes())
    qs = [q for s in art.sections for q in s.questions]
    assert len(qs) == 2
    assert qs[0].paired_answer == "Gia tốc a = g*sin(alpha)."
    assert qs[1].paired_answer == "Áp dụng 2. a = 5 m/s^2 => v = 4 m/s."


def test_docx_hdc_table_and_plain_fallback_extraction() -> None:
    d = docx.Document()
    tbl = d.add_table(rows=5, cols=3)
    rows = [
        ("Câu", "Nội dung", "Điểm"),
        ("I", "Câu 1. (4 điểm)", ""),
        ("1", "Tính vận tốc v = 5 m/s", "2,0"),
        ("2", "Tính động năng E = 12,5 J", "2,0"),
        ("II", "Câu 2. (6 điểm)", "6,0"),
    ]
    for r_i, row in enumerate(rows):
        for c_i, val in enumerate(row):
            tbl.cell(r_i, c_i).text = val
    buf = io.BytesIO()
    d.save(buf)
    raw = buf.getvalue()

    hdc = extract_docx_hdc_questions(raw)
    assert hdc is not None
    assert len(hdc) == 2
    assert hdc[0]["points"] == 4.0
    assert "v = 5 m/s" in hdc[0]["content_md"]
    assert hdc[1]["points"] == 6.0

    # Ensure plain OOXML text extractor emits Markdown table cleanly
    md_text = extract_docx_plain_text(raw)
    assert "| Câu | Nội dung | Điểm |" in md_text


def test_optional_real_physics10_hsg_if_present() -> None:
    sample = Path(
        r"C:\Users\admin\.gemini\antigravity\brain\8ad406a8-04ad-4b8f-87f5-eecc70413b70\.user_uploaded\media_1794769825349_76e1929e.pdf"
    )
    if not sample.exists():
        return
    art = extract_pdf_exam_structure(sample.read_bytes())
    qs = [q for s in art.sections for q in s.questions]
    assert len(qs) == 5
    assert [q.original_question_label for q in qs] == ["Câu I", "Câu II", "Câu III", "Câu IV", "Câu V"]
    assert sum(q.points for q in qs) == 20.0
