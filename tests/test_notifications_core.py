"""Pure unit tests for F06 Omnichannel Idempotent Notifications (P34)."""

from __future__ import annotations

import unicodedata
from datetime import date, datetime
from pathlib import Path

import pytest

from app.modules.notifications.public import (
    NotificationCategory,
    NotificationChannel,
    NotificationCommand,
    NotificationInboxState,
    NotificationPreferences,
    build_fsrs_daily_reminder_command,
    dispatch_notification_idempotent,
    mark_notification_read,
    resolve_target_channels,
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

    inbox = mark_notification_read(
        inbox, tenant_id="tenant-1", membership_id="mem-1", dedup_key="attempt.graded:att-42:rev-1"
    )
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
    assert (
        build_fsrs_daily_reminder_command(
            tenant_id="t1", membership_id="m1", vn_day=date(2026, 11, 13), due_card_count=0
        )
        is None
    )

    fsrs_cmd = build_fsrs_daily_reminder_command(
        tenant_id="t1", membership_id="m1", vn_day=date(2026, 11, 13), due_card_count=12
    )
    assert fsrs_cmd is not None
    inbox, d1 = dispatch_notification_idempotent(inbox, fsrs_cmd, prefs)
    inbox, d2 = dispatch_notification_idempotent(inbox, fsrs_cmd, prefs)
    assert d1 is not None
    assert [d.channel for d in d1.deliveries] == [NotificationChannel.IN_APP]
    assert d2 is None


def _cmd(**kw: object) -> NotificationCommand:
    base: dict[str, object] = {
        "tenant_id": "t1",
        "membership_id": "m1",
        "category": NotificationCategory.ATTEMPT_GRADED,
        "dedup_key": "k1",
        "title": "Tiêu đề",
        "body_text": "Nội dung",
    }
    base.update(kw)
    return NotificationCommand(**base)  # type: ignore[arg-type]


def test_dedup_is_scoped_to_the_recipient_not_global() -> None:
    prefs = NotificationPreferences()
    inbox, first = dispatch_notification_idempotent(NotificationInboxState(), _cmd(), prefs)
    inbox, other_member = dispatch_notification_idempotent(inbox, _cmd(membership_id="m2"), prefs)
    inbox, other_tenant = dispatch_notification_idempotent(inbox, _cmd(tenant_id="t2"), prefs)
    assert first is not None and other_member is not None and other_tenant is not None
    assert len(inbox.records) == 3
    inbox, replay = dispatch_notification_idempotent(inbox, _cmd(), prefs)
    assert replay is None and len(inbox.records) == 3


def test_mark_read_only_touches_the_addressed_recipient() -> None:
    prefs = NotificationPreferences()
    inbox = NotificationInboxState()
    for member in ("m1", "m2"):
        inbox, _ = dispatch_notification_idempotent(inbox, _cmd(membership_id=member), prefs)
    inbox = mark_notification_read(inbox, tenant_id="t1", membership_id="m1", dedup_key="k1")
    assert [(r.membership_id, r.is_read) for r in inbox.records] == [("m1", True), ("m2", False)]
    same = mark_notification_read(inbox, tenant_id="t1", membership_id="m1", dedup_key="k1")
    assert same is inbox  # idempotent: no change, same object
    for wrong in (("t2", "m2", "k1"), ("t1", "m2", "other-key"), ("t1", "nobody", "k1")):
        unchanged = mark_notification_read(inbox, tenant_id=wrong[0], membership_id=wrong[1], dedup_key=wrong[2])
        assert unchanged is inbox
    assert inbox.unread_count() == 1


def test_command_validation_rejects_blank_fields_and_long_keys() -> None:
    prefs = NotificationPreferences()
    inbox = NotificationInboxState()
    bad_commands = [
        _cmd(tenant_id=""),
        _cmd(tenant_id="  "),
        _cmd(membership_id=""),
        _cmd(title=""),
        _cmd(title="   "),
        _cmd(dedup_key=""),
        _cmd(dedup_key="   "),
        _cmd(dedup_key=None),
        _cmd(dedup_key="x" * 161),
    ]
    for cmd in bad_commands:
        with pytest.raises(ValueError):
            dispatch_notification_idempotent(inbox, cmd, prefs)
    ok, rec = dispatch_notification_idempotent(inbox, _cmd(dedup_key="x" * 160), prefs)
    assert rec is not None and ok.records
    # surrounding whitespace is normalised so " k " and "k" are the same notification
    inbox, first = dispatch_notification_idempotent(inbox, _cmd(dedup_key=" k "), prefs)
    inbox, second = dispatch_notification_idempotent(inbox, _cmd(dedup_key="k"), prefs)
    assert first is not None and second is None and first.dedup_key == "k"


def test_unknown_channels_are_never_delivered_even_for_critical_alerts() -> None:
    prefs = NotificationPreferences()
    channels = ("SMS", NotificationChannel.EMAIL, "PUSH")
    assert resolve_target_channels(channels, prefs, category=NotificationCategory.SYSTEM_ALERT, is_critical=True) == (
        NotificationChannel.EMAIL,
    )  # type: ignore[arg-type]
    assert resolve_target_channels(("SMS",), prefs, category=NotificationCategory.SYSTEM_ALERT, is_critical=True) == ()  # type: ignore[arg-type]


def test_resolve_channels_handles_duplicates_mutes_and_empty_requests() -> None:
    both = NotificationPreferences()
    requested = (NotificationChannel.EMAIL, NotificationChannel.IN_APP, NotificationChannel.EMAIL)
    cat = NotificationCategory.ASSIGNMENT_CREATED
    assert resolve_target_channels(requested, both, category=cat) == (
        NotificationChannel.EMAIL,
        NotificationChannel.IN_APP,
    )
    assert resolve_target_channels((), both, category=cat) == ()
    muted = NotificationPreferences(muted_categories=frozenset({cat}))
    assert resolve_target_channels(requested, muted, category=cat) == ()
    assert resolve_target_channels(requested, muted, category=cat, is_critical=True) != ()
    none_enabled = NotificationPreferences(enabled_channels=frozenset())
    assert resolve_target_channels(requested, none_enabled, category=cat) == ()
    # critical bypasses channel opt-outs
    assert len(resolve_target_channels(requested, none_enabled, category=cat, is_critical=True)) == 2


def test_fully_filtered_notification_is_not_recorded_and_can_be_sent_after_opt_in() -> None:
    off = NotificationPreferences(enabled_channels=frozenset())
    inbox, rec = dispatch_notification_idempotent(NotificationInboxState(), _cmd(), off)
    assert rec is None and inbox.records == () and inbox.seen_dedup_keys == frozenset()
    inbox, later = dispatch_notification_idempotent(inbox, _cmd(), NotificationPreferences())
    assert later is not None


def test_dispatch_does_not_mutate_the_previous_inbox() -> None:
    before = NotificationInboxState()
    after, _ = dispatch_notification_idempotent(before, _cmd(), NotificationPreferences())
    assert before.records == () and before.seen_dedup_keys == frozenset() and after is not before


def test_fsrs_reminder_key_is_per_day_per_member_and_validates_inputs() -> None:
    day = date(2026, 11, 13)
    a = build_fsrs_daily_reminder_command(tenant_id="t", membership_id="m", vn_day=day, due_card_count=3)
    b = build_fsrs_daily_reminder_command(tenant_id="t", membership_id="m", vn_day=day, due_card_count=9)
    c = build_fsrs_daily_reminder_command(tenant_id="t", membership_id="m", vn_day=date(2026, 11, 14), due_card_count=3)
    d = build_fsrs_daily_reminder_command(tenant_id="t", membership_id="n", vn_day=day, due_card_count=3)
    assert a is not None and b is not None and c is not None and d is not None
    assert a.dedup_key == b.dedup_key  # same day: count changes do not create a second reminder
    assert len({a.dedup_key, c.dedup_key, d.dedup_key}) == 3
    assert "3" in a.title and "9" in b.title
    assert build_fsrs_daily_reminder_command(tenant_id="t", membership_id="m", vn_day=day, due_card_count=-5) is None
    for bad_count in (True, 1.5, None, "3"):
        with pytest.raises(ValueError):
            build_fsrs_daily_reminder_command(tenant_id="t", membership_id="m", vn_day=day, due_card_count=bad_count)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        build_fsrs_daily_reminder_command(
            tenant_id="t",
            membership_id="m",
            vn_day=datetime(2026, 11, 13, 23, 0),
            due_card_count=1,  # type: ignore[arg-type]
        )


def test_vietnamese_text_is_carried_unchanged_in_nfc_and_nfd() -> None:
    for form in ("NFC", "NFD"):
        title = unicodedata.normalize(form, "Đã có kết quả bài thi")
        _, rec = dispatch_notification_idempotent(
            NotificationInboxState(), _cmd(title=title), NotificationPreferences()
        )
        assert rec is not None and rec.title == title
