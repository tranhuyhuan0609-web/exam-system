"""PDF engine adapter: pypdfium2 (render) + pdfplumber (text, tables, geometry) behind one interface.

This replaces PyMuPDF (AGPLv3) at the read/render boundary. It never imports fitz/pymupdf.

Coordinate system, for EVERY value this module returns or accepts
    * unit: PDF points (1/72 inch);
    * origin: TOP-LEFT of the page, x grows right, y grows DOWN (the same convention PyMuPDF
      exposes through ``page.rect`` / ``get_text`` / ``get_drawings``). The native PDF
      bottom-left origin never leaks out of this module.
    * sizes are those of the page as displayed (``/Rotate`` applied by pdfplumber and pdfium alike).

Memory: nothing is cached per page. Every method opens the page it needs, converts the result to
plain dataclasses and releases the page before returning, so memory is bounded by one page
(plus one rendered bitmap).

Out of scope on purpose: writing/assembling PDFs, redaction, OCR
(see docs/architecture/PDF_ENGINE_MIGRATION.md).
"""

from __future__ import annotations

import ctypes
import io
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

import pdfplumber
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
from PIL import Image

PathOrBytes = str | Path | bytes | bytearray | memoryview


class PDFEngineError(Exception):
    """Raised for any failure to open or read a PDF (corrupt, encrypted, closed, bad page index)."""


class PDFPasswordRequired(PDFEngineError):
    """The PDF is encrypted and needs a (different) password to open (pdfium ``FPDF_ERR_PASSWORD``)."""


_FPDF_ERR_PASSWORD = 4


@dataclass(frozen=True, slots=True)
class Rect:
    """Axis-aligned rectangle, top-left origin, points. ``x0 <= x1`` and ``y0 <= y1``."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    def as_tuple(self) -> tuple[float, float, float, float]:
        return self.x0, self.y0, self.x1, self.y1

    def expanded(self, pad: float) -> Rect:
        return Rect(self.x0 - pad, self.y0 - pad, self.x1 + pad, self.y1 + pad)

    def union(self, other: Rect) -> Rect:
        return Rect(min(self.x0, other.x0), min(self.y0, other.y0), max(self.x1, other.x1), max(self.y1, other.y1))

    def intersection(self, other: Rect) -> Rect | None:
        r = Rect(max(self.x0, other.x0), max(self.y0, other.y0), min(self.x1, other.x1), min(self.y1, other.y1))
        return r if r.x0 <= r.x1 and r.y0 <= r.y1 else None

    def intersects(self, other: Rect) -> bool:
        """True when the rectangles overlap or touch."""
        return self.intersection(other) is not None

    def contains_point(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def contains(self, other: Rect) -> bool:
        return self.x0 <= other.x0 and self.y0 <= other.y0 and other.x1 <= self.x1 and other.y1 <= self.y1

    def overlap_ratio(self, other: Rect) -> float:
        """Intersection area divided by THIS rectangle's area (0 when this one is degenerate)."""
        inter = self.intersection(other)
        if inter is None or self.area <= 0.0:
            return 0.0
        return inter.area / self.area


@dataclass(frozen=True, slots=True)
class Char:
    text: str
    rect: Rect
    size: float
    fontname: str
    color: tuple[float, ...] | None  # non-stroking (fill) colour as reported by the PDF, or None


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    rect: Rect


@dataclass(frozen=True, slots=True)
class TextLine:
    text: str
    rect: Rect


@dataclass(frozen=True, slots=True)
class Table:
    rect: Rect
    cells: tuple[Rect, ...]
    rows: tuple[tuple[str | None, ...], ...]  # extracted cell text, row-major


@dataclass(frozen=True, slots=True)
class ImageBox:
    rect: Rect
    src_width: int  # pixel size of the embedded bitmap (0 when unknown)
    src_height: int
    name: str = ""  # XObject name; the same picture drawn twice shares it


@dataclass(frozen=True, slots=True)
class Drawing:
    """A vector path object. ``kind`` is 'rect', 'line' or 'curve' (pdfplumber classification)."""

    rect: Rect
    kind: str
    stroke: bool
    fill: bool
    stroke_color: tuple[float, ...] | None
    fill_color: tuple[float, ...] | None
    linewidth: float
    # min(stroke alpha, fill alpha) of the matching pdfium path object; 1.0 when no object could be matched.
    opacity: float = 1.0


@dataclass(frozen=True, slots=True)
class Span:
    """A run of characters with one font, size, colour and alpha on one text line."""

    text: str
    rect: Rect
    size: float
    fontname: str
    color: int  # 0xRRGGBB fill colour, converted to RGB by pdfium (also for gray / CMYK)
    alpha: int  # 0..255, from the fill alpha of the text (ExtGState ca)


@dataclass(frozen=True, slots=True)
class RichLine:
    """A text line with its spans, in the style of ``get_text("dict")`` lines."""

    spans: tuple[Span, ...]
    rect: Rect

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)


@dataclass(frozen=True, slots=True)
class Block:
    """Like a ``get_text("blocks")`` tuple. ``kind`` 0 = text, 1 = image; text ends with a newline."""

    rect: Rect
    text: str
    number: int
    kind: int = 0


@dataclass(frozen=True, slots=True)
class _PChar:
    ch: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    font: str
    color: int
    alpha: int
    boxed: bool


# Tunables of the fitz-style line/block grouping (calibrated against PyMuPDF on the golden corpus).
LINE_BREAK_GAP_EM = 4.0  # a horizontal gap wider than this many em ends a text line
LINE_SHIFT_EM = 0.7  # a character whose centre is this many heights above or below the last one starts a new line
SPAN_SIZE_RATIO = 1.3  # a glyph box this much taller or shorter than its neighbour starts a new span
SPACE_GAP_EM = 0.2  # a gap wider than this many em between two characters of a row reads as a space
BLOCK_GAP_EM = 0.35  # lines closer than this many line heights (vertically) share a block


def _color(value: Any) -> tuple[float, ...] | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return (float(value),)
    try:
        return tuple(float(v) for v in value)
    except (TypeError, ValueError):
        return None  # pattern / named colour space: not representable as plain numbers


def _rect_of(obj: dict[str, Any]) -> Rect:
    return Rect(float(obj["x0"]), float(obj["top"]), float(obj["x1"]), float(obj["bottom"]))


def _center_in(obj: dict[str, Any], boxes: Sequence[Rect]) -> bool:
    cx, cy = (float(obj["x0"]) + float(obj["x1"])) / 2.0, (float(obj["top"]) + float(obj["bottom"])) / 2.0
    return any(b.contains_point(cx, cy) for b in boxes)


_FORM_DEPTH = 15  # how deep into nested form XObjects page objects are read

Matrix = tuple[float, float, float, float, float, float]


def _compose(first: Matrix, then: Matrix) -> Matrix:
    """The matrix that applies ``first`` and afterwards ``then`` (PDF row-vector convention)."""
    a, b, c, d, e, f = first
    p, q, r, s, t, u = then
    return (a * p + b * r, a * q + b * s, c * p + d * r, c * q + d * s, e * p + f * r + t, e * q + f * s + u)


def _page_matrix(obj: Any) -> Matrix:
    """The transform from the object's own space to page space (its matrix, then those of its forms)."""
    matrix: Matrix = tuple(obj.get_matrix().get())  # type: ignore[assignment]
    container = obj.container
    while container is not None and not isinstance(container, pdfium.PdfPage):
        matrix = _compose(matrix, tuple(container.get_matrix().get()))  # type: ignore[arg-type]
        container = getattr(container, "container", None)
    return matrix


def _apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def _image_box(obj: Any, page: Any, matrix: Matrix, height: float) -> ImageBox:
    corners = [_apply(matrix, x, y) for x, y in ((0, 0), (1, 0), (0, 1), (1, 1))]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    rect = Rect(min(xs), height - max(ys), max(xs), height - min(ys))
    meta = pdfium_raw.FPDF_IMAGEOBJ_METADATA()
    ok = pdfium_raw.FPDFImageObj_GetImageMetadata(obj.raw, page.raw, ctypes.byref(meta))
    width_px, height_px = (int(meta.width), int(meta.height)) if ok else (0, 0)
    # The same picture drawn twice has the same pixel size and encoded length: that is its identity here.
    encoded = pdfium_raw.FPDFImageObj_GetImageDataRaw(obj.raw, None, 0)
    name = f"{width_px}x{height_px}:{encoded}" if width_px and height_px and encoded else ""
    return ImageBox(rect, width_px, height_px, name)


def _path_drawing(obj: Any, matrix: Matrix, height: float) -> tuple[Drawing, tuple[tuple[int, int], ...]] | None:
    fill_mode, stroke = ctypes.c_int(), ctypes.c_int()
    pdfium_raw.FPDFPath_GetDrawMode(obj.raw, ctypes.byref(fill_mode), ctypes.byref(stroke))
    fill, stroked = bool(fill_mode.value), bool(stroke.value)
    if not (fill or stroked):
        return None
    points: list[tuple[float, float]] = []
    curved = False
    px, py = ctypes.c_float(), ctypes.c_float()
    for i in range(pdfium_raw.FPDFPath_CountSegments(obj.raw)):
        segment = pdfium_raw.FPDFPath_GetPathSegment(obj.raw, i)
        pdfium_raw.FPDFPathSegment_GetPoint(segment, ctypes.byref(px), ctypes.byref(py))
        curved = curved or pdfium_raw.FPDFPathSegment_GetType(segment) == pdfium_raw.FPDF_SEGMENT_BEZIERTO
        points.append(_apply(matrix, px.value, py.value))
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    rect = Rect(min(xs), height - max(ys), max(xs), height - min(ys))
    r_, g_, b_, a_ = (ctypes.c_uint() for _ in range(4))
    alphas = [1.0]
    stroke_color = fill_color = None
    if fill:
        pdfium_raw.FPDFPageObj_GetFillColor(obj.raw, *(ctypes.byref(v) for v in (r_, g_, b_, a_)))
        fill_color = (r_.value / 255.0, g_.value / 255.0, b_.value / 255.0)
        alphas.append(a_.value / 255.0)
    if stroked:
        pdfium_raw.FPDFPageObj_GetStrokeColor(obj.raw, *(ctypes.byref(v) for v in (r_, g_, b_, a_)))
        stroke_color = (r_.value / 255.0, g_.value / 255.0, b_.value / 255.0)
        alphas.append(a_.value / 255.0)
    width = ctypes.c_float()
    pdfium_raw.FPDFPageObj_GetStrokeWidth(obj.raw, ctypes.byref(width))
    scale = abs(matrix[0] * matrix[3] - matrix[1] * matrix[2]) ** 0.5
    if curved:
        kind = "curve"
    elif len({(round(x, 3), round(y, 3)) for x, y in points}) <= 2:
        kind = "line"
    elif _axis_aligned_box(points):
        kind = "rect"
    else:
        kind = "curve"
    drawing = Drawing(
        rect=rect,
        kind=kind,
        stroke=stroked,
        fill=fill,
        stroke_color=stroke_color,
        fill_color=fill_color,
        linewidth=float(width.value) * scale if stroked else 0.0,
        opacity=min(alphas),
    )
    return drawing, tuple((round(x * 100), round(y * 100)) for x, y in points)


def _complementary(first: Drawing, second: Drawing) -> bool:
    """One is only filled and the other only stroked."""
    return first.fill != second.fill and first.stroke != second.stroke and (first.fill or first.stroke) and (
        second.fill or second.stroke
    )


def _same_fill_and_touching(first: Drawing, second: Drawing) -> bool:
    """Consecutive fill-only paths of one colour whose boxes touch: pdfium reports the subpaths of one
    fill operation (a bar and its arrow head) as separate objects, PyMuPDF as one drawing."""
    return (
        first.fill
        and second.fill
        and not first.stroke
        and not second.stroke
        and first.fill_color == second.fill_color
        and first.opacity == second.opacity
        and first.rect.intersects(second.rect)
    )


def _union_fills(first: Drawing, second: Drawing) -> Drawing:
    return Drawing(
        rect=first.rect.union(second.rect),
        kind="curve",
        stroke=False,
        fill=True,
        stroke_color=None,
        fill_color=first.fill_color,
        linewidth=0.0,
        opacity=first.opacity,
    )


def _fill_and_stroke(first: Drawing, second: Drawing) -> Drawing:
    filled, stroked = (first, second) if first.fill else (second, first)
    return Drawing(
        rect=first.rect,
        kind=first.kind,
        stroke=True,
        fill=True,
        stroke_color=stroked.stroke_color,
        fill_color=filled.fill_color,
        linewidth=stroked.linewidth,
        opacity=min(first.opacity, second.opacity),
    )


def _axis_aligned_box(points: list[tuple[float, float]]) -> bool:
    """True for the 4 (or 5, closed) corner points of an axis-aligned rectangle."""
    distinct = {(round(x, 3), round(y, 3)) for x, y in points}
    if len(distinct) != 4:
        return False
    return len({x for x, _ in distinct}) == 2 and len({y for _, y in distinct}) == 2


def _normalised_matches(text: str, needle: str, match_case: bool) -> list[tuple[int, int]]:
    """(start, count) character spans of ``needle`` in ``text`` compared as NFD with collapsed whitespace."""
    haystack: list[str] = []
    origin: list[int] = []  # index in ``text`` of the character each normalised character came from
    for i, ch in enumerate(text):
        for part in unicodedata.normalize("NFD", ch):
            if part.isspace():
                if haystack and haystack[-1] == " ":
                    continue
                part = " "
            haystack.append(part if match_case else part.lower())
            origin.append(i)
    want = " ".join(unicodedata.normalize("NFD", needle).split())
    want = want if match_case else want.lower()
    flat = "".join(haystack)
    out: list[tuple[int, int]] = []
    pos = flat.find(want)
    while want and pos != -1:
        first, last = origin[pos], origin[pos + len(want) - 1]
        out.append((first, last - first + 1))
        pos = flat.find(want, pos + len(want))
    return out


def cluster_rects(
    rects: Iterable[Rect], x_tolerance: float = 3.0, y_tolerance: float = 3.0, final_filter: bool = True
) -> list[Rect]:
    """The clustering of ``PDFEngineAdapter.cluster_drawings`` on plain rectangles (see there)."""

    def neighbours(a: Rect, b: Rect) -> bool:
        return not (
            a.x1 < b.x0 - x_tolerance
            or a.x0 > b.x1 + x_tolerance
            or a.y1 < b.y0 - y_tolerance
            or a.y0 > b.y1 + y_tolerance
        )

    pending = sorted(set(rects), key=lambda r: (r.y1, r.x0))
    clusters: list[Rect] = []
    while pending:
        grown = pending[0]
        repeat = True
        while repeat:
            repeat = False
            for i in range(len(pending) - 1, 0, -1):  # back to front; the first box is the seed itself
                if neighbours(pending[i], grown):
                    grown = grown.union(pending[i])
                    del pending[i]
                    repeat = True
        clusters.append(grown)
        del pending[0]
        pending = sorted(set(pending), key=lambda r: (r.y1, r.x0))
    clusters = sorted(set(clusters), key=lambda r: (r.y1, r.x0))
    if not final_filter:
        return clusters
    return [r for r in clusters if r.width > x_tolerance and r.height > y_tolerance]


def merge_intersecting_rects_union_find(rects: Iterable[Rect], pad: float = 10.0) -> list[Rect]:
    """Merge rectangles that overlap once each is grown by ``pad`` points on every side.

    Union-Find over pairwise intersections, repeated until a fixpoint (a merged box can start to
    touch another one). Each output box is the union of the ORIGINAL (unpadded) members, so the
    padding only decides who is merged, not how big the result is. The output is sorted by
    ``(y0, x0, y1, x1)`` and does not depend on the input order.
    """
    working = list(rects)
    while True:
        n = len(working)
        if n < 2:
            return sorted(working, key=lambda r: (r.y0, r.x0, r.y1, r.x1))
        parent = list(range(n))

        def find(i: int, parent: list[int] = parent) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        grown = [r.expanded(pad) for r in working]
        for i in range(n):
            for j in range(i + 1, n):
                if grown[i].intersects(grown[j]):
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[max(ri, rj)] = min(ri, rj)

        clusters: dict[int, Rect] = {}
        for idx, r in enumerate(working):
            root = find(idx)
            clusters[root] = r if root not in clusters else clusters[root].union(r)
        merged = sorted(clusters.values(), key=lambda r: (r.y0, r.x0, r.y1, r.x1))
        if len(merged) == n:
            return merged
        working = merged


class PDFEngineAdapter:
    """Read-only PDF access. Use as a context manager so both engines are closed deterministically.

        with PDFEngineAdapter.open(pdf_bytes) as pdf:
            for i in range(pdf.page_count):
                words = pdf.words(i)
    """

    # The spec's name, usable as ``PDFEngineAdapter.merge_intersecting_rects_union_find(rects, pad)``.
    merge_intersecting_rects_union_find = staticmethod(merge_intersecting_rects_union_find)

    def __init__(self, data: bytes, *, password: str | None = None) -> None:
        self._data = data
        self._closed = False
        self._plumber: pdfplumber.PDF | None = None
        # Images and drawings of the LAST page read: one pdfplumber parse serves images(), drawings(),
        # cluster_drawings() and drawing_rects() (a page parse costs about a second). One page only.
        self._vector_page: tuple[int, list[ImageBox], list[Drawing]] | None = None
        self._password = password
        try:
            self._pdfium = pdfium.PdfDocument(data, password=password)
        except Exception as exc:  # PdfiumError, ValueError, ...
            if getattr(exc, "err_code", None) == _FPDF_ERR_PASSWORD:
                raise PDFPasswordRequired(f"PDF is password protected: {exc}") from exc
            raise PDFEngineError(f"Cannot open PDF: {exc}") from exc
        self._page_count = len(self._pdfium)

    @classmethod
    def open(cls, source: PathOrBytes, *, password: str | None = None) -> PDFEngineAdapter:
        if isinstance(source, (str, Path)):
            try:
                data = Path(source).read_bytes()
            except OSError as exc:
                raise PDFEngineError(f"Cannot read PDF file {source}: {exc}") from exc
        else:
            data = bytes(source)
        if not data:
            raise PDFEngineError("Cannot open PDF: empty input")
        return cls(data, password=password)

    # -- lifecycle ---------------------------------------------------------------------------

    def __enter__(self) -> PDFEngineAdapter:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._vector_page = None
        if self._plumber is not None:
            self._plumber.close()
            self._plumber = None
        self._pdfium.close()
        self._data = b""

    @property
    def closed(self) -> bool:
        return self._closed

    def _check(self, index: int) -> None:
        if self._closed:
            raise PDFEngineError("PDF document is closed")
        if not 0 <= index < self._page_count:
            raise PDFEngineError(f"Page index {index} out of range (0..{self._page_count - 1})")

    def _plumber_doc(self) -> pdfplumber.PDF:
        if self._plumber is None:
            try:
                self._plumber = pdfplumber.open(io.BytesIO(self._data), password=self._password)
            except Exception as exc:
                raise PDFEngineError(f"Cannot parse PDF structure: {exc}") from exc
        return self._plumber

    @contextmanager
    def _plumber_page(self, index: int) -> Iterator[Any]:
        """Yield the pdfplumber page and release its caches afterwards; wrap engine errors."""
        self._check(index)
        try:
            page = self._plumber_doc().pages[index]
        except PDFEngineError:
            raise
        except Exception as exc:
            raise PDFEngineError(f"Cannot read page {index}: {exc}") from exc
        try:
            yield page
        except PDFEngineError:
            raise
        except Exception as exc:
            raise PDFEngineError(f"Cannot read page {index}: {exc}") from exc
        finally:
            page.close()

    @staticmethod
    def _view(page: Any, clip: Rect | None, exclude: Sequence[Rect]) -> Any:
        """pdfplumber page restricted to ``clip`` with characters centred in ``exclude`` removed."""
        if clip is None and not exclude:
            return page

        def keep(obj: dict[str, Any]) -> bool:
            if clip is not None and not _center_in(obj, [clip]):
                return False
            return not (exclude and obj.get("object_type") == "char" and _center_in(obj, exclude))

        return page.filter(keep)

    # -- page geometry -----------------------------------------------------------------------

    @property
    def page_count(self) -> int:
        if self._closed:
            raise PDFEngineError("PDF document is closed")
        return self._page_count

    def page_size(self, index: int) -> tuple[float, float]:
        """(width, height) in points as displayed."""
        self._check(index)
        page = self._pdfium[index]
        try:
            return page.get_size()
        finally:
            page.close()

    def page_rect(self, index: int) -> Rect:
        w, h = self.page_size(index)
        return Rect(0.0, 0.0, w, h)

    # -- rendering ---------------------------------------------------------------------------

    def render(self, index: int, dpi: float = 300.0, clip: Rect | None = None) -> Image.Image:
        """Render a page (or ``clip``, in top-left points) to an RGB image of ~ size_pt * dpi / 72 pixels."""
        if dpi <= 0:
            raise PDFEngineError("dpi must be positive")
        self._check(index)
        page = self._pdfium[index]
        try:
            w, h = page.get_size()
            crop = (0.0, 0.0, 0.0, 0.0)
            if clip is not None:
                x0, y0 = max(0.0, min(w, clip.x0)), max(0.0, min(h, clip.y0))
                x1, y1 = max(x0, min(w, clip.x1)), max(y0, min(h, clip.y1))
                if x1 - x0 <= 0 or y1 - y0 <= 0:
                    raise PDFEngineError("Clip rectangle is empty or outside the page")
                crop = (x0, h - y1, w - x1, y0)  # pdfium order: left, bottom, right, top margins to cut
            bitmap = page.render(scale=dpi / 72.0, crop=crop, may_draw_forms=False)
            return bitmap.to_pil().convert("RGB")
        except PDFEngineError:
            raise
        except Exception as exc:
            raise PDFEngineError(f"Cannot render page {index}: {exc}") from exc
        finally:
            page.close()

    def render_png(self, index: int, dpi: float = 300.0, clip: Rect | None = None) -> bytes:
        buf = io.BytesIO()
        self.render(index, dpi, clip).save(buf, format="PNG")
        return buf.getvalue()

    # -- text --------------------------------------------------------------------------------

    def text_stream(self, index: int, *, newline_terminated: bool = False) -> str:
        """Plain text in content-stream order (pdfium text page), without layout reconstruction.

        Fast and low-memory; matches PyMuPDF's ``get_text("text")`` ordering more closely than
        ``text()``, which sorts by position. Lines are separated by ``\n``.

        ``newline_terminated=True`` ends a non-empty result with ``\n``, exactly like PyMuPDF's
        ``get_text("text")`` (so joining pages keeps a line break between them).
        """
        self._check(index)
        page = self._pdfium[index]
        try:
            textpage = page.get_textpage()
            try:
                text = textpage.get_text_range().replace("\r\n", "\n")
                if newline_terminated and text and not text.endswith("\n"):
                    text += "\n"
                return text
            finally:
                textpage.close()
        except Exception as exc:
            raise PDFEngineError(f"Cannot read text of page {index}: {exc}") from exc
        finally:
            page.close()

    def find_text(self, index: int, needle: str, *, match_case: bool = False) -> list[Rect]:
        """Bounding boxes (top-left origin) of every occurrence of ``needle``, like ``page.search_for``.

        One rectangle per occurrence (a match wrapped over two lines yields their union).
        pdfium matches on code points; when it finds nothing, a second pass compares both sides decomposed
        (NFD, so NFC and NFD Vietnamese match each other) and with whitespace runs collapsed (gap G6).
        """
        if not needle:
            return []
        self._check(index)
        page = self._pdfium[index]
        try:
            _, height = page.get_size()
            textpage = page.get_textpage()
            try:
                spans: list[tuple[int, int]] = []
                searcher = textpage.search(needle, match_case=match_case)
                while (match := searcher.get_next()) is not None:
                    spans.append(match)
                if not spans:
                    spans = _normalised_matches(textpage.get_text_range(), needle, match_case)
                found: list[Rect] = []
                for start, count in spans:
                    boxes = [textpage.get_charbox(i) for i in range(start, start + count)]
                    boxes = [b for b in boxes if b[2] > b[0] and b[3] > b[1]]
                    if boxes:
                        found.append(
                            Rect(
                                min(b[0] for b in boxes),
                                height - max(b[3] for b in boxes),
                                max(b[2] for b in boxes),
                                height - min(b[1] for b in boxes),
                            )
                        )
                return found
            finally:
                textpage.close()
        except Exception as exc:
            raise PDFEngineError(f"Cannot search page {index}: {exc}") from exc
        finally:
            page.close()

    def chars(self, index: int, *, clip: Rect | None = None, exclude: Sequence[Rect] = ()) -> list[Char]:
        with self._plumber_page(index) as page:
            return [
                Char(
                    text=c["text"],
                    rect=_rect_of(c),
                    size=float(c.get("size") or 0.0),
                    fontname=str(c.get("fontname") or ""),
                    color=_color(c.get("non_stroking_color")),
                )
                for c in self._view(page, clip, exclude).chars
            ]

    def words(self, index: int, *, clip: Rect | None = None, exclude: Sequence[Rect] = ()) -> list[Word]:
        with self._plumber_page(index) as page:
            view = self._view(page, clip, exclude)
            return [Word(w["text"], _rect_of(w)) for w in view.extract_words(keep_blank_chars=False)]

    def lines(self, index: int, *, clip: Rect | None = None, exclude: Sequence[Rect] = ()) -> list[TextLine]:
        """Text lines in reading order (top to bottom, then left to right), each with its bounding box."""
        with self._plumber_page(index) as page:
            view = self._view(page, clip, exclude)
            out = [TextLine(ln["text"], _rect_of(ln)) for ln in view.extract_text_lines(return_chars=False) or []]
        return sorted(out, key=lambda ln: (ln.rect.y0, ln.rect.x0))

    def text(self, index: int, *, clip: Rect | None = None, exclude: Sequence[Rect] = ()) -> str:
        """Page text, one line per row, ignoring characters centred inside any ``exclude`` rectangle."""
        return "\n".join(ln.text for ln in self.lines(index, clip=clip, exclude=exclude))

    # -- structure and geometry --------------------------------------------------------------

    def tables(
        self, index: int, *, min_cells: int = 2, table_settings: dict[str, Any] | None = None
    ) -> list[Table]:
        """Ruled tables (pdfplumber ``find_tables``), top to bottom.

        Candidates with fewer than ``min_cells`` cells are dropped. Caveat: pdfplumber derives tables from
        ruling edges, so overlapping or edge-sharing vector rectangles of a figure can be reported as a
        table; callers that care should also check ``Table.rows`` for text.
        """
        with self._plumber_page(index) as page:
            out: list[Table] = []
            for t in page.find_tables(table_settings=table_settings or {}):
                if len(t.cells) < min_cells:
                    continue
                x0, y0, x1, y1 = t.bbox
                rows = tuple(tuple(r) for r in t.extract())
                cells = tuple(Rect(*(float(v) for v in c)) for c in t.cells)
                out.append(Table(Rect(float(x0), float(y0), float(x1), float(y1)), cells, rows))
        return sorted(out, key=lambda t: (t.rect.y0, t.rect.x0))

    def table_rects(self, index: int) -> list[Rect]:
        return [t.rect for t in self.tables(index)]

    def images(self, index: int) -> list[ImageBox]:
        return list(self._vector(index)[0])

    def drawings(self, index: int) -> list[Drawing]:
        """Vector path objects (rects, straight lines, curves) with colours and stroke/fill flags.

        ``Drawing.opacity`` is read from pdfium: pdfplumber does not know ExtGState alpha. The pdfium path
        object is matched to each drawing by bounding box (only done when the page has a translucent path).
        """
        return list(self._vector(index)[1])

    def _vector(self, index: int) -> tuple[list[ImageBox], list[Drawing]]:
        self._check(index)
        if self._vector_page is not None and self._vector_page[0] == index:
            return self._vector_page[1], self._vector_page[2]
        images, drawings = self._read_vector(index)
        self._vector_page = (index, images, drawings)
        return images, drawings

    def _read_vector(self, index: int) -> tuple[list[ImageBox], list[Drawing]]:
        """Images and vector paths of a page from the pdfium page objects (one object = one painted path).

        This is how PyMuPDF counts drawings too: a path with several subpaths (an arrow with its head) is ONE
        drawing whose rectangle is the box of all its points. Objects inside form XObjects are mapped to page
        space by composing the matrices of their containers.
        """
        self._check(index)
        page = self._pdfium[index]
        try:
            height = page.get_size()[1]
            images: list[ImageBox] = []
            drawings: list[Drawing] = []
            last_points: tuple[tuple[int, int], ...] = ()
            wanted = [pdfium_raw.FPDF_PAGEOBJ_PATH, pdfium_raw.FPDF_PAGEOBJ_IMAGE]
            for obj in page.get_objects(filter=wanted, max_depth=_FORM_DEPTH):
                matrix = _page_matrix(obj)
                if obj.type == pdfium_raw.FPDF_PAGEOBJ_IMAGE:
                    images.append(_image_box(obj, page, matrix, height))
                    continue
                found = _path_drawing(obj, matrix, height)
                if found is None:
                    continue
                drawing, points = found
                # A path that is filled and then stroked (two operators, one path) is one drawing, as in PyMuPDF.
                if drawings and points == last_points and _complementary(drawings[-1], drawing):
                    drawings[-1] = _fill_and_stroke(drawings[-1], drawing)
                else:
                    drawings.append(drawing)
                    last_points = points
            return (
                sorted(images, key=lambda b: (b.rect.y0, b.rect.x0)),
                sorted(drawings, key=lambda d: (d.rect.y0, d.rect.x0, d.kind)),
            )
        except Exception as exc:
            raise PDFEngineError(f"Cannot read graphics of page {index}: {exc}") from exc
        finally:
            page.close()

    def cluster_drawings(
        self, index: int, *, x_tolerance: float = 3.0, y_tolerance: float = 3.0, final_filter: bool = True
    ) -> list[Rect]:
        """Join the boxes of neighbouring vector paths, like PyMuPDF's ``page.cluster_drawings``.

        Two boxes are neighbours when the gap between them is at most the tolerance on both axes. Only paths
        fully inside the page take part. Unlike ``merge_intersecting_rects_union_find`` the clusters are grown
        greedily from the lowest box upwards (same order as PyMuPDF), and with ``final_filter`` clusters not
        wider than ``x_tolerance`` or not taller than ``y_tolerance`` are dropped.
        """
        page_rect = self.page_rect(index)
        boxes = [d.rect for d in self.drawings(index) if page_rect.contains(d.rect)]
        return cluster_rects(boxes, x_tolerance, y_tolerance, final_filter)

    def drawing_rects(self, index: int, *, include_tables: bool = False) -> list[Rect]:
        """Bounding boxes of images and vector objects, by default WITHOUT those inside a detected table.

        This is the input a figure detector feeds to ``merge_intersecting_rects_union_find``.
        """
        tables = [] if include_tables else [t.expanded(4.0) for t in self.table_rects(index)]
        boxes = [i.rect for i in self.images(index)] + [d.rect for d in self.drawings(index)]
        return [b for b in boxes if not any(b.overlap_ratio(t) >= 0.5 for t in tables)]

    # -- fitz-style text structure (pdfium text page) ------------------------------------------

    def _pdfium_chars(self, index: int) -> list[_PChar]:
        """Every character of the pdfium text page in content-stream order, with its LOOSE box.

        The loose box spans the font's ascent..descent (like PyMuPDF line/span boxes), unlike the tight
        glyph box. Generated characters (pdfium inserts spaces and CR/LF between text objects) have an empty box.
        Fill colour and alpha come from pdfium, which converts gray/CMYK to RGB and applies ExtGState alpha.
        """
        self._check(index)
        page = self._pdfium[index]
        try:
            height = page.get_size()[1]
            textpage = page.get_textpage()
            try:
                tp = textpage.raw
                out: list[_PChar] = []
                r_, g_, b_, a_ = (ctypes.c_uint() for _ in range(4))
                font_buf = ctypes.create_string_buffer(160)
                flags = ctypes.c_int()
                box = pdfium_raw.FS_RECTF()
                for i in range(textpage.count_chars()):
                    code = pdfium_raw.FPDFText_GetUnicode(tp, i)
                    if code in (0xFFFE, 0xFFFF):
                        continue
                    ch = chr(code) if code else ""
                    ok = pdfium_raw.FPDFText_GetLooseCharBox(tp, i, ctypes.byref(box))
                    if not (ok and box.right > box.left and box.top > box.bottom):
                        out.append(_PChar(ch, 0.0, 0.0, 0.0, 0.0, 0.0, "", 0, 255, False))
                        continue
                    # FPDFText_GetFontSize is the unscaled font size (often 1.0 when the text matrix scales it):
                    # the loose box height is the reliable em proxy.
                    size = (float(box.top) - float(box.bottom)) / 1.15
                    pdfium_raw.FPDFText_GetFillColor(
                        tp, i, ctypes.byref(r_), ctypes.byref(g_), ctypes.byref(b_), ctypes.byref(a_)
                    )
                    n = pdfium_raw.FPDFText_GetFontInfo(tp, i, font_buf, len(font_buf), ctypes.byref(flags))
                    out.append(
                        _PChar(
                            ch,
                            float(box.left),
                            height - float(box.top),
                            float(box.right),
                            height - float(box.bottom),
                            size,
                            font_buf.value.decode("latin-1") if n else "",
                            (r_.value << 16) | (g_.value << 8) | b_.value,
                            a_.value,
                            True,
                        )
                    )
                return out
            finally:
                textpage.close()
        except Exception as exc:
            raise PDFEngineError(f"Cannot read text structure of page {index}: {exc}") from exc
        finally:
            page.close()

    def rich_lines(
        self, index: int, *, clip: Rect | None = None, line_gap: float = LINE_BREAK_GAP_EM
    ) -> list[RichLine]:
        """Text lines with spans (font, size, colour, alpha), in content-stream order.

        A line is a run of characters sharing a text row: it ends when the next
        character overlaps the row by less than 40 %, jumps back to the left, or sits more than ``line_gap`` em
        to the right (table cells, tab stops: PyMuPDF reports those as separate blocks too).
        With ``clip`` only characters whose centre lies inside the rectangle are read.
        """
        lines: list[RichLine] = []
        cur: list[_PChar] = []

        def flush() -> None:
            nonlocal cur
            chars, cur = cur, []
            if not any(c.ch.strip() for c in chars):
                return
            spans: list[Span] = []
            group: list[_PChar] = []

            def close_group() -> None:
                boxed = [c for c in group if c.boxed]
                if boxed:
                    rect = Rect(
                        min(c.x0 for c in boxed),
                        min(c.y0 for c in boxed),
                        max(c.x1 for c in boxed),
                        max(c.y1 for c in boxed),
                    )
                    first = boxed[0]
                    text = "".join(c.ch for c in group)
                    spans.append(Span(text, rect, first.size, first.font, first.color, first.alpha))
                group.clear()

            ref: _PChar | None = None
            for c in chars:
                if c.boxed:
                    if ref is not None and (
                        max(ref.size, c.size) > SPAN_SIZE_RATIO * min(ref.size, c.size)
                        or ref.font != c.font
                        or ref.color != c.color
                        or ref.alpha != c.alpha
                    ):
                        close_group()
                    ref = c
                group.append(c)
            close_group()
            if spans:
                rect = spans[0].rect
                for s in spans[1:]:
                    rect = rect.union(s.rect)
                lines.append(RichLine(tuple(spans), rect))

        row: tuple[float, float, float] | None = None  # y0, y1, right edge of the last boxed char
        space = _PChar(" ", 0.0, 0.0, 0.0, 0.0, 0.0, "", 0, 255, False)
        prev = space  # the last boxed character
        for c in self._pdfium_chars(index):
            if clip is not None and c.boxed and not clip.contains_point((c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2):
                continue
            if not c.boxed:
                # A generated space between two text objects. Generated CR/LF are ignored: pdfium also emits them
                # between a text and its superscript, so geometry alone decides about lines and word gaps.
                if c.ch == " " and cur and not cur[-1].ch.isspace():
                    cur.append(space)
                continue
            if row is not None:
                shift = abs((c.y0 + c.y1) / 2 - (prev.y0 + prev.y1) / 2)
                if (
                    shift > LINE_SHIFT_EM * max(c.y1 - c.y0, prev.y1 - prev.y0)
                    or c.x0 < row[2] - 1.5 * c.size
                    or c.x0 - row[2] > line_gap * c.size
                ):
                    flush()
                    row = None
                elif c.x0 - row[2] > SPACE_GAP_EM * c.size and cur and not cur[-1].ch.isspace() and not c.ch.isspace():
                    cur.append(space)  # PyMuPDF inserts a space where a gap separates two words
            cur.append(c)
            prev = c
            row = (c.y0, c.y1, c.x1) if row is None else (min(row[0], c.y0), max(row[1], c.y1), c.x1)
        flush()
        return lines

    def blocks(self, index: int, *, clip: Rect | None = None) -> list[Block]:
        """Paragraph-like text blocks in content-stream order, like ``get_text("blocks")`` (which, with
        PyMuPDF's default flags, reports no image blocks either).

        Consecutive lines belong to one block while the vertical gap stays below ``BLOCK_GAP_EM`` times the line
        height and the lines overlap horizontally; a jump up the page or sideways starts a new block.
        ``clip`` restricts the text like ``get_text("blocks", clip=)``.
        """
        blocks: list[Block] = []
        group: list[RichLine] = []
        bounds: Rect | None = None

        def close() -> None:
            nonlocal bounds
            if group and bounds is not None:
                blocks.append(Block(bounds, "".join(ln.text.rstrip("\n") + "\n" for ln in group), len(blocks)))
            group.clear()
            bounds = None

        for ln in self.rich_lines(index, clip=clip):
            if group and bounds is not None:
                prev = group[-1].rect
                height = max(prev.height, ln.rect.height)
                gap = ln.rect.y0 - prev.y1
                horizontal_ok = ln.rect.x0 <= bounds.x1 + height and ln.rect.x1 >= bounds.x0 - height
                if not (
                    -0.6 * height <= gap <= BLOCK_GAP_EM * height
                    and ln.rect.y0 >= prev.y0 - 0.6 * height
                    and horizontal_ok
                ):
                    close()
            group.append(ln)
            bounds = ln.rect if bounds is None else bounds.union(ln.rect)
        close()
        return blocks
