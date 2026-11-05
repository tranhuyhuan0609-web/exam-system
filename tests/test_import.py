import html
import io
import json
import unicodedata

import pytest
from sqlalchemy import select

from app.import_templates import SAMPLE_QUESTIONS, sample_docx, sample_text, sample_xlsx
from app.models import Chapter, Difficulty, Exam, ExamQuestion, Question, Subject
from app.question_import import ImportFileError, parse_file, parse_text

# ---------------------------------------------------------------- parser


def test_parse_text_supports_answer_line_star_inline_choices_and_multiline_fields():
    text = (
        "ĐỀ KIỂM TRA 15 PHÚT\n"  # preamble is ignored
        "Câu 1: Chu kì khi $\\omega = 4\\pi$ là\n"
        "(chọn đáp án gần đúng nhất)\n"
        "A. $0{,}5$ s\n"
        "B. 2 s\n"
        "C. 0,25 s\tD. 4 s\n"
        "Đáp án: A\n"
        "Mức độ: thông hiểu\n"
        "Lời giải: $T = 2\\pi/\\omega$\n"
        "Thay số được 0,5 s.\n"
        "\n"
        "Câu 2. Câu thứ hai\n"
        "A) Sai\n"
        "*B) Đúng\n"
    )
    q1, q2 = parse_text(text)
    assert q1.label == "Câu 1"
    assert q1.content == "Chu kì khi $\\omega = 4\\pi$ là\n(chọn đáp án gần đúng nhất)"
    assert q1.choices == ["$0{,}5$ s", "2 s", "0,25 s", "4 s"]
    assert q1.correct == 0
    assert q1.difficulty == Difficulty.UNDERSTANDING.value
    assert q1.explanation == "$T = 2\\pi/\\omega$\nThay số được 0,5 s."
    assert (q2.choices, q2.correct, q2.difficulty) == (["Sai", "Đúng"], 1, None)
    assert not q1.errors and not q2.errors


def test_parse_text_handles_decomposed_unicode_from_word():
    text = unicodedata.normalize("NFD", "Câu 1: Hỏi?\nA. x\nB. y\nĐáp án: B")
    (q,) = parse_text(text)
    assert q.correct == 1


def test_parse_file_reports_per_question_errors():
    text = (
        "Câu 1: Thiếu đáp án\nA. x\nB. y\n"
        "Câu 2: Một lựa chọn\nA. x\nĐáp án: A\n"
        "Câu 3: Sai mức độ\nA. x\nB. y\nĐáp án: A\nMức độ: khó"
    )
    q1, q2, q3 = parse_file("de.txt", text.encode())
    assert any("Chưa có đáp án" in e for e in q1.errors)
    assert any("từ 2 đến 6" in e for e in q2.errors)
    assert any("Mức độ" in e for e in q3.errors)


def test_parse_file_rejects_unsupported_or_empty_files():
    with pytest.raises(ImportFileError, match="Chỉ hỗ trợ"):
        parse_file("de.pdf", b"x")
    with pytest.raises(ImportFileError, match="Không tìm thấy câu hỏi"):
        parse_file("de.txt", "Chỉ có tiêu đề".encode())
    with pytest.raises(ImportFileError, match="Excel"):
        parse_file("de.xlsx", b"not a zip")


@pytest.mark.parametrize(
    ("filename", "make"),
    [("mau.txt", lambda: sample_text().encode("utf-8-sig")), ("mau.docx", sample_docx), ("mau.xlsx", sample_xlsx)],
)
def test_sample_files_import_cleanly(filename, make):
    questions = parse_file(filename, make())
    assert len(questions) == len(SAMPLE_QUESTIONS)
    assert [q.type for q in questions] == ["MULTIPLE_CHOICE", "MULTIPLE_CHOICE", "TRUE_FALSE", "SHORT_ANSWER"]
    for parsed, sample in zip(questions, SAMPLE_QUESTIONS, strict=True):
        assert parsed.errors == []
        assert parsed.content == sample["content"]
        assert parsed.choices == sample["choices"]
        assert parsed.explanation == sample["explanation"]
    assert "ABCD"[questions[0].correct] == "A"
    assert questions[2].truths == [True, False, True, False]
    assert questions[3].answers == ["50"]


def test_docx_reads_choices_inside_tables():
    from docx import Document

    doc = Document()
    doc.add_paragraph("Câu 1: Câu có bảng lựa chọn")
    table = doc.add_table(rows=2, cols=2)
    for cell, text in zip([c for row in table.rows for c in row.cells], ["A. 1", "B. 2", "C. 3", "*D. 4"], strict=True):
        cell.text = text
    buffer = io.BytesIO()
    doc.save(buffer)
    (q,) = parse_file("bang.docx", buffer.getvalue())
    assert (q.choices, q.correct, q.errors) == (["1", "2", "3", "4"], 3, [])


def test_xlsx_with_custom_headers_and_bad_answer():
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["Câu hỏi", "Đáp án A", "Đáp án B", "Đáp án C", "Đáp án đúng", "Lời giải"])
    wb.active.append(["1 + 1 = ?", 1, 2, 3, "b", "Hiển nhiên"])
    wb.active.append(["Sai đáp án", "x", "y", None, "Z", None])
    buffer = io.BytesIO()
    wb.save(buffer)
    ok, bad = parse_file("de.xlsx", buffer.getvalue())
    assert (ok.choices, ok.correct, ok.errors) == (["1", "2", "3"], 1, [])
    assert any("không hợp lệ" in e for e in bad.errors)


# ---------------------------------------------------------------- endpoints


@pytest.fixture
def lesson_scope(db):
    async def _make(s):
        subject = Subject(name="Vật lí 11", slug="vat-li-11")
        subject.chapters = [Chapter(name="Chương 1", position=1)]
        s.add(subject)
        await s.flush()
        return subject.id, subject.chapters[0].id

    return db(_make)


def upload(client, scope, content: bytes, filename="de.txt", **extra):
    data = {"scope": scope, "default_difficulty": "RECOGNITION", **extra}
    return client.post(
        "/admin/questions/import/preview", data=data, files={"file": (filename, content, "application/octet-stream")}
    )


def confirm_from_preview(client, preview_html: str, selected, **extra):
    start = preview_html.index('<textarea name="payload" hidden>') + len('<textarea name="payload" hidden>')
    payload = preview_html[start : preview_html.index("</textarea>", start)]
    data = {
        "scope": extra.pop("scope"),
        "default_difficulty": "RECOGNITION",
        "payload": html.unescape(payload),
        "selected": [str(i) for i in selected],
        **extra,
    }
    return client.post("/admin/questions/import/confirm", data=data, follow_redirects=False)


def test_import_preview_saves_nothing_and_confirm_creates_questions_and_exam(admin_client, db, lesson_scope):
    subject_id, chapter_id = lesson_scope
    preview = upload(admin_client, f"c:{chapter_id}", sample_text().encode(), create_exam="on", duration_minutes="20")
    assert preview.status_code == 200
    assert "Xem trước" in preview.text
    assert db(lambda s: s.scalar(select(Question.id))) is None

    r = confirm_from_preview(
        admin_client,
        preview.text,
        selected=[0, 1, 2, 3],
        scope=f"c:{chapter_id}",
        create_exam="on",
        exam_title="de",
        duration_minutes="20",
    )
    assert r.status_code == 303
    questions = db(lambda s: s.scalars(select(Question).order_by(Question.id)))
    assert len(questions) == 4
    assert all((q.subject_id, q.chapter_id, q.lesson_id) == (subject_id, chapter_id, None) for q in questions)
    assert [q.difficulty for q in questions] == [
        Difficulty.UNDERSTANDING,
        Difficulty.RECOGNITION,
        Difficulty.UNDERSTANDING,
        Difficulty.APPLICATION,
    ]
    assert [q.question_type.value for q in questions] == [
        "MULTIPLE_CHOICE",
        "MULTIPLE_CHOICE",
        "TRUE_FALSE",
        "SHORT_ANSWER",
    ]
    assert [c["is_correct"] for c in questions[2].choices] == [True, False, True, False]
    assert questions[3].choices == [] and questions[3].accepted_answers == ["50"]
    assert all(len({c["id"] for c in q.choices}) == 4 for q in questions[:3])

    exam = db(lambda s: s.scalar(select(Exam)))
    assert (exam.title, exam.duration_seconds, exam.is_published) == ("de", 1200, False)
    assert r.headers["location"] == f"/admin/exams/{exam.id}"
    order = db(lambda s: s.scalars(select(ExamQuestion.question_id).order_by(ExamQuestion.position)))
    assert order == [q.id for q in questions]


def test_import_flags_duplicates_and_rejects_tampered_payload(admin_client, db, lesson_scope):
    _, chapter_id = lesson_scope
    scope = f"c:{chapter_id}"
    first = upload(admin_client, scope, sample_text().encode())
    confirm_from_preview(admin_client, first.text, selected=[0, 1, 2, 3], scope=scope)

    again = upload(admin_client, scope, sample_text().encode())
    assert "Trùng" in again.text
    assert again.text.count('name="selected" value=') == 4
    assert "checked" not in again.text.split('name="selected"')[1].split(">")[0]

    # A payload edited in the browser is re-validated: an invalid question is skipped.
    bad = json.dumps([{"label": "x", "content": "Hỏi", "choices": ["a"], "correct": 0}])
    r = admin_client.post(
        "/admin/questions/import/confirm",
        data={"scope": scope, "default_difficulty": "RECOGNITION", "payload": bad, "selected": ["0"]},
    )
    assert "Không có câu hợp lệ" in r.text
    assert len(db(lambda s: s.scalars(select(Question.id)))) == 4


def test_import_form_errors(admin_client, lesson_scope):
    _, chapter_id = lesson_scope
    r = upload(admin_client, "", b"Cau 1: x\nA. a\nB. b\nDap an: A")
    assert r.status_code == 400
    assert "Vui lòng chọn môn" in r.text
    r = upload(admin_client, f"c:{chapter_id}", b"%PDF", filename="de.pdf")
    assert r.status_code == 400
    assert "Chỉ hỗ trợ" in r.text


def test_template_downloads(admin_client):
    for ext in ("xlsx", "docx", "txt"):
        r = admin_client.get(f"/admin/questions/import/template.{ext}")
        assert r.status_code == 200
        assert "attachment" in r.headers["content-disposition"]
