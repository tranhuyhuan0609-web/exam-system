"""VietJack English quizzes: passages and instructions lead groups of "Question N." items, underlining kept."""

from app.pipeline.parsers.vietjack import parse

PAGE = """<html><body><div class="middle-col">
<p>HOT Phòng luyện đề thi tất cả các môn lớp 1-12</p>
<h3 class="sub-title">Choose the letter A, B, C or D the word that has the underlined part different from others.</h3>
<p><b>Question 1:</b> </p>
<p><b>A.</b> ph<u>o</u>ne</p><p><b>B.</b> ph<u>o</u>to</p>
<p><b>C.</b> badmint<u>o</u>n</p><p><b>D.</b> <ins>o</ins>pen</p>
<section class="toggle"><label>Hiển thị đáp án</label><div class="toggle-content">
<p><b>Chọn đáp án: </b> C</p><p><b>Giải thích:</b> C phát âm là /ə/.</p></div></section>
<ins class="982a9496" data-key="ad"></ins>
<p>Questions 2-3. Read the following passage and mark the letter A, B, C, or D.</p>
<p>The family in Britain is changing. Discipline also (3) ____ from family to family.</p>
<p>Question 2. What is the passage about?</p>
<p>A. Families</p><p>B. Schools</p><p>C. Sports</p><p>D. Music</p>
<section class="toggle"><div class="toggle-content"><p>Đáp án đúng là: A</p><p>Nói về gia đình.</p>
</div></section>
<p>Quảng cáo</p>
<p>Question 3.</p>
<p>A. changes</p><p>B. alters</p><p>C. varies</p><p>D. adjusts</p>
<section class="toggle"><div class="toggle-content"><p>Đáp án đúng là: C</p>
<p>Questions 4-5. Read the following passage and mark the letter A, B, C, or D to fill each blank.</p>
<p>Each family forms its own values. Discipline also (4) ____ between families.</p>
</div></section>
<p>Quảng cáo</p>
<p>Question 4.</p>
<p>A. changes</p><p>B. alters</p><p>C. varies</p><p>D. adjusts</p>
<section class="toggle"><div class="toggle-content"><p>Đáp án đúng là: C</p></div></section>
</div></body></html>"""


def test_english_groups_keep_their_instruction_passage_and_underlining():
    questions = parse(PAGE, "https://vietjack.com/trac-nghiem-tieng-anh-6-moi/unit-1-reading.jsp").questions
    assert [q.issues for q in questions] == [[], [], [], []]
    first, reading, same_passage, cloze = questions
    # The instruction is the question; underlined letters stay (<u> and a bare <ins>, not an ad slot)
    assert first.content.startswith("Choose the letter") and "HOT" not in first.content
    assert [c["content"] for c in first.choices][3] == r"$\underline{\text{o}}$pen"
    assert [c["is_correct"] for c in first.choices] == [False, False, True, False]  # "Chọn đáp án: C"
    assert reading.content.startswith("Questions 2-3. Read") and reading.content.endswith("What is the passage about?")
    assert "Questions 4-5" not in same_passage.explanation  # the next passage was nested in this answer: cut out
    # "Quảng cáo" between two questions is not a new passage: question 3 keeps the passage of 2-3
    assert "(3) ____" in same_passage.content and same_passage.content.endswith("chỗ trống (3).")
    assert cloze.content.startswith("Questions 4-5.") and cloze.content.endswith("chỗ trống (4).")
    assert "Quảng cáo" not in cloze.content
