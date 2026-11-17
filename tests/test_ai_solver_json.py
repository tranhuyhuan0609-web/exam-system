"""`ai_solver._parse_json_object`: what real models send (LaTeX backslashes, chatter around the JSON)."""

from __future__ import annotations

from app.infrastructure.ai_solver import _parse_json_object


def test_latex_backslashes_a_model_leaves_unescaped_do_not_sink_the_answer():
    raw = r'{"answer": "B", "solution_steps": "dT = \frac{Q}{mc} = 55 \alpha", "difficulty_level": "TH"}'
    parsed = _parse_json_object(raw)
    assert parsed is not None and parsed["answer"] == "B"
    assert parsed["solution_steps"] == r"dT = \frac{Q}{mc} = 55 \alpha"


def test_valid_escapes_are_left_alone_and_chatter_around_the_json_is_ignored():
    raw = 'Đây là kết quả: {"answer": "A", "solution_steps": "dòng 1\\ndòng 2 \\"trích\\""} cảm ơn'
    parsed = _parse_json_object(raw)
    assert parsed is not None and parsed["answer"] == "A"
    assert parsed["solution_steps"] == 'dòng 1\ndòng 2 "trích"'


def test_text_without_a_json_object_is_none():
    assert _parse_json_object("không có JSON ở đây") is None
    assert _parse_json_object("[1, 2, 3]") is None
