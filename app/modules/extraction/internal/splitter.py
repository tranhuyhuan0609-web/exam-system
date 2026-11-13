"""A07 pure structural splitter for STUDY_GUIDE_MIXED handouts into Content & Question candidates."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class ContentBlockKind(StrEnum):
    THEORY_SUMMARY = "THEORY_SUMMARY"
    FORMULA_BOX = "FORMULA_BOX"
    METHOD_STEPS = "METHOD_STEPS"
    TIP_WARNING = "TIP_WARNING"
    WORKED_EXAMPLE = "WORKED_EXAMPLE"


class ExtractedQuestionKind(StrEnum):
    SINGLE_CHOICE = "SINGLE_CHOICE"
    CLUSTER_TRUE_FALSE = "CLUSTER_TRUE_FALSE"
    SHORT_NUMERIC = "SHORT_NUMERIC"
    ESSAY = "ESSAY"


@dataclass(frozen=True, slots=True)
class ProvenanceAnchor:
    page_no: int
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True, slots=True)
class PageSegmentInput:
    page_no: int
    text: str
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 595.0, 842.0)


@dataclass(frozen=True, slots=True)
class ContentCandidateBlock:
    kind: ContentBlockKind
    heading: str
    body_text: str
    anchors: tuple[ProvenanceAnchor, ...]


@dataclass(frozen=True, slots=True)
class QuestionCandidateItem:
    label: str  # e.g. "Câu 1", "Bài 2"
    kind: ExtractedQuestionKind
    stem_text: str
    options: dict[str, str]
    anchors: tuple[ProvenanceAnchor, ...]


@dataclass(frozen=True, slots=True)
class StudyGuideSplitResult:
    title: str
    content_blocks: tuple[ContentCandidateBlock, ...]
    question_candidates: tuple[QuestionCandidateItem, ...]


_EXERCISE_SECTION_RE = re.compile(
    r"^\s*(?:#+\s*)?(?:BÀI\s+TẬP(?:\s+TỰ\s+LUYỆN|\s+VẬN\s+DỤNG)?|PHẦN\s+(?:I|II|III|BÀI\s+TẬP))\b",
    re.IGNORECASE,
)

_QUESTION_HEAD_RE = re.compile(
    r"^\s*(?:#+\s*)?((?:Câu|Bài)\s+\d+)\s*[:.]\s*(.*)$",
    re.IGNORECASE,
)

_WORKED_EXAMPLE_RE = re.compile(r"^\s*(?:Ví dụ|VD)\s*\d*\s*[:.]", re.IGNORECASE)
_METHOD_RE = re.compile(r"^\s*(?:Phương pháp(?:\s+giải)?|Dạng\s+\d+|Cách\s+giải)\b", re.IGNORECASE)
_FORMULA_RE = re.compile(r"^\s*(?:Công thức|Định lý|Định lí|Hệ quả)\b", re.IGNORECASE)
_TIP_RE = re.compile(r"^\s*(?:Chú ý|Lưu ý|Mẹo nhanh|Ghi chú)\b", re.IGNORECASE)

_ABCD_OPT_RE = re.compile(r"(?:^|\n)\s*([A-D])\.\s+([^\n]+)")
_TF_SUB_RE = re.compile(r"(?:^|\n)\s*([a-d])\)\s+([^\n]+)")
_SHORT_NUMERIC_HINT_RE = re.compile(
    r"(?:trả lời ngắn|điền\s+(?:số|đáp\s+án)|làm\s+tròn\s+đến)",
    re.IGNORECASE,
)


def _classify_question_kind(body: str) -> tuple[ExtractedQuestionKind, dict[str, str]]:
    tf_matches = _TF_SUB_RE.findall(body)
    if len(tf_matches) == 4 and {m[0].lower() for m in tf_matches} == {"a", "b", "c", "d"}:
        return ExtractedQuestionKind.CLUSTER_TRUE_FALSE, {m[0].lower(): m[1].strip() for m in tf_matches}

    abcd_matches = _ABCD_OPT_RE.findall(body)
    if len(abcd_matches) >= 2:
        return ExtractedQuestionKind.SINGLE_CHOICE, {m[0].upper(): m[1].strip() for m in abcd_matches}

    if _SHORT_NUMERIC_HINT_RE.search(body):
        return ExtractedQuestionKind.SHORT_NUMERIC, {}

    return ExtractedQuestionKind.ESSAY, {}


def _classify_content_kind(line: str) -> ContentBlockKind:
    if _FORMULA_RE.match(line):
        return ContentBlockKind.FORMULA_BOX
    if _METHOD_RE.match(line):
        return ContentBlockKind.METHOD_STEPS
    if _TIP_RE.match(line):
        return ContentBlockKind.TIP_WARNING
    if _WORKED_EXAMPLE_RE.match(line):
        return ContentBlockKind.WORKED_EXAMPLE
    return ContentBlockKind.THEORY_SUMMARY


def split_study_guide_mixed(
    segments: Sequence[PageSegmentInput],
    *,
    default_title: str = "Chuyên đề",
) -> StudyGuideSplitResult:
    """
    Separate instructional blocks (`ContentCandidateBlock`) from exercises (`QuestionCandidateItem`)
    with provenance coordinates (`page_no` + `bbox`), keeping 100% deterministic pure behavior.
    """
    if not segments:
        return StudyGuideSplitResult(title=default_title, content_blocks=(), question_candidates=())

    in_exercise_zone = False
    doc_title = default_title

    content_blocks: list[ContentCandidateBlock] = []
    questions: list[QuestionCandidateItem] = []

    current_content_kind: ContentBlockKind | None = None
    current_content_head = ""
    current_content_lines: list[str] = []
    current_content_anchors: list[ProvenanceAnchor] = []

    current_q_label: str | None = None
    current_q_lines: list[str] = []
    current_q_anchors: list[ProvenanceAnchor] = []

    def _flush_content() -> None:
        nonlocal current_content_kind, current_content_head, current_content_lines, current_content_anchors
        if current_content_kind is not None and current_content_lines:
            content_blocks.append(
                ContentCandidateBlock(
                    kind=current_content_kind,
                    heading=current_content_head,
                    body_text="\n".join(current_content_lines).strip(),
                    anchors=tuple(dict.fromkeys(current_content_anchors)),
                )
            )
        current_content_kind = None
        current_content_head = ""
        current_content_lines = []
        current_content_anchors = []

    def _flush_question() -> None:
        nonlocal current_q_label, current_q_lines, current_q_anchors
        if current_q_label is not None:
            full_text = "\n".join(current_q_lines).strip()
            kind, opts = _classify_question_kind(full_text)
            questions.append(
                QuestionCandidateItem(
                    label=current_q_label,
                    kind=kind,
                    stem_text=full_text,
                    options=opts,
                    anchors=tuple(dict.fromkeys(current_q_anchors)),
                )
            )
        current_q_label = None
        current_q_lines = []
        current_q_anchors = []

    for seg in segments:
        anchor = ProvenanceAnchor(seg.page_no, *seg.bbox)
        for raw_line in seg.text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if line.startswith("# ") and doc_title == default_title and not content_blocks and not in_exercise_zone:
                doc_title = line[2:].strip()
                continue

            if _EXERCISE_SECTION_RE.match(line):
                _flush_content()
                _flush_question()
                in_exercise_zone = True
                continue

            if not in_exercise_zone:
                # Check if explicit structural content heading starts a new content block
                is_heading = (
                    _FORMULA_RE.match(line)
                    or _METHOD_RE.match(line)
                    or _TIP_RE.match(line)
                    or _WORKED_EXAMPLE_RE.match(line)
                )
                if is_heading:
                    _flush_content()
                    current_content_kind = _classify_content_kind(line)
                    current_content_head = line
                    current_content_lines = [line]
                    current_content_anchors = [anchor]
                else:
                    if current_content_kind is None:
                        current_content_kind = ContentBlockKind.THEORY_SUMMARY
                        current_content_head = "Lý thuyết trọng tâm"
                    current_content_lines.append(line)
                    current_content_anchors.append(anchor)
            else:
                q_match = _QUESTION_HEAD_RE.match(line)
                if q_match:
                    _flush_question()
                    current_q_label = q_match.group(1).strip()
                    rest = q_match.group(2).strip()
                    current_q_lines = [rest] if rest else []
                    current_q_anchors = [anchor]
                elif current_q_label is not None:
                    current_q_lines.append(line)
                    current_q_anchors.append(anchor)

    _flush_content()
    _flush_question()

    return StudyGuideSplitResult(
        title=doc_title,
        content_blocks=tuple(content_blocks),
        question_candidates=tuple(questions),
    )
