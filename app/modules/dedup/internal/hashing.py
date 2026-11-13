"""Pure text normalization and content/structural hashing (module A08).

Uses a forward chemical & numeric tokenizer (no variable-width lookbehind)
so numeric values in problem statements are replaced with ``<num>`` while
chemical subscripts (e.g. H2O, Fe3O4, Ca(OH)2) and math subscripts (e.g. u_1)
are preserved.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

CHEM_OR_NUMERIC_TOKEN_RE = re.compile(
    r"(?P<chem>(?:[A-Z][a-z]?\d*|\([A-Za-z0-9]+\)\d+|\[[A-Za-z0-9\(\)]+\]\d*)"
    r"(?:[A-Z][a-z]?\d*|\([A-Za-z0-9]+\)\d*)+|[A-Z][a-z]?\d+)"
    r"|(?P<math_sub>[A-Za-z]_\{?\d+\}?)"
    r"|(?P<num>(?<![A-Za-z_\\0-9])\d+(?:[.,]\d+)?)"
)

_SUBSCRIPT_TRANSLATION = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class QuestionHashes:
    """Deterministic SHA-256 content and structural hashes for a question."""

    content_hash: str
    structural_hash: str


def normalize_unicode_preserve_case(text: str) -> str:
    """Normalize Unicode to NFC, unify dashes/subscripts, and collapse whitespace."""
    nfc = unicodedata.normalize("NFC", text or "")
    nfc = nfc.replace("\u2212", "-").replace("\u2013", "-")
    nfc = nfc.translate(_SUBSCRIPT_TRANSLATION)
    return _WHITESPACE_RE.sub(" ", nfc).strip()


def normalize_text(text: str) -> str:
    """Normalize Vietnamese/LaTeX text to NFC lowercase with collapsed whitespace."""
    return normalize_unicode_preserve_case(text).lower()


def structural_normalize(text: str) -> str:
    """Replace standalone numeric literals with ``<num>`` while preserving chemistry."""
    cased_nfc = normalize_unicode_preserve_case(text)

    def _replacer(match: re.Match[str]) -> str:
        if match.group("chem") is not None:
            return match.group("chem")
        if match.group("math_sub") is not None:
            return match.group("math_sub")
        return "<NUM>"

    return CHEM_OR_NUMERIC_TOKEN_RE.sub(_replacer, cased_nfc).lower()


def content_hash(stem_md: str, options_md: Sequence[str] = ()) -> str:
    """Compute SHA-256 content hash invariant to option order."""
    norm_stem = normalize_text(stem_md)
    norm_opts = sorted(normalize_text(opt) for opt in options_md)
    canonical = f"{norm_stem}||{'|'.join(norm_opts)}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def structural_hash(stem_md: str, options_md: Sequence[str] = ()) -> str:
    """Compute SHA-256 structural hash invariant to option order and numeric literals."""
    struct_stem = structural_normalize(stem_md)
    struct_opts = sorted(structural_normalize(opt) for opt in options_md)
    canonical = f"{struct_stem}||{'|'.join(struct_opts)}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_question_hashes(stem_md: str, options_md: Sequence[str] = ()) -> QuestionHashes:
    """Compute both content and structural hashes for a question."""
    return QuestionHashes(
        content_hash=content_hash(stem_md, options_md),
        structural_hash=structural_hash(stem_md, options_md),
    )

