"""Unit tests for A06 pure bounding-box geometry and repeating margin detection (P17)."""

from __future__ import annotations

import math
import random
import time
from dataclasses import FrozenInstanceError

import pytest

from app.modules.layout.public import (
    BBoxRect,
    LayoutProfileConfig,
    PageLayoutInput,
    PageTextLine,
    detect_dynamic_repeating_margins,
    merge_intersecting_rects_union_find,
)


def test_bbox_rect_validation_frozen_and_zero_area_overlap_safety():
    r = BBoxRect(10.0, 20.0, 30.0, 50.0)
    with pytest.raises(FrozenInstanceError):
        r.x0 = 0.0  # type: ignore[misc]

    for bad in (
        (math.nan, 0.0, 1.0, 1.0),
        (0.0, math.inf, 1.0, 1.0),
        (0.0, 0.0, -math.inf, 1.0),
        (10.0, 0.0, 5.0, 5.0),  # x1 < x0
        (0.0, 10.0, 5.0, 5.0),  # y1 < y0
    ):
        with pytest.raises(ValueError):
            BBoxRect(*bad)

    zero_area = BBoxRect(10.0, 10.0, 10.0, 40.0)
    other = BBoxRect(5.0, 5.0, 25.0, 50.0)
    assert zero_area.area == 0.0
    assert zero_area.overlap_ratio(other) == 0.0


def test_union_find_merge_is_order_independent_and_scales_past_2000_rects():
    boxes = [
        BBoxRect(100.0, 100.0, 130.0, 120.0),
        BBoxRect(135.0, 105.0, 160.0, 125.0),  # merges with first under pad=10
        BBoxRect(10.0, 10.0, 40.0, 30.0),
        BBoxRect(15.0, 25.0, 45.0, 55.0),     # merges with third
        BBoxRect(300.0, 300.0, 340.0, 320.0),
    ]
    baseline = merge_intersecting_rects_union_find(boxes, pad=10.0)
    assert baseline == tuple(sorted(baseline, key=lambda b: (b.y0, b.x0)))

    rng = random.Random(7)
    for _ in range(10):
        shuffled = list(boxes)
        rng.shuffle(shuffled)
        assert merge_intersecting_rects_union_find(shuffled, pad=10.0) == baseline

    # 2,000 scattered small rectangles -> must finish well under 1.0s.
    grid_rects = [
        BBoxRect(col * 30.0, row * 30.0, col * 30.0 + 10.0, row * 30.0 + 10.0)
        for row in range(45)
        for col in range(45)
    ][:2000]
    t0 = time.perf_counter()
    merged = merge_intersecting_rects_union_find(grid_rects, pad=2.0)
    elapsed = time.perf_counter() - t0
    assert len(merged) == 2000
    assert elapsed < 1.0


def test_detect_repeating_headers_footers_watermarks_and_single_page_noop():
    # Single-page document: must drop nothing even if lines sit in header/footer bands.
    single = PageLayoutInput(
        page_index=0,
        page_width=600.0,
        page_height=800.0,
        lines=(
            PageTextLine("Trang 1/1", BBoxRect(250.0, 770.0, 350.0, 790.0)),
            PageTextLine("Header", BBoxRect(50.0, 10.0, 200.0, 30.0)),
        ),
    )
    single_res = detect_dynamic_repeating_margins([single])
    assert single_res.repeated_patterns == frozenset()
    assert dict(single_res.dropped_lines_by_page) == {}

    # 4-page exam document with numbered footer ("Trang X/4"), header, and centered watermark.
    pages = []
    for p in range(4):
        lines = (
            PageTextLine("SO GD&DT HA NOI", BBoxRect(40.0, 15.0, 200.0, 32.0)),
            PageTextLine("TUYENSINH247.COM", BBoxRect(220.0, 390.0, 380.0, 410.0)),
            PageTextLine(f"Cau {p + 1}: Tim x", BBoxRect(40.0, 200.0 + p * 30, 300.0, 220.0 + p * 30)),
            PageTextLine(f"Trang {p + 1}/4", BBoxRect(260.0, 765.0, 340.0, 785.0)),
        )
        pages.append(
            PageLayoutInput(page_index=p, page_width=600.0, page_height=800.0, lines=lines)
        )

    res = detect_dynamic_repeating_margins(pages, LayoutProfileConfig())
    assert res.header_patterns == frozenset({"SO GD&DT HA NOI"})
    assert res.footer_patterns == frozenset({"Trang <N>/<N>"})
    assert res.watermark_patterns == frozenset({"TUYENSINH247.COM"})
    assert len(res.dropped_lines_by_page) == 4
    for p in range(4):
        dropped_texts = {line.text for line in res.dropped_lines_by_page[p]}
        assert f"Cau {p + 1}: Tim x" not in dropped_texts
        assert "SO GD&DT HA NOI" in dropped_texts
        assert f"Trang {p + 1}/4" in dropped_texts
        assert "TUYENSINH247.COM" in dropped_texts
