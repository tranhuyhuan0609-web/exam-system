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
