"""Public facade of the layout geometry & OCR segmentation module (A06)."""

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
from app.modules.layout.internal.service import (
    ExtractedPageView,
    LayoutRegionView,
    LayoutValidationError,
    OcrJobNotFoundError,
    OcrLayoutJobView,
    get_ocr_layout_job,
    reprocess_ocr_layout_page,
    run_ocr_layout_job,
)

__all__ = [
    "BBoxRect",
    "ExtractedPageView",
    "LayoutProfileConfig",
    "LayoutRegionView",
    "LayoutValidationError",
    "OcrJobNotFoundError",
    "OcrLayoutJobView",
    "PageLayoutInput",
    "PageTextLine",
    "RepeatingMarginDetection",
    "detect_dynamic_repeating_margins",
    "get_ocr_layout_job",
    "merge_intersecting_rects_union_find",
    "normalize_margin_text",
    "reprocess_ocr_layout_page",
    "run_ocr_layout_job",
]
