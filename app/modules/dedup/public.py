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

__all__ = [
    "QuestionHashes",
    "compute_question_hashes",
    "content_hash",
    "normalize_text",
    "structural_hash",
    "structural_normalize",
]

