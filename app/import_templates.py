"""Downloadable sample files for the question importer. All samples import cleanly as-is."""

import io

# "type" uses the Excel "Dạng" labels; "answer" is written exactly as a teacher would type it.
SAMPLE_QUESTIONS = [
    {
        "type": "Trắc nghiệm",
        "content": r"Một vật dao động điều hoà với tần số góc $\omega = 4\pi$ rad/s. Chu kì dao động là",
        "choices": ["$0{,}5$ s", "$2$ s", "$0{,}25$ s", "$4$ s"],
        "answer": "A",
        "level": "Thông hiểu",
        "explanation": r"$T = \dfrac{2\pi}{\omega} = \dfrac{2\pi}{4\pi} = 0{,}5$ s",
    },
    {
        "type": "Trắc nghiệm",
        "content": "Trong dao động điều hoà, tốc độ của vật cực đại khi vật",
        "choices": ["ở vị trí biên", "đi qua vị trí cân bằng", "có gia tốc cực đại", "có li độ cực đại"],
        "answer": "B",
        "level": "Nhận biết",
        "explanation": r"$|v| = \omega\sqrt{A^2 - x^2}$ lớn nhất khi $x = 0$.",
    },
    {
        "type": "Đúng sai",
        "content": r"Một lượng khí lí tưởng được nén đẳng nhiệt từ $10$ lít xuống $5$ lít.",
        "choices": [
            "Áp suất của khí tăng gấp đôi.",
            "Nhiệt độ của khí tăng.",
            r"Tích $pV$ không đổi.",
            "Mật độ phân tử khí giảm.",
        ],
        "answer": "Đ S Đ S",
        "level": "Thông hiểu",
        "explanation": r"Đẳng nhiệt: $pV = \text{const}$, $V$ giảm một nửa nên $p$ tăng gấp đôi, mật độ phân tử tăng.",
    },
    {
        "type": "Trả lời ngắn",
        "content": r"Vật dao động điều hoà với $A = 5$ cm, $\omega = 10$ rad/s. Tốc độ cực đại của vật là bao nhiêu cm/s?",
        "choices": [],
        "answer": "50",
        "level": "Vận dụng",
        "explanation": r"$v_{\max} = \omega A = 10 \cdot 5 = 50$ cm/s",
    },
]

TEXT_RULES = [
    "Mỗi câu bắt đầu bằng “Câu 1:”, “Câu 2:”… Nội dung có thể xuống nhiều dòng.",
    "Trắc nghiệm: các lựa chọn bắt đầu bằng “A.”, “B.”, “C.”, “D.” (tối đa 6 lựa chọn A–F), "
    "đáp án ghi “Đáp án: B” hoặc đặt dấu * trước lựa chọn đúng (VD: “*B. …”).",
    "Đúng/Sai: 4 ý viết chữ thường “a)”, “b)”, “c)”, “d)”, đáp án ghi “Đáp án: Đ S Đ S”.",
    "Trả lời ngắn: không có lựa chọn, chỉ cần dòng “Đáp án: 30” (nhiều đáp án chấp nhận: “Đáp án: 1,25; 1,3”).",
    "Có thể để nhiều lựa chọn / ý trên một dòng nếu cách nhau bằng phím Tab.",
    "Không bắt buộc: dòng “Mức độ: Nhận biết / Thông hiểu / Vận dụng” và “Lời giải: …”.",
    "Công thức viết bằng LaTeX giữa hai dấu $, VD: $v = \\omega A$. "
    "File có công thức MathType nên dùng chức năng “AI nhập đề”.",
]


def sample_text() -> str:
    lines = []
    for i, q in enumerate(SAMPLE_QUESTIONS, start=1):
        lines.append(f"Câu {i}: {q['content']}")
        if q["type"] == "Đúng sai":
            lines += [f"{letter}) {c}" for letter, c in zip("abcd", q["choices"], strict=True)]
        elif i == 2:  # show the one-line layout
            lines.append("\t".join(f"{letter}. {c}" for letter, c in zip("ABCD", q["choices"], strict=True)))
        else:
            lines += [f"{letter}. {c}" for letter, c in zip("ABCD", q["choices"], strict=False)]
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
    header = ["Dạng", "Nội dung", "A", "B", "C", "D", "E", "F", "Đáp án", "Mức độ", "Lời giải"]
    ws.append(header)
    for q in SAMPLE_QUESTIONS:
        choices = (q["choices"] + [""] * 6)[:6]
        ws.append([q["type"], q["content"], *choices, q["answer"], q["level"], q["explanation"]])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2563EB")
    widths = {"A": 14, "B": 60, "C": 18, "D": 18, "E": 18, "F": 18, "G": 8, "H": 8, "I": 12, "J": 14, "K": 50}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"

    guide = wb.create_sheet("Hướng dẫn")
    for line in [
        "Chỉ sheet đầu tiên được đọc. Giữ nguyên dòng tiêu đề.",
        "Cột “Dạng”: Trắc nghiệm / Đúng sai / Trả lời ngắn (để trống thì hệ thống tự đoán).",
        "Cột “Nội dung”: nội dung câu hỏi (bắt buộc). Công thức LaTeX đặt giữa hai dấu $.",
        "Trắc nghiệm: cột A–F là các lựa chọn (ít nhất 2); “Đáp án” là một chữ cái A–F.",
        "Đúng sai: cột A–D là 4 ý a, b, c, d; “Đáp án” ghi Đ/S cho từng ý, VD: Đ S Đ S.",
        "Trả lời ngắn: bỏ trống A–F; “Đáp án” là con số, nhiều đáp án chấp nhận ngăn cách bằng dấu ;",
        "Cột “Mức độ” (không bắt buộc): Nhận biết / Thông hiểu / Vận dụng. Để trống sẽ dùng mức độ chọn khi tải lên.",
        "Cột “Lời giải” (không bắt buộc): hiện cho học sinh sau khi nộp bài.",
    ]:
        guide.append([line])
    guide.column_dimensions["A"].width = 110

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
