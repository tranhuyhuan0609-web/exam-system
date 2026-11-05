"""Cutting question figures out of page images without the surrounding question text.

1. Born-digital PDFs (also Word files converted by LibreOffice) carry the exact geometry of every vector
   drawing and embedded image. The model's box is only an estimate, so it is *snapped* to the union of the
   graphics inside it (plus labels such as "A", "R1" that sit fully inside the model's box), with a few
   pixels of margin. This is exact and never touches neighbouring text lines.
2. Scans and photos have no such geometry: the model's box is used with a tiny margin, and any band of
   content cut by the crop border (a partial text line above or below the figure) is trimmed away.
"""

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from app.ai.schemas import BoundingBox

SNAP_SLACK = 0.12  # how far outside the model's box a graphic or label may reach (fraction of the box size)
SNAP_MIN_COVER = 0.15  # snapped area must cover at least this share of the model's box
SNAP_PAD_PX = 6
FALLBACK_PAD = 0.004  # fraction of the page
INK_LEVEL = 160  # darker than this counts as ink; light watermarks are ignored
MAX_GRAPHIC_SHARE = 0.25  # ignore page-sized frames, backgrounds and watermarks
LIGHT_LUMINANCE = 0.75  # drawings lighter than this (watermarks, background tints) are not figure parts
LABEL_GAP = 12  # words this close to the figure (0–1000 scale, ~1.2% of the page) count as its labels


@dataclass
class PageGeometry:
    """Graphics and words of one PDF page, as 0–1000 boxes (same scale as the model's boxes)."""

    graphics: list[BoundingBox] = field(default_factory=list)
    # Text lines, each a list of word boxes. A line that runs outside a figure is question text, not a label.
    lines: list[list[BoundingBox]] = field(default_factory=list)


def page_geometry(page) -> PageGeometry:
    """Collect vector drawings, images and words of a PyMuPDF page."""
    width, height = page.rect.width, page.rect.height

    def norm(x0, y0, x1, y1) -> BoundingBox | None:
        if x1 < 0 or y1 < 0 or x0 > width or y0 > height:
            return None
        if (x1 - x0) * (y1 - y0) > MAX_GRAPHIC_SHARE * width * height:
            return None
        return BoundingBox(
            ymin=round(max(0, y0) / height * 1000),
            xmin=round(max(0, x0) / width * 1000),
            ymax=round(min(height, y1) / height * 1000),
            xmax=round(min(width, x1) / width * 1000),
        )

    def visible_and_dark(drawing) -> bool:
        colours = [c for c in (drawing.get("color"), drawing.get("fill")) if c]
        opacity = min(
            drawing.get("stroke_opacity") if drawing.get("stroke_opacity") is not None else 1,
            drawing.get("fill_opacity") if drawing.get("fill_opacity") is not None else 1,
        )
        if not colours or opacity < 0.5:
            return False
        luminance = min(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] for c in colours if len(c) >= 3)
        return luminance <= LIGHT_LUMINANCE

    geometry = PageGeometry()
    for drawing in page.get_drawings():
        if not visible_and_dark(drawing):
            continue
        r = drawing["rect"]
        if box := norm(r.x0, r.y0, r.x1, r.y1):
            geometry.graphics.append(box)
    for info in page.get_image_info():
        if box := norm(*info["bbox"]):
            geometry.graphics.append(box)
    lines: dict[tuple[int, int], list[BoundingBox]] = {}
    for x0, y0, x1, y1, _word, block, line, _n in page.get_text("words"):
        if box := norm(x0, y0, x1, y1):
            lines.setdefault((block, line), []).append(box)
    geometry.lines = list(lines.values())
    return geometry


def _area(b: BoundingBox) -> int:
    return max(0, b.ymax - b.ymin) * max(0, b.xmax - b.xmin)


def _inside(inner: BoundingBox, outer: BoundingBox) -> bool:
    return (
        outer.ymin <= inner.ymin and inner.ymax <= outer.ymax and outer.xmin <= inner.xmin and inner.xmax <= outer.xmax
    )


def _gap(a: BoundingBox, b: BoundingBox) -> int:
    """Distance between two boxes along the axis where they are furthest apart (0 if they overlap)."""
    dy = max(0, max(a.ymin, b.ymin) - min(a.ymax, b.ymax))
    dx = max(0, max(a.xmin, b.xmin) - min(a.xmax, b.xmax))
    return max(dx, dy)


def _union(boxes: list[BoundingBox]) -> BoundingBox:
    return BoundingBox(
        ymin=min(b.ymin for b in boxes),
        xmin=min(b.xmin for b in boxes),
        ymax=max(b.ymax for b in boxes),
        xmax=max(b.xmax for b in boxes),
    )


def snap_to_graphics(box: BoundingBox, geometry: PageGeometry | None, slack: float = SNAP_SLACK) -> BoundingBox | None:
    """Exact figure box from the page's graphics, or None when the page has none in that area.

    Graphics whose centre lies inside the model's box (with some slack) form the figure; words that sit
    inside that area and right next to the figure (axis labels, "A", "R1"...) are added as labels. The
    result never extends beyond the slack area, so neighbouring question text cannot leak in.
    """
    if geometry is None or not geometry.graphics:
        return None
    height, width = box.ymax - box.ymin, box.xmax - box.xmin
    slack_y, slack_x = max(8, int(height * slack)), max(8, int(width * slack))
    area = BoundingBox(
        ymin=box.ymin - slack_y, xmin=box.xmin - slack_x, ymax=box.ymax + slack_y, xmax=box.xmax + slack_x
    )
    hits = [
        g
        for g in geometry.graphics
        if area.ymin <= (g.ymin + g.ymax) / 2 <= area.ymax and area.xmin <= (g.xmin + g.xmax) / 2 <= area.xmax
    ]
    if not hits:
        return None
    graphics_box = _union(hits)
    # Labels: whole text lines inside the area that touch the drawing itself (measured against the graphics
    # only, never against other labels, so a paragraph next to the figure cannot be pulled in line by line).
    labels = [
        word
        for line in geometry.lines
        if all(_inside(w, area) for w in line) and any(_gap(w, graphics_box) <= LABEL_GAP for w in line)
        for word in line
    ]
    figure = _union([graphics_box, *labels])
    snapped = BoundingBox(
        ymin=max(figure.ymin, area.ymin),
        xmin=max(figure.xmin, area.xmin),
        ymax=min(figure.ymax, area.ymax),
        xmax=min(figure.xmax, area.xmax),
    )
    if _area(snapped) < SNAP_MIN_COVER * max(_area(box), 1):  # e.g. only an underline: not the figure
        return None
    return snapped


def _ink_runs(profile: list[int], threshold: float) -> list[tuple[int, int]]:
    """Runs (start, end exclusive) of rows/columns whose ink share exceeds the threshold."""
    runs, start = [], None
    for i, value in enumerate(profile):
        if value > threshold and start is None:
            start = i
        elif value <= threshold and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(profile)))
    return runs


def _trim_axis(runs: list[tuple[int, int]], length: int) -> tuple[int, int] | None:
    """Drop bands of content cut by the border (partial text lines); keep the rest."""
    if not runs:
        return None
    main = max(runs, key=lambda r: r[1] - r[0])
    kept = [r for r in runs if r == main or (r[0] > 0 and r[1] < length)]
    return kept[0][0], kept[-1][1]


def trim_cut_text(img: Image.Image, pad: int = SNAP_PAD_PX) -> Image.Image:
    """Remove partial text lines (or columns) that the crop border cut through."""
    ink = img.convert("L").point(lambda v: 255 if v < INK_LEVEL else 0)
    width, height = ink.size
    rows = list(ink.resize((1, height), Image.Resampling.BOX).getdata())
    y = _trim_axis(_ink_runs(rows, 1), height)
    if y is None:
        return img
    # Columns are measured on the kept rows only, so removed text lines do not widen the figure.
    band = ink.crop((0, y[0], width, y[1]))
    cols = list(band.resize((width, 1), Image.Resampling.BOX).getdata())
    x = _trim_axis(_ink_runs(cols, 1), width)
    if x is None:
        return img
    box = (max(0, x[0] - pad), max(0, y[0] - pad), min(width, x[1] + pad), min(height, y[1] + pad))
    return img.crop(box) if box != (0, 0, width, height) else img


def crop_figure(page_png: Path, box: BoundingBox, target: Path, geometry: PageGeometry | None = None) -> bool:
    """Cut the figure of `box` (0–1000 scale) out of the page. Returns False for degenerate boxes."""
    snapped = snap_to_graphics(box, geometry)
    with Image.open(page_png) as img:
        width, height = img.size
        use = snapped or box
        if snapped:
            pad_x = pad_y = SNAP_PAD_PX
        else:
            pad_x, pad_y = int(width * FALLBACK_PAD), int(height * FALLBACK_PAD)
        figure = cut(img, use, pad_x, pad_y)
        if figure is None:
            return False
        if not snapped:
            figure = trim_cut_text(figure)
        target.parent.mkdir(parents=True, exist_ok=True)
        figure.save(target, optimize=True)
    return True


def locate_crop(page: Image.Image, crop: Image.Image) -> tuple[int, int] | None:
    """Pixel position of `crop` inside `page` when it was cut from that exact image (lossless PNG)."""
    page, crop = page.convert("RGB"), crop.convert("RGB")
    page_w, page_h = page.size
    w, h = crop.size
    if w > page_w or h > page_h:
        return None
    page_bytes, crop_bytes = page.tobytes(), crop.tobytes()
    stride, cstride = page_w * 3, w * 3

    def crop_row(r: int) -> bytes:
        return crop_bytes[r * cstride : (r + 1) * cstride]

    # Search for the most varied row (a blank row would match everywhere).
    anchor = max(range(h), key=lambda r: len(set(crop_row(r)[::3])))
    target = crop_row(anchor)
    checks = range(0, h, max(1, h // 10))
    for y in range(anchor, page_h - (h - anchor) + 1):
        line = page_bytes[y * stride : (y + 1) * stride]
        start = 0
        while (i := line.find(target, start)) != -1:
            start = i + 1
            if i % 3:
                continue
            x, top = i // 3, y - anchor
            if all(
                page_bytes[(top + r) * stride + x * 3 : (top + r) * stride + x * 3 + cstride] == crop_row(r)
                for r in checks
            ):
                return x, top
    return None


def cut(img: Image.Image, box: BoundingBox, pad_x: int, pad_y: int) -> Image.Image | None:
    """Crop a 0–1000 box (plus padding in pixels) out of a page image; None if it is degenerate."""
    width, height = img.size
    left = max(0, min(box.xmin, box.xmax) * width // 1000 - pad_x)
    right = min(width, -(-max(box.xmin, box.xmax) * width // 1000) + pad_x)
    top = max(0, min(box.ymin, box.ymax) * height // 1000 - pad_y)
    bottom = min(height, -(-max(box.ymin, box.ymax) * height // 1000) + pad_y)
    if right - left < 20 or bottom - top < 20:
        return None
    return img.crop((left, top, right, bottom))
