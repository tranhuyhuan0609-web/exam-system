"""Pure unit tests for A07 Study-Guide Mixed Splitter & 3-Column Rubric Matrix (P36)."""

from __future__ import annotations

import unicodedata
from decimal import Decimal

import pytest

from app.modules.extraction.public import (
    MAX_GRID_COLS,
    MAX_GRID_ROWS,
    MAX_SPAN,
    ContentBlockKind,
    ExtractedQuestionKind,
    PageSegmentInput,
    TableCellSpan,
    TableGridLimitError,
    expand_table_grid,
    parse_three_column_rubric,
    split_study_guide_mixed,
)


def test_splits_mixed_bayes_study_guide_preserving_provenance_and_4_question_types() -> None:
    segments = [
        PageSegmentInput(
            page_no=1,
            bbox=(40.0, 50.0, 550.0, 400.0),
            text=(
                "# Chuyên đề Công thức Bayes\n"
                "Công thức Bayes:\n"
                "P(A|B) = P(B|A)P(A) / P(B)\n"
                "Phương pháp giải:\n"
                "Bước 1: Xác định hệ đầy đủ biến cố.\n"
                "Ví dụ 1: Trong xét nghiệm y sinh...\n"
                "Chú ý: Không nhầm lẫn P(A|B) với P(B|A).\n"
            ),
        ),
        PageSegmentInput(
            page_no=2,
            bbox=(40.0, 60.0, 550.0, 780.0),
            text=(
                "## BÀI TẬP TỰ LUYỆN\n"
                "Câu 1: Giá trị P(A|B) là:\n"
                "A. 0.25\n"
                "B. 0.50\n"
                "C. 0.75\n"
                "D. 0.90\n"
                "Câu 2: Xét các mệnh đề sau:\n"
                "a) P(A) > 0\n"
                "b) P(B) = P(B|A)P(A) + P(B|A')P(A')\n"
                "c) Hai biến cố độc lập nếu P(A|B) = P(A)\n"
                "d) Tổng xác suất bằng 2\n"
                "Câu 3: Trả lời ngắn (làm tròn đến hàng phần trăm): tính P(A|B).\n"
                "Câu 4: Chứng minh công thức xác suất toàn phần.\n"
            ),
        ),
    ]

    result = split_study_guide_mixed(segments)
    assert result.title == "Chuyên đề Công thức Bayes"
    assert [b.kind for b in result.content_blocks] == [
        ContentBlockKind.FORMULA_BOX,
        ContentBlockKind.METHOD_STEPS,
        ContentBlockKind.WORKED_EXAMPLE,
        ContentBlockKind.TIP_WARNING,
    ]
    assert result.content_blocks[0].anchors[0].page_no == 1

    assert [q.kind for q in result.question_candidates] == [
        ExtractedQuestionKind.SINGLE_CHOICE,
        ExtractedQuestionKind.CLUSTER_TRUE_FALSE,
        ExtractedQuestionKind.SHORT_NUMERIC,
        ExtractedQuestionKind.ESSAY,
    ]
    assert result.question_candidates[0].options["C"] == "0.75"
    assert len(result.question_candidates[1].options) == 4
    assert result.question_candidates[3].anchors[0].page_no == 2


def test_expands_colspan_rowspan_and_parses_3_column_rubric_to_exact_10_points() -> None:
    raw_table = [
        [TableCellSpan("Câu"), TableCellSpan("Nội dung"), TableCellSpan("Điểm")],
        # Câu 1 spans 2 sub-steps horizontally/vertically via rowspan=2 on Câu 1
        [TableCellSpan("Câu 1", rowspan=2), TableCellSpan("Ý 1a: Lập bảng"), TableCellSpan("1,5")],
        [TableCellSpan("Ý 1b: Tính tích phân"), TableCellSpan("2.5")],
        [TableCellSpan("Câu 2", colspan=2), TableCellSpan("6.0")],
    ]
    grid = expand_table_grid(raw_table)
    assert grid[2][0] == "Câu 1"
    assert grid[3][0] == "Câu 2" and grid[3][1] == "Câu 2"

    report = parse_three_column_rubric(raw_table, expected_total_points=Decimal("10.0"))
    assert len(report.rows) == 3
    assert report.total_points == Decimal("10.0")
    assert report.warnings == ()

    bad_report = parse_three_column_rubric(raw_table, expected_total_points=Decimal("8.0"))
    assert len(bad_report.warnings) == 1
    assert "expected 8.0" in bad_report.warnings[0]


def _nfd(text: str) -> str:
    return unicodedata.normalize("NFD", text)


def _row(q: str, body: str, pts: str) -> list[TableCellSpan]:
    return [TableCellSpan(q), TableCellSpan(body), TableCellSpan(pts)]


def test_first_data_row_with_points_unit_is_not_mistaken_for_header_blocker() -> None:
    rows = [_row("Câu 1", "Giải đúng", "0,5 điểm")]
    report = parse_three_column_rubric(rows, expected_total_points=None)
    assert [r.points for r in report.rows] == [Decimal("0.5")]
    assert report.rows[0].question_label == "Câu 1"


def test_real_header_row_is_still_skipped_in_nfc_and_nfd() -> None:
    for header in (_row("Câu", "Nội dung", "Điểm"), _row(_nfd("Câu"), _nfd("Nội dung"), _nfd("Điểm"))):
        report = parse_three_column_rubric([header, _row("Câu 1", "x", "1")], expected_total_points=None)
        assert [r.points for r in report.rows] == [Decimal("1")]
        assert report.warnings == ()


def test_header_only_applies_to_first_row() -> None:
    rows = [_row("Câu", "Nội dung", "Điểm"), _row("Câu", "Nội dung", "Điểm")]
    report = parse_three_column_rubric(rows, expected_total_points=None)
    assert report.rows == ()
    assert len(report.warnings) == 1


def test_points_parsing_sums_terms_and_rejects_ambiguous_or_negative_values() -> None:
    cases = {
        "0,25 + 0,25": Decimal("0.50"),
        "0.25+0.25+0.5": Decimal("1.00"),
        "1,0 đ": Decimal("1.0"),
        _nfd("0,5 điểm"): Decimal("0.5"),
        "10": Decimal("10"),
    }
    for raw, expected in cases.items():
        report = parse_three_column_rubric([_row("Câu 1", "x", raw)], expected_total_points=None)
        assert [r.points for r in report.rows] == [expected], raw

    for raw in ("abc", "", "0,5 hoặc 1", "0,5; 1", "1.000.000", "NaN", "inf", "0,5 điểm mỗi ý"):
        report = parse_three_column_rubric([_row("Câu 1", "x", raw)], expected_total_points=None)
        assert report.rows == (), raw
        assert report.warnings, raw


def test_negative_points_are_not_silently_turned_positive() -> None:
    report = parse_three_column_rubric([_row("Câu 1", "x", "-1")], expected_total_points=None)
    assert report.rows == ()
    assert report.total_points == Decimal("0")
    assert any("negative" in w for w in report.warnings)


def test_rubric_reconciliation_warns_on_mismatch_and_skips_blank_and_short_rows() -> None:
    rows = [_row("Câu 1", "a", "2"), _row("", "", ""), [TableCellSpan("Câu 2"), TableCellSpan("only two")]]
    report = parse_three_column_rubric(rows, expected_total_points=Decimal("10"))
    assert report.total_points == Decimal("2")
    assert any("unparseable" in w for w in report.warnings)
    assert any("expected 10" in w for w in report.warnings)
    assert parse_three_column_rubric([], expected_total_points=None).rows == ()


def test_expand_table_grid_rejects_huge_spans_and_grids() -> None:
    with pytest.raises(TableGridLimitError):
        expand_table_grid([[TableCellSpan("x", rowspan=3_000_000)]])
    with pytest.raises(TableGridLimitError):
        expand_table_grid([[TableCellSpan("x", colspan=3_000_000)]])
    with pytest.raises(TableGridLimitError):
        expand_table_grid([[TableCellSpan("x")] for _ in range(MAX_GRID_ROWS + 1)])
    wide = [[TableCellSpan("x") for _ in range(MAX_GRID_COLS + 1)]]
    with pytest.raises(TableGridLimitError):
        expand_table_grid(wide)
    # 300 rows x 40 cols = 12,000 cells > MAX_GRID_CELLS (10,000)
    too_many_cells = [[TableCellSpan("x") for _ in range(40)] for _ in range(300)]
    with pytest.raises(TableGridLimitError):
        expand_table_grid(too_many_cells)
    # at the limits it still works
    ok = expand_table_grid([[TableCellSpan("x", rowspan=MAX_SPAN, colspan=MAX_SPAN)]])
    assert len(ok) == MAX_SPAN and len(ok[0]) == MAX_SPAN
    for bad in (0, -1):
        with pytest.raises(ValueError):
            expand_table_grid([[TableCellSpan("x", rowspan=bad)]])
        with pytest.raises(ValueError):
            expand_table_grid([[TableCellSpan("x", colspan=bad)]])
    assert expand_table_grid([]) == []


def test_expand_table_grid_pads_ragged_rows() -> None:
    grid = expand_table_grid([[TableCellSpan("a"), TableCellSpan("b")], [TableCellSpan("c")]])
    assert grid == [["a", "b"], ["c", ""]]


def test_theory_under_phan_i_is_kept_as_content_blocker() -> None:
    text = "# Bayes\nPhần I. Lý thuyết\nĐịnh nghĩa xác suất có điều kiện.\nBài tập tự luyện\nCâu 1: abc"
    result = split_study_guide_mixed([PageSegmentInput(1, text)])
    assert len(result.content_blocks) == 1
    assert "xác suất có điều kiện" in result.content_blocks[0].body_text
    assert [q.label for q in result.question_candidates] == ["Câu 1"]


def test_only_bai_tap_headings_open_the_exercise_zone_in_nfc_and_nfd() -> None:
    for heading in ("BÀI TẬP", "Phần II. Bài tập vận dụng", "## Bài tập tự luyện", "PHẦN 3: BÀI TẬP"):
        for form in (heading, _nfd(heading)):
            result = split_study_guide_mixed([PageSegmentInput(1, f"Lý thuyết.\n{form}\nCâu 1: x")])
            assert len(result.question_candidates) == 1, form
            assert len(result.content_blocks) == 1, form
    for heading in ("Phần I. Lý thuyết", "Phần II. Phương pháp", "Phần III"):
        result = split_study_guide_mixed([PageSegmentInput(1, f"{heading}\nNội dung.\nCâu 1: x")])
        assert result.question_candidates == (), heading
        assert result.content_blocks, heading


def test_question_labelled_bai_tap_number_is_not_swallowed_as_heading() -> None:
    text = "Bài tập tự luyện\nCâu 1: a\nBài tập 2: b"
    result = split_study_guide_mixed([PageSegmentInput(1, text)])
    assert [q.label for q in result.question_candidates] == ["Câu 1"]
    assert "Bài tập 2: b" in result.question_candidates[0].stem_text


def test_exercise_text_before_first_question_is_reported_not_dropped() -> None:
    text = "BÀI TẬP\nLàm bài trong 10 phút.\nCâu 1: x"
    result = split_study_guide_mixed([PageSegmentInput(1, text)])
    assert result.unassigned_exercise_text == ("Làm bài trong 10 phút.",)
    assert result.warnings
    clean = split_study_guide_mixed([PageSegmentInput(1, "BÀI TẬP\nCâu 1: x")])
    assert clean.unassigned_exercise_text == () and clean.warnings == ()


def test_splitter_empty_and_blank_input() -> None:
    empty = split_study_guide_mixed([], default_title="T")
    assert empty.title == "T" and empty.content_blocks == () and empty.question_candidates == ()
    blank = split_study_guide_mixed([PageSegmentInput(1, "\n  \n")])
    assert blank.content_blocks == () and blank.question_candidates == ()


def test_question_labels_and_options_parse_in_nfd() -> None:
    text = _nfd("BÀI TẬP\nCâu 1: Chọn đáp án đúng\nA. Một\nB. Hai\n")
    result = split_study_guide_mixed([PageSegmentInput(1, text)])
    assert result.question_candidates[0].kind == ExtractedQuestionKind.SINGLE_CHOICE
    assert result.question_candidates[0].label == "Câu 1"
    assert result.question_candidates[0].options["B"] == "Hai"


def test_cluster_requires_all_four_distinct_statements() -> None:
    text = "BÀI TẬP\nCâu 1: x\na) 1\nb) 2\nc) 3\nc) 4\n"
    kind = split_study_guide_mixed([PageSegmentInput(1, text)]).question_candidates[0].kind
    assert kind == ExtractedQuestionKind.ESSAY


def test_title_only_taken_before_any_content() -> None:
    result = split_study_guide_mixed([PageSegmentInput(1, "Mở đầu.\n# Tiêu đề muộn")])
    assert result.title == "Chuyên đề"
