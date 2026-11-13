"""Chapter 9.1 Quantitative Golden & Held-out Extraction Gate evaluator (GATE-06a / 06b).

Evaluates extracted question boundaries, figures/formulas preservation, and
coordinate/footer noise redaction rates against V11.3 quantitative thresholds
without storing any third-party exam bytes or PII in Git.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_BOUNDARY_PRECISION = 0.98
MIN_BOUNDARY_RECALL = 0.96
MIN_FIGURE_FORMULA_PRESERVATION = 0.99
REQUIRED_CLEAN_TEXT_REDACTION = 1.00


@dataclass(frozen=True)
class CanonicalGoldenSpec:
    corpus_id: str
    subject: str
    expected_contents: int
    expected_questions: int


CANONICAL_GOLDEN_CORPUS: tuple[CanonicalGoldenSpec, ...] = (
    CanonicalGoldenSpec("golden_01_phy10_essay.pdf", "PHYSICS", 0, 5),
    CanonicalGoldenSpec("golden_02_phy11_vector.pdf", "PHYSICS", 0, 5),
    CanonicalGoldenSpec("golden_03_chem11_legacy.doc", "CHEMISTRY", 0, 8),
    CanonicalGoldenSpec("golden_04_bio11_canvas.docx", "BIOLOGY", 0, 10),
    CanonicalGoldenSpec("golden_05_math11_ole.docx", "MATH", 0, 10),
    CanonicalGoldenSpec("golden_06_phy12_hdc_table.docx", "PHYSICS", 0, 10),
    CanonicalGoldenSpec("golden_07_no_header_sheets.pdf", "MATH", 0, 10),
    CanonicalGoldenSpec("golden_08_combined_exam_sol.pdf", "PHYSICS", 0, 5),
    CanonicalGoldenSpec("golden_09_word_shapes_graph.docx", "PHYSICS", 0, 7),
    CanonicalGoldenSpec("golden_10_mobile_photo_skewed.jpg", "MATH", 0, 2),
    CanonicalGoldenSpec("golden_11_bayes_mixed_guide.pdf", "MATH", 1, 67),
)


@dataclass(frozen=True)
class DocumentExtractionObservation:
    corpus_id: str
    is_held_out: bool
    true_positive_boundaries: int
    false_positive_boundaries: int
    false_negative_boundaries: int
    expected_figures_and_formulas: int
    preserved_figures_and_formulas: int
    extracted_questions_total: int
    clean_text_questions: int
    noise_leaks_detected: int = 0


@dataclass(frozen=True)
class GoldenEvaluationReport:
    golden_file_count: int
    held_out_file_count: int
    boundary_precision: float
    boundary_recall: float
    figure_formula_preservation_rate: float
    clean_text_redaction_rate: float
    passed: bool
    failed_criteria: tuple[str, ...]
    leaking_corpus_ids: tuple[str, ...]


def evaluate_extraction_quality(
    observations: list[DocumentExtractionObservation] | tuple[DocumentExtractionObservation, ...],
    *,
    require_full_11_golden: bool = False,
    require_30_held_out: bool = False,
) -> GoldenEvaluationReport:
    """Evaluate aggregate extraction quality across golden and held-out runs."""
    tp = sum(o.true_positive_boundaries for o in observations)
    fp = sum(o.false_positive_boundaries for o in observations)
    fn = sum(o.false_negative_boundaries for o in observations)
    exp_ff = sum(o.expected_figures_and_formulas for o in observations)
    pres_ff = sum(o.preserved_figures_and_formulas for o in observations)
    total_q = sum(o.extracted_questions_total for o in observations)
    clean_q = sum(o.clean_text_questions for o in observations)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    ff_rate = pres_ff / exp_ff if exp_ff > 0 else 1.0
    redaction_rate = clean_q / total_q if total_q > 0 else 0.0

    golden_ids = {o.corpus_id for o in observations if not o.is_held_out}
    held_out_ids = {o.corpus_id for o in observations if o.is_held_out}
    leaking_ids = tuple(
        sorted(
            o.corpus_id
            for o in observations
            if o.noise_leaks_detected > 0 or o.clean_text_questions < o.extracted_questions_total
        )
    )

    failures: list[str] = []
    if precision < MIN_BOUNDARY_PRECISION:
        failures.append(f"PRECISION_BELOW_THRESHOLD:{precision:.4f}<{MIN_BOUNDARY_PRECISION:.2f}")
    if recall < MIN_BOUNDARY_RECALL:
        failures.append(f"RECALL_BELOW_THRESHOLD:{recall:.4f}<{MIN_BOUNDARY_RECALL:.2f}")
    if ff_rate < MIN_FIGURE_FORMULA_PRESERVATION:
        failures.append(
            f"FIGURE_FORMULA_BELOW_THRESHOLD:{ff_rate:.4f}<{MIN_FIGURE_FORMULA_PRESERVATION:.2f}"
        )
    if redaction_rate < REQUIRED_CLEAN_TEXT_REDACTION or leaking_ids:
        failures.append(
            f"CLEAN_TEXT_REDACTION_FAILED:{redaction_rate:.4f}<{REQUIRED_CLEAN_TEXT_REDACTION:.2f}"
        )
    if require_full_11_golden:
        expected_ids = {s.corpus_id for s in CANONICAL_GOLDEN_CORPUS}
        missing = expected_ids - golden_ids
        if missing:
            failures.append(f"MISSING_GOLDEN_FILES:{len(missing)}")
    if require_30_held_out and len(held_out_ids) < 30:
        failures.append(f"INSUFFICIENT_HELD_OUT:{len(held_out_ids)}<30")

    return GoldenEvaluationReport(
        golden_file_count=len(golden_ids),
        held_out_file_count=len(held_out_ids),
        boundary_precision=round(precision, 4),
        boundary_recall=round(recall, 4),
        figure_formula_preservation_rate=round(ff_rate, 4),
        clean_text_redaction_rate=round(redaction_rate, 4),
        passed=len(failures) == 0,
        failed_criteria=tuple(failures),
        leaking_corpus_ids=leaking_ids,
    )
