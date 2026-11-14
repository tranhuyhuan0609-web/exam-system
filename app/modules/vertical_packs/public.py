"""Public API of the vertical_packs module (G06)."""

from .internal.verticals import (
    VerticalPackConfig,
    VerticalPackError,
    VerticalPackRegistry,
    resolve_localized_label,
)

__all__ = [
    "VerticalPackConfig",
    "VerticalPackError",
    "VerticalPackRegistry",
    "resolve_localized_label",
]
