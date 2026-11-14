"""Public facade for B07 Vietnamese FTS, Search Discovery & Collections.

Other modules import only from here, never from `app.modules.search.internal`.
"""

from app.modules.search.internal.normalize import (
    MAX_SEARCH_QUERY_CHARS,
    MAX_SEARCH_QUERY_TERMS,
    ParsedSearchQuery,
    fold_for_search,
    parse_search_query,
)
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
