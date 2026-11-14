"""Builders for the PDFs the tests read: a fitz-like sketch API on reportlab, scans on Pillow, pypdf encryption.

PyMuPDF is gone (ADR-010), so tests no longer call ``pymupdf.open()``. Coordinates are PDF points with the origin at
the TOP-LEFT of the page (the adapter's convention); ``text`` takes the BASELINE like fitz ``insert_text``.
Text is DejaVu Sans (the font bundled for stamping), which writes the whole Vietnamese alphabet.
"""

import io
from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.infrastructure.pdf_engine import DEFAULT_STAMP_FONT

FONT = "DejaVuTest"
pdfmetrics.registerFont(TTFont(FONT, str(DEFAULT_STAMP_FONT)))

Colour = tuple[float, float, float]
A4 = (595.0, 842.0)


class SketchPage:
    """One page being drawn. Obtained from ``PdfSketch.new_page``; draw on it until the next page is started."""

    def __init__(self, pdf: canvas.Canvas, number: int, width: float, height: float) -> None:
        self._pdf, self.number, self.width, self.height = pdf, number, width, height

    def text(
        self, x: float, baseline: float, text: str, size: float = 11.0, color: Colour = (0, 0, 0), alpha: float = 1.0
    ) -> None:
        pdf = self._pdf
        pdf.saveState()
        pdf.setFillColorRGB(*color)
        pdf.setFillAlpha(alpha)
        pdf.setFont(FONT, size)
        pdf.drawString(x, self.height - baseline, text)
        pdf.restoreState()

    def lines(self, x: float, top: float, text: str, size: float = 11.0, color: Colour = (0, 0, 0)) -> None:
        """Several lines (split on newlines) with the first line's top at ``top``."""
        for k, line in enumerate(text.split("\n")):
            self.text(x, top + size + k * size * 1.4, line, size, color)

    def rect(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        color: Colour | None = (0, 0, 0),
        fill: Colour | None = None,
        width: float = 1.0,
        stroke_alpha: float = 1.0,
        fill_alpha: float = 1.0,
    ) -> None:
        pdf = self._pdf
        pdf.saveState()
        pdf.setLineWidth(width)
        pdf.setStrokeAlpha(stroke_alpha)
        pdf.setFillAlpha(fill_alpha)
        if color is not None:
            pdf.setStrokeColorRGB(*color)
        if fill is not None:
            pdf.setFillColorRGB(*fill)
        pdf.rect(x0, self.height - y1, x1 - x0, y1 - y0, stroke=int(color is not None), fill=int(fill is not None))
        pdf.restoreState()

    def line(self, start: tuple[float, float], end: tuple[float, float], *, width: float = 1.0) -> None:
        pdf = self._pdf
        pdf.saveState()
        pdf.setLineWidth(width)
        pdf.line(start[0], self.height - start[1], end[0], self.height - end[1])
        pdf.restoreState()

    def circle(
        self,
        cx: float,
        cy: float,
        radius: float,
        *,
        color: Colour | None = None,
        fill: Colour | None = None,
        fill_alpha: float = 1.0,
    ) -> None:
        pdf = self._pdf
        pdf.saveState()
        pdf.setFillAlpha(fill_alpha)
        if color is not None:
            pdf.setStrokeColorRGB(*color)
        if fill is not None:
            pdf.setFillColorRGB(*fill)
        pdf.circle(cx, self.height - cy, radius, stroke=int(color is not None), fill=int(fill is not None))
        pdf.restoreState()

    def image(self, x0: float, y0: float, x1: float, y1: float, data: bytes) -> None:
        self._pdf.drawImage(ImageReader(io.BytesIO(data)), x0, self.height - y1, x1 - x0, y1 - y0)


class PdfSketch:
    """``sketch = PdfSketch(); page = sketch.new_page(); page.text(...); data = sketch.tobytes()``."""

    def __init__(self) -> None:
        self._buf = io.BytesIO()
        self._pdf = canvas.Canvas(self._buf)
        self._pages = 0
        self._open = False

    def new_page(self, width: float = A4[0], height: float = A4[1]) -> SketchPage:
        if self._open:
            self._pdf.showPage()
        self._pdf.setPageSize((width, height))
        self._open = True
        self._pages += 1
        return SketchPage(self._pdf, self._pages - 1, width, height)

    def tobytes(self) -> bytes:
        if self._open:
            self._pdf.showPage()
            self._open = False
            self._pdf.save()
        return self._buf.getvalue()

    def save(self, path: str | Path) -> None:
        Path(path).write_bytes(self.tobytes())


def blank_pdf(pages: int = 1) -> bytes:
    """A document of ``pages`` empty A4 pages."""
    sketch = PdfSketch()
    for _ in range(pages):
        sketch.new_page()
    return sketch.tobytes()


def png(size: tuple[int, int], colour: str | tuple[int, int, int] = "white") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


def scanned_pdf(pages: Sequence[Sequence[str]], *, dpi: int = 110, size: tuple[float, float] = A4) -> bytes:
    """Pages that are only pictures of text, like a scanned paper: each page is a list of text lines."""
    scale = dpi / 72
    font = ImageFont.truetype(str(DEFAULT_STAMP_FONT), round(20 * scale * 1.0))
    images = []
    for lines in pages:
        page = Image.new("RGB", (round(size[0] * scale), round(size[1] * scale)), "white")
        draw = ImageDraw.Draw(page)
        for k, line in enumerate(lines):
            draw.text((60 * scale, (60 + k * 30) * scale), line, fill="black", font=font)
        images.append(page)
    buf = io.BytesIO()
    images[0].save(buf, format="PDF", save_all=True, append_images=images[1:], resolution=dpi)
    return buf.getvalue()


def encrypted(data: bytes, user_password: str, owner_password: str) -> bytes:
    """``data`` protected with AES-256 (needs the ``cryptography`` package, as pypdf does)."""
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.encrypt(user_password=user_password, owner_password=owner_password, algorithm="AES-256")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
