"""Public facade for B07 Vietnamese FTS, Search Discovery & Collections.

Other modules import only from here, never from `app.modules.search.internal`.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from app.modules.search.internal.normalize import (
    MAX_SEARCH_QUERY_CHARS,
    MAX_SEARCH_QUERY_TERMS,
    ParsedSearchQuery,
    fold_for_search,
    parse_search_query,
)

if TYPE_CHECKING:
    from app.modules.search.internal.service import (
        CollectionItemView,
        CollectionNotFoundError,
        CollectionView,
        SearchConflictError,
        SearchDocumentView,
        SearchHitView,
        SearchNotFoundError,
        SearchResultPage,
        SearchValidationError,
        add_collection_item,
        create_collection,
        get_collection,
        handle_takedown_for_entity,
        remove_search_document,
        search_documents_fts,
        upsert_search_document,
    )

_SERVICE_EXPORTS = frozenset(
    {
        "CollectionItemView",
        "CollectionNotFoundError",
        "CollectionView",
        "SearchConflictError",
        "SearchDocumentView",
        "SearchHitView",
        "SearchNotFoundError",
        "SearchResultPage",
        "SearchValidationError",
        "add_collection_item",
        "create_collection",
        "get_collection",
        "handle_takedown_for_entity",
        "remove_search_document",
        "search_documents_fts",
        "upsert_search_document",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily load DB service exports so pure-core callers avoid registering ORM models."""
    if name in _SERVICE_EXPORTS:
        service_mod = importlib.import_module("app.modules.search.internal.service")
        value = getattr(service_mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "MAX_SEARCH_QUERY_CHARS",
    "MAX_SEARCH_QUERY_TERMS",
    "CollectionItemView",
    "CollectionNotFoundError",
    "CollectionView",
    "ParsedSearchQuery",
    "SearchConflictError",
    "SearchDocumentView",
    "SearchHitView",
    "SearchNotFoundError",
    "SearchResultPage",
    "SearchValidationError",
    "add_collection_item",
    "create_collection",
    "fold_for_search",
    "get_collection",
    "handle_takedown_for_entity",
    "parse_search_query",
    "remove_search_document",
    "search_documents_fts",
    "upsert_search_document",
]
