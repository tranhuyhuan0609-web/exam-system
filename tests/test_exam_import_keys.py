"""Exam import keeps only the questions: the answers and marking schemes printed in the file are attached to their
question, setters' names and matrix tags are dropped, a marking-scheme file alone gives no question."""

import asyncio

from app.ai.essay_exam import clean_paper_text, parse_essay_html
from app.ai.pipeline import run_pipeline
from app.ai.schemas import ExtractedChoice, ExtractedQuestion, Solution
from app.application.library_publish import answered_in_file, exam_code, subject_name
from app.infrastructure.storage import LocalStorage
from tests.test_ai_import import _noop_progress, make_pdf

PAPER = """
<table><tr><td><p>SỞ GD&amp;ĐT HƯNG YÊN</p><p><strong>TRƯỜNG THPT CHUYÊN HƯNG YÊN</strong></p></td>
<td><p><strong>ĐỀ THI CHỌN HỌC SINH GIỎI</strong></p></td></tr></table>
<p><strong>Câu 1.</strong> (<em>4,00 điểm</em>)</p>
<p><strong>1.1. Trái Đất (NB 0,5 – VDC 1,5):</strong> So sánh hiện tượng đêm trắng và ngày địa cực?</p>
<p><strong>1.2. Thổ nhưỡng (TH 1,0 -- VD 1,0):</strong> Giải thích sự phân bố đất theo khí hậu?</p>
<p><strong>Câu 2. (3,0 điểm)</strong></p>
<p>Tại sao đô thị có vai trò thúc đẩy phát triển kinh tế - xã hội của vùng?</p>
<p>SĐT: 0348 509 263</p>
<p>------------ HẾT ------------</p>
<table><tr><th><p><strong>HƯỚNG DẪN CHẤM</strong></p></th></tr></table>
<table>
<tr><th>Câu</th><th>Ý</th><th>Đáp án</th><th>Điểm</th></tr>
<tr><td><strong>1</strong></td><td><p>1.1</p><p>1.2</p></td>
<td><p>- Giống nhau: thời gian chiếu sáng dài.</p><p>- Khác nhau: ngày địa cực Mặt Trời không lặn.</p></td>
<td><p>0,5</p><p>0,25</p></td></tr>
<tr><td></td><td></td><td><p>Đất phân bố theo đới khí hậu và sinh vật.</p></td><td>1,0</td></tr>
<tr><td><strong>2</strong></td><td></td><td><p>Đô thị là trung tâm kinh tế, thu hút đầu tư.</p></td><td>1,5</td></tr>
</table>
<p><strong>Người ra đề: Trần Thị Huấn</strong></p>
"""


def test_an_essay_paper_keeps_its_questions_and_takes_its_marking_scheme_as_solutions():
    paper = parse_essay_html(PAPER)
    assert not paper.key_only and [q.number for q in paper.questions] == [1, 2]
    first, second = (q.content for q in paper.questions)
    assert first.startswith("1.1. Trái Đất: So sánh") and "NB 0,5" not in first and "VD 1,0" not in first
    assert "SỞ GD" not in first and "TRƯỜNG" not in first
    assert second == "Tại sao đô thị có vai trò thúc đẩy phát triển kinh tế - xã hội của vùng?"  # no phone, no HẾT
    assert paper.points == {0: 4.0, 1: 3.0}
    assert paper.explanations[0].startswith("- Giống nhau") and "Đất phân bố theo đới" in paper.explanations[0]
    assert "0,25" not in paper.explanations[0] and "1.1" not in paper.explanations[0]  # no score or part columns
    assert paper.explanations[1] == "Đô thị là trung tâm kinh tế, thu hút đầu tư."
    assert all("Huấn" not in text for text in [first, second, *paper.explanations.values()])


def test_a_marking_scheme_alone_gives_no_question():
    key_only = PAPER[PAPER.index("<table><tr><th><p><strong>HƯỚNG DẪN CHẤM") :]
    assert parse_essay_html(key_only).key_only


def test_setters_and_exam_room_lines_are_cleaned_from_any_paper():
    text = (
        "Họ và tên thí sinh: ........ Số báo danh: ....\nTính $2+2$.\n"
        "Giáo viên ra đề: Nguyễn Văn A\nEmail: gv@school.edu.vn"
    )
    assert clean_paper_text(text) == "Tính $2+2$."


class KeyReader:
    """A model that reads the answer table (Câu 1: C) and the marking scheme of the essay printed in the file."""

    def __init__(self):
        self.solved: list[list[int]] = []

    models_used = {"fake"}

    async def extract(self, pages):
        mc = ExtractedQuestion(
            number=1,
            page=1,
            type="MULTIPLE_CHOICE",
            content="Tính $2 \\cdot 4$.",
            choices=[
                ExtractedChoice(label=label, content=v) for label, v in zip("ABCD", ["6", "7", "8", "9"], strict=True)
            ],
            key_label="C",
        )
        essay = ExtractedQuestion(
            number=2,
            page=1,
            type="ESSAY",
            content="Chứng minh $\\sqrt 2$ vô tỉ.",
            choices=[],
            key="Giả sử $\\sqrt2 = p/q$ …",
        )
        return [mc, essay]

    async def solve(self, questions, figures):
        self.solved.append([q.number for q in questions])
        return [
            Solution(
                number=q.number,
                step_by_step_solution="2·4 = 8",
                final_value="8",
                matches_an_option=True,
                correct_label="C",
                short_answer=None,
                difficulty="RECOGNITION",
            )
            for q in questions
        ]


def test_answers_read_in_the_file_are_attached_to_their_question(tmp_path):
    storage = LocalStorage(tmp_path)
    asyncio.run(storage.put("raw/de.pdf", make_pdf(1)))
    reader = KeyReader()

    def run(solve):
        return asyncio.run(
            run_pipeline(
                job_id=1,
                source_key="raw/de.pdf",
                storage=storage,
                provider=reader,
                soffice="soffice",
                default_difficulty="UNDERSTANDING",
                progress=_noop_progress,
                solve=solve,
            )
        )[1]

    mc, essay = run(solve=True)
    assert reader.solved == [[1]]  # the essay has its marking scheme: not sent to the solver
    assert mc["correct"] == 2 and mc["printed"] == "C" and mc["flags"] == []
    assert essay["explanation"].startswith("Giả sử") and essay["key_from_file"] and essay["flags"] == []
    assert answered_in_file([mc, essay]) == ""  # the file's answers settle the paper

    reader.solved.clear()
    mc, _ = run(solve=False)  # saving AI calls: what the file answers is not solved again
    assert reader.solved == [] and mc["correct"] == 2 and "no_solution" not in mc["flags"]


def test_library_names_are_exam_subject_number():
    assert exam_code(["Ôn thi THPT – Toán", "Đề thi THPT 2024"]) == "THPTQG"
    assert exam_code(["Luyện thi HSG", "Vật lí", "Duyên hải & ĐBBB 2024"]) == "HSG-DHBB"
    assert exam_code(["Ôn thi vào 10 – Toán"]) == "TS10"
    assert subject_name(["Luyện thi HSG", "Địa lý", "x"]) == "Địa lí"


def test_a_variation_table_in_latex_becomes_table_rows_or_goes_when_the_figure_shows_it():
    from app.ai.pipeline import _tables
    from app.domain.latex import repair_latex

    # What a model sent, once JSON-decoded: "\nearrow" lost its backslash-n to a line break
    raw = (
        r"Cho bảng biến thiên: \begin{center} \begin{tabular}{|c|ccc|} \hline $x$ & $-\infty$ & 1 & $+\infty$ "
        r"\\ \hline $f(x)$ & 3 & " + "\n" + r"earrow & | \\ \hline \end{tabular} \end{center} Tiệm cận đứng là"
    )
    fixed = repair_latex(raw)
    assert r"\nearrow" in fixed
    rows = _tables(fixed, figure=None)
    assert "tabular" not in rows and r"$x$ | $-\infty$ | 1 | $+\infty$" in rows
    assert r"$f(x)$ | 3 | $\nearrow$ | ‖" in rows  # the double bar is a cell, not a column separator
    assert _tables(fixed, figure="figures/1.png") == "Cho bảng biến thiên:\nTiệm cận đứng là"


def test_a_gemini_timeout_moves_to_the_next_model_instead_of_failing_the_file():
    import pytest

    from app.ai.provider import AIError, GeminiProvider
    from app.ai.schemas import ExtractionResult

    calls = []

    class Models:
        async def generate_content(self, model, contents, config):
            calls.append(model)
            raise TimeoutError

    provider = GeminiProvider("k", ["a", "b"], base_delay=0, max_rounds=1)
    fake = type("C", (), {"aio": type("A", (), {"models": Models()})()})()
    provider._client = provider._pool._clients["k"] = fake  # one key: the provider's own client
    with pytest.raises(AIError, match="quá thời gian chờ"):
        asyncio.run(provider._generate(["x"], ExtractionResult, models=["a", "b"]))
    assert calls == ["a", "b"]  # every model tried, then a clear error (no empty "Lỗi hệ thống")


def test_essay_pdf_code_first_7_steps_and_flags(tmp_path):
    """Steps 1–5 & 7: PyMuPDF dict extraction, sub-parts intact, figure captions, self-verification, flags, matrix diff."""  # noqa: E501
    import pymupdf

    from app.ai.essay_exam import (
        extract_matrix_difficulty,
        match_essay_header,
        parse_figure_captions,
        read_essay_pdf,
        validate_essay_split,
    )

    # Step 1: Regex header matching
    assert match_essay_header("Bài 1. (5,0 điểm) – DAO ĐỘNG CƠ HỌC") == (1, 5.0, "DAO ĐỘNG CƠ HỌC")
    assert match_essay_header("Câu 2: (3,5 điểm)") == (2, 3.5, "")

    # Step 3: Figure caption mapping ("Hình 1.1" in stem -> "stem", "Hình 1.2" in part 3 -> "3")
    sample_content = (
        "Cho cơ hệ như Hình 1.1 gồm lò xo nhẹ độ cứng k.\n"
        "1. Viết phương trình chuyển động.\n"
        "2a) Tính chu kỳ dao động.\n"
        "3. Khi gắn thêm vật như Hình 1.2 thì biên độ bằng bao nhiêu?"
    )
    assert parse_figure_captions(sample_content) == {"Hình 1.1": "stem", "Hình 1.2": "3"}

    # Step 4: Self-verification (declared count, contiguous 1..N, total points)
    q1 = ExtractedQuestion(number=1, page=1, type="ESSAY", content="Đề bài 1", choices=[])
    q2 = ExtractedQuestion(number=2, page=1, type="ESSAY", content="Đề bài 2", choices=[])
    assert validate_essay_split([q1, q2], {0: 5.0, 1: 5.0}, declared_count=2, declared_total_points=10.0)
    assert not validate_essay_split([q1, q2], {0: 5.0, 1: 5.0}, declared_count=5, declared_total_points=10.0)

    # Step 7: Matrix difficulty extraction
    assert extract_matrix_difficulty("1.1. Trái Đất (NB 0,5 – VDC 1,5): So sánh") == "ADVANCED_APPLICATION"
    assert extract_matrix_difficulty("Câu hỏi tự luận không có ma trận") is None

    # Build a PDF with Unicode Vietnamese text layer + vector figure + embedded answer section
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_htmlbox(pymupdf.Rect(72, 40, 520, 75), "SỞ GD&ĐT HÀ NỘI - ĐỀ THI HỌC SINH GIỎI (Đề thi gồm 02 bài)")
    p1.insert_htmlbox(pymupdf.Rect(72, 80, 520, 105), "Bài 1. (5,0 điểm) – DAO ĐỘNG CƠ HỌC")
    p1.insert_htmlbox(
        pymupdf.Rect(72, 110, 520, 135),
        "Cho con lắc lò xo bố trí như Hình 1.1 trên mặt phẳng ngang (NB 1,0 – VDC 4,0).",
    )
    p1.draw_rect(pymupdf.Rect(120, 140, 260, 210), color=(0, 0, 0), width=2)
    p1.insert_htmlbox(pymupdf.Rect(72, 225, 520, 248), "1. Tìm vị trí cân bằng của vật.")
    p1.insert_htmlbox(pymupdf.Rect(72, 250, 520, 273), "2a) Tính vận tốc cực đại của vật.")
    p1.insert_htmlbox(pymupdf.Rect(72, 275, 520, 300), "3. Đồ thị năng lượng có dạng như Hình 1.2, tính cơ năng.")
    p1.insert_htmlbox(pymupdf.Rect(72, 315, 520, 340), "Bài 2. (5,0 điểm) – ĐIỆN XOAY CHIỀU")
    p1.insert_htmlbox(pymupdf.Rect(72, 345, 520, 375), "1. Tính tổng trở của mạch khi chưa có đoạn đề dẫn ở đầu bài.")
    p1.insert_htmlbox(pymupdf.Rect(72, 385, 520, 410), "------------ HẾT ------------")
    p1.insert_htmlbox(pymupdf.Rect(72, 415, 520, 440), "HƯỚNG DẪN CHẤM")
    p1.insert_htmlbox(pymupdf.Rect(72, 445, 520, 480), "Bài 1: Vị trí cân bằng x = 0, cơ năng W = 0,5 J.")

    pdf_path = tmp_path / "hsg_vatli.pdf"
    doc.save(pdf_path)
    doc.close()

    paper = read_essay_pdf(pdf_path)
    assert paper is not None
    assert [q.number for q in paper.questions] == [1, 2]
    assert paper.points == {0: 5.0, 1: 5.0}
    # Sub-parts 1., 2a), 3. stay intact inside Bài 1's content
    assert "1. Tìm vị trí cân bằng" in paper.questions[0].content
    assert "2a) Tính vận tốc cực đại" in paper.questions[0].content
    assert "3. Đồ thị năng lượng" in paper.questions[0].content
    assert paper.figure_captions[0] == {"Hình 1.1": "stem", "Hình 1.2": "3"}
    assert 0 in paper.figures  # cropped from vector rect
    assert paper.matrix_difficulties[0] == "ADVANCED_APPLICATION"
    # Bài 2 started directly with "1. Tính tổng trở..." without an introductory stem -> flagged missing_stem
    assert "missing_stem" in paper.question_flags.get(1, [])
    # Embedded answer for Bài 1 extracted directly
    assert "W = 0,5 J" in paper.explanations.get(0, "")

    # Run pipeline: Bài 1 has embedded answer in file, Bài 2 is flagged missing_stem -> AI solver is NEVER called!
    storage = LocalStorage(tmp_path)
    asyncio.run(storage.put("raw/hsg.pdf", pdf_path.read_bytes()))
    reader = KeyReader()
    _, drafts = asyncio.run(
        run_pipeline(
            job_id=99,
            source_key="raw/hsg.pdf",
            storage=storage,
            provider=reader,
            soffice="soffice",
            default_difficulty="HSG",
            progress=_noop_progress,
            solve=True,
        )
    )
    assert reader.solved == []  # neither question sent to AI solver
    d1, d2 = drafts
    assert d1["difficulty"] == "ADVANCED_APPLICATION" and d1["difficulty_from_matrix"] is True
    assert d1["key_from_file"] is True and "W = 0,5 J" in d1["explanation"]
    assert d2["difficulty"] == "HSG" and d2["difficulty_from_matrix"] is False  # Admin's default, not AI guessed
    assert "missing_stem" in d2["flags"]


def test_library_first_publish_and_student_create_exam(admin_client, make_user, db):
    """Publishing an AI import saves the document to the Library (/library); students go to /library to create an exam."""  # noqa: E501
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.main import app
    from app.models import Difficulty, Exam, ImportJob, JobStatus, LibraryDocument, UserRole
    from tests.factories import make_tree

    async def seed_job(s):
        t = await make_tree(s, {"Vật lí 12": {"Chương 1: Dao động": {}}})
        job = ImportJob(
            original_filename="De-HSG-Vat-Li-12.pdf",
            stored_path="raw/de-hsg.pdf",
            status=JobStatus.DONE,
            root_id=t["Vật lí 12"].id,
            category_id=t["Chương 1: Dao động"].id,
            default_difficulty=Difficulty.HSG,
            page_count=1,
            drafts=[
                {
                    "number": 1,
                    "page": 1,
                    "type": "ESSAY",
                    "content": "Cho con lắc lò xo dao động điều hòa. 1. Tính chu kỳ. 2. Tính cơ năng.",
                    "choices": [],
                    "correct": None,
                    "truths": [],
                    "answers": [],
                    "explanation": "Chu kỳ $T = 2\\pi\\sqrt{m/k}$.",
                    "difficulty": "HSG",
                    "figure": None,
                    "flags": [],
                    "key_from_file": True,
                    "question_id": None,
                }
            ],
        )
        s.add(job)
        await s.flush()
        return job.id

    job_id = db(seed_job)

    # Admin publishes without checking create_exam: saved to Library & Question Bank only
    resp = admin_client.post(
        f"/admin/ai-imports/{job_id}/publish",
        data={"selected": ["0"]},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    doc = db(lambda s: s.scalar(select(LibraryDocument).where(LibraryDocument.import_job_id == job_id)))
    assert doc is not None and doc.exam_id is None and doc.title == "De-HSG-Vat-Li-12"

    # Student visits /library and clicks "Tạo đề luyện tập" on the document
    make_user("student_lib@example.com", role=UserRole.STUDENT)
    student = TestClient(app)
    student.post("/login", data={"email": "student_lib@example.com", "password": "password123"})

    create_resp = student.post(f"/library/{doc.id}/create-exam", follow_redirects=False)
    assert create_resp.status_code == 303
    assert create_resp.headers["location"].startswith("/exams/")
    exam_id = int(create_resp.headers["location"].rsplit("/", 1)[-1])
    exam = db(lambda s: s.get(Exam, exam_id))
    assert exam is not None and exam.is_published and exam.title == "De-HSG-Vat-Li-12"
