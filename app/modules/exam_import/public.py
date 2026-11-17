"""Public API of the exam_import module (A04)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from .internal.structure import (
    BRANDED_PDF_ALLOWED_RIGHTS,
    HEADER_EXPANSION_PT,
    HEADER_PROXIMITY_THRESHOLD_PT,
    BannerCanvasPlan,
    ExamImportError,
    ExamImportSession,
    ExamImportStatus,
    ExamSectionDraft,
    ImportedQuestionRef,
    StimulusBlock,
    StructureValidationReport,
    approve_exam_import_session,
    compute_banner_canvas_adjustment,
    is_branded_pdf_allowed,
    validate_exam_import_structure,
)

if TYPE_CHECKING:
    from .internal.service import (
        BrandedPdfRightsDeniedError,
        ExamImportInvalidStateError,
        ExamImportListItem,
        ExamImportNotFoundError,
        ExamImportPage,
        ExamImportServiceError,
        ExamImportStructureView,
        ExamImportValidationError,
        ProvenanceInput,
        ProvenanceRecordView,
        QuestionDraftInput,
        SectionDraftInput,
        StimulusInput,
        approve_exam_import,
        auto_extract_exam_import_structure,
        create_exam_import_session,
        get_exam_import_structure,
        list_exam_imports,
        open_review_for_job,
        render_exam_import_page_png,
        update_exam_import_structure,
    )

_SERVICE_EXPORTS = frozenset(
    {
        "BrandedPdfRightsDeniedError",
        "ExamImportInvalidStateError",
        "ExamImportNotFoundError",
        "ExamImportServiceError",
        "ExamImportListItem",
        "ExamImportPage",
        "ExamImportStructureView",
        "ExamImportValidationError",
        "ProvenanceInput",
        "ProvenanceRecordView",
        "QuestionDraftInput",
        "SectionDraftInput",
        "StimulusInput",
        "approve_exam_import",
        "auto_extract_exam_import_structure",
        "create_exam_import_session",
        "get_exam_import_structure",
        "list_exam_imports",
        "open_review_for_job",
        "render_exam_import_page_png",
        "update_exam_import_structure",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily load DB service exports so pure-core callers avoid registering ORM models."""
    if name in _SERVICE_EXPORTS:
        service_mod = importlib.import_module(f"{__package__}.internal.service")
        value = getattr(service_mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "BRANDED_PDF_ALLOWED_RIGHTS",
    "HEADER_EXPANSION_PT",
    "HEADER_PROXIMITY_THRESHOLD_PT",
    "BannerCanvasPlan",
    "BrandedPdfRightsDeniedError",
    "ExamImportError",
    "ExamImportInvalidStateError",
    "ExamImportListItem",
    "ExamImportNotFoundError",
    "ExamImportPage",
    "ExamImportServiceError",
    "ExamImportSession",
    "ExamImportStatus",
    "ExamImportStructureView",
    "ExamImportValidationError",
    "ExamSectionDraft",
    "ImportedQuestionRef",
    "ProvenanceInput",
    "ProvenanceRecordView",
    "QuestionDraftInput",
    "SectionDraftInput",
    "StimulusBlock",
    "StimulusInput",
    "StructureValidationReport",
    "approve_exam_import",
    "approve_exam_import_session",
    "auto_extract_exam_import_structure",
    "compute_banner_canvas_adjustment",
    "create_exam_import_session",
    "get_exam_import_structure",
    "is_branded_pdf_allowed",
    "list_exam_imports",
    "open_review_for_job",
    "render_exam_import_page_png",
    "update_exam_import_structure",
    "validate_exam_import_structure",
]
