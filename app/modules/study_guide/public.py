"""Public API for app.modules.study_guide (A07 Study-Guide Splitter & 3-Col Rubric Matcher)."""

from app.modules.study_guide.internal.rubric_table import (
    RubricParseReport,
    RubricRowItem,
    TableCellSpan,
    expand_table_grid,
    parse_three_column_rubric,
)
from app.modules.study_guide.internal.splitter import (
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
    "expand_table_grid",
    "parse_three_column_rubric",
    "split_study_guide_mixed",
]
