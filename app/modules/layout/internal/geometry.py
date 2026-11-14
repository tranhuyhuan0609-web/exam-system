"""Immutable bounding-box primitives and fast union-find clustering (module A06)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BBoxRect:
    """Immutable 2D axis-aligned bounding box with finite, non-inverted coordinates."""

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        for val in (self.x0, self.y0, self.x1, self.y1):
            if not math.isfinite(val):
                raise ValueError(f"BBoxRect coordinates must be finite, got {val!r}")
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError(
                f"BBoxRect must satisfy x1 >= x0 and y1 >= y0, got ({self.x0}, {self.y0}, {self.x1}, {self.y1})"
            )

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height

    def expanded(self, pad: float) -> BBoxRect:
        if not math.isfinite(pad):
            raise ValueError(f"pad must be finite, got {pad!r}")
        return BBoxRect(self.x0 - pad, self.y0 - pad, self.x1 + pad, self.y1 + pad)

    def intersects(self, other: BBoxRect) -> bool:
        return not (self.x1 < other.x0 or self.x0 > other.x1 or self.y1 < other.y0 or self.y0 > other.y1)

    def overlap_ratio(self, other: BBoxRect) -> float:
        if self.area <= 0.0 or not self.intersects(other):
            return 0.0
        ix0, iy0 = max(self.x0, other.x0), max(self.y0, other.y0)
        ix1, iy1 = min(self.x1, other.x1), min(self.y1, other.y1)
        inter_area = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
        return min(1.0, inter_area / self.area)

    def union(self, other: BBoxRect) -> BBoxRect:
        return BBoxRect(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )


def _sweep_axis(expanded: list[tuple[float, float, float, float]]) -> int:
    """Choose sweep axis (0 for X, 1 for Y) with tighter median extent vs total span."""
    if len(expanded) <= 1:
        return 0
    xs0 = [b[0] for b in expanded]
    xs1 = [b[2] for b in expanded]
    ys0 = [b[1] for b in expanded]
    ys1 = [b[3] for b in expanded]
    span_x = max(xs1) - min(xs0)
    span_y = max(ys1) - min(ys0)
    widths = sorted(x1 - x0 for x0, x1 in zip(xs0, xs1, strict=True))
    heights = sorted(y1 - y0 for y0, y1 in zip(ys0, ys1, strict=True))
    mid = len(expanded) // 2
    ratio_x = widths[mid] / span_x if span_x > 0.0 else 1.0
    ratio_y = heights[mid] / span_y if span_y > 0.0 else 1.0
    return 0 if ratio_x <= ratio_y else 1


def _uf_find(parent: list[int], i: int) -> int:
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def _uf_union(parent: list[int], rank: list[int], a: int, b: int) -> None:
    ra, rb = _uf_find(parent, a), _uf_find(parent, b)
    if ra == rb:
        return
    if rank[ra] < rank[rb]:
        parent[ra] = rb
    elif rank[ra] > rank[rb]:
        parent[rb] = ra
    else:
        parent[rb] = ra
        rank[ra] += 1


def merge_intersecting_rects_union_find(
    rects: Sequence[BBoxRect],
    pad: float = 10.0,
) -> tuple[BBoxRect, ...]:
    """Merge rectangles whose `pad`-expanded bounds intersect via union-find + axis sweep.

    Order-independent: output clusters are sorted canonically by `(y0, x0, y1, x1)`.
    """
    if not math.isfinite(pad) or pad < 0.0:
        raise ValueError(f"pad must be finite and non-negative, got {pad!r}")
    if not rects:
        return ()

    working = sorted(rects, key=lambda b: (b.y0, b.x0, b.y1, b.x1))
    while True:
        n = len(working)
        if n <= 1:
            return tuple(working)

        parent = list(range(n))
        rank = [0] * n
        expanded = [(r.x0 - pad, r.y0 - pad, r.x1 + pad, r.y1 + pad) for r in working]
        axis = _sweep_axis(expanded)

        if axis == 0:
            order = sorted(range(n), key=lambda idx: (expanded[idx][0], expanded[idx][2]))
            for pos in range(n):
                i = order[pos]
                _, iy0, ix1, iy1 = expanded[i]
                for q in range(pos + 1, n):
                    j = order[q]
                    jx0, jy0, _, jy1 = expanded[j]
                    if jx0 > ix1:
                        break
                    if not (iy1 < jy0 or iy0 > jy1):
                        _uf_union(parent, rank, i, j)
        else:
            order = sorted(range(n), key=lambda idx: (expanded[idx][1], expanded[idx][3]))
            for pos in range(n):
                i = order[pos]
                ix0, _, ix1, iy1 = expanded[i]
                for q in range(pos + 1, n):
                    j = order[q]
                    jx0, jy0, jx1, _ = expanded[j]
                    if jy0 > iy1:
                        break
                    if not (ix1 < jx0 or ix0 > jx1):
                        _uf_union(parent, rank, i, j)

        clusters: dict[int, BBoxRect] = {}
        for idx, rect in enumerate(working):
            root = _uf_find(parent, idx)
            clusters[root] = rect if root not in clusters else clusters[root].union(rect)

        next_working = sorted(clusters.values(), key=lambda b: (b.y0, b.x0, b.y1, b.x1))
        if len(next_working) == n:
            return tuple(next_working)
        working = next_working
