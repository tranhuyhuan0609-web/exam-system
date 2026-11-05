import pymupdf
from PIL import Image, ImageDraw

from app.ai.figures import crop_figure, locate_crop, page_geometry, snap_to_graphics, trim_cut_text
from app.ai.schemas import BoundingBox


def exam_page(tmp_path):
    """A page with question text hugging a figure (like the real Buổi 12 handout)."""
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((60, 300), "Câu 5: dòng chữ ngay sát phía trên hình vẽ, kéo dài sang trái.", fontsize=11)
    page.insert_text((60, 420), "từ từ di chuyển sang trái 4 cm, dòng chữ sát phía dưới hình.", fontsize=11)
    for y in range(330, 400, 12):  # a paragraph running down the left of the figure
        page.insert_text((60, y), "đoạn văn bên trái hình", fontsize=11)
    page.draw_rect(pymupdf.Rect(350, 305, 520, 405), color=(0, 0, 0), width=1.5)  # the figure
    page.insert_text((420, 360), "A", fontsize=12)  # label inside the figure
    page.insert_text((525, 400), "x (cm)", fontsize=9)  # axis label just outside the drawing
    page.draw_circle((300, 360), 200, color=(0.87, 0.92, 0.96), fill=(0.87, 0.92, 0.96), fill_opacity=0.5)  # watermark
    png = tmp_path / "page.png"
    page.get_pixmap(dpi=150).save(png)
    return page_geometry(page), png


def test_snapping_keeps_the_figure_and_its_labels_but_not_question_text(tmp_path):
    geometry, _ = exam_page(tmp_path)
    # A sloppy model box that also covers the neighbouring text lines.
    sloppy = BoundingBox(ymin=360, xmin=560, ymax=530, xmax=910)
    snapped = snap_to_graphics(sloppy, geometry)
    assert snapped is not None
    # Figure spans y 305..405 (of 800) -> 381..506; x 350..520 (+ "x (cm)" label to ~560) -> 583..~935.
    assert 375 <= snapped.ymin <= 385 and 500 <= snapped.ymax <= 512
    assert 578 <= snapped.xmin <= 590
    assert snapped.xmax > 880  # the axis label is kept
    # The watermark circle (light, half transparent) is ignored, so the box did not grow to the page centre.
    assert snapped.xmin > 500


def test_crop_contains_no_neighbouring_text(tmp_path):
    geometry, png = exam_page(tmp_path)
    target = tmp_path / "fig.png"
    assert crop_figure(png, BoundingBox(ymin=360, xmin=560, ymax=530, xmax=910), target, geometry)
    with Image.open(target) as fig:
        width, height = fig.size
    # 150 dpi page = 1250 x 1667 px; the figure is ~170pt x 100pt -> ~355 x 210 px (+ label, + 6 px margins).
    assert 330 <= width <= 470 and 200 <= height <= 240


def test_without_vector_data_partial_text_lines_at_the_border_are_trimmed():
    img = Image.new("RGB", (300, 200), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 280, 6), fill="black")  # bottom of a text line cut by the crop border
    draw.rectangle((40, 30, 260, 170), outline="black", width=3)  # the figure
    draw.rectangle((10, 192, 290, 199), fill="black")  # top of the next text line
    trimmed = trim_cut_text(img, pad=0)
    assert trimmed.size == (221, 141)


def test_locate_crop_finds_an_exact_sub_image():
    page = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(page).rectangle((120, 80, 260, 190), outline="black", width=2)
    ImageDraw.Draw(page).text((150, 120), "R1", fill="black")
    assert locate_crop(page, page.crop((110, 70, 270, 200))) == (110, 70)
    assert locate_crop(page, Image.new("RGB", (50, 50), "red")) is None


def test_no_graphics_means_no_snapping():
    assert snap_to_graphics(BoundingBox(ymin=0, xmin=0, ymax=100, xmax=100), None) is None
