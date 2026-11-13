"""Public API for app.modules.extraction (A07 Study-Guide Splitter & 3-Col Rubric Matcher)."""

from app.modules.extraction.internal.rubric_table import (
    MAX_GRID_COLS,
    MAX_GRID_ROWS,
    MAX_SPAN,
    RubricParseReport,
    RubricRowItem,
    TableCellSpan,
    TableGridLimitError,
    expand_table_grid,
    parse_three_column_rubric,
)
from app.modules.extraction.internal.splitter import (
    ContentBlockKind,
    ContentCandidateBlock,
    ExtractedQuestionKind,
    PageSegmentInput,
    ProvenanceAnchor,
    QuestionCandidateItem,
    StudyGuideSplitResult,
    split_study_guide_mixed,
)

__all__ = [
    "ContentBlockKind",
    "ContentCandidateBlock",
    "ExtractedQuestionKind",
    "PageSegmentInput",
    "ProvenanceAnchor",
    "QuestionCandidateItem",
    "RubricParseReport",
    "RubricRowItem",
    "StudyGuideSplitResult",
    "TableCellSpan",
    "MAX_GRID_COLS",
    "MAX_GRID_ROWS",
    "MAX_SPAN",
    "TableGridLimitError",
    "expand_table_grid",
    "parse_three_column_rubric",
    "split_study_guide_mixed",
]
