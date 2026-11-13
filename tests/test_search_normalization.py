"""Unit tests for B07 Vietnamese search normalization and query parser (P21)."""

from __future__ import annotations

import unicodedata

from app.contracts.text import fold_vietnamese_ascii
from app.modules.search.public import (
    MAX_SEARCH_QUERY_CHARS,
    MAX_SEARCH_QUERY_TERMS,
    fold_for_search,
    parse_search_query,
)


def test_fold_for_search_handles_nfc_nfd_d_bar_case_and_whitespace():
    nfc = "  Công thức   Bayes:\tĐường tròn   đơn vị  ±√2  "
    nfd = unicodedata.normalize("NFD", nfc)

    expected = "cong thuc bayes: duong tron don vi +/-√2"
    assert fold_for_search(nfc) == expected
    assert fold_for_search(nfd) == expected
    assert fold_for_search("") == ""
    assert fold_vietnamese_ascii("Đề_thi_Toán_12.pdf") == "de thi toan 12 pdf"


def test_parse_search_query_extracts_phrases_exclusions_and_strips_control_chars():
    raw = '  "Công\x00 thức\tBayes"   dao động -"cơ học" -Sinh  \n  Vật lí  '
    parsed = parse_search_query(raw)

    assert parsed.phrases == ("cong thuc bayes",)
    assert parsed.exclude_phrases == ("co hoc",)
    assert parsed.include_terms == ("dao", "dong", "vat", "li")
    assert parsed.exclude_terms == ("sinh",)
    assert parsed.was_truncated is False


def test_parse_search_query_enforces_200_char_and_20_word_caps():
    over_200 = " ".join(["toan"] * 60)  # 299 chars
    p_chars = parse_search_query(over_200)
    assert len(p_chars.normalized_text) <= MAX_SEARCH_QUERY_CHARS
    assert p_chars.was_truncated is True

    # Short words < 200 chars total, but > 20 words
    over_20_words = " ".join(f"w{i}" for i in range(30))
    assert len(over_20_words) < MAX_SEARCH_QUERY_CHARS
    p_words = parse_search_query(over_20_words)
    assert len(p_words.include_terms) == MAX_SEARCH_QUERY_TERMS
    assert p_words.was_truncated is True
