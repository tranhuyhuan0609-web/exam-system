"""Uncaptioned figures (reaction schemes, structural formulas) are cropped as whole bands with their labels."""

import io

import pymupdf
from PIL import Image

from app.ai.essay_exam import figure_bands
from app.ai.figures import page_geometry
from app.infrastructure.pdf_engine import PDFEngineAdapter


def geometry_of(page):
    """The geometry the readers see: the page is saved and read back through the PDF engine adapter."""
    with PDFEngineAdapter.open(page.parent.tobytes()) as reader:
        return page_geometry(reader, page.number)

PROSE = "Xác định cấu tạo của các chất từ A đến M trong sơ đồ sau và viết phương trình phản ứng xảy ra"


def _hexagon(page, cx, cy, r=14):
    pts = [(cx + r * dx, cy + r * dy) for dx, dy in ((0, -1), (0.87, -0.5), (0.87, 0.5), (0, 1), (-0.87, 0.5), (-0.87, -0.5))]  # noqa: E501
    for a, b in zip(pts, pts[1:] + pts[:1]):  # noqa: B905
        page.draw_line(a, b, width=0.8)


def _scheme_page() -> pymupdf.Page:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 100), PROSE, fontsize=10)
    # Row 1: molecule -> reagent arrow -> label
    _hexagon(page, 100, 160)
    page.draw_line((130, 160), (200, 160), width=0.8)
    page.insert_text((140, 152), "BnCl", fontsize=8)
    page.insert_text((140, 172), "K2CO3, DMF", fontsize=8)
    page.insert_text((215, 164), "A", fontsize=10)
    # Row 2 (below, no prose between): reagents spread across the line are not question text
    page.insert_text((60, 214), "1, p-TSA", fontsize=8)
    page.insert_text((300, 214), "Me2SO4", fontsize=8)
    page.draw_line((60, 220), (120, 220), width=0.8)
    page.draw_line((300, 220), (360, 220), width=0.8)
    _hexagon(page, 450, 225)
    # Question text, then a second scheme
    page.insert_text((60, 300), "2. " + PROSE, fontsize=10)
    _hexagon(page, 120, 360)
    page.draw_line((150, 360), (220, 360), width=0.8)
    page.insert_text((230, 364), "B", fontsize=10)
    return page


def _inline_formula_page() -> pymupdf.Page:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    buf = io.BytesIO()
    Image.new("RGB", (120, 30), "black").save(buf, format="PNG")
    # A MathType-like formula image on its own line, printed a little off the text line: not a figure.
    page.insert_text((60, 120), "Cho dãy số thực được xác định bởi công thức truy hồi sau", fontsize=10)
    page.insert_image(pymupdf.Rect(150, 130, 270, 160), stream=buf.getvalue())
    page.insert_text((60, 180), "Chứng minh rằng dãy có giới hạn hữu hạn và tìm giới hạn đó.", fontsize=10)
    return page


def test_scheme_rows_join_into_one_band_with_labels_and_prose_splits_bands():
    bands = figure_bands(geometry_of(_scheme_page()), 0, 1000)
    assert len(bands) == 2
    first, second = bands
    # Row 1 and row 2 form one figure; reagents and the label "A" are inside it.
    assert first.ymin <= 160 / 842 * 1000 - 14 and first.ymax >= 225 / 842 * 1000
    assert first.xmin <= 60 / 595 * 1000 + 2 and first.xmax >= 225 / 595 * 1000
    # The question line between the schemes is in neither band.
    line_y = 300 / 842 * 1000
    assert first.ymax < line_y and second.ymin > line_y - 5
    assert second.xmax >= 234 / 595 * 1000  # label "B" (printed from x=230)


def test_small_formula_images_are_not_figures():
    assert figure_bands(geometry_of(_inline_formula_page()), 0, 1000) == []
