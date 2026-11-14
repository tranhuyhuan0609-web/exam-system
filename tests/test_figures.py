from PIL import Image, ImageDraw

from app.ai.figures import crop_figure, locate_crop, page_geometry, snap_to_graphics, trim_cut_text
from app.ai.schemas import BoundingBox
from app.infrastructure.pdf_engine import PDFEngineAdapter
from tests.pdf_fixtures import PdfSketch, png


def geometry_of(sketch, page):
    """The geometry the readers see: the page is saved and read back through the PDF engine adapter."""
    with PDFEngineAdapter.open(sketch.tobytes()) as reader:
        return page_geometry(reader, page.number)


def new_page(width=600, height=800):
    sketch = PdfSketch()
    return sketch, sketch.new_page(width, height)


def exam_page(tmp_path):
    """A page with question text hugging a figure (like the real Buổi 12 handout)."""
    sketch, page = new_page()
    page.text(60, 300, "Câu 5: dòng chữ ngay sát phía trên hình vẽ, kéo dài sang trái.", 11)
    page.text(60, 420, "từ từ di chuyển sang trái 4 cm, dòng chữ sát phía dưới hình.", 11)
    for y in range(330, 400, 12):  # a paragraph running down the left of the figure
        page.text(60, y, "đoạn văn bên trái hình", 11)
    page.rect(350, 305, 520, 405, width=1.5)  # the figure
    page.text(420, 360, "A", 12)  # label inside the figure
    page.text(525, 400, "x (cm)", 9)  # axis label just outside the drawing
    page.circle(300, 360, 200, color=(0.87, 0.92, 0.96), fill=(0.87, 0.92, 0.96), fill_alpha=0.5)  # watermark
    png = tmp_path / "page.png"
    with PDFEngineAdapter.open(sketch.tobytes()) as reader:
        reader.render(0, dpi=150).save(png)
    return geometry_of(sketch, page), png


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


def test_watermark_text_is_flagged_and_not_a_label():
    sketch, page = new_page()
    page.text(60, 100, "dark question text", 12)
    page.text(60, 300, "WATERMARK", 20, (0.85, 0.85, 0.85))
    geometry = geometry_of(sketch, page)
    assert geometry.has_light_text
    assert len(geometry.lines) == 1  # only the dark line; the watermark is not a label candidate


def test_page_without_watermark_is_not_flagged():
    sketch, page = new_page()
    page.text(60, 100, "dark question text", 12)
    assert not geometry_of(sketch, page).has_light_text


def test_translucent_and_light_shapes_are_not_figure_parts():
    sketch, page = new_page()
    page.rect(100, 100, 200, 200, width=1.5)  # a real figure part
    page.rect(300, 100, 400, 200, fill=(0, 0, 0), stroke_alpha=0.2, fill_alpha=0.2)  # see-through watermark shape
    page.rect(100, 300, 200, 400, color=(0.9, 0.9, 0.9), fill=(0.9, 0.9, 0.9))  # light tint
    geometry = geometry_of(sketch, page)
    assert len(geometry.graphics) == 1
    box = geometry.graphics[0]
    assert (box.xmin, box.xmax) == (167, 333)  # x 100..200 of 600


def test_embedded_images_are_graphics_and_tiny_dummies_are_ignored():

    sketch, page = new_page()
    page.image(100, 100, 260, 220, png((80, 60), "black"))
    page.image(300, 100, 310, 110, png((4, 4), "black"))  # inline-equation placeholder
    geometry = geometry_of(sketch, page)
    assert len(geometry.images) == 1
    assert geometry.images == geometry.graphics


def test_page_sized_frames_are_not_graphics():
    sketch, page = new_page()
    page.rect(10, 10, 590, 790)  # page border
    page.rect(100, 100, 200, 200)
    assert len(geometry_of(sketch, page).graphics) == 1


def test_unlabeled_diagrams_are_clustered_drawings_of_a_sensible_size():
    from app.ai.figures import extract_unlabeled_diagrams

    sketch, page = new_page()
    for x in (100, 157, 214):  # a circuit-like row of boxes 2 pt apart
        page.rect(x, 300, x + 55, 400)
    page.line((50, 700), (60, 700))  # a short dash: smaller than the cluster tolerance
    with PDFEngineAdapter.open(sketch.tobytes()) as reader:
        boxes = extract_unlabeled_diagrams(reader, 0)
    assert len(boxes) == 1
    assert (boxes[0].xmin, boxes[0].xmax) == (167, 448)  # x 100..269 of 600
    assert (boxes[0].ymin, boxes[0].ymax) == (375, 500)  # y 300..400 of 800
