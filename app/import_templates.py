"""Downloadable sample files for the question importer. Both samples import cleanly as-is."""

import io

SAMPLE_QUESTIONS = [
    {
        "content": r"Một vật dao động điều hoà với tần số góc $\omega = 4\pi$ rad/s. Chu kì dao động là",
        "choices": ["$0{,}5$ s", "$2$ s", "$0{,}25$ s", "$4$ s"],
        "answer": "A",
        "level": "Thông hiểu",
        "explanation": r"$T = \dfrac{2\pi}{\omega} = \dfrac{2\pi}{4\pi} = 0{,}5$ s",
    },
    {
        "content": "Trong dao động điều hoà, tốc độ của vật cực đại khi vật",
        "choices": ["ở vị trí biên", "đi qua vị trí cân bằng", "có gia tốc cực đại", "có li độ cực đại"],
        "answer": "B",
        "level": "Nhận biết",
        "explanation": r"$|v| = \omega\sqrt{A^2 - x^2}$ lớn nhất khi $x = 0$.",
    },
    {
        "content": r"Vật dao động điều hoà với $A = 5$ cm, $\omega = 10$ rad/s. Tốc độ cực đại là",
        "choices": ["$2$ cm/s", "$0{,}5$ cm/s", "$50$ cm/s", "$500$ cm/s"],
        "answer": "C",
        "level": "Vận dụng",
        "explanation": r"$v_{\max} = \omega A = 50$ cm/s",
    },
]

TEXT_RULES = [
    "Mỗi câu bắt đầu bằng “Câu 1:”, “Câu 2:”… Nội dung có thể xuống nhiều dòng.",
    "Các lựa chọn bắt đầu bằng “A.”, “B.”, “C.”, “D.” (tối đa 6 lựa chọn A–F). "
    "Có thể để nhiều lựa chọn trên một dòng nếu cách nhau bằng phím Tab.",
    "Đáp án đúng: thêm dòng “Đáp án: B” hoặc đặt dấu * trước lựa chọn đúng (VD: “*B. …”).",
    "Không bắt buộc: dòng “Mức độ: Nhận biết / Thông hiểu / Vận dụng” và “Lời giải: …”.",
    "Công thức viết bằng LaTeX giữa hai dấu $, VD: $v = \\omega A$. "
    "Công thức MathType / Equation của Word chưa được hỗ trợ.",
]


def sample_text() -> str:
    lines = []
    for i, q in enumerate(SAMPLE_QUESTIONS, start=1):
        lines.append(f"Câu {i}: {q['content']}")
        if i == 3:  # show the one-line layout
            lines.append("\t".join(f"{letter}. {c}" for letter, c in zip("ABCD", q["choices"], strict=True)))
        else:
            lines += [f"{letter}. {c}" for letter, c in zip("ABCD", q["choices"], strict=True)]
        lines += [f"Đáp án: {q['answer']}", f"Mức độ: {q['level']}", f"Lời giải: {q['explanation']}", ""]
    return "\n".join(lines)


def sample_docx() -> bytes:
    from docx import Document

    doc = Document()
    doc.add_heading("File mẫu nhập câu hỏi", level=1)
    for rule in TEXT_RULES:
        doc.add_paragraph(rule, style="List Bullet")
    doc.add_paragraph("")
    for line in sample_text().splitlines():
        doc.add_paragraph(line)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def sample_xlsx() -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Câu hỏi"
    header = ["Nội dung", "A", "B", "C", "D", "E", "F", "Đáp án", "Mức độ", "Lời giải"]
    ws.append(header)
    for q in SAMPLE_QUESTIONS:
        ws.append([q["content"], *q["choices"], "", "", q["answer"], q["level"], q["explanation"]])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2563EB")
    widths = {"A": 60, "B": 18, "C": 18, "D": 18, "E": 18, "F": 10, "G": 10, "H": 10, "I": 14, "J": 50}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"

    guide = wb.create_sheet("Hướng dẫn")
    for line in [
        "Chỉ sheet đầu tiên được đọc. Giữ nguyên dòng tiêu đề.",
        "Cột “Nội dung”: nội dung câu hỏi (bắt buộc). Công thức LaTeX đặt giữa hai dấu $.",
        "Cột A–F: các lựa chọn, cần ít nhất 2 (A, B). Bỏ trống E, F nếu chỉ có 4 lựa chọn.",
        "Cột “Đáp án”: một chữ cái A–F.",
        "Cột “Mức độ” (không bắt buộc): Nhận biết / Thông hiểu / Vận dụng. Để trống sẽ dùng mức độ chọn khi tải lên.",
        "Cột “Lời giải” (không bắt buộc): hiện cho học sinh sau khi nộp bài.",
    ]:
        guide.append([line])
    guide.column_dimensions["A"].width = 110

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
