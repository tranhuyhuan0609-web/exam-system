"""PDF writers of the engine adapter (PR C, gaps G1/G2): slice, merge, stamp. Fixtures are made with reportlab."""

import io

import pytest
from PIL import Image, ImageChops
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from app.infrastructure.pdf_engine import (
    PDFEngineAdapter,
    PDFEngineError,
    Rect,
    TextStamp,
    merge_pdfs,
    slice_pdf,
    stamp_pages,
)


def _pdf(texts: list[str], size: tuple[float, float] = (595.0, 842.0), rotate: int = 0) -> bytes:
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=size)
    for text in texts:
        pdf.drawString(72, size[1] - 100, text)
        pdf.showPage()
    pdf.save()
    if not rotate:
        return buf.getvalue()
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(buf.getvalue())))
    for page in writer.pages:
        page.rotate(rotate)  # /Rotate on an unchanged media box, as scanners and "landscape" exports do
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _texts(data: bytes) -> list[str]:
    with PDFEngineAdapter.open(data) as pdf:
        return [pdf.text_stream(i).strip() for i in range(pdf.page_count)]


def test_slice_keeps_the_requested_pages_only():
    out = slice_pdf(_pdf(["A", "B", "C", "D"]), 1, 2)
    assert _texts(out) == ["B", "C"]


@pytest.mark.parametrize(("first", "last"), [(-1, 1), (2, 1), (0, 4)])
def test_slice_rejects_a_bad_range(first, last):
    with pytest.raises(PDFEngineError):
        slice_pdf(_pdf(["A", "B", "C", "D"]), first, last)


def test_slice_rejects_garbage():
    with pytest.raises(PDFEngineError):
        slice_pdf(b"not a pdf", 0, 0)


def test_merge_joins_documents_and_reports_first_pages():
    out, starts = merge_pdfs([_pdf(["cover"]), _pdf(["p1", "p2"]), _pdf(["q1"])])
    assert _texts(out) == ["cover", "p1", "p2", "q1"]
    assert starts == [1, 2, 4]


def test_merge_errors_become_engine_errors():
    with pytest.raises(PDFEngineError):
        merge_pdfs([_pdf(["A"]), b"broken"])
    with pytest.raises(PDFEngineError):
        merge_pdfs([])


def test_stamp_writes_text_at_the_requested_top_left_position():
    banner = TextStamp("BAN XEM THU", Rect(16, 800, 579, 820), 9.0, align="center", background=(1, 1, 1))
    out = stamp_pages(_pdf(["body"]), lambda i, w, h: [banner])
    with PDFEngineAdapter.open(out) as pdf:
        assert "body" in pdf.text_stream(0)
        (hit,) = pdf.find_text(0, "BAN XEM THU")
    assert 800 <= hit.y0 and hit.y1 <= 820
    assert abs((hit.x0 + hit.x1) / 2 - 297.5) < 3  # centred in the box


def test_stamp_per_page_and_alignment():
    def build(index, width, height):
        return [] if index == 0 else [TextStamp(f"page {index}", Rect(16, 4, width - 16, 18), 7.5, align="right")]

    out = stamp_pages(_pdf(["a", "b"]), build)
    with PDFEngineAdapter.open(out) as pdf:
        assert "page" not in pdf.text_stream(0)
        (hit,) = pdf.find_text(1, "page 1")
    assert hit.x1 > 560 and hit.y1 < 20  # right-aligned at the top edge


def test_stamp_shrinks_text_that_is_too_wide():
    stamp = TextStamp("x" * 30, Rect(10, 10, 110, 30), 12.0)
    out = stamp_pages(_pdf(["a"]), lambda *_: [stamp])
    with PDFEngineAdapter.open(out) as pdf:
        (hit,) = pdf.find_text(0, "x" * 30)
    assert hit.x1 <= 111


def _painted_box(image: Image.Image) -> Rect:
    """Bounding box (points at 72 dpi) of the non-white pixels."""
    left, top, right, bottom = ImageChops.invert(image.convert("L")).getbbox()
    return Rect(left, top, right, bottom)


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_stamp_lands_in_the_displayed_top_left_corner_of_a_rotated_page(rotate):
    seen = []

    def build(index, width, height):
        seen.append((width, height))
        return [TextStamp("", Rect(10, 10, 110, 30), background=(0.0, 0.0, 0.0))]  # a black box marks the spot

    out = stamp_pages(_pdf([""], size=(400.0, 600.0), rotate=rotate), build)
    assert seen == [(600.0, 400.0) if rotate in (90, 270) else (400.0, 600.0)]
    with PDFEngineAdapter.open(out) as pdf:
        box = _painted_box(pdf.render(0, dpi=72))  # what a viewer shows, /Rotate applied
    assert (box.x0, box.y0, box.x1, box.y1) == pytest.approx((10, 10, 110, 30), abs=1.5)


def test_stamp_rejects_garbage():
    with pytest.raises(PDFEngineError):
        stamp_pages(b"nope", lambda *_: [])
