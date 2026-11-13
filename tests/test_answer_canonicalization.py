"""Unit tests for C02 numeric answer canonicalization & subject lock (P18)."""

from __future__ import annotations

import unicodedata
from decimal import Decimal

import pytest

from app.modules.quality.public import (
    DEFAULT_MATH_PROBABILITY_KEYWORDS,
    canonicalize_numeric_answer,
    enforce_subject_lock,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0,5", Decimal("0.5")),
        ("0.5", Decimal("0.5")),
        ("-1,25", Decimal("-1.25")),
        ("0,125", Decimal("0.125")),
        ("3/4", Decimal("0.75")),
        ("3/4 kg", Decimal("0.75")),
        ("3/4 m/s", Decimal("0.75")),
        ("10 m/s", Decimal("10")),
        ("2,5kg", Decimal("2.5")),
        ("30°", Decimal("30")),
        ("50%", Decimal("50")),
        ("1000", Decimal("1000")),
    ],
)
def test_canonicalize_numeric_answer_valid_forms_and_units(raw, expected):
    res = canonicalize_numeric_answer(raw)
    assert isinstance(res, Decimal)
    assert res == expected


@pytest.mark.parametrize(
    "bad_raw",
    [
        None,
        "",
        "   ",
        "1.000",
        "1,000",
        "12.345",
        "1,2.3",
        "1.2.3",
        "1/0",
        "1/2/3",
        "abc",
        "1e3",
        "NaN",
    ],
)
def test_canonicalize_numeric_answer_rejects_ambiguous_or_invalid_inputs(bad_raw):
    assert canonicalize_numeric_answer(bad_raw) is None


def test_enforce_subject_lock_source_priority_nfc_case_and_reason_codes():
    assert "công thức bayes" in DEFAULT_MATH_PROBABILITY_KEYWORDS

    # Source subject always wins even when inferred=BIO and Bayes keyword is present.
    locked = enforce_subject_lock(
        "Áp dụng công thức Bayes trong xét nghiệm dương tính",
        inferred_subject="BIO",
        source_subject="BIO",
    )
    assert locked.subject_code == "BIO"
    assert locked.reason_code == "SOURCE_SUBJECT_LOCKED"

    # Without source subject: NFD text + uppercase matches case-insensitively after NFC folding.
    nfd_text = unicodedata.normalize("NFD", "Áp dụng CÔNG THỨC BAYES để tính xác suất.")
    overridden = enforce_subject_lock(nfd_text, inferred_subject="BIO", source_subject=None)
    assert overridden.subject_code == "MATH"
    assert overridden.reason_code == "OVERRIDDEN_BIO_TO_MATH_PROBABILITY"

    # Custom keyword list parameter overrides default list cleanly.
    kept_custom = enforce_subject_lock(
        "Áp dụng công thức Bayes",
        inferred_subject="BIO",
        source_subject=None,
        math_probability_keywords=("không gian mẫu",),
    )
    assert kept_custom.subject_code == "BIO"
    assert kept_custom.reason_code == "INFERRED_SUBJECT_KEPT"

    # Non-BIO inferred subject is kept unchanged.
    phys = enforce_subject_lock("Xác suất dao động", inferred_subject="PHYSICS", source_subject=None)
    assert phys.subject_code == "PHYSICS"
    assert phys.reason_code == "INFERRED_SUBJECT_KEPT"
