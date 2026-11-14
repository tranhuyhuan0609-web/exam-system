"""A04 Complete Exam Import core rules.

Exam hierarchy reconstruction, dynamic header padding, and BRANDED_PDF rights gating.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum

from app.contracts.enums import RightsStatus

HEADER_PROXIMITY_THRESHOLD_PT = 120.0
HEADER_EXPANSION_PT = 70.0
BRANDED_PDF_ALLOWED_RIGHTS: frozenset[RightsStatus] = frozenset({RightsStatus.OWNED, RightsStatus.ALLOWED_DERIVATIVE})


class ExamImportStatus(StrEnum):
    DRAFT = "DRAFT"
    STRUCTURE_READY = "STRUCTURE_READY"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ExamImportError(ValueError):
    """Raised when exam structure validation or state transitions fail."""


@dataclass(frozen=True)
class BannerCanvasPlan:
    original_page_height_pt: float
    expanded_page_height_pt: float
    top_expansion_pt: float
    shifted_first_item_y0_pt: float
    header_expanded: bool


@dataclass(frozen=True)
class StimulusBlock:
    stimulus_id: str
    title: str
    page_number: int


@dataclass(frozen=True)
class ImportedQuestionRef:
    question_no: int
    candidate_id: str
    points: float
    page_number: int
    stimulus_id: str | None = None
    paired_answer: str | None = None


@dataclass(frozen=True)
class ExamSectionDraft:
    section_code: str
    title: str
    questions: tuple[ImportedQuestionRef, ...]


@dataclass(frozen=True)
class ExamImportSession:
    session_id: str
    tenant_id: str
    source_document_id: str
    rights_status: RightsStatus
    sections: tuple[ExamSectionDraft, ...]
    stimuli: tuple[StimulusBlock, ...] = ()
    expected_total_points: float | None = 10.0
    status: ExamImportStatus = ExamImportStatus.DRAFT


@dataclass(frozen=True)
class StructureValidationReport:
    valid: bool
    question_count: int
    total_points: float
    branded_pdf_allowed: bool
    errors: tuple[str, ...]


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def compute_banner_canvas_adjustment(
    *,
    page_height_pt: float,
    first_item_y0_pt: float,
) -> BannerCanvasPlan:
    """Expand page top by 70pt when Câu 1 sits too close to the upper edge (< 120pt)."""
    if not _is_finite_number(page_height_pt) or not _is_finite_number(first_item_y0_pt):
        raise ExamImportError("page_height_pt and first_item_y0_pt must be finite numbers")
    if page_height_pt <= 0:
        raise ExamImportError("page_height_pt must be positive")
    if first_item_y0_pt < 0:
        raise ExamImportError("first_item_y0_pt cannot be negative")

    if first_item_y0_pt < HEADER_PROXIMITY_THRESHOLD_PT:
        return BannerCanvasPlan(
            original_page_height_pt=page_height_pt,
            expanded_page_height_pt=page_height_pt + HEADER_EXPANSION_PT,
            top_expansion_pt=HEADER_EXPANSION_PT,
            shifted_first_item_y0_pt=first_item_y0_pt + HEADER_EXPANSION_PT,
            header_expanded=True,
        )

    return BannerCanvasPlan(
        original_page_height_pt=page_height_pt,
        expanded_page_height_pt=page_height_pt,
        top_expansion_pt=0.0,
        shifted_first_item_y0_pt=first_item_y0_pt,
        header_expanded=False,
    )


def is_branded_pdf_allowed(rights_status: RightsStatus) -> bool:
    """Only OWNED or ALLOWED_DERIVATIVE documents may emit branded PDF exports."""
    return rights_status in BRANDED_PDF_ALLOWED_RIGHTS


def validate_exam_import_structure(
    session: ExamImportSession,
    *,
    require_answer_keys: bool = True,
) -> StructureValidationReport:
    """Validate section ordering, unique question numbers, stimuli integrity, and point totals."""
    errors: list[str] = []
    if not (session.tenant_id or "").strip() or not (session.source_document_id or "").strip():
        errors.append("tenant_id and source_document_id are required")
    if session.expected_total_points is not None and not _is_finite_number(session.expected_total_points):
        errors.append(f"expected_total_points must be a finite number, got {session.expected_total_points!r}")
    if not session.sections:
        errors.append("exam import session contains no sections")

    stimulus_ids = {s.stimulus_id for s in session.stimuli}
    seen_question_nos: set[int] = set()
    question_count = 0
    total_points = 0.0
    points_ok = True

    for sec in session.sections:
        if not sec.questions:
            errors.append(f"section {sec.section_code!r} has no questions")
            continue
        prev_no: int | None = None
        for item in sec.questions:
            question_count += 1
            if item.question_no <= 0:
                errors.append(f"invalid question_no {item.question_no}")
            if item.question_no in seen_question_nos:
                errors.append(f"duplicate question_no {item.question_no}")
            seen_question_nos.add(item.question_no)

            if prev_no is not None and item.question_no <= prev_no:
                errors.append(f"section {sec.section_code!r} question order out of sequence at {item.question_no}")
            prev_no = item.question_no

            if not _is_finite_number(item.points):
                points_ok = False
                errors.append(f"question {item.question_no} has non-finite points {item.points!r}")
            elif item.points <= 0:
                points_ok = False
                errors.append(f"question {item.question_no} has non-positive points {item.points}")
            else:
                total_points += item.points

            if item.stimulus_id is not None and item.stimulus_id not in stimulus_ids:
                errors.append(f"question {item.question_no} references missing stimulus {item.stimulus_id!r}")

            if require_answer_keys and not (item.paired_answer or "").strip():
                errors.append(f"question {item.question_no} missing paired answer key")

    rounded_total = round(total_points, 4)
    if (
        points_ok
        and _is_finite_number(session.expected_total_points)
        and session.expected_total_points is not None
        and abs(rounded_total - session.expected_total_points) > 1e-4
    ):
        errors.append(f"total points {rounded_total} != expected {session.expected_total_points}")

    return StructureValidationReport(
        valid=not errors,
        question_count=question_count,
        total_points=rounded_total,
        branded_pdf_allowed=is_branded_pdf_allowed(session.rights_status),
        errors=tuple(errors),
    )


def approve_exam_import_session(
    session: ExamImportSession,
    *,
    require_answer_keys: bool = True,
) -> tuple[ExamImportSession, StructureValidationReport]:
    """Approve an exam import session only when full structure reconstruction validates cleanly."""
    if session.status not in (ExamImportStatus.DRAFT, ExamImportStatus.STRUCTURE_READY):
        raise ExamImportError(f"cannot approve session in status {session.status}")

    report = validate_exam_import_structure(session, require_answer_keys=require_answer_keys)
    if not report.valid:
        raise ExamImportError("; ".join(report.errors))

    return replace(session, status=ExamImportStatus.APPROVED), report
