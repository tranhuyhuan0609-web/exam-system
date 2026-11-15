"""Public interface for module vertical_packs (G06)."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from app.modules.vertical_packs.internal.verticals import (
    VerticalPackConfig,
    VerticalPackError,
    VerticalPackRegistry,
    resolve_localized_label,
)

if TYPE_CHECKING:
    from app.modules.vertical_packs.internal.models import LocaleBundleRecord, VerticalPackRecord
    from app.modules.vertical_packs.internal.service import (
        LocaleBundleView,
        LocalizedLabelView,
        VerticalPackNotFoundError,
        VerticalPackView,
        activate_vertical_pack,
        deactivate_vertical_pack,
        list_vertical_packs,
        register_vertical_pack,
        resolve_localized_ui_label,
        upsert_locale_bundle,
    )
    from app.modules.vertical_packs.router import router

_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "LocaleBundleRecord": ("app.modules.vertical_packs.internal.models", "LocaleBundleRecord"),
    "VerticalPackRecord": ("app.modules.vertical_packs.internal.models", "VerticalPackRecord"),
    "LocaleBundleView": ("app.modules.vertical_packs.internal.service", "LocaleBundleView"),
    "LocalizedLabelView": ("app.modules.vertical_packs.internal.service", "LocalizedLabelView"),
    "VerticalPackNotFoundError": ("app.modules.vertical_packs.internal.service", "VerticalPackNotFoundError"),
    "VerticalPackView": ("app.modules.vertical_packs.internal.service", "VerticalPackView"),
    "activate_vertical_pack": ("app.modules.vertical_packs.internal.service", "activate_vertical_pack"),
    "deactivate_vertical_pack": ("app.modules.vertical_packs.internal.service", "deactivate_vertical_pack"),
    "list_vertical_packs": ("app.modules.vertical_packs.internal.service", "list_vertical_packs"),
    "register_vertical_pack": ("app.modules.vertical_packs.internal.service", "register_vertical_pack"),
    "resolve_localized_ui_label": ("app.modules.vertical_packs.internal.service", "resolve_localized_ui_label"),
    "upsert_locale_bundle": ("app.modules.vertical_packs.internal.service", "upsert_locale_bundle"),
    "router": ("app.modules.vertical_packs.router", "router"),
}

__all__ = [
    "LocaleBundleRecord",
    "LocaleBundleView",
    "LocalizedLabelView",
    "VerticalPackConfig",
    "VerticalPackError",
    "VerticalPackNotFoundError",
    "VerticalPackRecord",
    "VerticalPackRegistry",
    "VerticalPackView",
    "activate_vertical_pack",
    "deactivate_vertical_pack",
    "list_vertical_packs",
    "register_vertical_pack",
    "resolve_localized_label",
    "resolve_localized_ui_label",
    "router",
    "upsert_locale_bundle",
]


def __getattr__(name: str) -> Any:
    target = _LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, attr_name = target
    value = getattr(import_module(module_path), attr_name)
    globals()[name] = value
    return value
