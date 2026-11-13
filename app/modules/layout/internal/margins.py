"""Dynamic header, footer, and watermark band detection across multi-page documents (A06)."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from app.modules.layout.internal.geometry import BBoxRect

_DIGITS_RE = re.compile(r"\d+")


@dataclass(frozen=True, slots=True)
class LayoutProfileConfig:
    """Layout thresholds aligned with VietnameseExamDocumentProcessor specification."""

    header_band_ratio: float = 0.065
    footer_band_ratio: float = 0.065
    max_badge_area_ratio: float = 0.035
    min_figure_width: float = 35.0
    min_figure_height: float = 30.0
    cluster_padding_pt: float = 10.0

    def __post_init__(self) -> None:
        if not (0.0 < self.header_band_ratio <= 0.5):
            raise ValueError("header_band_ratio must be in (0.0, 0.5]")
        if not (0.0 < self.footer_band_ratio <= 0.5):
            raise ValueError("footer_band_ratio must be in (0.0, 0.5]")


@dataclass(frozen=True, slots=True)
class PageTextLine:
    """Single text line on a page with its bounding box."""

    text: str
    bbox: BBoxRect


@dataclass(frozen=True, slots=True)
class PageLayoutInput:
    """Pure page geometry and text lines without any PDF-library dependency."""

    page_index: int
    page_width: float
    page_height: float
    lines: tuple[PageTextLine, ...] = ()

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.page_width)
            or not math.isfinite(self.page_height)
            or self.page_width <= 0.0
            or self.page_height <= 0.0
        ):
            raise ValueError("page_width and page_height must be finite and positive")


@dataclass(frozen=True, slots=True)
class RepeatingMarginDetection:
    """Detected repeating header/footer/watermark patterns and dropped lines per page."""

    repeated_patterns: frozenset[str]
    header_patterns: frozenset[str]
    footer_patterns: frozenset[str]
    watermark_patterns: frozenset[str]
    dropped_lines_by_page: Mapping[int, tuple[PageTextLine, ...]]


def normalize_margin_text(text: str) -> str:
    """Normalize text and replace numbers with `<N>` so `Trang 1/4` matches `Trang 2/4`."""
    collapsed = " ".join(unicodedata.normalize("NFKC", text or "").split())
    return _DIGITS_RE.sub("<N>", collapsed)


def _quantized_center(bbox: BBoxRect, page_width: float, page_height: float) -> tuple[int, int]:
    # 2% (~16pt on A4) buckets keep fixed watermarks grouped while separating stacked body lines.
    cx_ratio = ((bbox.x0 + bbox.x1) * 0.5) / page_width
    cy_ratio = ((bbox.y0 + bbox.y1) * 0.5) / page_height
    return (round(cx_ratio * 50), round(cy_ratio * 50))


def detect_dynamic_repeating_margins(
    pages: Sequence[PageLayoutInput],
    config: LayoutProfileConfig | None = None,
) -> RepeatingMarginDetection:
    """Detect repeating headers, footers, and position-consistent watermarks across pages.

    A 0-page or 1-page document never strips anything.
    """
    cfg = config if config is not None else LayoutProfileConfig()
    if len(pages) < 2:
        return RepeatingMarginDetection(
            repeated_patterns=frozenset(),
            header_patterns=frozenset(),
            footer_patterns=frozenset(),
            watermark_patterns=frozenset(),
            dropped_lines_by_page=MappingProxyType({}),
        )

    header_page_counts: Counter[str] = Counter()
    footer_page_counts: Counter[str] = Counter()
    watermark_page_counts: Counter[tuple[str, tuple[int, int]]] = Counter()

    per_page_tagged: list[tuple[PageLayoutInput, list[tuple[PageTextLine, str, str, tuple[int, int]]]]] = []

    for page in pages:
        top_limit = page.page_height * cfg.header_band_ratio
        bottom_limit = page.page_height * (1.0 - cfg.footer_band_ratio)
        seen_header: set[str] = set()
        seen_footer: set[str] = set()
        seen_watermark: set[tuple[str, tuple[int, int]]] = set()
        tagged: list[tuple[PageTextLine, str, str, tuple[int, int]]] = []

        for line in page.lines:
            collapsed = " ".join(unicodedata.normalize("NFKC", line.text or "").split())
            if not collapsed:
                continue
            q_center = _quantized_center(line.bbox, page.page_width, page.page_height)
            in_header = line.bbox.y0 <= top_limit
            in_footer = line.bbox.y1 >= bottom_limit

            if in_header:
                norm = _DIGITS_RE.sub("<N>", collapsed)
                seen_header.add(norm)
                band = "header"
            elif in_footer:
                norm = _DIGITS_RE.sub("<N>", collapsed)
                seen_footer.add(norm)
                band = "footer"
            else:
                # Watermarks repeat identical literal text at fixed page coordinates.
                norm = collapsed
                seen_watermark.add((norm, q_center))
                band = "watermark"

            tagged.append((line, norm, band, q_center))

        for pat in seen_header:
            header_page_counts[pat] += 1
        for pat in seen_footer:
            footer_page_counts[pat] += 1
        for wm_key in seen_watermark:
            watermark_page_counts[wm_key] += 1

        per_page_tagged.append((page, tagged))

    min_repeat = max(2, len(pages) // 2)
    header_patterns = frozenset(p for p, c in header_page_counts.items() if c >= min_repeat)
    footer_patterns = frozenset(p for p, c in footer_page_counts.items() if c >= min_repeat)
    watermark_keys = frozenset(k for k, c in watermark_page_counts.items() if c >= min_repeat)
    watermark_patterns = frozenset(pat for pat, _ in watermark_keys)
    all_patterns = header_patterns | footer_patterns | watermark_patterns

    dropped: dict[int, tuple[PageTextLine, ...]] = {}
    for page, tagged in per_page_tagged:
        page_drops = [
            line
            for line, norm, band, q_center in tagged
            if (band == "header" and norm in header_patterns)
            or (band == "footer" and norm in footer_patterns)
            or (band == "watermark" and (norm, q_center) in watermark_keys)
        ]
        if page_drops:
            dropped[page.page_index] = tuple(page_drops)

    return RepeatingMarginDetection(
        repeated_patterns=all_patterns,
        header_patterns=header_patterns,
        footer_patterns=footer_patterns,
        watermark_patterns=watermark_patterns,
        dropped_lines_by_page=MappingProxyType(dropped),
    )
