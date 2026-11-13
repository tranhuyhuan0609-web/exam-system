"""Numeric answer canonicalization and subject lock helpers for C02 quality verification."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

DEFAULT_MATH_PROBABILITY_KEYWORDS: tuple[str, ...] = (
    "xác suất",
    "công thức bayes",
    "biến cố",
    "ngẫu nhiên",
    "xét nghiệm dương tính",
)

# Reject ambiguous thousands-vs-decimal strings like "1.000", "1,000", "12.345",
# while allowing leading-zero decimals such as "0.125" or "0,375".
_AMBIGUOUS_THOUSANDS_RE = re.compile(r"^[+-]?[1-9]\d{0,2}[.,]\d{3}$")
_PLAIN_DECIMAL_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
# Strip trailing units (including compound slashes like m/s, km/h, m/s^2) or % / ° suffixes,
# without mistaking numeric fraction slashes like "3/4 kg" for part of the unit.
_TRAILING_UNIT_RE = re.compile(r"(?:[a-zA-Z]+(?:[/^*][a-zA-Z0-9^]+)*|[%°]+[a-zA-Z0-9]*)$")


@dataclass(frozen=True, slots=True)
class SubjectLockResult:
    """Result of applying source-truth subject lock and BIO->MATH probability guard."""

    subject_code: str
    reason_code: str
    matched_keyword: str | None = None


def _parse_single_decimal_component(raw_part: str) -> Decimal | None:
    if not raw_part:
        return None
    if raw_part.count(",") + raw_part.count(".") > 1:
        return None
    if _AMBIGUOUS_THOUSANDS_RE.fullmatch(raw_part):
        return None
    normalized = raw_part.replace(",", ".")
    if not _PLAIN_DECIMAL_RE.fullmatch(normalized):
        return None
    try:
        value = Decimal(normalized)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    return value


def canonicalize_numeric_answer(raw_val: str | None) -> Decimal | None:
    """Canonicalize a numeric answer string (fractions, comma decimals, units) to Decimal.

    Note: Per P18 decision aligning with the C02 spec, trailing ``%`` (e.g. ``"50%"``)
    strips the percent symbol rather than dividing by 100 (returning ``Decimal("50")``).
    Ambiguous thousands-vs-decimal forms such as ``"1.000"`` or ``"1,000"`` return ``None``.
    """
    if raw_val is None:
        return None
    compact = unicodedata.normalize("NFKC", raw_val).strip().replace(" ", "")
    if not compact:
        return None

    stripped = _TRAILING_UNIT_RE.sub("", compact)
    if not stripped:
        return None

    if "/" in stripped:
        num_str, den_str, *extra = stripped.split("/")
        if extra:
            return None
        num = _parse_single_decimal_component(num_str)
        den = _parse_single_decimal_component(den_str)
        if num is None or den is None or den == 0:
            return None
        result = num / den
        return result if result.is_finite() else None

    return _parse_single_decimal_component(stripped)


def enforce_subject_lock(
    text_md: str,
    inferred_subject: str,
    source_subject: str | None = None,
    *,
    math_probability_keywords: Sequence[str] = DEFAULT_MATH_PROBABILITY_KEYWORDS,
) -> SubjectLockResult:
    """Lock the question subject: source metadata always wins; guard BIO->MATH probability."""
    if source_subject is not None and source_subject.strip():
        return SubjectLockResult(
            subject_code=source_subject.strip(),
            reason_code="SOURCE_SUBJECT_LOCKED",
        )

    inferred_clean = (inferred_subject or "").strip()
    if inferred_clean.upper() == "BIO":
        normalized_text = unicodedata.normalize("NFC", text_md or "").casefold()
        for kw in math_probability_keywords:
            normalized_kw = unicodedata.normalize("NFC", kw).casefold()
            if normalized_kw and normalized_kw in normalized_text:
                return SubjectLockResult(
                    subject_code="MATH",
                    reason_code="OVERRIDDEN_BIO_TO_MATH_PROBABILITY",
                    matched_keyword=kw,
                )

    return SubjectLockResult(
        subject_code=inferred_clean,
        reason_code="INFERRED_SUBJECT_KEPT",
    )
