"""Taxonomy service operations for node creation, versioning, aliases and ltree queries."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.taxonomy.internal.models import (
    TaxonomyAlias,
    TaxonomyNode,
    TaxonomyNodeVersion,
)

_LTREE_INVALID_RE = re.compile(r"[^A-Za-z0-9_]+")
_WHITESPACE_RE = re.compile(r"\s+")


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
    resolved_path = code_path
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
        code=code,
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
        name=name,
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
) -> tuple[TaxonomyNodeRecord, TaxonomyNodeVersionRecord]:
    """Append version N+1 to an existing node without mutating node.id or node.code_path."""
    stmt = select(TaxonomyNode).where(TaxonomyNode.id == taxonomy_node_id).with_for_update()
    node = await session.scalar(stmt)
    if node is None:
        raise LookupError(f"taxonomy node {taxonomy_node_id} not found")

    next_version = node.current_version_no + 1
    node.current_version_no = next_version
    version = TaxonomyNodeVersion(
        tenant_id=node.tenant_id,
        taxonomy_node_id=node.id,
        version_no=next_version,
        name=name,
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
