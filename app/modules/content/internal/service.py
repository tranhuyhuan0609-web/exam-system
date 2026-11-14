"""B01 content service: items, immutable versions and publication, under the caller's tenant RLS.

Rules enforced here and again by the database (so a bug here cannot publish what the DDL forbids):
* a version is edited only while DRAFT; a published one is frozen (trigger `IMMUTABLE_VIOLATION`) and a
  change is a new version `version_no + 1` created as DRAFT (`create_next_version`);
* an item reaches PUBLISHED only when VERIFIED, with cleared rights and a current version
  (`chk_content_publish_gate`); `publish_version` reports the failing reasons before the database would;
* publishing writes the `publications` log row and the `content.published` outbox event in the same
  transaction.

The B02 validator is injected (`validator`): this module must not import the authoring module, which
imports this one for its types. Old versions stay PUBLISHED when a newer one is published: the spec's
immutability trigger forbids moving a frozen version to SUPERSEDED, so `content_items.current_version_id`
alone says which version is served.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.events import EventEnvelopeContract
from app.modules.content.internal.models import (
    CLEARED_RIGHTS,
    ContentAsset,
    ContentItem,
    ContentVersion,
    Publication,
)
from app.modules.content.internal.versioning import (
    ContentBlockDraft,
    ContentBlockType,
    ContentStudioError,
    ContentType,
    ContentValidationReport,
    ContentVersionState,
    ContentVersionStatus,
    edit_draft_blocks,
    fork_next_content_version,
    publish_content_version,
)
from app.modules.outbox.public import enqueue

CONTENT_PUBLISHED_EVENT = "content.published"


class ContentNotFoundError(LookupError):
    """The item, version or referenced node does not exist or belongs to another tenant (same answer)."""


class ContentConflictError(ValueError):
    """A state conflict; `code` is one of SLUG_TAKEN, DRAFT_EXISTS, CONTENT_TAKEN_DOWN, CONTENT_NOT_PUBLISHED,
    CONTENT_NOT_DRAFT."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PublishGateBlockedError(ValueError):
    """The publish gate is not met; `reasons` says why (verification, rights, status)."""

    def __init__(self, reasons: Sequence[str]) -> None:
        self.reasons = tuple(reasons)
        super().__init__("; ".join(self.reasons))


class ContentValidationFailedError(ValueError):
    """The B02 validator rejected the version; `issues` lists every problem found."""

    def __init__(self, report: ContentValidationReport) -> None:
        self.issues = report.issues
        super().__init__("; ".join(f"[{i.code}] {i.message}" for i in report.issues) or "validation failed")


@dataclass(frozen=True, slots=True)
class ContentItemRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    visibility_scope: str
    content_type: str
    slug: str
    curriculum_node_id: uuid.UUID | None
    primary_taxonomy_node_id: uuid.UUID | None
    current_version_id: uuid.UUID | None
    acquisition_status: str
    verification_status: str
    rights_status: str
    publication_status: str
    access_tier: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ContentVersionRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    content_item_id: uuid.UUID
    version_no: int
    title: str
    summary_md: str | None
    blocks: tuple[dict[str, Any], ...]
    status: str
    is_immutable: bool
    created_by: uuid.UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PublishResult:
    item: ContentItemRecord
    version: ContentVersionRecord
    publication_id: uuid.UUID
    event_id: uuid.UUID
    linked_asset_ids: tuple[uuid.UUID, ...]


def _item(row: ContentItem) -> ContentItemRecord:
    return ContentItemRecord(**{f: getattr(row, f) for f in ContentItemRecord.__dataclass_fields__})


def _version(row: ContentVersion) -> ContentVersionRecord:
    values = {f: getattr(row, f) for f in ContentVersionRecord.__dataclass_fields__ if f != "blocks"}
    return ContentVersionRecord(blocks=tuple(row.blocks_json or ()), **values)


def blocks_to_json(blocks: Iterable[ContentBlockDraft]) -> list[dict[str, Any]]:
    return [
        {"block_order": b.block_order, "block_type": str(b.block_type), "payload_json": dict(b.payload_json)}
        for b in sorted(blocks, key=lambda b: b.block_order)
    ]


def blocks_from_json(raw: Iterable[dict[str, Any]]) -> tuple[ContentBlockDraft, ...]:
    return tuple(
        ContentBlockDraft(
            block_order=item["block_order"],
            block_type=ContentBlockType(item["block_type"]),
            payload_json=item.get("payload_json") or {},
        )
        for item in raw
    )


def _state(version: ContentVersion, item: ContentItem) -> ContentVersionState:
    return ContentVersionState(
        content_item_id=str(item.id),
        tenant_id=str(item.tenant_id),
        content_type=ContentType(item.content_type),
        version_no=version.version_no,
        title=version.title,
        summary_md=version.summary_md or "",
        blocks=blocks_from_json(version.blocks_json or ()),
        status=ContentVersionStatus(version.status),
        is_immutable=version.is_immutable,
        created_by=None if version.created_by is None else str(version.created_by),
    )


async def _require_visible_node(session: AsyncSession, table: str, node_id: uuid.UUID | None, label: str) -> None:
    """The node must be visible to the tenant (its own, or system public): foreign keys alone would also
    accept another tenant's private node, because referential checks bypass row-level security."""
    if node_id is None:
        return
    # table is one of two literals chosen by the caller, never input
    found = await session.scalar(text(f"SELECT 1 FROM {table} WHERE id = :id"), {"id": node_id})
    if found is None:
        raise ContentNotFoundError(f"{label} not found")


async def create_content_item(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    created_by: uuid.UUID,
    content_type: str,
    slug: str,
    title: str,
    summary_md: str | None = None,
    blocks: Sequence[ContentBlockDraft] = (),
    curriculum_node_id: uuid.UUID | None = None,
    primary_taxonomy_node_id: uuid.UUID | None = None,
    visibility_scope: str = "TENANT_PRIVATE",
    access_tier: str = "FREE",
    rights_status: str = "PENDING_REVIEW",
) -> tuple[ContentItemRecord, ContentVersionRecord]:
    """Create an item with its first version (`version_no = 1`, DRAFT). Status axes start fail-closed."""
    await _require_visible_node(session, "curriculum_nodes", curriculum_node_id, "curriculum node")
    await _require_visible_node(session, "taxonomy_nodes", primary_taxonomy_node_id, "taxonomy node")
    try:
        async with session.begin_nested():
            item = (
                await session.execute(
                    insert(ContentItem)
                    .values(
                        tenant_id=tenant_id,
                        content_type=content_type,
                        slug=slug,
                        curriculum_node_id=curriculum_node_id,
                        primary_taxonomy_node_id=primary_taxonomy_node_id,
                        visibility_scope=visibility_scope,
                        access_tier=access_tier,
                        rights_status=rights_status,
                    )
                    .returning(ContentItem)
                )
            ).scalar_one()
            version = (
                await session.execute(
                    insert(ContentVersion)
                    .values(
                        tenant_id=tenant_id,
                        content_item_id=item.id,
                        version_no=1,
                        title=title,
                        summary_md=summary_md,
                        blocks_json=blocks_to_json(blocks),
                        created_by=created_by,
                    )
                    .returning(ContentVersion)
                )
            ).scalar_one()
    except IntegrityError as exc:
        if "uq_content_items_tenant_id_slug" in str(exc.orig):
            raise ContentConflictError("SLUG_TAKEN", f"slug {slug!r} is already used in this tenant") from exc
        raise
    return _item(item), _version(version)


async def get_content_item(session: AsyncSession, item_id: uuid.UUID) -> ContentItemRecord | None:
    row = await session.scalar(
        select(ContentItem).where(ContentItem.id == item_id).execution_options(populate_existing=True)
    )
    return None if row is None else _item(row)


async def get_content_version(session: AsyncSession, version_id: uuid.UUID) -> ContentVersionRecord | None:
    row = await session.scalar(
        select(ContentVersion).where(ContentVersion.id == version_id).execution_options(populate_existing=True)
    )
    return None if row is None else _version(row)


async def list_content_versions(session: AsyncSession, item_id: uuid.UUID) -> list[ContentVersionRecord]:
    rows = await session.scalars(
        select(ContentVersion)
        .where(ContentVersion.content_item_id == item_id)
        .order_by(ContentVersion.version_no)
        .execution_options(populate_existing=True)
    )
    return [_version(r) for r in rows]


async def create_next_version(
    session: AsyncSession,
    item_id: uuid.UUID,
    *,
    created_by: uuid.UUID,
    title: str | None = None,
    summary_md: str | None = None,
    blocks: Sequence[ContentBlockDraft] | None = None,
) -> ContentVersionRecord:
    """Fork the newest (published) version into `version_no + 1` as an editable DRAFT.

    One draft at a time: while a DRAFT exists it is the thing to edit, so a second one is a conflict.
    """
    item = await session.scalar(select(ContentItem).where(ContentItem.id == item_id).with_for_update())
    if item is None:
        raise ContentNotFoundError("content item not found")
    latest = await session.scalar(
        select(ContentVersion)
        .where(ContentVersion.content_item_id == item_id)
        .order_by(ContentVersion.version_no.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )
    if latest is None:
        raise ContentNotFoundError("content item has no version")
    if latest.status == ContentVersionStatus.DRAFT:
        raise ContentConflictError("DRAFT_EXISTS", f"version {latest.version_no} is still a DRAFT: edit it")
    try:
        _, draft = fork_next_content_version(_state(latest, item), created_by=str(created_by))
        draft = edit_draft_blocks(
            draft, title=title, summary_md=summary_md, blocks=None if blocks is None else tuple(blocks)
        )
    except ContentStudioError as exc:
        raise ContentConflictError(exc.code, str(exc)) from exc
    row = (
        await session.execute(
            insert(ContentVersion)
            .values(
                tenant_id=item.tenant_id,
                content_item_id=item.id,
                version_no=draft.version_no,
                title=draft.title,
                summary_md=draft.summary_md or None,
                blocks_json=blocks_to_json(draft.blocks),
                created_by=created_by,
            )
            .returning(ContentVersion)
        )
    ).scalar_one()
    return _version(row)


async def set_draft_version_blocks(
    session: AsyncSession,
    version_id: uuid.UUID,
    blocks: Sequence[ContentBlockDraft],
    *,
    title: str | None = None,
    summary_md: str | None = None,
) -> ContentVersionRecord:
    """Replace the content of an editable DRAFT version; any other status is a conflict (fork instead)."""
    version = await session.scalar(select(ContentVersion).where(ContentVersion.id == version_id).with_for_update())
    if version is None:
        raise ContentNotFoundError("content version not found")
    item = await session.scalar(select(ContentItem).where(ContentItem.id == version.content_item_id))
    if item is None:
        raise ContentNotFoundError("content item not found")
    try:
        edited = edit_draft_blocks(_state(version, item), title=title, summary_md=summary_md, blocks=tuple(blocks))
    except ContentStudioError as exc:
        raise ContentConflictError(exc.code, str(exc)) from exc
    row = (
        await session.execute(
            update(ContentVersion)
            .where(ContentVersion.id == version_id)
            .values(title=edited.title, summary_md=edited.summary_md or None, blocks_json=blocks_to_json(edited.blocks))
            .returning(ContentVersion)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return _version(row)


def gate_reasons(item: ContentItem) -> list[str]:
    """Why `item` cannot be PUBLISHED yet (empty: the gate is met)."""
    reasons = []
    if item.publication_status == "TAKEN_DOWN" or item.rights_status == "TAKEDOWN":
        reasons.append("the item was taken down")
    if item.verification_status != "VERIFIED":
        reasons.append(f"verification_status is {item.verification_status}, VERIFIED is required")
    if item.rights_status not in CLEARED_RIGHTS:
        reasons.append(f"rights_status is {item.rights_status}, one of {', '.join(CLEARED_RIGHTS)} is required")
    return reasons


async def publish_version(
    session: AsyncSession,
    version_id: uuid.UUID,
    *,
    published_by: uuid.UUID,
    validator: Callable[[ContentVersionState], ContentValidationReport],
    own_asset_ids: Iterable[uuid.UUID] = (),
    correlation_id: str | None = None,
) -> PublishResult:
    """Validate and freeze a DRAFT version, make it the item's current version and announce it.

    `own_asset_ids` are the validated assets that belong to the tenant: they are recorded in
    `content_assets` (assets of the system tenant are referenced in the blocks only, since the composite
    foreign key ties both ends to one tenant).
    """
    version = await session.scalar(select(ContentVersion).where(ContentVersion.id == version_id).with_for_update())
    if version is None:
        raise ContentNotFoundError("content version not found")
    item = await session.scalar(
        select(ContentItem)
        .where(ContentItem.id == version.content_item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if item is None:
        raise ContentNotFoundError("content item not found")

    report_box: list[ContentValidationReport] = []

    def capture(state: ContentVersionState) -> ContentValidationReport:
        report_box.append(validator(state))
        return report_box[-1]

    try:
        publish_content_version(_state(version, item), validator=capture)
    except ContentStudioError as exc:
        if report_box and not report_box[-1].valid:
            raise ContentValidationFailedError(report_box[-1]) from exc
        raise ContentConflictError(exc.code, str(exc)) from exc
    if reasons := gate_reasons(item):
        raise PublishGateBlockedError(reasons)

    await session.execute(update(ContentVersion).where(ContentVersion.id == version.id).values(status="PUBLISHED"))
    await session.execute(
        update(ContentItem)
        .where(ContentItem.id == item.id)
        .values(publication_status="PUBLISHED", current_version_id=version.id)
    )
    publication_id = (
        await session.execute(
            insert(Publication)
            .values(
                tenant_id=item.tenant_id,
                entity_type="CONTENT",
                entity_id=item.id,
                version_id=version.id,
                published_by=published_by,
            )
            .returning(Publication.id)
        )
    ).scalar_one()
    linked = tuple(dict.fromkeys(own_asset_ids))
    for asset_id in linked:
        await session.execute(
            insert(ContentAsset).values(content_version_id=version.id, asset_id=asset_id, tenant_id=item.tenant_id)
        )
    envelope = EventEnvelopeContract(
        event_type=CONTENT_PUBLISHED_EVENT,
        tenant_id=item.tenant_id,
        aggregate_type="content_item",
        aggregate_id=item.id,
        correlation_id=correlation_id,
        payload={
            "content_item_id": str(item.id),
            "version_id": str(version.id),
            "version_no": version.version_no,
            "curriculum_node_id": None if item.curriculum_node_id is None else str(item.curriculum_node_id),
        },
    )
    await enqueue(session, envelope)

    fresh_version = await session.scalar(
        select(ContentVersion).where(ContentVersion.id == version.id).execution_options(populate_existing=True)
    )
    fresh_item = await session.scalar(
        select(ContentItem).where(ContentItem.id == item.id).execution_options(populate_existing=True)
    )
    assert fresh_version is not None and fresh_item is not None
    return PublishResult(_item(fresh_item), _version(fresh_version), publication_id, envelope.event_id, linked)
