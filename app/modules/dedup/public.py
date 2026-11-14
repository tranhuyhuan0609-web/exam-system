"""Public entry point of the dedup module (A08)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from app.modules.dedup.internal.service import (
        CandidateNotFoundError,
        DedupError,
        DedupMatchCandidate,
        DedupMatchResult,
        DedupValidationError,
        DuplicateClusterNotFoundError,
        DuplicateClusterView,
        DuplicateMemberView,
        decide_duplicate_cluster,
        get_duplicate_cluster,
        match_question_candidate,
    )

_SERVICE_EXPORTS = frozenset(
    {
        "CandidateNotFoundError",
        "DedupError",
        "DedupMatchCandidate",
        "DedupMatchResult",
        "DedupValidationError",
        "DuplicateClusterNotFoundError",
        "DuplicateClusterView",
        "DuplicateMemberView",
        "decide_duplicate_cluster",
        "get_duplicate_cluster",
        "match_question_candidate",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily load DB service exports so pure-core callers avoid registering ORM models."""
    if name in _SERVICE_EXPORTS:
        service_mod = importlib.import_module("app.modules.dedup.internal.service")
        value = getattr(service_mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "DEFAULT_AUTO_CONFIRM_THRESHOLD",
    "DEFAULT_PROPOSE_THRESHOLD",
    "CandidateNotFoundError",
    "DedupError",
    "DedupMatchCandidate",
    "DedupMatchResult",
    "DedupValidationError",
    "DocumentKind",
    "DocumentPairing",
    "DuplicateClusterNotFoundError",
    "DuplicateClusterView",
    "DuplicateMemberView",
    "PairingDocumentInput",
    "PairingScore",
    "PairingSignals",
    "QuestionHashes",
    "auto_pair_exam_and_hdc_documents",
    "classify_pairing_document",
    "clean_pairing_stem",
    "compute_question_hashes",
    "content_hash",
    "decide_duplicate_cluster",
    "fold_vietnamese_ascii",
    "get_duplicate_cluster",
    "is_solution_filename",
    "match_question_candidate",
    "normalize_text",
    "score_exam_and_solution_pairing",
    "structural_hash",
    "structural_normalize",
]
