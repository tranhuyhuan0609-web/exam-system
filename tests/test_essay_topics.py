"""The section title printed in a question heading classifies the question but is not part of its text."""

from app.ai.essay_exam import strip_topic
from app.ai.pipeline import _QUESTION_LABEL, _draft
from app.ai.schemas import ExtractedQuestion


def test_strip_topic_removes_a_leading_title_line_in_any_case():
    assert strip_topic("CƠ HỌC CHẤT ĐIỂM\nTrong bài tập này", "CƠ HỌC CHẤT ĐIỂM") == "Trong bài tập này"
    assert strip_topic("Cơ học chất điểm. Trong bài tập này", "CƠ HỌC CHẤT ĐIỂM") == "Trong bài tập này"
    assert strip_topic("Trong bài tập này", "CƠ HỌC CHẤT ĐIỂM") == "Trong bài tập này"
    assert strip_topic("Trong bài tập này", None) == "Trong bài tập này"


def test_roman_question_heading_is_removed_from_transcribed_text():
    assert _QUESTION_LABEL.sub("", "Câu I. (4,0 điểm) CƠ HỌC CHẤT ĐIỂM\nx", count=1) == "CƠ HỌC CHẤT ĐIỂM\nx"
    assert _QUESTION_LABEL.sub("", "Câu IV (4,0 điểm) CHUYỂN ĐỘNG", count=1) == "CHUYỂN ĐỘNG"
    assert _QUESTION_LABEL.sub("", "Câu Imperial", count=1) == "Câu Imperial"


def test_draft_keeps_topic_apart_from_content():
    q = ExtractedQuestion(
        number=1, page=1, type="ESSAY", content="CƠ HỌC CHẤT ĐIỂM\nMột vật được ném xiên với vận tốc 20 m/s.", choices=[]
    )
    draft = _draft(q, None, None, False, "HSG", topic="CƠ HỌC CHẤT ĐIỂM")
    assert draft["content"] == "Một vật được ném xiên với vận tốc 20 m/s."
    assert draft["topic_title"] == "CƠ HỌC CHẤT ĐIỂM"
