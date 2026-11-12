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

import io
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

PathOrBytes = str | Path | bytes | bytearray | memoryview


class PDFEngineError(Exception):
    """Raised for any failure to open or read a PDF (corrupt, encrypted, closed, bad page index)."""


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
        self._password = password
        try:
            self._pdfium = pdfium.PdfDocument(data, password=password)
        except Exception as exc:  # PdfiumError, ValueError, ...
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

    def text_stream(self, index: int) -> str:
        """Plain text in content-stream order (pdfium text page), without layout reconstruction.

        Fast and low-memory; matches PyMuPDF's ``get_text("text")`` ordering more closely than
        ``text()``, which sorts by position. Lines are separated by ``\n``.
        """
        self._check(index)
        page = self._pdfium[index]
        try:
            textpage = page.get_textpage()
            try:
                return textpage.get_text_range().replace("\r\n", "\n")
            finally:
                textpage.close()
        except Exception as exc:
            raise PDFEngineError(f"Cannot read text of page {index}: {exc}") from exc
        finally:
            page.close()

    def find_text(self, index: int, needle: str, *, match_case: bool = False) -> list[Rect]:
        """Bounding boxes (top-left origin) of every occurrence of ``needle``, like ``page.search_for``.

        One rectangle per occurrence (a match wrapped over two lines yields their union).
        Matching is exact on code points: no Unicode normalisation is applied.
        """
        if not needle:
            return []
        self._check(index)
        page = self._pdfium[index]
        try:
            _, height = page.get_size()
            textpage = page.get_textpage()
            try:
                searcher = textpage.search(needle, match_case=match_case)
                found: list[Rect] = []
                while (match := searcher.get_next()) is not None:
                    start, count = match
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
        with self._plumber_page(index) as page:
            out = []
            for i in page.images or []:
                sw, sh = i.get("srcsize") or (0, 0)
                out.append(ImageBox(_rect_of(i), int(sw or 0), int(sh or 0)))
        return sorted(out, key=lambda b: (b.rect.y0, b.rect.x0))

    def drawings(self, index: int) -> list[Drawing]:
        """Vector path objects (rects, straight lines, curves) with colours and stroke/fill flags."""
        with self._plumber_page(index) as page:
            out: list[Drawing] = []
            for kind, objs in (("rect", page.rects), ("line", page.lines), ("curve", page.curves)):
                for o in objs or []:
                    out.append(
                        Drawing(
                            rect=_rect_of(o),
                            kind=kind,
                            stroke=bool(o.get("stroke")),
                            fill=bool(o.get("fill")),
                            stroke_color=_color(o.get("stroking_color")),
                            fill_color=_color(o.get("non_stroking_color")),
                            linewidth=float(o.get("linewidth") or 0.0),
                        )
                    )
        return sorted(out, key=lambda d: (d.rect.y0, d.rect.x0, d.kind))

    def drawing_rects(self, index: int, *, include_tables: bool = False) -> list[Rect]:
        """Bounding boxes of images and vector objects, by default WITHOUT those inside a detected table.

        This is the input a figure detector feeds to ``merge_intersecting_rects_union_find``.
        """
        tables = [] if include_tables else [t.expanded(4.0) for t in self.table_rects(index)]
        boxes = [i.rect for i in self.images(index)] + [d.rect for d in self.drawings(index)]
        return [b for b in boxes if not any(b.overlap_ratio(t) >= 0.5 for t in tables)]
