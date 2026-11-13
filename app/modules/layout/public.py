"""Public facade of the layout geometry module (A06)."""

from app.modules.layout.internal.geometry import (
    BBoxRect,
    merge_intersecting_rects_union_find,
)
from app.modules.layout.internal.margins import (
    LayoutProfileConfig,
    PageLayoutInput,
    PageTextLine,
    RepeatingMarginDetection,
    detect_dynamic_repeating_margins,
    normalize_margin_text,
)

__all__ = [
    "BBoxRect",
    "LayoutProfileConfig",
    "PageLayoutInput",
    "PageTextLine",
    "RepeatingMarginDetection",
    "detect_dynamic_repeating_margins",
    "merge_intersecting_rects_union_find",
    "normalize_margin_text",
]
