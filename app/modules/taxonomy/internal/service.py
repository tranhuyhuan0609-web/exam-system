"""Taxonomy service operations for node creation, versioning, aliases and ltree queries."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.taxonomy.internal.models import (
    LtreeType,
    TaxonomyAlias,
    TaxonomyNode,
    TaxonomyNodeVersion,
)
from app.platform.pagination import CursorPage, clamp_limit, decode_cursor, encode_cursor

_LTREE_INVALID_RE = re.compile(r"[^A-Za-z0-9_]+")
_LTREE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
_WHITESPACE_RE = re.compile(r"\s+")

VALID_TAXONOMY_NODE_TYPES = frozenset({"DOMAIN", "SUBJECT", "TOPIC", "CONCEPT", "SKILL"})
VALID_TAXONOMY_VISIBILITY_SCOPES = frozenset({"SYSTEM_PUBLIC", "TENANT_PRIVATE", "SHARED_PARTNER"})
_LIST_NODES_ROUTE = "GET /api/v1/taxonomy/nodes"


@dataclass(frozen=True, slots=True)
class TaxonomyNodeRecord:
    """Public snapshot of a taxonomy node."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    visibility_scope: str
    parent_id: uuid.UUID | None
    node_type: str
    code: str
    code_path: str
    current_version_no: int
    is_active: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TaxonomyNodeVersionRecord:
    """Public snapshot of a taxonomy node version."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    taxonomy_node_id: uuid.UUID
    version_no: int
    name: str
    description_md: str | None
    created_by: uuid.UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TaxonomyNodeViewRecord:
    """Taxonomy node enriched with its current version's name and description."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    visibility_scope: str
    parent_id: uuid.UUID | None
    node_type: str
    code: str
    code_path: str
    current_version_no: int
    is_active: bool
    created_at: datetime
    name: str | None
    description_md: str | None


def validate_ltree_path(raw_path: str) -> str:
    """Validate that a dot-separated ltree path contains only `[A-Za-z0-9_]+` labels."""
    stripped = raw_path.strip()
    if not stripped:
        raise ValueError("ltree code_path must not be empty")
    segments = stripped.split(".")
    for seg in segments:
        if not _LTREE_SEGMENT_RE.match(seg):
            raise ValueError(f"invalid ltree path segment {seg!r} in {raw_path!r}")
    return stripped


def normalize_ltree_label(raw: str) -> str:
    """Convert a segment string into a valid PostgreSQL ltree label (`[A-Za-z0-9_]+`)."""
    cleaned = _LTREE_INVALID_RE.sub("_", raw.strip()).strip("_")
    if not cleaned:
        raise ValueError(f"cannot build valid ltree label from {raw!r}")
    return cleaned


def normalize_alias(alias_name: str) -> str:
    """Normalize an alias string for tenant-wide uniqueness checks."""
    collapsed = _WHITESPACE_RE.sub(" ", alias_name.strip()).casefold()
    if not collapsed:
        raise ValueError("alias_name must not be empty")
    return collapsed


def _to_node_record(row: TaxonomyNode) -> TaxonomyNodeRecord:
    return TaxonomyNodeRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        visibility_scope=row.visibility_scope,
        parent_id=row.parent_id,
        node_type=row.node_type,
        code=row.code,
        code_path=row.code_path,
        current_version_no=row.current_version_no,
        is_active=row.is_active,
        created_at=row.created_at,
    )


def _to_version_record(row: TaxonomyNodeVersion) -> TaxonomyNodeVersionRecord:
    return TaxonomyNodeVersionRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        taxonomy_node_id=row.taxonomy_node_id,
        version_no=row.version_no,
        name=row.name,
        description_md=row.description_md,
        created_by=row.created_by,
        created_at=row.created_at,
    )


async def create_taxonomy_node(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    node_type: str,
    code: str,
    name: str,
    visibility_scope: str = "TENANT_PRIVATE",
    parent_id: uuid.UUID | None = None,
    code_path: str | None = None,
    description_md: str | None = None,
    created_by: uuid.UUID | None = None,
    is_active: bool = True,
) -> tuple[TaxonomyNodeRecord, TaxonomyNodeVersionRecord]:
    """Create a taxonomy node together with version 1 of its metadata."""
    if node_type not in VALID_TAXONOMY_NODE_TYPES:
        raise ValueError(f"unsupported node_type {node_type!r}")
    if visibility_scope not in VALID_TAXONOMY_VISIBILITY_SCOPES:
        raise ValueError(f"unsupported visibility_scope {visibility_scope!r}")
    if not code or not code.strip():
        raise ValueError("code must not be blank")
    if not name or not name.strip():
        raise ValueError("name must not be blank")

    resolved_path = validate_ltree_path(code_path) if code_path is not None else None
    if resolved_path is None:
        leaf_source = code.rsplit(".", 1)[-1]
        leaf = normalize_ltree_label(leaf_source)
        if parent_id is not None:
            parent_path = await session.scalar(select(TaxonomyNode.code_path).where(TaxonomyNode.id == parent_id))
            if parent_path is None:
                raise LookupError(f"parent taxonomy node {parent_id} not found")
            resolved_path = f"{parent_path}.{leaf}"
        else:
            resolved_path = leaf

    node = TaxonomyNode(
        tenant_id=tenant_id,
        visibility_scope=visibility_scope,
        parent_id=parent_id,
        node_type=node_type,
        code=code.strip(),
        code_path=resolved_path,
        current_version_no=1,
        is_active=is_active,
    )
    session.add(node)
    await session.flush()

    version = TaxonomyNodeVersion(
        tenant_id=tenant_id,
        taxonomy_node_id=node.id,
        version_no=1,
        name=name.strip(),
        description_md=description_md,
        created_by=created_by,
    )
    session.add(version)
    await session.flush()
    await session.refresh(node)
    await session.refresh(version)
    return _to_node_record(node), _to_version_record(version)


async def create_node_version(
    session: AsyncSession,
    *,
    taxonomy_node_id: uuid.UUID,
    name: str,
    description_md: str | None = None,
    created_by: uuid.UUID | None = None,
    actor_tenant_id: uuid.UUID | None = None,
) -> tuple[TaxonomyNodeRecord, TaxonomyNodeVersionRecord]:
    """Append version N+1 to an existing node without mutating node.id or node.code_path."""
    if not name or not name.strip():
        raise ValueError("name must not be blank")

    # Read under SELECT RLS first so SYSTEM_PUBLIC nodes owned by another tenant yield 403 rather than 404.
    visible = await session.scalar(select(TaxonomyNode).where(TaxonomyNode.id == taxonomy_node_id))
    if visible is None:
        raise LookupError(f"taxonomy node {taxonomy_node_id} not found")
    if actor_tenant_id is not None and visible.tenant_id != actor_tenant_id:
        raise PermissionError("cannot create a new version on a taxonomy node owned by another tenant")

    node = await session.scalar(
        select(TaxonomyNode).where(TaxonomyNode.id == taxonomy_node_id).with_for_update()
    )
    if node is None:
        raise LookupError(f"taxonomy node {taxonomy_node_id} not found")

    next_version = node.current_version_no + 1
    node.current_version_no = next_version
    version = TaxonomyNodeVersion(
        tenant_id=node.tenant_id,
        taxonomy_node_id=node.id,
        version_no=next_version,
        name=name.strip(),
        description_md=description_md,
        created_by=created_by,
    )
    session.add(version)
    await session.flush()
    await session.refresh(node)
    await session.refresh(version)
    return _to_node_record(node), _to_version_record(version)


async def list_subtree(
    session: AsyncSession,
    root_code_path: str,
    *,
    include_root: bool = True,
    active_only: bool = False,
) -> list[TaxonomyNodeRecord]:
    """Query the taxonomy subtree rooted at `root_code_path` via PostgreSQL `ltree <@`."""
    stmt = select(TaxonomyNode).where(TaxonomyNode.code_path.descendant_of(root_code_path))
    if not include_root:
        stmt = stmt.where(TaxonomyNode.code_path != root_code_path)
    if active_only:
        stmt = stmt.where(TaxonomyNode.is_active.is_(True))
    stmt = stmt.order_by(TaxonomyNode.code_path.asc(), TaxonomyNode.created_at.asc())
    rows = (await session.scalars(stmt)).all()
    return [_to_node_record(r) for r in rows]


async def get_node_by_code(
    session: AsyncSession,
    *,
    code: str,
    tenant_id: uuid.UUID | None = None,
) -> TaxonomyNodeRecord | None:
    """Look up a taxonomy node by its code (optionally scoped to a tenant_id)."""
    stmt = select(TaxonomyNode).where(TaxonomyNode.code == code)
    if tenant_id is not None:
        stmt = stmt.where(TaxonomyNode.tenant_id == tenant_id)
    row = await session.scalar(stmt)
    return _to_node_record(row) if row is not None else None


async def get_node_by_id(
    session: AsyncSession,
    node_id: uuid.UUID,
) -> TaxonomyNodeRecord | None:
    """Look up a taxonomy node by primary key under current RLS visibility."""
    row = await session.scalar(select(TaxonomyNode).where(TaxonomyNode.id == node_id))
    return _to_node_record(row) if row is not None else None


async def list_taxonomy_nodes(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    cursor_secret: str,
    root_path: str | None = None,
    include_root: bool = True,
    node_type: str | None = None,
    active_only: bool = False,
    limit: int = 50,
    cursor: str | None = None,
) -> CursorPage[TaxonomyNodeViewRecord]:
    """List taxonomy nodes with optional ltree `<@` subtree filter and keyset pagination."""
    safe_limit = clamp_limit(limit, default=50)
    stmt = (
        select(
            TaxonomyNode,
            TaxonomyNodeVersion.name,
            TaxonomyNodeVersion.description_md,
        )
        .outerjoin(
            TaxonomyNodeVersion,
            and_(
                TaxonomyNodeVersion.taxonomy_node_id == TaxonomyNode.id,
                TaxonomyNodeVersion.version_no == TaxonomyNode.current_version_no,
            ),
        )
    )

    if root_path is not None:
        validated_root = validate_ltree_path(root_path)
        stmt = stmt.where(TaxonomyNode.code_path.descendant_of(validated_root))
        if not include_root:
            stmt = stmt.where(TaxonomyNode.code_path != cast(validated_root, LtreeType()))

    if node_type is not None:
        if node_type not in VALID_TAXONOMY_NODE_TYPES:
            raise ValueError(f"unsupported node_type {node_type!r}")
        stmt = stmt.where(TaxonomyNode.node_type == node_type)

    if active_only:
        stmt = stmt.where(TaxonomyNode.is_active.is_(True))

    if cursor is not None:
        decoded = decode_cursor(
            cursor,
            secret=cursor_secret,
            allowed_sort_fields=frozenset({"code_path"}),
            tenant_id=str(tenant_id),
            route=_LIST_NODES_ROUTE,
            expected_sort_field="code_path",
            expected_direction="asc",
        )
        after_path = validate_ltree_path(str(decoded.sort_value))
        after_id = uuid.UUID(decoded.tie_id)
        after_ltree = cast(after_path, LtreeType())
        stmt = stmt.where(
            or_(
                TaxonomyNode.code_path > after_ltree,
                and_(TaxonomyNode.code_path == after_ltree, TaxonomyNode.id > after_id),
            )
        )

    stmt = stmt.order_by(TaxonomyNode.code_path.asc(), TaxonomyNode.id.asc()).limit(safe_limit + 1)
    rows = (await session.execute(stmt)).all()
    has_more = len(rows) > safe_limit
    page_rows = rows[:safe_limit]

    items = tuple(
        TaxonomyNodeViewRecord(
            id=node.id,
            tenant_id=node.tenant_id,
            visibility_scope=node.visibility_scope,
            parent_id=node.parent_id,
            node_type=node.node_type,
            code=node.code,
            code_path=node.code_path,
            current_version_no=node.current_version_no,
            is_active=node.is_active,
            created_at=node.created_at,
            name=ver_name,
            description_md=ver_desc,
        )
        for node, ver_name, ver_desc in page_rows
    )

    next_cursor: str | None = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor(
            sort_field="code_path",
            sort_value=last.code_path,
            tie_id=str(last.id),
            direction="asc",
            secret=cursor_secret,
            tenant_id=str(tenant_id),
            route=_LIST_NODES_ROUTE,
        )

    return CursorPage(items=items, next_cursor=next_cursor, has_more=has_more, limit=safe_limit)


async def list_node_versions(
    session: AsyncSession,
    taxonomy_node_id: uuid.UUID,
) -> list[TaxonomyNodeVersionRecord]:
    """Return all versions for a taxonomy node ordered by version_no ascending."""
    stmt = (
        select(TaxonomyNodeVersion)
        .where(TaxonomyNodeVersion.taxonomy_node_id == taxonomy_node_id)
        .order_by(TaxonomyNodeVersion.version_no.asc())
    )
    rows = (await session.scalars(stmt)).all()
    return [_to_version_record(r) for r in rows]


async def add_taxonomy_alias(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    taxonomy_node_id: uuid.UUID,
    alias_name: str,
) -> uuid.UUID:
    """Register an alternative search alias for a taxonomy node."""
    alias = TaxonomyAlias(
        tenant_id=tenant_id,
        taxonomy_node_id=taxonomy_node_id,
        alias_name=alias_name.strip(),
        normalized_alias=normalize_alias(alias_name),
    )
    session.add(alias)
    await session.flush()
    return alias.id
