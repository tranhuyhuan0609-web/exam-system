"""Pure unit tests for A07 Study-Guide Mixed Splitter & 3-Column Rubric Matrix (P36)."""

from __future__ import annotations

from decimal import Decimal

from app.modules.study_guide.public import (
    ContentBlockKind,
    ExtractedQuestionKind,
    PageSegmentInput,
    TableCellSpan,
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
