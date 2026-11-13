"""Unit tests for A08/A03 4-signal exam-and-solution pairing (P9)."""

from __future__ import annotations

import unicodedata
from decimal import Decimal

import pytest

from app.modules.dedup.public import (
    DocumentKind,
    PairingDocumentInput,
    auto_pair_exam_and_hdc_documents,
    classify_pairing_document,
    clean_pairing_stem,
    is_solution_filename,
    score_exam_and_solution_pairing,
)


@pytest.mark.parametrize(
    ("filename", "expected_is_sol"),
    [
        ("Đề_thi_Toán_12_chính_thức.pdf", False),
        ("De-Toan-THPT-2025.pdf", False),
        ("Đáp án Toán 12 chính thức.pdf", True),
        (unicodedata.normalize("NFD", "Đáp_án_Toán_12.pdf"), True),
        ("toan_12_dap_an.docx", True),
        ("HDC-Vat-Li-12.pdf", True),
        ("Hướng dẫn chấm Vật lí 12 chi tiết.pdf", True),
        (unicodedata.normalize("NFD", "Huong dan cham Hoa hoc.pdf"), True),
        ("toan-12-loi-giai-chi-tiet.pdf", True),
    ],
)
def test_solution_detection_handles_accents_nfd_and_separators(filename, expected_is_sol):
    assert is_solution_filename(filename) is expected_is_sol


def test_clean_pairing_stem_matches_accented_nfc_nfd_and_separators():
    exam_stem = clean_pairing_stem("Đề_thi_Toán_12_2025_chính_thức.pdf")
    sol_nfc = clean_pairing_stem("Đáp án thi Toán 12 2025 chi tiết.pdf")
    sol_nfd = clean_pairing_stem(unicodedata.normalize("NFD", "toan-12-2025-loi-giai-thi.pdf"))

    assert exam_stem == "thi toan 12 2025"
    assert sol_nfc == "thi toan 12 2025"
    assert sol_nfd == "toan 12 2025 thi"
    assert set(exam_stem.split()) == set(sol_nfd.split())


def test_missing_folder_subject_or_count_does_not_score_as_match():
    exam = PairingDocumentInput(doc_id="ex-1", filename="De_Toan_10.pdf")
    sol = PairingDocumentInput(doc_id="sol-1", filename="Dap_an_Toan_10.pdf")

    scored = score_exam_and_solution_pairing(exam, sol)
    assert scored.signals.filename_similarity == Decimal("1.000")
    assert scored.signals.folder_proximity == Decimal("0.000")
    assert scored.signals.subject_match == Decimal("0.000")
    assert scored.signals.item_count_similarity == Decimal("0.000")
    assert scored.total_score == Decimal("0.400")


def test_four_signal_weights_and_thresholds():
    exam = PairingDocumentInput(
        doc_id="ex-1",
        filename="Đề Toán 12 THPTQG 2025.pdf",
        folder_id="folder-toan",
        subject_code="MATH",
        question_count=50,
    )
    sol_perfect = PairingDocumentInput(
        doc_id="sol-1",
        filename="Đáp_án_Toán_12_THPTQG_2025.pdf",
        folder_id="folder-toan",
        subject_code="math",
        question_count=50,
    )
    perfect = score_exam_and_solution_pairing(exam, sol_perfect)
    assert perfect.total_score == Decimal("1.000")

    # Different folder (0.25 lost), diff question count by 5 -> 0.5*0.15 = 0.075, name_sim=1 -> 0.675 (PROPOSED)
    sol_proposed = PairingDocumentInput(
        doc_id="sol-2",
        filename="HDC_Toan_12_THPTQG_2025.pdf",
        folder_id="other-folder",
        subject_code="MATH",
        question_count=45,
    )
    proposed = score_exam_and_solution_pairing(exam, sol_proposed)
    assert proposed.total_score == Decimal("0.675")


def test_strict_one_to_one_pairing_by_descending_score_and_doc_id_tiebreak():
    # One shared solution matches two identical-looking exams equally: must pair with lower doc_id exam only.
    ex_b = PairingDocumentInput(
        doc_id="exam-b",
        filename="De_Vat_Li_12_Khao_Sat.pdf",
        folder_id="f1",
        subject_code="PHYSICS",
        question_count=40,
    )
    ex_a = PairingDocumentInput(
        doc_id="exam-a",
        filename="De_Vat_Li_12_Khao_Sat.pdf",
        folder_id="f1",
        subject_code="PHYSICS",
        question_count=40,
    )
    sol_1 = PairingDocumentInput(
        doc_id="sol-1",
        filename="HDC_Vat_Li_12_Khao_Sat.pdf",
        folder_id="f1",
        subject_code="PHYSICS",
        question_count=40,
    )
    # Proposed match (0.675) for another exam/sol pair
    ex_c = PairingDocumentInput(
        doc_id="exam-c",
        filename="De_Toan_11_GK1.pdf",
        folder_id="f2",
        subject_code="MATH",
        question_count=20,
    )
    sol_2 = PairingDocumentInput(
        doc_id="sol-2",
        filename="loi-giai-Toan-11-GK1.pdf",
        folder_id="f3",
        subject_code="MATH",
        question_count=15,
    )

    p1 = auto_pair_exam_and_hdc_documents([ex_b, sol_2, ex_c, sol_1, ex_a])
    p2 = auto_pair_exam_and_hdc_documents([ex_a, sol_1, ex_b, ex_c, sol_2])

    assert p1 == p2
    assert len(p1) == 2
    assert p1[0].exam_doc_id == "exam-a"
    assert p1[0].solution_doc_id == "sol-1"
    assert p1[0].status == "CONFIRMED"
    assert p1[1].exam_doc_id == "exam-c"
    assert p1[1].solution_doc_id == "sol-2"
    assert p1[1].status == "PROPOSED"


def test_combined_exam_with_solution_is_classified_distinctly_and_excluded_from_pairing():
    assert classify_pairing_document("Đề + đáp án Toán 11.pdf") == DocumentKind.EXAM_WITH_SOLUTION
    assert classify_pairing_document("De_va_loi_giai_Toan_11.docx") == DocumentKind.EXAM_WITH_SOLUTION
    assert classify_pairing_document("Đề thi kèm HDC Vật lí 12.pdf") == DocumentKind.EXAM_WITH_SOLUTION
    assert is_solution_filename("Đề + đáp án Toán 11.pdf") is False

    # Accented non-"đề" words whose unaccented form is "de" (e.g. "dễ") stay pure SOLUTION.
    assert classify_pairing_document("Hướng dẫn chấm dễ hiểu.pdf") == DocumentKind.SOLUTION
    assert is_solution_filename("Hướng dẫn chấm dễ hiểu.pdf") is True

    pure_exam = PairingDocumentInput(
        doc_id="ex-11",
        filename="Đề Toán 11.pdf",
        folder_id="f-11",
        subject_code="MATH",
        question_count=30,
    )
    combined = PairingDocumentInput(
        doc_id="mix-11",
        filename="Đề + đáp án Toán 11.pdf",
        folder_id="f-11",
        subject_code="MATH",
        question_count=30,
    )
    assert auto_pair_exam_and_hdc_documents([pure_exam, combined]) == ()
