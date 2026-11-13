"""Pure unit tests for F06 Omnichannel Idempotent Notifications (P34)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.modules.notifications.public import (
    NotificationCategory,
    NotificationChannel,
    NotificationCommand,
    NotificationInboxState,
    NotificationPreferences,
    build_fsrs_daily_reminder_command,
    dispatch_notification_idempotent,
    mark_notification_read,
)


def test_channels_match_ddl_check_constraints() -> None:
    ddl = Path("docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql").read_text(encoding="utf-8")
    for ch in NotificationChannel:
        assert f"'{ch.value}'" in ddl


def test_replaying_attempt_graded_5_times_delivers_exact_one_notification() -> None:
    inbox = NotificationInboxState()
    prefs = NotificationPreferences()
    cmd = NotificationCommand(
        tenant_id="tenant-1",
        membership_id="mem-1",
        category=NotificationCategory.ATTEMPT_GRADED,
        dedup_key="attempt.graded:att-42:rev-1",
        title="Đã có kết quả bài thi",
        body_text="Điểm của bạn là 8.75",
    )

    emitted = []
    for _ in range(5):
        inbox, rec = dispatch_notification_idempotent(inbox, cmd, prefs)
        if rec is not None:
            emitted.append(rec)

    assert len(emitted) == 1
    assert len(inbox.records) == 1
    assert inbox.unread_count() == 1
    assert len(emitted[0].deliveries) == 2

    inbox = mark_notification_read(inbox, dedup_key="attempt.graded:att-42:rev-1")
    assert inbox.unread_count() == 0


def test_preferences_filter_channels_and_fsrs_daily_reminder_deduplicates_per_day() -> None:
    prefs = NotificationPreferences(
        enabled_channels=frozenset({NotificationChannel.IN_APP}),
        muted_categories=frozenset({NotificationCategory.ASSIGNMENT_CREATED}),
    )
    inbox = NotificationInboxState()

    muted_cmd = NotificationCommand(
        tenant_id="t1",
        membership_id="m1",
        category=NotificationCategory.ASSIGNMENT_CREATED,
        dedup_key="asg.created:1",
        title="Bài tập mới",
        body_text="...",
    )
    inbox, muted_res = dispatch_notification_idempotent(inbox, muted_cmd, prefs)
    assert muted_res is None

    # Critical alert bypasses channel/category mutes safely
    crit_cmd = NotificationCommand(
        tenant_id="t1",
        membership_id="m1",
        category=NotificationCategory.SYSTEM_ALERT,
        dedup_key="sec.alert:1",
        title="Cảnh báo bảo mật",
        body_text="Đăng nhập mới",
        is_critical=True,
    )
    inbox, crit_res = dispatch_notification_idempotent(inbox, crit_cmd, prefs)
    assert crit_res is not None
    assert len(crit_res.deliveries) == 2

    # FSRS daily reminder: deterministic dedup key prevents cron spam
    assert build_fsrs_daily_reminder_command(
        tenant_id="t1", membership_id="m1", vn_day=date(2026, 11, 13), due_card_count=0
    ) is None

    fsrs_cmd = build_fsrs_daily_reminder_command(
        tenant_id="t1", membership_id="m1", vn_day=date(2026, 11, 13), due_card_count=12
    )
    assert fsrs_cmd is not None
    inbox, d1 = dispatch_notification_idempotent(inbox, fsrs_cmd, prefs)
    inbox, d2 = dispatch_notification_idempotent(inbox, fsrs_cmd, prefs)
    assert d1 is not None
    assert [d.channel for d in d1.deliveries] == [NotificationChannel.IN_APP]
    assert d2 is None
