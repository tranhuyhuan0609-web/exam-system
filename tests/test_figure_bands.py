"""Uncaptioned figures (reaction schemes, structural formulas) are cropped as whole bands with their labels."""

from app.ai.essay_exam import figure_bands
from app.ai.figures import page_geometry
from app.infrastructure.pdf_engine import PDFEngineAdapter
from tests.pdf_fixtures import PdfSketch, png


def geometry_of(sketch):
    """The geometry the readers see: the page is saved and read back through the PDF engine adapter."""
    with PDFEngineAdapter.open(sketch.tobytes()) as reader:
        return page_geometry(reader, 0)


PROSE = "Xác định cấu tạo của các chất từ A đến M trong sơ đồ sau và viết phương trình phản ứng xảy ra"


def _hexagon(page, cx, cy, r=14):
    pts = [
        (cx + r * dx, cy + r * dy)
        for dx, dy in ((0, -1), (0.87, -0.5), (0.87, 0.5), (0, 1), (-0.87, 0.5), (-0.87, -0.5))
    ]  # noqa: E501
    for a, b in zip(pts, pts[1:] + pts[:1]):  # noqa: B905
        page.line(a, b, width=0.8)


def _scheme_page() -> PdfSketch:
    sketch = PdfSketch()
    page = sketch.new_page(595, 842)
    page.text(60, 100, PROSE, 10)
    # Row 1: molecule -> reagent arrow -> label
    _hexagon(page, 100, 160)
    page.line((130, 160), (200, 160), width=0.8)
    page.text(140, 152, "BnCl", 8)
    page.text(140, 172, "K2CO3, DMF", 8)
    page.text(215, 164, "A", 10)
    # Row 2 (below, no prose between): reagents spread across the line are not question text
    page.text(60, 214, "1, p-TSA", 8)
    page.text(300, 214, "Me2SO4", 8)
    page.line((60, 220), (120, 220), width=0.8)
    page.line((300, 220), (360, 220), width=0.8)
    _hexagon(page, 450, 225)
    # Question text, then a second scheme
    page.text(60, 300, "2. " + PROSE, 10)
    _hexagon(page, 120, 360)
    page.line((150, 360), (220, 360), width=0.8)
    page.text(230, 364, "B", 10)
    return sketch


def _inline_formula_page() -> PdfSketch:
    sketch = PdfSketch()
    page = sketch.new_page(595, 842)
    # A MathType-like formula image on its own line, printed a little off the text line: not a figure.
    page.text(60, 120, "Cho dãy số thực được xác định bởi công thức truy hồi sau", 10)
    page.image(150, 130, 270, 160, png((120, 30), "black"))
    page.text(60, 180, "Chứng minh rằng dãy có giới hạn hữu hạn và tìm giới hạn đó.", 10)
    return sketch


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
