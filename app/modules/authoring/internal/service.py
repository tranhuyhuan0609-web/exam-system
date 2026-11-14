"""B02 authoring service: working drafts of content blocks, validation and block rows of published versions.

An author edits a *draft* (`content_drafts`, one per author and item): a working copy of the blocks that
may be saved half-finished. `validate_draft` runs the Block Studio checks (KaTeX syntax, forbidden
commands, asset references the tenant may embed) and records the errors on the draft; only a valid draft
can be applied to the item's editable DRAFT version, which B01 then publishes. Everything runs under the
caller's tenant RLS, so another tenant's item, draft or asset is simply not there.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.authoring.internal.catalog import load_asset_catalog
from app.modules.authoring.internal.models import ContentBlock, ContentDraft
from app.modules.authoring.internal.studio import collect_asset_references, validate_content_version
from app.modules.content.public import (
    ContentBlockDraft,
    ContentConflictError,
    ContentNotFoundError,
    ContentType,
    ContentVersionState,
    ContentVersionStatus,
    blocks_from_json,
    blocks_to_json,
    get_content_item,
    list_content_versions,
    set_draft_version_blocks,
)


class DraftNotFoundError(LookupError):
    """The draft (or the item) does not exist or belongs to another tenant (same answer)."""


class DraftForbiddenError(PermissionError):
    """Only the author of a draft (or an administrator) may change it."""


@dataclass(frozen=True, slots=True)
class DraftRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    content_item_id: uuid.UUID
    author_id: uuid.UUID
    working_blocks: tuple[dict[str, Any], ...]
    validation_errors: tuple[dict[str, Any], ...]
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    draft: DraftRecord
    valid: bool
    issues: tuple[dict[str, Any], ...]
    applied_version_id: uuid.UUID | None


def _record(row: ContentDraft) -> DraftRecord:
    return DraftRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        content_item_id=row.content_item_id,
        author_id=row.author_id,
        working_blocks=tuple(row.working_blocks_json or ()),
        validation_errors=tuple(row.validation_errors_json or ()),
        updated_at=row.updated_at,
    )


async def _own_item(session: AsyncSession, tenant_id: uuid.UUID, item_id: uuid.UUID):
    """The tenant's own item: a visible item of the system tenant is public material, not editable here."""
    item = await get_content_item(session, item_id)
    if item is None or item.tenant_id != tenant_id:
        raise DraftNotFoundError("content item not found")
    return item


async def create_draft(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    content_item_id: uuid.UUID,
    author_id: uuid.UUID,
    blocks: Sequence[ContentBlockDraft] | None = None,
) -> tuple[DraftRecord, bool]:
    """The author's draft of the item, created (copy of the newest version's blocks) when missing.

    Returns `(draft, created)`; an existing draft is returned untouched so a retry never loses work.
    """
    await _own_item(session, tenant_id, content_item_id)
    existing = await session.scalar(
        select(ContentDraft)
        .where(ContentDraft.content_item_id == content_item_id, ContentDraft.author_id == author_id)
        .order_by(ContentDraft.updated_at.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )
    if existing is not None and blocks is None:
        return _record(existing), False
    if blocks is None:
        versions = await list_content_versions(session, content_item_id)
        working = list(versions[-1].blocks) if versions else []
    else:
        working = blocks_to_json(blocks)
    if existing is not None:
        row = (
            await session.execute(
                update(ContentDraft)
                .where(ContentDraft.id == existing.id)
                .values(working_blocks_json=working, validation_errors_json=[], updated_at=func.now())
                .returning(ContentDraft)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        return _record(row), False
    row = (
        await session.execute(
            insert(ContentDraft)
            .values(
                tenant_id=tenant_id,
                content_item_id=content_item_id,
                author_id=author_id,
                working_blocks_json=working,
            )
            .returning(ContentDraft)
        )
    ).scalar_one()
    return _record(row), True


async def _editable_draft(
    session: AsyncSession, draft_id: uuid.UUID, *, actor_user_id: uuid.UUID, actor_is_admin: bool
) -> ContentDraft:
    row = await session.scalar(
        select(ContentDraft)
        .where(ContentDraft.id == draft_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise DraftNotFoundError("draft not found")
    if row.author_id != actor_user_id and not actor_is_admin:
        raise DraftForbiddenError("only the author or an administrator may change this draft")
    return row


async def update_draft(
    session: AsyncSession,
    draft_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    actor_is_admin: bool,
    blocks: Sequence[ContentBlockDraft],
) -> DraftRecord:
    """Replace the working blocks; the recorded validation errors belonged to the old blocks and are cleared."""
    await _editable_draft(session, draft_id, actor_user_id=actor_user_id, actor_is_admin=actor_is_admin)
    row = (
        await session.execute(
            update(ContentDraft)
            .where(ContentDraft.id == draft_id)
            .values(working_blocks_json=blocks_to_json(blocks), validation_errors_json=[], updated_at=func.now())
            .returning(ContentDraft)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return _record(row)


async def validate_draft(
    session: AsyncSession,
    draft_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    actor_is_admin: bool,
    apply_to_version: bool = False,
) -> ValidationOutcome:
    """Run the Block Studio checks on the working blocks and record the result on the draft.

    With `apply_to_version`, a valid draft replaces the blocks of the item's DRAFT version (B01 publishes it
    afterwards); a newest version that is already published is a ContentConflictError (fork it first).
    """
    draft = await _editable_draft(session, draft_id, actor_user_id=actor_user_id, actor_is_admin=actor_is_admin)
    item = await _own_item(session, draft.tenant_id, draft.content_item_id)
    versions = await list_content_versions(session, item.id)
    if not versions:
        raise ContentNotFoundError("content item has no version")
    newest = versions[-1]
    blocks = blocks_from_json(draft.working_blocks_json or ())
    state = ContentVersionState(
        content_item_id=str(item.id),
        tenant_id=str(item.tenant_id),
        content_type=ContentType(item.content_type),
        version_no=newest.version_no,
        title=newest.title,
        summary_md=newest.summary_md or "",
        blocks=blocks,
        status=ContentVersionStatus.DRAFT,
    )
    catalog = await load_asset_catalog(session, collect_asset_references(blocks))
    report = validate_content_version(state, asset_catalog=catalog)
    issues = [{"block_order": i.block_order, "code": i.code, "message": i.message} for i in report.issues]

    applied: uuid.UUID | None = None
    if apply_to_version and report.valid:
        if newest.status != ContentVersionStatus.DRAFT or newest.is_immutable:
            raise ContentConflictError(
                "NO_EDITABLE_VERSION",
                f"version {newest.version_no} is {newest.status}: create the next version before applying a draft",
            )
        await set_draft_version_blocks(session, newest.id, blocks)
        applied = newest.id
    row = (
        await session.execute(
            update(ContentDraft)
            .where(ContentDraft.id == draft_id)
            .values(validation_errors_json=issues, updated_at=func.now())
            .returning(ContentDraft)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return ValidationOutcome(_record(row), report.valid, tuple(issues), applied)


async def materialize_version_blocks(
    session: AsyncSession, *, tenant_id: uuid.UUID, version_id: uuid.UUID, blocks: Sequence[ContentBlockDraft]
) -> int:
    """Write the blocks of a (published) version as `content_blocks` rows, replacing any earlier rows."""
    await session.execute(delete(ContentBlock).where(ContentBlock.content_version_id == version_id))
    rows = [
        {
            "tenant_id": tenant_id,
            "content_version_id": version_id,
            "block_order": b.block_order,
            "block_type": str(b.block_type),
            "payload_json": dict(b.payload_json),
        }
        for b in blocks
    ]
    if rows:
        await session.execute(insert(ContentBlock), rows)
    return len(rows)
