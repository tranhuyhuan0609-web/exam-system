"""Redaction, object removal, page extension and Unicode stamps of the PDF engine adapter (PR C, gaps G2/G3).

Fixtures are built with reportlab (no PyMuPDF): coloured and translucent text, form XObjects, pictures.
"""

import io
from collections.abc import Callable

import pytest
from PIL import Image, ImageChops
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.ai.figures import is_light_colour, render_without_diagram_text, render_without_light_text
from app.infrastructure.pdf_engine import (
    ImageStamp,
    LineStamp,
    PageObject,
    PDFEngineAdapter,
    PDFEngineError,
    Rect,
    TextStamp,
    extend_page_top,
    redact_pdf,
    stamp_pages,
)

W, H = 400.0, 600.0


def _build(draw: Callable[[canvas.Canvas], None], rotate: int = 0) -> bytes:
    """One W x H page; ``draw`` uses reportlab's bottom-left coordinates."""
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=(W, H))
    draw(pdf)
    pdf.showPage()
    pdf.save()
    if not rotate:
        return buf.getvalue()
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(buf.getvalue())))
    writer.pages[0].rotate(rotate)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _text(pdf: canvas.Canvas, x: float, top: float, s: str, rgb=(0, 0, 0), alpha: float = 1.0, size=14) -> None:
    pdf.saveState()
    pdf.setFillColorRGB(*rgb)
    pdf.setFillAlpha(alpha)
    pdf.setFont("Helvetica", size)
    pdf.drawString(x, H - top, s)
    pdf.restoreState()


def _png(colour=(200, 30, 30), size=(40, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


def _ink(image: Image.Image, box: Rect | None = None) -> Rect | None:
    """Bounding box (pixels) of the non-white pixels, optionally inside ``box``."""
    if box is not None:
        image = image.crop((int(box.x0), int(box.y0), int(box.x1), int(box.y1)))
    found = ImageChops.invert(image.convert("L")).point(lambda v: 255 if v > 40 else 0).getbbox()
    return Rect(*found) if found else None


def _watermarked() -> bytes:
    def draw(pdf):
        _text(pdf, 50, 100, "Question text")
        _text(pdf, 50, 300, "WATERMARK", rgb=(0.75, 0.75, 0.75))  # light grey
        _text(pdf, 50, 400, "GHOST", alpha=0.2)  # translucent black
        pdf.rect(50, H - 520, 100, 60)  # a figure: kept

    return _build(draw)


# -- find_text on rotated pages ------------------------------------------------------------------


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_find_text_uses_the_displayed_coordinates_of_a_rotated_page(rotate):
    data = _build(lambda pdf: _text(pdf, 50, 100, "HELLO", size=20), rotate)
    with PDFEngineAdapter.open(data) as pdf:
        (hit,) = pdf.find_text(0, "HELLO")
        ink = _ink(pdf.render(0, dpi=72))
    assert ink is not None
    # The match box and the painted glyphs are the same place on the page as displayed.
    assert hit.intersection(ink.expanded(2)) is not None
    assert hit.overlap_ratio(ink.expanded(3)) > 0.6


def test_find_text_honours_a_crop_box_origin():
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(_build(lambda pdf: _text(pdf, 50, 100, "HELLO")))))
    page = writer.pages[0]
    page.cropbox.lower_left = (20, 30)  # displayed page starts at x=20, y=30 of the media box
    page.mediabox.lower_left = (20, 30)
    out = io.BytesIO()
    writer.write(out)
    with PDFEngineAdapter.open(out.getvalue()) as pdf:
        (hit,) = pdf.find_text(0, "HELLO")
    assert hit.x0 == pytest.approx(30, abs=2)  # 50 - 20
    assert 85 <= hit.y0 <= 100  # top stays measured from the displayed top


# -- page objects and render_without (G3 b) ------------------------------------------------------


def test_page_objects_report_kind_colour_and_alpha():
    with PDFEngineAdapter.open(_watermarked()) as pdf:
        objects = pdf.page_objects(0)
    texts = [o for o in objects if o.kind == "text"]
    assert len(texts) == 3 and any(o.kind == "path" for o in objects)
    light = [o for o in texts if is_light_colour(o.color, o.alpha)]
    assert len(light) == 2  # the grey one and the translucent one
    dark = next(o for o in texts if not is_light_colour(o.color, o.alpha))
    assert 85 <= dark.rect.y0 <= 100 and dark.color == 0


def test_render_without_light_text_keeps_the_rest():
    with PDFEngineAdapter.open(_watermarked()) as pdf:
        plain = pdf.render(0, dpi=72)
        clean = render_without_light_text(pdf, 0, 72)
        assert clean is not None
        assert "WATERMARK" in pdf.text_stream(0)  # the open document is not changed
    assert _ink(plain, Rect(40, 280, 300, 310)) is not None
    assert _ink(clean, Rect(40, 280, 300, 310)) is None  # watermark gone
    assert _ink(clean, Rect(40, 85, 300, 105)) is not None  # question text kept
    assert _ink(clean, Rect(45, 455, 155, 525)) is not None  # figure kept


def test_render_without_diagram_text_drops_only_labels_inside_the_diagram():
    def draw(pdf):
        _text(pdf, 50, 100, "Question")
        pdf.rect(40, H - 400, 200, 150)  # diagram 250..400
        _text(pdf, 100, 320, "OH")

    with PDFEngineAdapter.open(_build(draw)) as pdf:
        image = render_without_diagram_text(pdf, 0, [Rect(40, 250, 240, 400)], dpi=72)
    assert _ink(image, Rect(95, 305, 130, 322)) is None  # label gone
    assert _ink(image, Rect(40, 85, 200, 105)) is not None  # question text kept
    assert _ink(image, Rect(38, 248, 42, 402)) is not None  # the diagram's own lines kept


def test_render_without_light_text_returns_none_when_there_is_none():
    with PDFEngineAdapter.open(_build(lambda pdf: _text(pdf, 50, 100, "dark only"))) as pdf:
        assert render_without_light_text(pdf, 0, 72) is None


def test_render_without_removes_objects_inside_form_xobjects():
    def draw(pdf):
        pdf.beginForm("stamp")
        _text(pdf, 50, 300, "IN A FORM", rgb=(0.9, 0.9, 0.9))
        pdf.endForm()
        pdf.doForm("stamp")
        _text(pdf, 50, 100, "Top level")

    with PDFEngineAdapter.open(_build(draw)) as pdf:
        nested = [o for o in pdf.page_objects(0) if o.depth > 0]
        assert nested and nested[0].kind == "text" and 280 <= nested[0].rect.y1 <= 305
        image, removed = pdf.render_without(0, 72, drop=lambda o: o.kind == "text" and o.depth > 0)
    assert removed == 1
    assert _ink(image, Rect(40, 280, 300, 310)) is None
    assert _ink(image, Rect(40, 85, 300, 105)) is not None


def test_render_without_honours_clip_and_validates():
    with PDFEngineAdapter.open(_watermarked()) as pdf:
        image, _ = pdf.render_without(0, 72, drop=lambda o: False, clip=Rect(0, 0, 100, 50))
        assert image.size == (100, 50)
        with pytest.raises(PDFEngineError):
            pdf.render_without(0, 0, drop=lambda o: False)
        with pytest.raises(PDFEngineError):
            pdf.render_without(5, 72, drop=lambda o: False)


# -- redact_pdf (G3 c) ---------------------------------------------------------------------------


def _header_page(rotate: int = 0) -> bytes:
    def draw(pdf):
        _text(pdf, 20, 20, "PRO: someone@example.com", size=8)
        _text(pdf, 50, 100, "Body stays")
        pdf.drawImage(
            ImageReader(io.BytesIO(_png())),
            20,
            H - 80,
            60,
            40,
        )  # an old logo at top 40..80
        pdf.drawImage(
            ImageReader(io.BytesIO(_png((30, 30, 200)))),
            0,
            H - 600,
            400,
            400,
        )  # a big picture that only grazes the region below: kept
        _text(pdf, 20, 590, "GV. Old Teacher - 0817", size=8)

    return _build(draw, rotate)


def test_redact_pdf_removes_objects_and_paints_white():
    regions = [Rect(0, 0, W, 85), Rect(0, 580, W, H)]
    out = redact_pdf(_header_page(), lambda i, w, h: regions)
    with PDFEngineAdapter.open(out) as pdf:
        text = pdf.text_stream(0)
        kinds = [(o.kind, o.rect) for o in pdf.page_objects(0)]
        image = pdf.render(0, dpi=72)
    assert "PRO:" not in text and "Old Teacher" not in text and "Body stays" in text
    pictures = [r for k, r in kinds if k == "image"]
    assert len(pictures) == 1 and pictures[0].height > 300  # the logo went, the big picture stayed
    assert _ink(image, Rect(0, 0, W, 84)) is None  # the header band is white
    assert _ink(image, Rect(0, 581, W, H)) is None  # painted over the big picture too


def test_redact_pdf_without_fill_and_keeping_images():
    out = redact_pdf(_header_page(), lambda i, w, h: [Rect(0, 0, W, 85)], remove_images=False, fill=None)
    with PDFEngineAdapter.open(out) as pdf:
        assert "PRO:" not in pdf.text_stream(0)
        assert len([o for o in pdf.page_objects(0) if o.kind == "image"]) == 2


@pytest.mark.parametrize("rotate", [90, 180, 270])
def test_redact_pdf_regions_are_displayed_coordinates_on_rotated_pages(rotate):
    data = _build(lambda pdf: (_text(pdf, 50, 100, "KEEP"), _text(pdf, 50, 300, "DROP")), rotate)
    with PDFEngineAdapter.open(data) as pdf:
        (hit,) = pdf.find_text(0, "DROP")
    out = redact_pdf(data, lambda i, w, h: [hit.expanded(3)])
    with PDFEngineAdapter.open(out) as pdf:
        text = pdf.text_stream(0)
        assert _ink(pdf.render(0, dpi=72), hit.expanded(2)) is None
    assert "DROP" not in text and "KEEP" in text


def test_redact_pdf_leaves_pages_without_regions_alone():
    data = _header_page()
    out = redact_pdf(data, lambda i, w, h: [])
    with PDFEngineAdapter.open(out) as pdf:
        assert "PRO:" in pdf.text_stream(0)
    with pytest.raises(PDFEngineError):
        redact_pdf(b"garbage", lambda i, w, h: [])


# -- extend_page_top -----------------------------------------------------------------------------


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_extend_page_top_adds_a_blank_band_above_the_content(rotate):
    data = _build(lambda pdf: _text(pdf, 50, 100, "HELLO", size=20), rotate)
    with PDFEngineAdapter.open(data) as pdf:
        w, h = pdf.page_size(0)
        (before,) = pdf.find_text(0, "HELLO")
    out = extend_page_top(data, 0, 70)
    with PDFEngineAdapter.open(out) as pdf:
        assert pdf.page_size(0) == pytest.approx((w, h + 70))
        (after,) = pdf.find_text(0, "HELLO")
        assert _ink(pdf.render(0, dpi=72), Rect(0, 0, w, 70)) is None
    assert after.y0 == pytest.approx(before.y0 + 70, abs=0.5)
    assert after.x0 == pytest.approx(before.x0, abs=0.5)


def test_extend_page_top_validates():
    data = _build(lambda pdf: None)
    with pytest.raises(PDFEngineError):
        extend_page_top(data, 1, 70)
    with pytest.raises(PDFEngineError):
        extend_page_top(data, 0, 0)


# -- Unicode text, pictures and lines (G2) -------------------------------------------------------


def test_text_stamp_writes_vietnamese_with_the_bundled_font():
    footer = "Hệ thống Luyện thi & Ngân hàng Đề PRO • Trang 1/1"
    out = stamp_pages(_build(lambda pdf: None), lambda i, w, h: [TextStamp.at(40, h - 14, footer, 8)])
    with PDFEngineAdapter.open(out) as pdf:
        assert footer in pdf.text_stream(0)
        (hit,) = pdf.find_text(0, "Ngân hàng Đề")
    assert H - 24 <= hit.y0 and hit.y1 <= H - 10


def test_text_stamp_rejects_a_missing_font_file(tmp_path):
    stamp = TextStamp("x", Rect(0, 0, 100, 20), font=tmp_path / "missing.ttf")
    with pytest.raises(PDFEngineError):
        stamp_pages(_build(lambda pdf: None), lambda *_: [stamp])


def test_image_and_line_stamps(tmp_path):
    logo = tmp_path / "logo.png"
    logo.write_bytes(_png((0, 0, 0), (100, 50)))  # 2:1, placed in a square: centred, 30 pt tall

    def build(i, w, h):
        return [ImageStamp(logo, Rect(40, 12, 100, 72)), LineStamp(40, 100, w - 40, 100, (0, 0, 0), 2.0)]

    out = stamp_pages(_build(lambda pdf: None), build)
    with PDFEngineAdapter.open(out) as pdf:
        image = pdf.render(0, dpi=72)
    logo_ink = _ink(image, Rect(0, 0, W, 90))
    assert logo_ink is not None
    assert (logo_ink.x0, logo_ink.y0, logo_ink.x1, logo_ink.y1) == pytest.approx((40, 27, 100, 57), abs=1.5)
    line_ink = _ink(image, Rect(0, 90, W, 110))
    assert (
        line_ink is not None
        and line_ink.x0 == pytest.approx(40, abs=1.5)
        and line_ink.x1 == pytest.approx(360, abs=1.5)
    )


def test_image_stamp_accepts_bytes_and_stretching():
    stamp = ImageStamp(_png((0, 0, 0), (100, 50)), Rect(40, 12, 100, 72), keep_proportion=False)
    out = stamp_pages(_build(lambda pdf: None), lambda *_: [stamp])
    with PDFEngineAdapter.open(out) as pdf:
        ink = _ink(pdf.render(0, dpi=72))
    assert ink is not None and (ink.x0, ink.y0, ink.x1, ink.y1) == pytest.approx((40, 12, 100, 72), abs=1.5)


# -- reading around figures (G3 a) ---------------------------------------------------------------


def test_blocks_exclude_skips_text_inside_a_figure():
    def draw(pdf):
        _text(pdf, 50, 100, "Question about the figure")
        _text(pdf, 60, 300, "OH")  # a label inside the figure

    with PDFEngineAdapter.open(_build(draw)) as pdf:
        assert "OH" in "".join(b.text for b in pdf.blocks(0))
        texts = "".join(b.text for b in pdf.blocks(0, exclude=[Rect(40, 250, 200, 320)]))
    assert "OH" not in texts and "Question about the figure" in texts


def test_page_object_is_a_plain_value():
    obj = PageObject("text", Rect(0, 0, 1, 1), 0, 255, 0)
    assert obj.kind == "text" and obj.rect.area == 1
