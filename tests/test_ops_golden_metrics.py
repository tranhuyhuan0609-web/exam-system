"""Tests for Chapter 9.1 Golden & Held-out Extraction Gate evaluator (P59)."""

from __future__ import annotations

import pytest

from app.ops.golden_metrics import (
    CANONICAL_GOLDEN_CORPUS,
    DocumentExtractionObservation,
    GoldenMetricsError,
    evaluate_extraction_quality,
)


def test_canonical_11_golden_and_30_held_out_pass_when_meeting_all_thresholds() -> None:
    obs: list[DocumentExtractionObservation] = [
        DocumentExtractionObservation(
            corpus_id=spec.corpus_id,
            is_held_out=False,
            true_positive_boundaries=spec.expected_questions,
            false_positive_boundaries=0,
            false_negative_boundaries=0,
            expected_figures_and_formulas=10,
            preserved_figures_and_formulas=10,
            extracted_questions_total=spec.expected_questions,
            clean_text_questions=spec.expected_questions,
        )
        for spec in CANONICAL_GOLDEN_CORPUS
    ]
    for idx in range(30):
        obs.append(
            DocumentExtractionObservation(
                corpus_id=f"held_out_{idx + 1:02d}.pdf",
                is_held_out=True,
                true_positive_boundaries=20,
                false_positive_boundaries=0,
                false_negative_boundaries=0,
                expected_figures_and_formulas=5,
                preserved_figures_and_formulas=5,
                extracted_questions_total=20,
                clean_text_questions=20,
            )
        )

    report = evaluate_extraction_quality(
        obs,
        require_full_11_golden=True,
        require_30_held_out=True,
    )
    assert report.passed is True
    assert report.golden_file_count == 11
    assert report.held_out_file_count == 30
    assert report.boundary_precision == 1.0
    assert report.clean_text_redaction_rate == 1.0
    assert report.failed_criteria == ()


def test_detects_precision_drop_and_axis_noise_leak_fail_closed() -> None:
    bad = [
        DocumentExtractionObservation(
            corpus_id="golden_09_word_shapes_graph.docx",
            is_held_out=False,
            true_positive_boundaries=45,
            false_positive_boundaries=3,  # 45/48 = 0.9375 < 0.98
            false_negative_boundaries=4,  # 45/49 = 0.9184 < 0.96
            expected_figures_and_formulas=100,
            preserved_figures_and_formulas=98,  # 0.98 < 0.99
            extracted_questions_total=48,
            clean_text_questions=47,
            noise_leaks_detected=1,
        )
    ]
    report = evaluate_extraction_quality(bad, require_full_11_golden=True)
    assert report.passed is False
    assert report.leaking_corpus_ids == ("golden_09_word_shapes_graph.docx",)
    assert any(c.startswith("PRECISION_BELOW_THRESHOLD") for c in report.failed_criteria)
    assert any(c.startswith("RECALL_BELOW_THRESHOLD") for c in report.failed_criteria)
    assert any(c.startswith("FIGURE_FORMULA_BELOW_THRESHOLD") for c in report.failed_criteria)
    assert any(c.startswith("CLEAN_TEXT_REDACTION_FAILED") for c in report.failed_criteria)
    assert any(c.startswith("MISSING_GOLDEN_FILES") for c in report.failed_criteria)
    assert any(c.startswith("GOLDEN_QUESTION_COUNT_MISMATCH") for c in report.failed_criteria)
    assert report.files_below_threshold == ("golden_09_word_shapes_graph.docx",)


def test_golden_metrics_rejects_inconsistent_or_duplicate_observations() -> None:
    with pytest.raises(GoldenMetricsError):
        evaluate_extraction_quality([])

    base = DocumentExtractionObservation(
        corpus_id="golden_01_phy10_essay.pdf",
        is_held_out=False,
        true_positive_boundaries=5,
        false_positive_boundaries=0,
        false_negative_boundaries=0,
        expected_figures_and_formulas=2,
        preserved_figures_and_formulas=2,
        extracted_questions_total=5,
        clean_text_questions=5,
    )
    with pytest.raises(GoldenMetricsError, match="duplicate"):
        evaluate_extraction_quality([base, base])

    with pytest.raises(GoldenMetricsError, match="preserved"):
        evaluate_extraction_quality(
            [
                DocumentExtractionObservation(
                    corpus_id="doc-a",
                    is_held_out=True,
                    true_positive_boundaries=5,
                    false_positive_boundaries=0,
                    false_negative_boundaries=0,
                    expected_figures_and_formulas=2,
                    preserved_figures_and_formulas=3,
                    extracted_questions_total=5,
                    clean_text_questions=5,
                )
            ]
        )
    with pytest.raises(GoldenMetricsError, match="clean"):
        evaluate_extraction_quality(
            [
                DocumentExtractionObservation(
                    corpus_id="doc-a",
                    is_held_out=True,
                    true_positive_boundaries=5,
                    false_positive_boundaries=0,
                    false_negative_boundaries=0,
                    expected_figures_and_formulas=2,
                    preserved_figures_and_formulas=2,
                    extracted_questions_total=5,
                    clean_text_questions=6,
                )
            ]
        )
