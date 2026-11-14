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


def _session(points: float, expected: float | None = 10.0, tenant_id: str = "tenant-a") -> ExamImportSession:
    return ExamImportSession(
        session_id="imp-x",
        tenant_id=tenant_id,
        source_document_id="doc-x",
        rights_status=RightsStatus.OWNED,
        expected_total_points=expected,
        sections=(
            ExamSectionDraft(
                section_code="PART_1",
                title="Phan I",
                questions=(ImportedQuestionRef(1, "cand-1", points, 1, paired_answer="A"),),
            ),
        ),
    )


@pytest.mark.parametrize("points", [float("nan"), float("inf"), float("-inf"), -1.0, 0.0])
def test_non_finite_or_non_positive_points_are_invalid(points: float) -> None:
    # Regression: NaN points made every comparison False, so the session came out valid=True.
    report = validate_exam_import_structure(_session(points, expected=None))
    assert report.valid is False
    with pytest.raises(ExamImportError):
        approve_exam_import_session(_session(points, expected=None))


@pytest.mark.parametrize("expected", [float("nan"), float("inf")])
def test_non_finite_expected_total_is_invalid(expected: float) -> None:
    report = validate_exam_import_structure(_session(10.0, expected=expected))
    assert report.valid is False
    assert any("expected_total_points" in e for e in report.errors)


def test_missing_tenant_or_empty_sections_are_invalid() -> None:
    assert validate_exam_import_structure(_session(10.0, tenant_id="  ")).valid is False
    empty = ExamImportSession("i", "t", "d", RightsStatus.OWNED, sections=())
    assert validate_exam_import_structure(empty).valid is False


def test_out_of_sequence_question_numbers_are_reported() -> None:
    s = ExamImportSession(
        "i",
        "t",
        "d",
        RightsStatus.OWNED,
        expected_total_points=None,
        sections=(
            ExamSectionDraft(
                "P",
                "P",
                (
                    ImportedQuestionRef(2, "a", 1.0, 1, paired_answer="A"),
                    ImportedQuestionRef(1, "b", 1.0, 1, paired_answer="A"),
                ),
            ),
        ),
    )
    assert any("out of sequence" in e for e in validate_exam_import_structure(s).errors)


@pytest.mark.parametrize(
    "height,y0",
    [(float("nan"), 10.0), (842.0, float("nan")), (842.0, float("inf")), (0, 5), (842, -1)],
)
def test_banner_adjustment_rejects_bad_geometry(height: float, y0: float) -> None:
    with pytest.raises(ExamImportError):
        compute_banner_canvas_adjustment(page_height_pt=height, first_item_y0_pt=y0)


def test_unknown_rights_status_never_allows_branded_pdf() -> None:
    assert is_branded_pdf_allowed("MADE_UP") is False  # type: ignore[arg-type]
    assert is_branded_pdf_allowed(None) is False  # type: ignore[arg-type]


def test_approve_rejects_already_approved_session() -> None:
    approved, _ = approve_exam_import_session(_session(10.0))
    with pytest.raises(ExamImportError):
        approve_exam_import_session(approved)
