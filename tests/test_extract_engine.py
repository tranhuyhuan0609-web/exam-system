from pathlib import Path

from app.ai.extract_engine import (
    extract_short_answer_value,
    has_mathtype_ole,
    is_solution_name,
    pair_exam_and_solution_files,
    pair_solutions_into_questions,
    split_into_structured_questions,
)


def test_is_solution_name():
    assert is_solution_name("ĐÁP ÁN LÝ 11.docx")
    assert is_solution_name("HDC_TOAN_10.pdf")
    assert is_solution_name("02-DE1-Huong-dan-cham.docx")
    assert not is_solution_name("ĐỀ CHÍNH THỨC LÝ 11 (HÙNG VƯƠNG - 2024).docx")
    assert not is_solution_name("02-DE1.pdf")


def test_has_mathtype_ole(tmp_path):
    import zipfile

    docx = tmp_path / "math.docx"
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("word/document.xml", "<w:object><o:OLEObject/></w:object>")
    assert has_mathtype_ole(docx)


def test_pair_exam_and_solution():
    files = [
        Path("02-DE1.docx"),
        Path("02-DE1.pdf"),
        Path("02-DE1-DAP-AN.docx"),
    ]
    pairs = pair_exam_and_solution_files(files)
    assert len(pairs) == 1
    exam, sol = pairs[0]
    assert exam.name == "02-DE1.docx"  # .docx prioritized over .pdf
    assert sol is not None
    assert sol.name == "02-DE1-DAP-AN.docx"


def test_pair_exam_and_solution_handles_restarted_numbers():
    files = [
        Path("De thi Vat ly 11.docx"),
        Path("Dap an Vat ly 11.docx"),
        Path("De thi Hoa hoc 11.docx"),
        Path("Huong dan cham Hoa hoc 11.docx"),
    ]
    pairs = pair_exam_and_solution_files(files)
    assert [(exam.stem, solution.stem if solution else None) for exam, solution in pairs] == [
        ("De thi Vat ly 11", "Dap an Vat ly 11"),
        ("De thi Hoa hoc 11", "Huong dan cham Hoa hoc 11"),
    ]


def test_split_and_pair_structured_questions():
    exam_text = """
Bài 1. (5,0 điểm) – DAO ĐỘNG CƠ HỌC
Một con lắc lò xo có k = 100 N/m như Hình 1.1.
1. Tính chu kì dao động riêng của con lắc.
2. Tìm vận tốc cực đại khi A = 5 cm.

Bài 2. (5,0 điểm) – NHIỆT HỌC
Một khối khí lí tưởng thực hiện chu trình như Hình 2a.
1. Tính nhiệt lượng khối khí nhận vào.
"""
    figure_map = {"Hình 1.1": b"fake_png_1", "Hình 2a": b"fake_png_2"}
    questions = split_into_structured_questions(exam_text, figure_map)
    assert len(questions) == 2

    q1 = questions[0]
    assert q1.number == 1
    assert q1.total_points == 5.0
    assert "DAO ĐỘNG CƠ HỌC" in q1.topic_raw
    assert "Hình 1.1" in q1.attached_figures
    assert len(q1.sub_questions) == 2
    assert q1.sub_questions[0].sub_index == "1"

    sol_text = """
Bài 1. (5,0 điểm) – DAO ĐỘNG CƠ HỌC
1. Chu kì dao động: T = 2π√(m/k). Đáp số: T = 0,2 s.
2. Vận tốc cực đại: v_max = ωA. Kết quả: v = 15,7 m/s.

Bài 2. (5,0 điểm) – NHIỆT HỌC
1. Nhiệt lượng: Q = ΔU + A. Đáp án: Q = 120 J.
"""
    pair_solutions_into_questions(questions, sol_text)
    assert q1.sub_questions[0].solution is not None
    assert q1.sub_questions[0].short_answer_value == "0.2"
    assert q1.sub_questions[1].short_answer_value == "15.7"
    assert questions[1].sub_questions[0].short_answer_value == "120"


def test_structured_question_hash_ignores_formatting_noise():
    first = split_into_structured_questions("Bài 1. (1,0 điểm)\n  Nội dung   câu hỏi.\n")[0]
    second = split_into_structured_questions("Bài 1. (1,0 điểm)\nNội dung câu hỏi")[0]
    assert first.content_hash == second.content_hash


def test_extract_short_answer_value():
    assert extract_short_answer_value("Đáp số: v = 29,83 m/s") == "29.83"
    assert extract_short_answer_value("Kết quả: 12,5") == "12.5"
    assert extract_short_answer_value("Vậy hiệu suất H = 45%") == "45"
    assert extract_short_answer_value("Thời gian là 2.5 s") == "2.5"
