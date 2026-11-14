"""Database service layer for B07 Search, Discovery & Collections."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.search.internal.fts import build_fts_query_plan
from app.modules.search.internal.models import (
    SEARCH_DOC_TYPES,
    Collection,
    CollectionItem,
    SearchDocument,
)

_LTREE_PATH_RE = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")
_VALID_VISIBILITY = frozenset({"SYSTEM_PUBLIC", "TENANT_PRIVATE", "SHARED_PARTNER"})
_VALID_COLLECTION_ITEM_TYPES = frozenset({"CONTENT", "QUESTION", "ASSESSMENT", "COLLECTION"})


class SearchValidationError(ValueError):
    """Raised when search filters, ltree paths, or collection payloads are invalid."""


class SearchNotFoundError(LookupError):
    """Raised when a search document or collection resource cannot be found."""


class CollectionNotFoundError(SearchNotFoundError):
    """Raised when a collection does not exist or is invisible to the tenant."""


class SearchConflictError(RuntimeError):
    """Raised on duplicate or conflicting collection item states."""


@dataclass(frozen=True)
class SearchDocumentView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    visibility_scope: str
    doc_type: str
    entity_id: uuid.UUID
    title: str
    body_plain: str
    subject_code: str | None
    grade_band: str | None
    taxonomy_path: str | None
    updated_at: datetime


@dataclass(frozen=True)
class SearchHitView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    visibility_scope: str
    doc_type: str
    entity_id: uuid.UUID
    title: str
    snippet: str
    subject_code: str | None
    grade_band: str | None
    taxonomy_path: str | None
    score: float
    updated_at: datetime


@dataclass(frozen=True)
class SearchResultPage:
    items: tuple[SearchHitView, ...]
    total_count: int
    limit: int
    offset: int
    was_truncated: bool
    normalized_query: str


@dataclass(frozen=True)
class CollectionItemView:
    collection_id: uuid.UUID
    tenant_id: uuid.UUID
    item_type: str
    item_id: uuid.UUID
    sequence_order: int


@dataclass(frozen=True)
class CollectionView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    owner_membership_id: uuid.UUID
    title: str
    created_at: datetime
    items: tuple[CollectionItemView, ...]


def _validate_ltree(path: str | None) -> str | None:
    if path is None:
        return None
    cleaned = path.strip()
    if not cleaned:
        return None
    if not _LTREE_PATH_RE.fullmatch(cleaned):
        raise SearchValidationError(f"Invalid taxonomy_path '{path}'; expected dot-separated ltree labels.")
    return cleaned


def _doc_to_view(row: SearchDocument) -> SearchDocumentView:
    return SearchDocumentView(
        id=row.id,
        tenant_id=row.tenant_id,
        visibility_scope=row.visibility_scope,
        doc_type=row.doc_type,
        entity_id=row.entity_id,
        title=row.title,
        body_plain=row.body_plain,
        subject_code=row.subject_code,
        grade_band=row.grade_band,
        taxonomy_path=row.taxonomy_path,
        updated_at=row.updated_at,
    )


async def upsert_search_document(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    doc_type: str,
    entity_id: uuid.UUID,
    title: str,
    body_plain: str,
    visibility_scope: str = "TENANT_PRIVATE",
    subject_code: str | None = None,
    grade_band: str | None = None,
    taxonomy_path: str | None = None,
) -> SearchDocumentView:
    """Insert or refresh a document projection in `search_documents`."""
    normalized_doc_type = doc_type.strip().upper()
    if normalized_doc_type not in SEARCH_DOC_TYPES:
        raise SearchValidationError(f"Unsupported doc_type '{doc_type}'")
    normalized_scope = visibility_scope.strip().upper()
    if normalized_scope not in _VALID_VISIBILITY:
        raise SearchValidationError(f"Unsupported visibility_scope '{visibility_scope}'")
    cleaned_title = title.strip()
    if not cleaned_title:
        raise SearchValidationError("title cannot be empty")
    cleaned_tax = _validate_ltree(taxonomy_path)

    stmt = (
        pg_insert(SearchDocument)
        .values(
            tenant_id=tenant_id,
            visibility_scope=normalized_scope,
            doc_type=normalized_doc_type,
            entity_id=entity_id,
            title=cleaned_title,
            body_plain=body_plain or "",
            subject_code=subject_code.strip().upper() if subject_code and subject_code.strip() else None,
            grade_band=grade_band.strip().upper() if grade_band and grade_band.strip() else None,
            taxonomy_path=cleaned_tax,
        )
        .on_conflict_do_update(
            index_elements=["tenant_id", "doc_type", "entity_id"],
            set_={
                "visibility_scope": normalized_scope,
                "title": cleaned_title,
                "body_plain": body_plain or "",
                "subject_code": subject_code.strip().upper() if subject_code and subject_code.strip() else None,
                "grade_band": grade_band.strip().upper() if grade_band and grade_band.strip() else None,
                "taxonomy_path": cleaned_tax,
                "updated_at": func.now(),
            },
        )
        .returning(SearchDocument.id)
    )
    doc_id = (await session.execute(stmt)).scalar_one()
    row = (await session.execute(select(SearchDocument).where(SearchDocument.id == doc_id))).scalar_one()
    return _doc_to_view(row)


async def remove_search_document(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    entity_id: uuid.UUID,
    doc_type: str | None = None,
) -> int:
    """Immediately purge a search projection row (e.g. on TAKEN_DOWN)."""
    params: dict[str, Any] = {"tenant_id": tenant_id, "entity_id": entity_id}
    sql = "DELETE FROM search_documents WHERE tenant_id = :tenant_id AND entity_id = :entity_id"
    if doc_type is not None:
        normalized_doc_type = doc_type.strip().upper()
        if normalized_doc_type not in SEARCH_DOC_TYPES:
            raise SearchValidationError(f"Unsupported doc_type '{doc_type}'")
        sql += " AND doc_type = :doc_type"
        params["doc_type"] = normalized_doc_type
    result = await session.execute(text(sql), params)
    return int(result.rowcount or 0)


async def handle_takedown_for_entity(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    target_entity_type: str,
    target_entity_id: uuid.UUID,
) -> int:
    """Remove TAKEN_DOWN/TAKEDOWN entity from `search_documents` immediately."""
    mapping = {
        "CONTENT_ITEM": "CONTENT",
        "CONTENT": "CONTENT",
        "QUESTION": "QUESTION",
        "ASSESSMENT": "ASSESSMENT",
    }
    doc_type = mapping.get(target_entity_type.strip().upper())
    return await remove_search_document(
        session,
        tenant_id=tenant_id,
        entity_id=target_entity_id,
        doc_type=doc_type,
    )


async def _detect_optional_tables(session: AsyncSession) -> dict[str, bool]:
    row = (
        await session.execute(
            text(
                """
                SELECT
                    EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'takedown_cases'
                          AND column_name = 'target_entity_id'
                    ) AS has_takedown_cases,
                    EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'content_items'
                          AND column_name = 'publication_status'
                    ) AS has_content_items,
                    EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'questions'
                          AND column_name = 'publication_status'
                    ) AS has_questions,
                    EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'assessments'
                          AND column_name = 'publication_status'
                    ) AS has_assessments
                """
            )
        )
    ).mappings().one()
    return {k: bool(v) for k, v in row.items()}


async def search_documents_fts(
    session: AsyncSession,
    *,
    query_text: str = "",
    doc_type: str | None = None,
    subject_code: str | None = None,
    grade_band: str | None = None,
    taxonomy_path: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> SearchResultPage:
    """Search `search_documents` using Vietnamese `f_unaccent` FTS + `pg_trgm`, excluding `TAKEN_DOWN`."""
    if limit < 1 or limit > 100:
        raise SearchValidationError("limit must be between 1 and 100")
    if offset < 0:
        raise SearchValidationError("offset must be non-negative")

    normalized_doc_type: str | None = None
    if doc_type is not None and doc_type.strip():
        normalized_doc_type = doc_type.strip().upper()
        if normalized_doc_type not in SEARCH_DOC_TYPES:
            raise SearchValidationError(f"Unsupported doc_type '{doc_type}'")

    cleaned_tax = _validate_ltree(taxonomy_path)
    plan = build_fts_query_plan(query_text)

    conditions: list[str] = [
        "(sd.tenant_id = (SELECT current_app_tenant_id()) "
        "OR (sd.tenant_id = system_public_tenant_id() AND sd.visibility_scope = 'SYSTEM_PUBLIC'))"
    ]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    optional_tables = await _detect_optional_tables(session)
    if optional_tables["has_takedown_cases"]:
        conditions.append(
            "NOT EXISTS (SELECT 1 FROM takedown_cases tc "
            "WHERE tc.target_entity_id = sd.entity_id AND tc.status = 'EXECUTED')"
        )
    if optional_tables["has_content_items"]:
        conditions.append(
            "NOT (sd.doc_type = 'CONTENT' AND EXISTS ("
            "SELECT 1 FROM content_items ci WHERE ci.id = sd.entity_id "
            "AND (ci.publication_status = 'TAKEN_DOWN' OR ci.rights_status = 'TAKEDOWN')))"
        )
    if optional_tables["has_questions"]:
        conditions.append(
            "NOT (sd.doc_type = 'QUESTION' AND EXISTS ("
            "SELECT 1 FROM questions q WHERE q.id = sd.entity_id "
            "AND (q.publication_status = 'TAKEN_DOWN' OR q.rights_status = 'TAKEDOWN')))"
        )
    if optional_tables["has_assessments"]:
        conditions.append(
            "NOT (sd.doc_type = 'ASSESSMENT' AND EXISTS ("
            "SELECT 1 FROM assessments a WHERE a.id = sd.entity_id "
            "AND (a.publication_status = 'TAKEN_DOWN' OR a.rights_status = 'TAKEDOWN')))"
        )

    if normalized_doc_type is not None:
        conditions.append("sd.doc_type = :doc_type")
        params["doc_type"] = normalized_doc_type
    if subject_code is not None and subject_code.strip():
        conditions.append("upper(sd.subject_code) = :subject_code")
        params["subject_code"] = subject_code.strip().upper()
    if grade_band is not None and grade_band.strip():
        conditions.append("upper(sd.grade_band) = :grade_band")
        params["grade_band"] = grade_band.strip().upper()
    if cleaned_tax is not None:
        conditions.append("sd.taxonomy_path IS NOT NULL AND sd.taxonomy_path <@ CAST(:taxonomy_path AS ltree)")
        params["taxonomy_path"] = cleaned_tax

    score_terms: list[str] = []
    match_or_parts: list[str] = []

    if plan.include_phrases:
        phrase_and_parts: list[str] = []
        for idx, phrase in enumerate(plan.include_phrases):
            key = f"inc_phrase_{idx}"
            params[key] = phrase
            phrase_and_parts.append(f"sd.search_vector @@ phraseto_tsquery('simple', :{key})")
            score_terms.append(f"ts_rank_cd(sd.search_vector, phraseto_tsquery('simple', :{key})) * 12.0")
        if plan.include_tsquery_prefix:
            params["prefix_q"] = plan.include_tsquery_prefix
            phrase_and_parts.append("sd.search_vector @@ to_tsquery('simple', :prefix_q)")
            score_terms.append("ts_rank_cd(sd.search_vector, to_tsquery('simple', :prefix_q)) * 10.0")
        conditions.append("(" + " AND ".join(phrase_and_parts) + ")")
    elif plan.include_tsquery_prefix:
        params["prefix_q"] = plan.include_tsquery_prefix
        match_or_parts.append("sd.search_vector @@ to_tsquery('simple', :prefix_q)")
        score_terms.append("ts_rank_cd(sd.search_vector, to_tsquery('simple', :prefix_q)) * 10.0")
        if plan.folded_include_text:
            params["folded_q"] = plan.folded_include_text
            match_or_parts.append("similarity(lower(f_unaccent(sd.title)), :folded_q) >= 0.3")
            score_terms.append("COALESCE(similarity(lower(f_unaccent(sd.title)), :folded_q), 0)")
        conditions.append("(" + " OR ".join(match_or_parts) + ")")

    for idx, ex_phrase in enumerate(plan.exclude_phrases):
        key = f"exc_phrase_{idx}"
        params[key] = ex_phrase
        conditions.append(f"NOT (sd.search_vector @@ phraseto_tsquery('simple', :{key}))")

    for idx, ex_term in enumerate(plan.exclude_terms):
        key = f"exc_term_{idx}"
        params[key] = ex_term
        conditions.append(f"NOT (sd.search_vector @@ plainto_tsquery('simple', :{key}))")

    where_sql = " AND ".join(conditions)
    score_expr = " + ".join(score_terms) if score_terms else "0.0"

    count_sql = f"SELECT COUNT(*) FROM search_documents sd WHERE {where_sql}"
    total_count = int((await session.execute(text(count_sql), params)).scalar_one() or 0)

    list_sql = f"""
        SELECT
            sd.id,
            sd.tenant_id,
            sd.visibility_scope,
            sd.doc_type,
            sd.entity_id,
            sd.title,
            LEFT(sd.body_plain, 280) AS snippet,
            sd.subject_code,
            sd.grade_band,
            sd.taxonomy_path::text AS taxonomy_path,
            ({score_expr})::double precision AS score,
            sd.updated_at
        FROM search_documents sd
        WHERE {where_sql}
        ORDER BY score DESC, sd.updated_at DESC, sd.id DESC
        LIMIT :limit OFFSET :offset
    """
    rows = (await session.execute(text(list_sql), params)).mappings().all()
    items = tuple(
        SearchHitView(
            id=uuid.UUID(str(r["id"])),
            tenant_id=uuid.UUID(str(r["tenant_id"])),
            visibility_scope=str(r["visibility_scope"]),
            doc_type=str(r["doc_type"]),
            entity_id=uuid.UUID(str(r["entity_id"])),
            title=str(r["title"]),
            snippet=str(r["snippet"] or ""),
            subject_code=r["subject_code"],
            grade_band=r["grade_band"],
            taxonomy_path=r["taxonomy_path"],
            score=float(r["score"] or 0.0),
            updated_at=r["updated_at"],
        )
        for r in rows
    )
    return SearchResultPage(
        items=items,
        total_count=total_count,
        limit=limit,
        offset=offset,
        was_truncated=plan.parsed.was_truncated,
        normalized_query=plan.parsed.normalized_text,
    )


async def create_collection(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    owner_membership_id: uuid.UUID,
    title: str,
) -> CollectionView:
    """Create a tenant-scoped saved collection owned by the caller's membership."""
    cleaned_title = title.strip()
    if not cleaned_title:
        raise SearchValidationError("Collection title cannot be empty")
    if len(cleaned_title) > 255:
        raise SearchValidationError("Collection title exceeds 255 characters")

    row = Collection(
        tenant_id=tenant_id,
        owner_membership_id=owner_membership_id,
        title=cleaned_title,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return CollectionView(
        id=row.id,
        tenant_id=row.tenant_id,
        owner_membership_id=row.owner_membership_id,
        title=row.title,
        created_at=row.created_at,
        items=(),
    )


async def get_collection(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID,
) -> CollectionView:
    """Fetch a collection with its ordered items within the caller tenant (RLS-enforced)."""
    col = (
        await session.execute(
            select(Collection).where(
                Collection.id == collection_id,
                Collection.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if col is None:
        raise CollectionNotFoundError(f"Collection {collection_id} not found")

    item_rows = (
        await session.execute(
            select(CollectionItem)
            .where(
                CollectionItem.collection_id == collection_id,
                CollectionItem.tenant_id == tenant_id,
            )
            .order_by(CollectionItem.sequence_order.asc(), CollectionItem.item_id.asc())
        )
    ).scalars().all()
    items = tuple(
        CollectionItemView(
            collection_id=it.collection_id,
            tenant_id=it.tenant_id,
            item_type=it.item_type,
            item_id=it.item_id,
            sequence_order=it.sequence_order,
        )
        for it in item_rows
    )
    return CollectionView(
        id=col.id,
        tenant_id=col.tenant_id,
        owner_membership_id=col.owner_membership_id,
        title=col.title,
        created_at=col.created_at,
        items=items,
    )


async def add_collection_item(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    collection_id: uuid.UUID,
    item_type: str,
    item_id: uuid.UUID,
    sequence_order: int | None = None,
) -> CollectionItemView:
    """Add or update an item inside an existing tenant collection."""
    normalized_type = item_type.strip().upper()
    if normalized_type not in _VALID_COLLECTION_ITEM_TYPES:
        raise SearchValidationError(f"Unsupported collection item_type '{item_type}'")
    if sequence_order is not None and sequence_order < 0:
        raise SearchValidationError("sequence_order must be non-negative")

    col = (
        await session.execute(
            select(Collection).where(
                Collection.id == collection_id,
                Collection.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if col is None:
        raise CollectionNotFoundError(f"Collection {collection_id} not found")

    if sequence_order is None:
        max_order = (
            await session.execute(
                select(func.coalesce(func.max(CollectionItem.sequence_order), -1)).where(
                    CollectionItem.collection_id == collection_id,
                    CollectionItem.tenant_id == tenant_id,
                )
            )
        ).scalar_one()
        resolved_order = int(max_order) + 1
    else:
        resolved_order = int(sequence_order)

    stmt = (
        pg_insert(CollectionItem)
        .values(
            collection_id=collection_id,
            tenant_id=tenant_id,
            item_type=normalized_type,
            item_id=item_id,
            sequence_order=resolved_order,
        )
        .on_conflict_do_update(
            index_elements=["collection_id", "item_type", "item_id"],
            set_={"sequence_order": resolved_order},
        )
        .returning(
            CollectionItem.collection_id,
            CollectionItem.tenant_id,
            CollectionItem.item_type,
            CollectionItem.item_id,
            CollectionItem.sequence_order,
        )
    )
    row = (await session.execute(stmt)).mappings().one()
    return CollectionItemView(
        collection_id=uuid.UUID(str(row["collection_id"])),
        tenant_id=uuid.UUID(str(row["tenant_id"])),
        item_type=str(row["item_type"]),
        item_id=uuid.UUID(str(row["item_id"])),
        sequence_order=int(row["sequence_order"]),
    )
