"""Public API for app.modules.notifications (F06 Omnichannel Notifications)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from app.modules.notifications.internal.router import (
    NotificationCategory,
    NotificationChannel,
    NotificationCommand,
    NotificationInboxState,
    NotificationPreferences,
    NotificationRecord,
    ScheduledDelivery,
    build_fsrs_daily_reminder_command,
    dispatch_notification_idempotent,
    mark_notification_read,
    resolve_target_channels,
)

if TYPE_CHECKING:
    from app.modules.notifications.internal.service import (
        DeliveryView,
        InboxView,
        NotificationDispatchResult,
        NotificationNotFoundError,
        NotificationValidationError,
        NotificationView,
        PreferencesView,
        dispatch_notification,
        get_preferences,
        handle_attempt_graded_notification,
        list_inbox,
        read_notification,
        update_preferences,
    )

_SERVICE_EXPORTS = frozenset(
    {
        "DeliveryView",
        "InboxView",
        "NotificationDispatchResult",
        "NotificationNotFoundError",
        "NotificationValidationError",
        "NotificationView",
        "PreferencesView",
        "dispatch_notification",
        "get_preferences",
        "handle_attempt_graded_notification",
        "list_inbox",
        "read_notification",
        "update_preferences",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily load DB service exports so pure-core callers avoid importing ORM models."""
    if name in _SERVICE_EXPORTS:
        service_mod = importlib.import_module("app.modules.notifications.internal.service")
        value = getattr(service_mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DeliveryView",
    "InboxView",
    "NotificationCategory",
    "NotificationChannel",
    "NotificationCommand",
    "NotificationDispatchResult",
    "NotificationInboxState",
    "NotificationNotFoundError",
    "NotificationPreferences",
    "NotificationRecord",
    "NotificationValidationError",
    "NotificationView",
    "PreferencesView",
    "ScheduledDelivery",
    "build_fsrs_daily_reminder_command",
    "dispatch_notification",
    "dispatch_notification_idempotent",
    "get_preferences",
    "handle_attempt_graded_notification",
    "list_inbox",
    "mark_notification_read",
    "read_notification",
    "resolve_target_channels",
    "update_preferences",
]
