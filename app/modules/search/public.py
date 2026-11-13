"""Public facade of the Vietnamese search normalization module (B07)."""

from app.modules.search.internal.normalize import (
    MAX_SEARCH_QUERY_CHARS,
    MAX_SEARCH_QUERY_TERMS,
    ParsedSearchQuery,
    fold_for_search,
    parse_search_query,
)

__all__ = [
    "MAX_SEARCH_QUERY_CHARS",
    "MAX_SEARCH_QUERY_TERMS",
    "ParsedSearchQuery",
    "fold_for_search",
    "parse_search_query",
]
