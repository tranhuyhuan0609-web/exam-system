"""F06 notification deduplication, preference filtering, and digest builders."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum


class NotificationChannel(StrEnum):
    """Exact DDL CHECK values for `notification_deliveries.channel`."""

    IN_APP = "IN_APP"
    EMAIL = "EMAIL"


class NotificationCategory(StrEnum):
    ASSIGNMENT_CREATED = "ASSIGNMENT_CREATED"
    ASSIGNMENT_DUE_SOON = "ASSIGNMENT_DUE_SOON"
    ATTEMPT_GRADED = "ATTEMPT_GRADED"
    FSRS_DAILY_REMINDER = "FSRS_DAILY_REMINDER"
    SYSTEM_ALERT = "SYSTEM_ALERT"


@dataclass(frozen=True, slots=True)
class NotificationPreferences:
    """Member-level channel and category opt-out preferences."""

    enabled_channels: frozenset[NotificationChannel] = field(
        default_factory=lambda: frozenset({NotificationChannel.IN_APP, NotificationChannel.EMAIL})
    )
    muted_categories: frozenset[NotificationCategory] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class NotificationCommand:
    tenant_id: str
    membership_id: str
    category: NotificationCategory
    dedup_key: str
    title: str
    body_text: str
    requested_channels: tuple[NotificationChannel, ...] = (
        NotificationChannel.IN_APP,
        NotificationChannel.EMAIL,
    )
    is_critical: bool = False


@dataclass(frozen=True, slots=True)
class ScheduledDelivery:
    dedup_key: str
    channel: NotificationChannel


@dataclass(frozen=True, slots=True)
class NotificationRecord:
    tenant_id: str
    membership_id: str
    category: NotificationCategory
    dedup_key: str
    title: str
    body_text: str
    is_read: bool = False
    deliveries: tuple[ScheduledDelivery, ...] = ()


@dataclass(frozen=True, slots=True)
class NotificationInboxState:
    """In-memory inbox state modeling UNIQUE `dedup_key` idempotency."""

    records: tuple[NotificationRecord, ...] = ()
    seen_dedup_keys: frozenset[str] = field(default_factory=frozenset)

    def unread_count(self) -> int:
        return sum(1 for r in self.records if not r.is_read)


def resolve_target_channels(
    requested: Sequence[NotificationChannel],
    prefs: NotificationPreferences,
    *,
    category: NotificationCategory,
    is_critical: bool = False,
) -> tuple[NotificationChannel, ...]:
    """Filter requested delivery channels against user preferences."""
    if not is_critical and category in prefs.muted_categories:
        return ()

    seen: set[NotificationChannel] = set()
    active: list[NotificationChannel] = []
    for ch in requested:
        if ch in seen:
            continue
        if is_critical or ch in prefs.enabled_channels:
            seen.add(ch)
            active.append(ch)
    return tuple(active)


def dispatch_notification_idempotent(
    inbox: NotificationInboxState,
    cmd: NotificationCommand,
    prefs: NotificationPreferences,
) -> tuple[NotificationInboxState, NotificationRecord | None]:
    """
    Dispatch a notification with `dedup_key` idempotency.

    Replaying the same event N times guarantees 0 duplicate notifications and 0 duplicate deliveries.
    """
    key = cmd.dedup_key.strip()
    if not key:
        raise ValueError("dedup_key must be non-empty")
    if len(key) > 160:
        raise ValueError(f"dedup_key exceeds DDL VARCHAR(160) limit: len={len(key)}")

    if key in inbox.seen_dedup_keys:
        return inbox, None

    channels = resolve_target_channels(
        cmd.requested_channels,
        prefs,
        category=cmd.category,
        is_critical=cmd.is_critical,
    )
    if not channels:
        return inbox, None

    deliveries = tuple(ScheduledDelivery(dedup_key=key, channel=ch) for ch in channels)
    rec = NotificationRecord(
        tenant_id=cmd.tenant_id,
        membership_id=cmd.membership_id,
        category=cmd.category,
        dedup_key=key,
        title=cmd.title,
        body_text=cmd.body_text,
        is_read=False,
        deliveries=deliveries,
    )
    next_inbox = NotificationInboxState(
        records=inbox.records + (rec,),
        seen_dedup_keys=inbox.seen_dedup_keys | {key},
    )
    return next_inbox, rec


def mark_notification_read(inbox: NotificationInboxState, *, dedup_key: str) -> NotificationInboxState:
    """Idempotently mark an inbox notification read."""
    updated: list[NotificationRecord] = []
    changed = False
    for rec in inbox.records:
        if rec.dedup_key == dedup_key and not rec.is_read:
            updated.append(replace(rec, is_read=True))
            changed = True
        else:
            updated.append(rec)
    return replace(inbox, records=tuple(updated)) if changed else inbox


def build_fsrs_daily_reminder_command(
    *,
    tenant_id: str,
    membership_id: str,
    vn_day: date,
    due_card_count: int,
) -> NotificationCommand | None:
    """Create a deterministic per-day FSRS reminder command (`None` when 0 cards due)."""
    if due_card_count <= 0:
        return None
    return NotificationCommand(
        tenant_id=tenant_id,
        membership_id=membership_id,
        category=NotificationCategory.FSRS_DAILY_REMINDER,
        dedup_key=f"fsrs_due:{tenant_id}:{membership_id}:{vn_day.isoformat()}",
        title=f"Bạn có {due_card_count} thẻ cần ôn tập hôm nay",
        body_text=f"Hoàn thành {due_card_count} thẻ ghi nhớ FSRS trong ngày để giữ chuỗi ổn định.",
        requested_channels=(NotificationChannel.IN_APP, NotificationChannel.EMAIL),
    )
