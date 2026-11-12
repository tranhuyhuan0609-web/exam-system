"""Tests for the PDF engine adapter. PDFs are built here with pypdfium2's raw API (no PyMuPDF)."""

import ctypes
import io
import random

import pypdfium2 as pdfium
import pypdfium2.raw as raw
import pytest
from PIL import Image

from app.infrastructure.pdf_engine import (
    PDFEngineAdapter,
    PDFEngineError,
    Rect,
    merge_intersecting_rects_union_find,
)

PAGE_W, PAGE_H = 595.0, 842.0


class _Builder:
    """Tiny PDF writer. Callers pass TOP-LEFT coordinates; the builder flips them to PDF space."""

    def __init__(self, width: float = PAGE_W, height: float = PAGE_H) -> None:
        self.doc = pdfium.PdfDocument.new()
        self.page = self.doc.new_page(width, height)
        self.h = height
        self._font = raw.FPDFText_LoadStandardFont(self.doc.raw, b"Helvetica")

    def text(self, x: float, baseline_top: float, s: str, size: float = 12.0) -> None:
        obj = raw.FPDFPageObj_CreateTextObj(self.doc.raw, self._font, size)
        buf = (s + "\0").encode("utf-16-le")
        arr = (ctypes.c_ubyte * len(buf)).from_buffer_copy(buf)
        raw.FPDFText_SetText(obj, ctypes.cast(arr, raw.FPDF_WIDESTRING))
        raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, self.h - baseline_top)
        raw.FPDFPage_InsertObject(self.page.raw, obj)

    def rect(self, x: float, top: float, w: float, h: float, *, fill: bool = False, rgb=(0, 0, 0)) -> None:
        obj = raw.FPDFPageObj_CreateNewRect(x, self.h - top - h, w, h)
        raw.FPDFPageObj_SetStrokeColor(obj, *rgb, 255)
        raw.FPDFPageObj_SetStrokeWidth(obj, 1)
        raw.FPDFPageObj_SetFillColor(obj, *rgb, 255)
        raw.FPDFPath_SetDrawMode(obj, 1 if fill else 0, 0 if fill else 1)
        raw.FPDFPage_InsertObject(self.page.raw, obj)

    def image(self, x: float, top: float, w: float, h: float) -> None:
        pic = pdfium.PdfImage.new(self.doc)
        pic.set_bitmap(pdfium.PdfBitmap.from_pil(Image.new("RGB", (40, 30), (200, 30, 30))))
        pic.set_matrix(pdfium.PdfMatrix().scale(w, h).translate(x, self.h - top - h))
        self.page.insert_obj(pic)

    def build(self) -> bytes:
        raw.FPDFPage_GenerateContent(self.page.raw)
        buf = io.BytesIO()
        self.doc.save(buf)
        self.page.close()
        self.doc.close()
        return buf.getvalue()


@pytest.fixture(scope="module")
def sample_pdf() -> bytes:
    b = _Builder()
    b.text(72, 100, "Alpha heading")
    b.text(72, 700, "Omega footer")
    b.text(300, 400, "SECRET stamp")
    b.rect(72, 150, 100, 60)  # a vector "figure" in two parts, 6 pt apart (no shared edge => not a table)
    b.rect(178, 150, 60, 60)
    b.image(350, 150, 80, 60)
    # ruled 2 x 3 table
    for r in range(2):
        for c in range(3):
            b.rect(72 + c * 100, 500 + r * 40, 100, 40)
            b.text(80 + c * 100, 525 + r * 40, f"r{r}c{c}")
    return b.build()


@pytest.fixture
def pdf(sample_pdf):
    with PDFEngineAdapter.open(sample_pdf) as adapter:
        yield adapter


def _two_page_pdf() -> bytes:
    doc = pdfium.PdfDocument.new()
    doc.new_page(200, 300)
    doc.new_page(400, 100)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# -- open / lifecycle ---------------------------------------------------------------------------


def test_open_from_bytes_and_path(tmp_path, sample_pdf):
    path = tmp_path / "x.pdf"
    path.write_bytes(sample_pdf)
    with PDFEngineAdapter.open(path) as a, PDFEngineAdapter.open(str(path)) as b:
        assert a.page_count == b.page_count == 1
    with PDFEngineAdapter.open(sample_pdf) as c:
        assert c.page_count == 1


def test_invalid_input_raises_adapter_error(tmp_path):
    with pytest.raises(PDFEngineError):
        PDFEngineAdapter.open(b"this is not a pdf")
    with pytest.raises(PDFEngineError):
        PDFEngineAdapter.open(b"")
    with pytest.raises(PDFEngineError):
        PDFEngineAdapter.open(tmp_path / "missing.pdf")


def test_close_is_deterministic_and_idempotent(sample_pdf):
    adapter = PDFEngineAdapter.open(sample_pdf)
    assert adapter.words(0)  # also initialises the pdfplumber side
    adapter.close()
    adapter.close()
    assert adapter.closed
    with pytest.raises(PDFEngineError):
        adapter.page_size(0)
    with pytest.raises(PDFEngineError):
        _ = adapter.page_count
    with pytest.raises(PDFEngineError):
        adapter.words(0)


def test_context_manager_closes(sample_pdf):
    with PDFEngineAdapter.open(sample_pdf) as adapter:
        pass
    assert adapter.closed


def test_page_index_out_of_range(pdf):
    for bad in (-1, 1):
        with pytest.raises(PDFEngineError):
            pdf.page_size(bad)
        with pytest.raises(PDFEngineError):
            pdf.render(bad)
        with pytest.raises(PDFEngineError):
            pdf.lines(bad)


# -- geometry / rendering -----------------------------------------------------------------------


def test_page_count_and_sizes():
    with PDFEngineAdapter.open(_two_page_pdf()) as adapter:
        assert adapter.page_count == 2
        assert adapter.page_size(0) == pytest.approx((200, 300))
        assert adapter.page_size(1) == pytest.approx((400, 100))
        assert adapter.page_rect(1) == Rect(0, 0, pytest.approx(400), pytest.approx(100))


@pytest.mark.parametrize("dpi", [72, 150, 300])
def test_render_page_size_matches_dpi(pdf, dpi):
    image = pdf.render(0, dpi)
    assert image.mode == "RGB"
    assert abs(image.width - PAGE_W * dpi / 72) <= 1
    assert abs(image.height - PAGE_H * dpi / 72) <= 1


def test_render_clip_matches_clip_rect(pdf):
    clip = Rect(100, 200, 300, 260)
    image = pdf.render(0, 144, clip)
    assert abs(image.width - clip.width * 2) <= 1
    assert abs(image.height - clip.height * 2) <= 1


def test_render_clip_is_top_left_origin(pdf):
    # The heading sits near the top: clipping the top band must contain dark pixels, the bottom band of
    # an empty zone must not.
    top = pdf.render(0, 72, Rect(60, 80, 250, 110)).convert("L")
    empty = pdf.render(0, 72, Rect(60, 760, 250, 800)).convert("L")
    assert top.getextrema()[0] < 100
    assert empty.getextrema()[0] > 250


def test_render_clip_outside_page_raises(pdf):
    with pytest.raises(PDFEngineError):
        pdf.render(0, 72, Rect(1000, 1000, 1100, 1100))
    with pytest.raises(PDFEngineError):
        pdf.render(0, 0)


def test_render_png_decodes(pdf):
    data = pdf.render_png(0, 72, Rect(0, 0, 100, 50))
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert Image.open(io.BytesIO(data)).size == pdf.render(0, 72, Rect(0, 0, 100, 50)).size


# -- text ---------------------------------------------------------------------------------------


def test_words_have_top_left_boxes_inside_page(pdf):
    words = {w.text: w for w in pdf.words(0)}
    assert {"Alpha", "heading", "Omega", "footer", "SECRET", "stamp"} <= set(words)
    page = Rect(0, 0, PAGE_W, PAGE_H)
    for w in words.values():
        assert page.contains(w.rect)
        assert w.rect.x0 < w.rect.x1 and w.rect.y0 < w.rect.y1
    assert words["Alpha"].rect.y0 < words["Omega"].rect.y0  # lower on the page => larger y
    assert words["Alpha"].rect.y1 < 120  # heading placed at y=100 from the top
    assert words["Omega"].rect.y0 > 680
    assert words["Alpha"].rect.x0 < words["heading"].rect.x0


def test_lines_in_reading_order(pdf):
    lines = pdf.lines(0)
    texts = [ln.text for ln in lines]
    assert "Alpha heading" in texts and "Omega footer" in texts
    assert texts.index("Alpha heading") < texts.index("SECRET stamp") < texts.index("Omega footer")
    ys = [ln.rect.y0 for ln in lines]
    assert ys == sorted(ys)


def test_chars_expose_size_and_colour(pdf):
    chars = [c for c in pdf.chars(0) if c.text == "A"]
    assert chars and chars[0].size == pytest.approx(12.0, abs=0.5)
    assert chars[0].fontname
    assert chars[0].rect.height > 0


def test_text_excluding_region(pdf):
    secret = next(w for w in pdf.words(0) if w.text == "SECRET").rect
    assert "SECRET" in pdf.text(0)
    region = Rect(secret.x0 - 5, secret.y0 - 5, secret.x1 + 200, secret.y1 + 5)
    filtered = pdf.text(0, exclude=[region])
    assert "SECRET" not in filtered and "stamp" not in filtered
    assert "Alpha heading" in filtered and "Omega footer" in filtered
    assert "SECRET" not in {w.text for w in pdf.words(0, exclude=[region])}
    assert "SECRET" not in "".join(c.text for c in pdf.chars(0, exclude=[region]))
    assert all(ln.text != "SECRET stamp" for ln in pdf.lines(0, exclude=[region]))


def test_text_clip_keeps_only_region(pdf):
    clipped = pdf.text(0, clip=Rect(0, 60, PAGE_W, 130))
    assert clipped.strip() == "Alpha heading"
    assert {w.text for w in pdf.words(0, clip=Rect(0, 60, PAGE_W, 130))} == {"Alpha", "heading"}


def test_text_stream_in_content_order(pdf):
    stream = pdf.text_stream(0)
    assert stream.index("Alpha heading") < stream.index("Omega footer")
    assert "\r" not in stream


def test_find_text_returns_top_left_rects(pdf):
    (hit,) = pdf.find_text(0, "Omega footer")
    assert hit.y0 > 680 and hit.y1 < PAGE_H and hit.x0 == pytest.approx(72, abs=2)
    (top,) = pdf.find_text(0, "alpha")  # case-insensitive by default
    assert top.y1 < hit.y0
    assert pdf.find_text(0, "alpha", match_case=True) == []
    assert pdf.find_text(0, "nonexistent") == []
    assert pdf.find_text(0, "") == []


def test_lone_rectangle_is_not_a_table():
    b = _Builder()
    b.rect(50, 50, 100, 60)
    with PDFEngineAdapter.open(b.build()) as adapter:
        assert adapter.tables(0) == []


# -- structure ----------------------------------------------------------------------------------


def test_find_tables_ruled_2x3(pdf):
    tables = pdf.tables(0)
    assert len(tables) == 1
    table = tables[0]
    assert len(table.cells) == 6
    assert table.rows == (("r0c0", "r0c1", "r0c2"), ("r1c0", "r1c1", "r1c2"))
    assert table.rect.x0 == pytest.approx(72, abs=1) and table.rect.x1 == pytest.approx(372, abs=1)
    assert table.rect.y0 == pytest.approx(500, abs=1) and table.rect.y1 == pytest.approx(580, abs=1)
    assert pdf.table_rects(0) == [table.rect]


def test_no_tables_on_blank_page():
    with PDFEngineAdapter.open(_two_page_pdf()) as adapter:
        assert adapter.tables(0) == []


def test_images_have_top_left_bbox(pdf):
    images = pdf.images(0)
    assert len(images) == 1
    r = images[0].rect
    assert (r.x0, r.y0, r.x1, r.y1) == pytest.approx((350, 150, 430, 210), abs=1)
    assert (images[0].src_width, images[0].src_height) == (40, 30)


def test_drawings_report_rects_with_flags(pdf):
    drawings = pdf.drawings(0)
    assert len(drawings) == 8  # two figure rects + six table cells
    assert all(d.kind in {"rect", "line", "curve"} for d in drawings)
    figure = next(
        d for d in drawings if d.rect.x0 == pytest.approx(72, abs=1) and d.rect.y0 == pytest.approx(150, abs=1)
    )
    assert figure.kind == "rect" and figure.stroke and not figure.fill
    assert figure.rect.width == pytest.approx(100, abs=1) and figure.rect.height == pytest.approx(60, abs=1)
    assert figure.stroke_color is not None


def test_filled_drawing_colour():
    b = _Builder()
    b.rect(50, 50, 80, 40, fill=True, rgb=(255, 0, 0))
    with PDFEngineAdapter.open(b.build()) as adapter:
        (d,) = adapter.drawings(0)
        assert d.fill and d.fill_color == pytest.approx((1.0, 0.0, 0.0))


def test_drawing_rects_exclude_table_and_feed_union_find(pdf):
    boxes = pdf.drawing_rects(0)
    assert all(not (b.y0 >= 495 and b.y1 <= 585) for b in boxes)  # table cells are not "figures"
    assert len(pdf.drawing_rects(0, include_tables=True)) == len(boxes) + 6
    merged = PDFEngineAdapter.merge_intersecting_rects_union_find(boxes, pad=4.0)
    # the two vector rects fuse; the picture stays separate
    assert len(merged) == 2
    fused = merged[0]
    assert (fused.x0, fused.y0) == pytest.approx((72, 150), abs=1)
    assert (fused.x1, fused.y1) == pytest.approx((238, 210), abs=1)


# -- union-find ---------------------------------------------------------------------------------


def test_union_find_empty_and_single():
    assert merge_intersecting_rects_union_find([]) == []
    r = Rect(0, 0, 10, 10)
    assert merge_intersecting_rects_union_find([r]) == [r]


def test_union_find_overlapping_merge():
    out = merge_intersecting_rects_union_find([Rect(0, 0, 10, 10), Rect(5, 5, 20, 20)], pad=0)
    assert out == [Rect(0, 0, 20, 20)]


def test_union_find_chain_merges_into_one():
    chain = [Rect(i * 10, 0, i * 10 + 12, 10) for i in range(6)]  # each overlaps only its neighbour
    assert merge_intersecting_rects_union_find(chain, pad=0) == [Rect(0, 0, 62, 10)]


def test_union_find_disjoint_stay_apart():
    a, b = Rect(0, 0, 10, 10), Rect(100, 100, 110, 110)
    assert merge_intersecting_rects_union_find([b, a], pad=5) == [a, b]


def test_union_find_padding_decides_merging_not_size():
    a, b = Rect(0, 0, 10, 10), Rect(18, 0, 28, 10)  # 8 pt apart
    assert len(merge_intersecting_rects_union_find([a, b], pad=3)) == 2
    assert merge_intersecting_rects_union_find([a, b], pad=4) == [Rect(0, 0, 28, 10)]  # unpadded union


def test_union_find_fixpoint_second_round():
    # padded A touches padded B only after neither moved; C bridges via the merged box
    rects = [Rect(0, 0, 10, 10), Rect(14, 0, 24, 10), Rect(28, 0, 38, 10)]
    assert merge_intersecting_rects_union_find(rects, pad=2) == [Rect(0, 0, 38, 10)]


def test_union_find_order_independent():
    rects = [
        Rect(0, 0, 20, 20),
        Rect(15, 15, 40, 40),
        Rect(38, 0, 60, 18),
        Rect(200, 200, 220, 230),
        Rect(0, 100, 5, 105),
    ]
    expected = merge_intersecting_rects_union_find(rects, pad=2)
    rng = random.Random(7)
    for _ in range(20):
        shuffled = rects[:]
        rng.shuffle(shuffled)
        assert merge_intersecting_rects_union_find(shuffled, pad=2) == expected
    assert len(expected) == 3
