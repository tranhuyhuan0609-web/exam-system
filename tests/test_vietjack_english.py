"""VietJack English quizzes: gaps students fill in, letter options "a." with underlined sounds, "Câu N." headers
followed by "Question N." lines."""

from app.pipeline.parsers.vietjack import parse

URL = "https://vietjack.com/trac-nghiem-tieng-anh-8-moi/unit-3-vocabulary-and-grammar.jsp"


def _answer(*paragraphs: str) -> str:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return f'<section class="toggle"><label>Hiển thị đáp án</label><div class="toggle-content">{body}</div></section>'


PAGE = (
    "<html><body><div class='content'>"
    "<p><strong>Câu 1. Use the correct form of the words.</strong></p>"
    "<p>When you grow up, you will have more independence and more "
    '<input size="14" type="text" /> as well. (responsible)</p>'
    + _answer(
        "<strong>When you grow up, you will have more independence and more ________ as well. (responsible)</strong>",
        "<strong>Giải thích:</strong> independence là danh từ",
        "<strong>Đáp án:</strong> When you grow up, you will have more independence and more "
        "<strong><ins>responsibility</ins></strong> as well.",
    )
    + "<p><strong>Câu 2. Use the correct form of the words.</strong></p>"
    '<p>She is no longer in her <input size="14" type="text" /> (adolescent).</p>'
    + _answer(
        "<strong>She is no longer in her..................... (adolescent).</strong>",
        "<strong>Đáp án: adolescence</strong>",
    )
    + "<p><strong>Câu 3. Chọn từ có phần gạch chân đọc khác các từ còn lại</strong></p>"
    "<p>a. r<ins>e</ins>member</p><p>b. b<ins>e</ins>come</p><p>c. d<ins>e</ins>sign</p><p>d. diff<ins>e</ins>rent</p>"
    + _answer("r<ins>e</ins>member /rɪˈmɛmbə/", "Đáp án D đọc là /ə/", "Đáp án cần chọn là: d")
    + "<p><strong>Câu 4. Choose the best answer:</strong></p>"
    "<p><strong>Question 4. </strong> She has been _______ because she has a big assignment to complete.</p>"
    "<p>A. frustration</p><p>B. frustrated</p><p>C. frustrating</p><p>D. frustratedly</p>"
    + _answer("=&gt; She has been <strong>frustrated</strong>.", "<strong>Đáp án: B</strong>")
    + "</div></body></html>"
)


def test_english_gaps_options_and_answers_are_read_without_ai():
    questions = {q.external_id.rsplit("#", 1)[1]: q for q in parse(PAGE, URL).questions}
    assert sorted(questions) == ["mu1", "mu2", "mu3", "mu5"]  # one question; the id it had when read as two

    gap = questions["mu1"]
    assert gap.question_type == "SHORT_ANSWER" and gap.accepted_answers == ["responsibility"] and not gap.issues
    assert "more ________ as well" in gap.content  # students see where to write
    assert "________" not in gap.explanation and "independence là danh từ" in gap.explanation
    assert (
        questions["mu2"].accepted_answers == ["adolescence"] and "her ________ (adolescent)" in questions["mu2"].content
    )

    sounds = questions["mu3"]
    assert [c["content"] for c in sounds.choices] == [
        r"r$\underline{\text{e}}$member",
        r"b$\underline{\text{e}}$come",
        r"d$\underline{\text{e}}$sign",
        r"diff$\underline{\text{e}}$rent",
    ]
    assert [c["is_correct"] for c in sounds.choices] == [False, False, False, True] and not sounds.issues
    assert sounds.content == "Chọn từ có phần gạch chân đọc khác các từ còn lại"  # the options are not in the stem

    best = questions["mu5"]
    assert best.content == "Choose the best answer:\nShe has been _______ because she has a big assignment to complete."
    assert [c["is_correct"] for c in best.choices] == [False, True, False, False] and not best.issues
