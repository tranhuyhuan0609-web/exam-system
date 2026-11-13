"""Tests for A04 complete exam import core rules."""

from __future__ import annotations

import pytest

from app.contracts.enums import RightsStatus
from app.modules.exam_import.public import (
    HEADER_EXPANSION_PT,
    ExamImportError,
    ExamImportSession,
    ExamImportStatus,
    ExamSectionDraft,
    ImportedQuestionRef,
    StimulusBlock,
    approve_exam_import_session,
    compute_banner_canvas_adjustment,
    is_branded_pdf_allowed,
    validate_exam_import_structure,
)


def test_dynamic_header_expands_top_margin_when_cau_1_above_120pt() -> None:
    tight = compute_banner_canvas_adjustment(page_height_pt=842.0, first_item_y0_pt=85.0)
    assert tight.header_expanded is True
    assert tight.top_expansion_pt == HEADER_EXPANSION_PT
    assert tight.expanded_page_height_pt == 912.0
    assert tight.shifted_first_item_y0_pt == 155.0

    spacious = compute_banner_canvas_adjustment(page_height_pt=842.0, first_item_y0_pt=120.0)
    assert spacious.header_expanded is False
    assert spacious.top_expansion_pt == 0.0
    assert spacious.expanded_page_height_pt == 842.0


def test_branded_pdf_allowed_only_for_owned_or_allowed_derivative() -> None:
    assert is_branded_pdf_allowed(RightsStatus.OWNED) is True
    assert is_branded_pdf_allowed(RightsStatus.ALLOWED_DERIVATIVE) is True
    assert is_branded_pdf_allowed(RightsStatus.LICENSED) is False
    assert is_branded_pdf_allowed(RightsStatus.PENDING_REVIEW) is False
    assert is_branded_pdf_allowed(RightsStatus.RESTRICTED) is False
    assert is_branded_pdf_allowed(RightsStatus.TAKEDOWN) is False


def test_validate_and_approve_valid_exam_structure_with_stimuli() -> None:
    session = ExamImportSession(
        session_id="imp-1",
        tenant_id="tenant-a",
        source_document_id="doc-1",
        rights_status=RightsStatus.OWNED,
        expected_total_points=10.0,
        stimuli=(StimulusBlock(stimulus_id="stim-1", title="Đọc đoạn văn sau", page_number=2),),
        sections=(
            ExamSectionDraft(
                section_code="PART_1",
                title="Phần I",
                questions=(
                    ImportedQuestionRef(1, "cand-1", 5.0, 1, paired_answer="A"),
                    ImportedQuestionRef(2, "cand-2", 5.0, 2, stimulus_id="stim-1", paired_answer="C"),
                ),
            ),
        ),
    )
    approved, report = approve_exam_import_session(session)
    assert approved.status == ExamImportStatus.APPROVED
    assert report.valid is True
    assert report.question_count == 2
    assert report.branded_pdf_allowed is True


def test_validate_catches_dangling_stimulus_duplicate_no_and_points_mismatch() -> None:
    bad = ExamImportSession(
        session_id="imp-bad",
        tenant_id="tenant-a",
        source_document_id="doc-2",
        rights_status=RightsStatus.LICENSED,
        expected_total_points=10.0,
        sections=(
            ExamSectionDraft(
                section_code="PART_1",
                title="Phần I",
                questions=(
                    ImportedQuestionRef(1, "cand-1", 3.0, 1, stimulus_id="missing-stim", paired_answer="A"),
                    ImportedQuestionRef(1, "cand-2", 3.0, 1, paired_answer=None),
                ),
            ),
        ),
    )
    report = validate_exam_import_structure(bad)
    assert report.valid is False
    assert report.branded_pdf_allowed is False
    assert any("duplicate question_no 1" in e for e in report.errors)
    assert any("missing stimulus" in e for e in report.errors)
    assert any("missing paired answer key" in e for e in report.errors)
    assert any("total points" in e for e in report.errors)

    with pytest.raises(ExamImportError):
        approve_exam_import_session(bad)
