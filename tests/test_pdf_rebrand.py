"""Branded exam PDFs without PyMuPDF (PR C, G1+G2+G3): exam_pdf.rebrand_pdf_preserve_exam_title and the
UniversalExamAndMaterialIngestor PDF paths. Source exams are built with reportlab and the bundled DejaVu font."""

import io
from collections.abc import Callable

import pytest
from PIL import Image, ImageChops
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.application.exam_pdf import rebrand_pdf_preserve_exam_title
from app.infrastructure.pdf_engine import DEFAULT_STAMP_FONT, PDFEngineAdapter, Rect
from universal_ingestor import UniversalExamAndMaterialIngestor

W, H = 595.0, 842.0
pdfmetrics.registerFont(TTFont("TestVN", str(DEFAULT_STAMP_FONT)))


def _png(colour=(200, 30, 30), size=(40, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


def _exam(pages: list[Callable[[canvas.Canvas], None]]) -> bytes:
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=(W, H))
    for draw in pages:
        pdf.setFont("TestVN", 11)
        draw(pdf)
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


def _at(pdf: canvas.Canvas, x: float, top: float, text: str) -> None:
    pdf.drawString(x, H - top, text)


def _official_first_page(pdf):
    pdf.drawImage(ImageReader(io.BytesIO(_png())), 40, H - 90, 80, 50)  # old school logo, top left
    _at(pdf, 40, 105, "SỞ GD&ĐT CŨ")
    _at(pdf, 330, 60, "KỲ THI TỐT NGHIỆP THPT 2025")
    _at(pdf, 330, 160, "ĐỀ CHÍNH THỨC")
    _at(pdf, 40, 200, "Câu 1. Nội dung câu hỏi")
    _at(pdf, 40, 820, "GV. Phan Nhật Linh - SĐT: 0817 098 716")


def _plain_page(pdf):
    _at(pdf, 40, 20, "PRO: someone@example.com")
    _at(pdf, 40, 200, "Câu 2. Tiếp theo")
    _at(pdf, 40, 820, "GV. Phan Nhật Linh - SĐT: 0817 098 716")


def _worksheet_first_page(pdf):
    _at(pdf, 40, 40, "Câu 1. Bài tập bắt đầu ngay đầu trang")


def _ink(image: Image.Image, box: Rect) -> bool:
    crop = image.crop((int(box.x0), int(box.y0), int(box.x1), int(box.y1)))
    return ImageChops.invert(crop.convert("L")).point(lambda v: 255 if v > 40 else 0).getbbox() is not None


@pytest.fixture
def logo(tmp_path):
    path = tmp_path / "logo.png"
    path.write_bytes(_png((10, 40, 160), (120, 120)))
    return path


def test_official_exam_keeps_its_title_and_gets_the_new_logo_and_footer(tmp_path, logo):
    source = tmp_path / "in.pdf"
    source.write_bytes(_exam([_official_first_page, _plain_page]))
    out = rebrand_pdf_preserve_exam_title(source, tmp_path / "out.pdf", logo, brand_footer="Hệ thống H2Edu")
    with PDFEngineAdapter.open(out) as pdf:
        assert pdf.page_count == 2 and pdf.page_size(0) == pytest.approx((W, H))
        first, second = pdf.text_stream(0), pdf.text_stream(1)
        pictures = [o for o in pdf.page_objects(0) if o.kind == "image"]
        assert pdf.find_text(0, "KỲ THI TỐT NGHIỆP")  # the exam title on the right stays
    assert "SỞ GD&ĐT CŨ" not in first  # old unit in the left corner is gone
    assert "Phan Nhật Linh" not in first and "0817" not in second and "PRO:" not in second
    assert "ĐỀ CHÍNH THỨC" in first and "Câu 1. Nội dung câu hỏi" in first and "Câu 2. Tiếp theo" in second
    assert "Hệ thống H2Edu • Trang 1/2" in first and "Hệ thống H2Edu • Trang 2/2" in second
    assert len(pictures) == 1  # old logo removed, the new one placed in the cleared corner
    assert pictures[0].rect.x1 < 330 and pictures[0].rect.y0 >= 34


def test_worksheet_without_header_gets_a_70pt_banner(tmp_path, logo):
    source = tmp_path / "in.pdf"
    source.write_bytes(_exam([_worksheet_first_page, _plain_page]))
    out = rebrand_pdf_preserve_exam_title(source, tmp_path / "out.pdf", logo, brand_footer="Hệ thống H2Edu")
    with PDFEngineAdapter.open(out) as pdf:
        assert pdf.page_size(0) == pytest.approx((W, H + 70)) and pdf.page_size(1) == pytest.approx((W, H))
        (question,) = pdf.find_text(0, "Câu 1. Bài tập")
        assert pdf.find_text(0, "HỆ THỐNG H2EDU")  # banner text, upper-cased, Vietnamese glyphs
        image = pdf.render(0, dpi=72)
    assert 100 <= question.y0 <= 112  # moved down by 70 pt
    assert _ink(image, Rect(40, 12, 90, 62))  # logo
    assert _ink(image, Rect(40, 66, W - 40, 70))  # separator line


def test_rebrand_without_logo(tmp_path):
    source = tmp_path / "in.pdf"
    source.write_bytes(_exam([_official_first_page]))
    out = rebrand_pdf_preserve_exam_title(source, tmp_path / "out.pdf", None)
    with PDFEngineAdapter.open(out) as pdf:
        assert not [o for o in pdf.page_objects(0) if o.kind == "image"]
        assert "Trang 1/1" in pdf.text_stream(0)


# -- UniversalExamAndMaterialIngestor -------------------------------------------------------------


@pytest.fixture
def ingestor(tmp_path, logo, monkeypatch):
    monkeypatch.chdir(tmp_path)  # it writes app/static/docs relative to the working directory
    return UniversalExamAndMaterialIngestor(str(logo), str(tmp_path / "storage"), "Hệ thống H2Edu")


def test_ingestor_rebrands_an_official_exam_and_cuts_a_preview(tmp_path, ingestor):
    source = tmp_path / "exam.pdf"
    source.write_bytes(_exam([_official_first_page, _plain_page]))
    paths = ingestor.rebrand_pdf_smart(str(source), "d1")
    with PDFEngineAdapter.open(paths["branded_pdf"]) as pdf:
        assert pdf.page_count == 2
        assert "Hệ thống H2Edu • Trang 2/2" in pdf.text_stream(1)
        assert "PRO:" not in pdf.text_stream(1) and "SỞ GD&ĐT CŨ" not in pdf.text_stream(0)
    with PDFEngineAdapter.open(paths["preview_pdf"]) as pdf:
        assert pdf.page_count == 1


def test_ingestor_rebrands_a_worksheet_with_an_80pt_banner(tmp_path, ingestor):
    source = tmp_path / "sheet.pdf"
    source.write_bytes(_exam([_worksheet_first_page]))
    paths = ingestor.rebrand_pdf_smart(str(source), "d2", is_pro=True)
    assert paths["preview_pdf"] is None  # one page: nothing to preview
    with PDFEngineAdapter.open(paths["branded_pdf"]) as pdf:
        assert pdf.page_size(0) == pytest.approx((W, H + 80))
        (question,) = pdf.find_text(0, "Câu 1. Bài tập")
    assert 110 <= question.y0 <= 122


def test_ingestor_detects_the_document_type(tmp_path, ingestor):
    official, sheet = tmp_path / "a.pdf", tmp_path / "b.pdf"
    official.write_bytes(_exam([_official_first_page]))
    sheet.write_bytes(_exam([_worksheet_first_page]))
    assert ingestor.detect_document_type(str(official)) == "OFFICIAL_EXAM"
    assert ingestor.detect_document_type(str(sheet)) == "WORKSHEET_NO_HEADER"


def test_ingestor_tags_figures_and_drops_their_labels(tmp_path, ingestor):
    def page(pdf):
        _at(pdf, 40, 150, "Câu 1. Cho mạch điện như hình")
        pdf.rect(100, H - 400, 200, 150)  # a figure from 250 to 400 (top-left)
        pdf.line(100, H - 300, 300, H - 300)
        _at(pdf, 150, 320, "R1")  # label inside the figure
        _at(pdf, 40, 450, "Câu 2. Sau hình")

    source = tmp_path / "fig.pdf"
    source.write_bytes(_exam([page]))
    text, figures = ingestor.extract_text_with_embedded_figure_tags(str(source), "d3")
    assert list(figures) == ["[[FIG_P1_0]]"]
    assert Image.open(figures["[[FIG_P1_0]]"]).size[0] > 800  # 300 dpi crop
    lines = text.splitlines()
    assert lines == ["Câu 1. Cho mạch điện như hình", "[[FIG_P1_0]]", "Câu 2. Sau hình"]


def test_ingestor_vietjack_footer_is_replaced(tmp_path, ingestor):
    def page(pdf):
        _at(pdf, 40, 100, "Dạng 1: Công thức Bayes")
        _at(pdf, 40, 130, "Phương pháp: áp dụng công thức")
        _at(pdf, 40, 820, "GV. Phan Nhật Linh - 0817 098 716")

    source = tmp_path / "Chuyen de.pdf"
    source.write_bytes(_exam([page, page]))
    pages, _, _ = ingestor.parse_vietjack_study_guide(str(source))
    assert pages == 2
    branded = tmp_path / "app" / "static" / "docs" / "Chuyen_de_branded.pdf"
    with PDFEngineAdapter.open(branded) as pdf:
        text = pdf.text_stream(1)
    assert "Phan Nhật Linh" not in text
    assert "H2Edu • Ngân hàng Tài liệu & Luyện thi THPTQG — Trang 2/2" in text
