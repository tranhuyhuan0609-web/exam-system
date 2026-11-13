"""Reading order of positioned PDF line fragments (app.ai.essay_exam._in_reading_order)."""

from app.ai.essay_exam import PdfLine, _in_reading_order


def _line(text: str, ymin: int, xmin: int) -> PdfLine:
    return PdfLine(page=1, ymin=ymin, xmin=xmin, ymax=ymin + 12, xmax=xmin + 100, text=text, has_pua=False)


def _texts(lines: list[PdfLine]) -> list[str]:
    return [ln.text for ln in _in_reading_order(lines)]


def test_fragments_of_one_row_stay_left_to_right_across_a_bucket_boundary() -> None:
    # 227 and 228 fall on two sides of a multiple of 6: a fixed-bucket sort put D before C.
    assert _texts([_line("D.", 227, 500), _line("C.", 228, 100)]) == ["C.", "D."]


def test_rows_are_ordered_top_to_bottom() -> None:
    lines = [_line("C.", 240, 100), _line("B.", 200, 500), _line("D.", 241, 500), _line("A.", 201, 100)]
    assert _texts(lines) == ["A.", "B.", "C.", "D."]


def test_a_row_does_not_grow_by_chaining_close_lines() -> None:
    # Each line is 4 below the previous one, but the third is 8 below the first: it starts a new row.
    lines = [_line("first", 100, 300), _line("second", 104, 200), _line("third", 108, 100)]
    assert _texts(lines) == ["second", "first", "third"]


def test_empty_input() -> None:
    assert _in_reading_order([]) == []
