"""Vietnamese search normalization and safe query parsing (module B07)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.contracts.text import strip_vietnamese_diacritics

MAX_SEARCH_QUERY_CHARS = 200
MAX_SEARCH_QUERY_TERMS = 20

# Align punctuation/math symbols with PostgreSQL's pinned `public.unaccent` dictionary.
_PG_UNACCENT_EXTRA_TRANSLATION = str.maketrans(
    {
        "\u00a1": "!",
        "\u00a9": "(c)",
        "\u00ab": "<<",
        "\u00ad": "-",
        "\u00ae": "(r)",
        "\u00b1": "+/-",
        "\u00bb": ">>",
        "\u00bc": " 1/4",
        "\u00bd": " 1/2",
        "\u00be": " 3/4",
        "\u00bf": "?",
        "\u00d7": "*",
        "\u00f7": "/",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2016": "||",
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": ",",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": ",,",
        "\u201f": '"',
        "\u2024": ".",
        "\u2025": "..",
        "\u2026": "...",
        "\u2032": "'",
        "\u2033": '"',
        "\u2039": "<",
        "\u203a": ">",
        "\u203c": "!!",
        "\u2044": "/",
        "\u204e": "*",
        "\u2102": "c",
        "\u2103": "°c",
        "\u2109": "°f",
        "\u210d": "h",
        "\u2113": "l",
        "\u2115": "n",
        "\u2116": "no",
        "\u2119": "p",
        "\u211a": "q",
        "\u211d": "r",
        "\u2124": "z",
        "\u2212": "-",
        "\u2215": "/",
        "\u2216": "\\",
        "\u2223": "|",
        "\u2225": "||",
        "\u226a": "<<",
        "\u226b": ">>",
    }
)

_WHITESPACE_CONTROL_MAP = {
    "\t": " ",
    "\n": " ",
    "\r": " ",
}
_TOKEN_SCAN_RE = re.compile(r'(-?"[^"]*"|\S+)')


@dataclass(frozen=True, slots=True)
class ParsedSearchQuery:
    """Structured search query extracted from raw user input without building SQL strings."""

    normalized_text: str
    include_terms: tuple[str, ...]
    exclude_terms: tuple[str, ...]
    phrases: tuple[str, ...]
    exclude_phrases: tuple[str, ...]
    was_truncated: bool = False


def fold_for_search(text: str) -> str:
    """Fold text: NFC -> strip accents (incl. đ/Đ & PG unaccent symbols) -> lower -> collapse spaces."""
    if not text:
        return ""
    stripped = strip_vietnamese_diacritics(text)
    pg_aligned = stripped.translate(_PG_UNACCENT_EXTRA_TRANSLATION)
    lowered = pg_aligned.lower()
    return " ".join(lowered.split())


def _strip_control_chars(raw: str) -> str:
    out: list[str] = []
    for ch in raw:
        if ch in _WHITESPACE_CONTROL_MAP:
            out.append(" ")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            continue
        else:
            out.append(ch)
    return "".join(out)


def parse_search_query(raw: str | None) -> ParsedSearchQuery:
    """Parse raw search input into bounded, folded include/exclude terms and quoted phrases."""
    if not raw:
        return ParsedSearchQuery(
            normalized_text="",
            include_terms=(),
            exclude_terms=(),
            phrases=(),
            exclude_phrases=(),
            was_truncated=False,
        )

    cleaned = _strip_control_chars(unicodedata.normalize("NFC", raw))
    cleaned_collapsed = " ".join(cleaned.split())
    was_truncated = len(cleaned_collapsed) > MAX_SEARCH_QUERY_CHARS
    bounded = cleaned_collapsed[:MAX_SEARCH_QUERY_CHARS]

    include_terms: list[str] = []
    exclude_terms: list[str] = []
    phrases: list[str] = []
    exclude_phrases: list[str] = []
    term_budget = 0

    for match in _TOKEN_SCAN_RE.finditer(bounded):
        token = match.group(0)
        is_excluded = False
        if token.startswith("-") and len(token) > 1:
            is_excluded = True
            token = token[1:]

        is_phrase = len(token) >= 2 and token.startswith('"') and token.endswith('"')
        if is_phrase:
            inner = token[1:-1]
            folded_phrase = fold_for_search(inner)
            if not folded_phrase:
                continue
            phrase_words = folded_phrase.split()
            if term_budget + len(phrase_words) > MAX_SEARCH_QUERY_TERMS:
                was_truncated = True
                break
            term_budget += len(phrase_words)
            if is_excluded:
                exclude_phrases.append(folded_phrase)
            else:
                phrases.append(folded_phrase)
            continue

        # Handle dangling quote or plain token cleanly.
        folded = fold_for_search(token.strip('"'))
        if not folded:
            continue
        sub_words = folded.split()
        for word in sub_words:
            if word == "-":
                continue
            if term_budget >= MAX_SEARCH_QUERY_TERMS:
                was_truncated = True
                break
            term_budget += 1
            if is_excluded:
                exclude_terms.append(word)
            else:
                include_terms.append(word)
        if term_budget >= MAX_SEARCH_QUERY_TERMS and was_truncated:
            break

    normalized_parts = [*phrases, *include_terms]
    return ParsedSearchQuery(
        normalized_text=" ".join(normalized_parts)[:MAX_SEARCH_QUERY_CHARS],
        include_terms=tuple(include_terms),
        exclude_terms=tuple(exclude_terms),
        phrases=tuple(phrases),
        exclude_phrases=tuple(exclude_phrases),
        was_truncated=was_truncated,
    )
