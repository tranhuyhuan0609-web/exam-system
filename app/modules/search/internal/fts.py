"""PostgreSQL Vietnamese FTS (`f_unaccent` + `tsvector`) and `pg_trgm` query builder (B07)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.modules.search.internal.normalize import ParsedSearchQuery, fold_for_search, parse_search_query

_SAFE_TOKEN_RE = re.compile(r"[0-9a-z]+", re.ASCII)


@dataclass(frozen=True)
class FtsQueryPlan:
    """Safe, parameterized PostgreSQL FTS + trigram query components derived from user query text."""

    parsed: ParsedSearchQuery
    folded_include_text: str
    include_tsquery_prefix: str
    include_phrases: tuple[str, ...]
    exclude_terms: tuple[str, ...]
    exclude_phrases: tuple[str, ...]
    has_query: bool


def _to_safe_lexemes(text_value: str) -> list[str]:
    return _SAFE_TOKEN_RE.findall(fold_for_search(text_value))


def build_fts_query_plan(raw_query: str | None) -> FtsQueryPlan:
    """Compile user search input into safe `tsquery` strings and trigram/phrase bindings."""
    parsed = parse_search_query(raw_query or "")

    prefix_tokens: list[str] = []
    for term in parsed.include_terms:
        lexemes = _to_safe_lexemes(term)
        for lex in lexemes:
            prefix_tokens.append(f"{lex}:*")

    phrase_texts: list[str] = []
    for phrase in parsed.phrases:
        cleaned = " ".join(_to_safe_lexemes(phrase))
        if cleaned:
            phrase_texts.append(cleaned)

    exclude_terms = tuple(
        lex
        for term in parsed.exclude_terms
        for lex in _to_safe_lexemes(term)
    )
    exclude_phrases = tuple(
        " ".join(_to_safe_lexemes(phrase))
        for phrase in parsed.exclude_phrases
        if " ".join(_to_safe_lexemes(phrase))
    )

    folded_parts: list[str] = list(phrase_texts)
    folded_parts.extend(" ".join(_to_safe_lexemes(t)) for t in parsed.include_terms)
    folded_include = " ".join(p for p in folded_parts if p).strip()

    include_tsquery_prefix = " & ".join(prefix_tokens)
    has_query = bool(
        include_tsquery_prefix or phrase_texts or exclude_terms or exclude_phrases
    )

    return FtsQueryPlan(
        parsed=parsed,
        folded_include_text=folded_include,
        include_tsquery_prefix=include_tsquery_prefix,
        include_phrases=tuple(phrase_texts),
        exclude_terms=exclude_terms,
        exclude_phrases=exclude_phrases,
        has_query=has_query,
    )
