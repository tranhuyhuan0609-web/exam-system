"""Public entry point of the dedup module (A08)."""

from __future__ import annotations

from app.modules.dedup.internal.hashing import (
    QuestionHashes,
    compute_question_hashes,
    content_hash,
    normalize_text,
    structural_hash,
    structural_normalize,
)
from app.modules.dedup.internal.pairing import (
    DEFAULT_AUTO_CONFIRM_THRESHOLD,
    DEFAULT_PROPOSE_THRESHOLD,
    DocumentKind,
    DocumentPairing,
    PairingDocumentInput,
    PairingScore,
    PairingSignals,
    auto_pair_exam_and_hdc_documents,
    classify_pairing_document,
    clean_pairing_stem,
    fold_vietnamese_ascii,
    is_solution_filename,
    score_exam_and_solution_pairing,
)

__all__ = [
    "DEFAULT_AUTO_CONFIRM_THRESHOLD",
    "DEFAULT_PROPOSE_THRESHOLD",
    "DocumentKind",
    "DocumentPairing",
    "PairingDocumentInput",
    "PairingScore",
    "PairingSignals",
    "QuestionHashes",
    "auto_pair_exam_and_hdc_documents",
    "classify_pairing_document",
    "clean_pairing_stem",
    "compute_question_hashes",
    "content_hash",
    "fold_vietnamese_ascii",
    "is_solution_filename",
    "normalize_text",
    "score_exam_and_solution_pairing",
    "structural_hash",
    "structural_normalize",
]
