"""Database service layer for F06 Omnichannel Notifications & Deliveries."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.internal.models import Notification, NotificationDelivery
from app.modules.notifications.internal.router import (
    NotificationCategory,
    NotificationChannel,
    NotificationCommand,
    NotificationInboxState,
    NotificationPreferences,
    dispatch_notification_idempotent,
)


class NotificationValidationError(ValueError):
    """Raised when notification command or preference payloads are invalid."""


class NotificationNotFoundError(LookupError):
    """Raised when a notification cannot be found for the caller's membership."""


@dataclass(frozen=True, slots=True)
class DeliveryView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    notification_id: uuid.UUID
    channel: str
    status: str
    delivered_at: datetime


@dataclass(frozen=True, slots=True)
class NotificationView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    dedup_key: str
    title: str
    body_text: str
    is_read: bool
    created_at: datetime
    deliveries: tuple[DeliveryView, ...]


@dataclass(frozen=True, slots=True)
class NotificationDispatchResult:
    created: bool
    notification: NotificationView | None
    deliveries: tuple[DeliveryView, ...]


@dataclass(frozen=True, slots=True)
class InboxView:
    items: tuple[NotificationView, ...]
    unread_count: int


@dataclass(frozen=True, slots=True)
class PreferencesView:
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    enabled_channels: tuple[NotificationChannel, ...]
    muted_categories: tuple[NotificationCategory, ...]


def _delivery_view(row: NotificationDelivery) -> DeliveryView:
    return DeliveryView(
        id=row.id,
        tenant_id=row.tenant_id,
        notification_id=row.notification_id,
        channel=row.channel,
        status=row.status,
        delivered_at=row.delivered_at,
    )


def _notification_view(row: Notification, deliveries: Sequence[NotificationDelivery]) -> NotificationView:
    return NotificationView(
        id=row.id,
        tenant_id=row.tenant_id,
        membership_id=row.membership_id,
        dedup_key=row.dedup_key,
        title=row.title,
        body_text=row.body_text,
        is_read=row.is_read,
        created_at=row.created_at,
        deliveries=tuple(_delivery_view(d) for d in deliveries),
    )


def _parse_preferences(raw: dict[str, object] | None) -> NotificationPreferences:
    if not isinstance(raw, dict):
        return NotificationPreferences()
    raw_channels = raw.get("enabled_channels")
    if isinstance(raw_channels, list):
        try:
            channels = frozenset(NotificationChannel(str(ch)) for ch in raw_channels)
        except ValueError as exc:
            raise NotificationValidationError(f"invalid stored channel preference: {exc}") from exc
    else:
        channels = frozenset({NotificationChannel.IN_APP, NotificationChannel.EMAIL})

    raw_muted = raw.get("muted_categories")
    if isinstance(raw_muted, list):
        try:
            muted = frozenset(NotificationCategory(str(cat)) for cat in raw_muted)
        except ValueError as exc:
            raise NotificationValidationError(f"invalid stored muted category preference: {exc}") from exc
    else:
        muted = frozenset()
    return NotificationPreferences(enabled_channels=channels, muted_categories=muted)


async def get_preferences(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
) -> PreferencesView:
    """Read member-level notification preferences from `tenants.settings_json`."""
    raw = (
        await session.execute(
            text("SELECT settings_json->'notification_preferences'->:mid AS pref FROM tenants WHERE id = :tid"),
            {"tid": tenant_id, "mid": str(membership_id)},
        )
    ).scalar_one_or_none()
    prefs = _parse_preferences(raw if isinstance(raw, dict) else None)
    return PreferencesView(
        tenant_id=tenant_id,
        membership_id=membership_id,
        enabled_channels=tuple(sorted(prefs.enabled_channels, key=lambda c: c.value)),
        muted_categories=tuple(sorted(prefs.muted_categories, key=lambda c: c.value)),
    )


async def update_preferences(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    enabled_channels: Sequence[NotificationChannel | str] | None = None,
    muted_categories: Sequence[NotificationCategory | str] | None = None,
) -> PreferencesView:
    """Persist member-level notification preferences atomically in `tenants.settings_json`."""
    current = await get_preferences(session, tenant_id=tenant_id, membership_id=membership_id)
    next_channels = current.enabled_channels
    if enabled_channels is not None:
        parsed_ch: list[NotificationChannel] = []
        seen_ch: set[NotificationChannel] = set()
        for ch in enabled_channels:
            val = ch if isinstance(ch, NotificationChannel) else NotificationChannel(str(ch).strip().upper())
            if val not in seen_ch:
                seen_ch.add(val)
                parsed_ch.append(val)
        next_channels = tuple(sorted(parsed_ch, key=lambda c: c.value))

    next_muted = current.muted_categories
    if muted_categories is not None:
        parsed_cat: list[NotificationCategory] = []
        seen_cat: set[NotificationCategory] = set()
        for cat in muted_categories:
            category = (
                cat if isinstance(cat, NotificationCategory) else NotificationCategory(str(cat).strip().upper())
            )
            if category not in seen_cat:
                seen_cat.add(category)
                parsed_cat.append(category)
        next_muted = tuple(sorted(parsed_cat, key=lambda c: c.value))

    payload_json = json.dumps(
        {
            "enabled_channels": [c.value for c in next_channels],
            "muted_categories": [c.value for c in next_muted],
        }
    )
    await session.execute(
        text(
            "UPDATE tenants SET settings_json = jsonb_set("
            "jsonb_set(COALESCE(settings_json, '{}'::jsonb), '{notification_preferences}', "
            "COALESCE(settings_json->'notification_preferences', '{}'::jsonb), true), "
            "ARRAY['notification_preferences', :mid], CAST(:pref AS jsonb), true), "
            "updated_at = NOW() WHERE id = :tid"
        ),
        {"tid": tenant_id, "mid": str(membership_id), "pref": payload_json},
    )
    return PreferencesView(
        tenant_id=tenant_id,
        membership_id=membership_id,
        enabled_channels=next_channels,
        muted_categories=next_muted,
    )


async def dispatch_notification(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    command: NotificationCommand,
    preferences: NotificationPreferences | None = None,
) -> NotificationDispatchResult:
    """
    Persist a notification idempotently by `dedup_key` and fan out channel deliveries.

    Decision #7: each `notification_deliveries` row writes explicit `tenant_id`.
    """
    try:
        cmd_tid = uuid.UUID(command.tenant_id)
        cmd_mid = uuid.UUID(command.membership_id)
    except ValueError as exc:
        raise NotificationValidationError(f"invalid UUID in notification command: {exc}") from exc

    if cmd_tid != tenant_id:
        raise NotificationValidationError("command.tenant_id must match scoped tenant_id")

    if preferences is None:
        pref_view = await get_preferences(session, tenant_id=tenant_id, membership_id=cmd_mid)
        prefs = NotificationPreferences(
            enabled_channels=frozenset(pref_view.enabled_channels),
            muted_categories=frozenset(pref_view.muted_categories),
        )
    else:
        prefs = preferences

    try:
        _, record = dispatch_notification_idempotent(NotificationInboxState(), command, prefs)
    except ValueError as exc:
        raise NotificationValidationError(str(exc)) from exc

    if record is None:
        return NotificationDispatchResult(created=False, notification=None, deliveries=())

    insert_stmt = (
        pg_insert(Notification)
        .values(
            tenant_id=tenant_id,
            membership_id=cmd_mid,
            dedup_key=record.dedup_key,
            title=record.title,
            body_text=record.body_text,
            is_read=False,
        )
        .on_conflict_do_nothing(index_elements=[Notification.dedup_key])
        .returning(Notification)
        .execution_options(populate_existing=True)
    )
    inserted = (await session.execute(insert_stmt)).scalar_one_or_none()
    if inserted is None:
        return NotificationDispatchResult(created=False, notification=None, deliveries=())

    deliveries: list[NotificationDelivery] = []
    for sched in record.deliveries:
        deliv = NotificationDelivery(
            tenant_id=tenant_id,
            notification_id=inserted.id,
            channel=sched.channel.value,
            status="SENT",
        )
        session.add(deliv)
        deliveries.append(deliv)
    await session.flush()
    for d in deliveries:
        await session.refresh(d)

    delivery_views = tuple(_delivery_view(d) for d in deliveries)
    return NotificationDispatchResult(
        created=True,
        notification=_notification_view(inserted, deliveries),
        deliveries=delivery_views,
    )


async def handle_attempt_graded_notification(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    attempt_id: uuid.UUID,
    revision_no: int = 1,
    total_score: str | Decimal | None = None,
    preferences: NotificationPreferences | None = None,
) -> NotificationDispatchResult:
    """Idempotent consumer handler for `attempt.graded` events (replaying N times -> max 1 row)."""
    score_suffix = f": {total_score}" if total_score is not None else ""
    cmd = NotificationCommand(
        tenant_id=str(tenant_id),
        membership_id=str(membership_id),
        category=NotificationCategory.ATTEMPT_GRADED,
        dedup_key=f"attempt.graded:{attempt_id}:rev-{revision_no}",
        title="Bài làm đã được chấm điểm",
        body_text=f"Kết quả lượt làm {attempt_id} (lần chấm #{revision_no}) đã sẵn sàng{score_suffix}.",
    )
    return await dispatch_notification(session, tenant_id=tenant_id, command=cmd, preferences=preferences)


async def list_inbox(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    unread_only: bool = False,
    limit: int = 50,
) -> InboxView:
    """List a member's inbox notifications plus delivery statuses and unread count."""
    stmt = select(Notification).where(
        Notification.tenant_id == tenant_id,
        Notification.membership_id == membership_id,
    )
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    rows = (
        await session.execute(stmt.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit))
    ).scalars().all()

    notif_ids = [r.id for r in rows]
    deliveries_by_notif: dict[uuid.UUID, list[NotificationDelivery]] = {}
    if notif_ids:
        deliv_rows = (
            await session.execute(
                select(NotificationDelivery)
                .where(
                    NotificationDelivery.tenant_id == tenant_id,
                    NotificationDelivery.notification_id.in_(notif_ids),
                )
                .order_by(NotificationDelivery.delivered_at.asc(), NotificationDelivery.id.asc())
            )
        ).scalars().all()
        for d in deliv_rows:
            deliveries_by_notif.setdefault(d.notification_id, []).append(d)

    unread_count = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.tenant_id == tenant_id,
                    Notification.membership_id == membership_id,
                    Notification.is_read.is_(False),
                )
            )
        ).scalar()
        or 0
    )
    return InboxView(
        items=tuple(_notification_view(r, deliveries_by_notif.get(r.id, ())) for r in rows),
        unread_count=unread_count,
    )


async def read_notification(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    notification_id: uuid.UUID,
) -> NotificationView:
    """Mark one notification in the caller's inbox as read (idempotent)."""
    row = (
        await session.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.tenant_id == tenant_id,
                Notification.membership_id == membership_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotificationNotFoundError(f"notification {notification_id} not found for member")

    if not row.is_read:
        row.is_read = True
        await session.flush()
        await session.refresh(row)

    deliv_rows = (
        await session.execute(
            select(NotificationDelivery).where(
                NotificationDelivery.tenant_id == tenant_id,
                NotificationDelivery.notification_id == row.id,
            )
        )
    ).scalars().all()
    return _notification_view(row, deliv_rows)
