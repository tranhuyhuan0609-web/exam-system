"""Cutting a bundle of exams (one PDF, many "Đề số…" / "Mã đề…") into one library document per exam."""

import asyncio
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.infrastructure.storage import get_bronze_storage, get_storage
from app.models import ImportJob, LibraryDocument, LibraryStatus
from app.pipeline.adapters.drive import FOLDER, DriveFile
from app.pipeline.adapters.memory import MemoryRawStore
from app.pipeline.domain.exam_split import Segment, find_exams
from app.pipeline.use_cases.drive_sync import DriveSource, sync_drive
from app.pipeline.use_cases.library_split import page_texts


@pytest.mark.parametrize(
    ("pages", "expected"),
    [
        (  # cover page left out; numbered exams, then their answers
            ["TUYỂN TẬP ĐỀ\nMục lục", "SỞ GD&ĐT\nĐỀ SỐ 1\nCâu 1", "Câu 20", "ĐỀ SỐ 02\nCâu 1", "ĐÁP ÁN ĐỀ SỐ 1\n1A"],
            [("Đề số 1", 1, 2), ("Đề số 2", 3, 3), ("Đáp án đề số 1", 4, 4)],
        ),
        (  # official paper: the code repeats in every page header
            ["Mã đề thi 101\nCâu 1", "Trang 2/4 - Mã đề 101", "Mã đề thi: 102\nCâu 1", "Trang 2/4 - Mã đề 102"],
            [("Mã đề 101", 0, 1), ("Mã đề 102", 2, 3)],
        ),
        (
            ["ĐỀ THI THỬ TN THPT 2024 – ĐỀ SỐ 5\nCâu 1", "Đề thi thử số 6\nCâu 1"],
            [("Đề số 5", 0, 0), ("Đề số 6", 1, 1)],
        ),
        (  # a numbered exam that also prints its code: one exam, not two
            ["ĐỀ SỐ 1\nMã đề 101\nCâu 1", "Mã đề 101", "ĐỀ SỐ 2\nMã đề 202\nCâu 1"],
            [("Mã đề 101", 0, 1), ("Mã đề 202", 2, 2)],
        ),
        (  # glued files: the code sits too low on page 1, but the page numbering starts again
            [
                "Trang 1/2 - WordToan\nĐỀ CHÍNH THỨC\nCâu 1",
                "Trang 2/2\nCâu 30",
                "Trang 1/2\nĐÁP ÁN CHI TIẾT MÃ ĐỀ 104\nCâu 1",
                "Trang 2/2",
                "LỜI GIẢI THAM KHẢO\nKỲ THI 2024\nMÃ ĐỀ: 123\nCâu 1",
            ],
            [("Phần 1", 0, 1), ("Đáp án mã đề 104", 2, 3), ("Đáp án mã đề 123", 4, 4)],
        ),
        (["Chuyên đề hàm số\nBài 1", "Câu 3: theo đề bài, có đề 12 câu"], []),  # not a bundle
        (["ĐỀ SỐ 1\nCâu 1", "Câu 2"], []),  # a single exam: nothing to cut
        (["Câu 5. Năm 2024 có đề 2024 người", "ĐỀ THI THPT 2024\nCâu 1"], []),  # years are not exam numbers
    ],
)
def test_find_exams(pages, expected):
    assert find_exams(pages) == [Segment(label, first, last) for label, first, last in expected]


def _bundle() -> bytes:
    with pymupdf.open() as pdf:
        for text in ["TUYỂN TẬP 2 ĐỀ TOÁN", "ĐỀ SỐ 1<br>Câu 1", "Câu 2", "ĐỀ SỐ 2<br>Câu 1"]:
            pdf.new_page().insert_htmlbox(pymupdf.Rect(72, 72, 500, 300), text)  # Noto: Vietnamese glyphs
        return pdf.tobytes()


class BundleDrive:
    async def children(self, folder_id):
        return {
            "root": [DriveFile("f-exams", "Đề thi", FOLDER)],
            "f-exams": [DriveFile("x-bundle", "Tuyển tập đề Toán.pdf", "application/pdf", 9, "m1")],
        }.get(folder_id, [])

    async def download(self, file):
        return _bundle(), "application/pdf"


SOURCE = DriveSource.model_validate(
    {
        "source_id": "demo",
        "name": "Demo",
        "folder_id": "root",
        "include": [
            {
                "folder": "Đề thi",
                "kind": "EXAM",
                "status": "PUBLISHED",
                "downloadable": True,
                "targets": [{"match": "to[aá]n", "category": ["Ôn thi THPT – Toán", "Tuyển tập"]}],
            }
        ],
    }
)


def test_sync_cuts_a_bundle_into_one_document_and_one_ai_import_per_exam(db):
    stats = db(lambda s: sync_drive(s, SOURCE, BundleDrive(), get_bronze_storage(), MemoryRawStore(), get_storage()))
    assert stats["new"] == 1 and stats["split_parts"] == 2 and stats["import_jobs"] == 2
    docs = db(lambda s: s.scalars(select(LibraryDocument).order_by(LibraryDocument.id)))
    bundle, first, second = docs
    assert (first.title, first.page_from, first.page_to, first.parent_id) == ("Đề số 1", 2, 3, bundle.id)
    assert first.folder_path == ["Đề thi", "Tuyển tập đề Toán"]  # listed in a folder named after the bundle
    assert first.status == LibraryStatus.PUBLISHED and first.downloadable and first.category_id == bundle.category_id
    part = asyncio.run(get_bronze_storage().get(second.storage_key))
    assert page_texts(part)[0].startswith("ĐỀ SỐ 2")
    jobs = db(lambda s: s.scalars(select(ImportJob).order_by(ImportJob.id)))
    assert [j.original_filename for j in jobs] == ["Tuyển tập đề Toán – Đề số 1.pdf", "Tuyển tập đề Toán – Đề số 2.pdf"]
    assert bundle.import_job_id == jobs[0].id and second.import_job_id == jobs[1].id


def test_admin_splits_a_stored_document_again(admin_client, db):
    db(lambda s: sync_drive(s, SOURCE, BundleDrive(), get_bronze_storage(), MemoryRawStore(), get_storage()))
    bundle_id = db(lambda s: s.scalar(select(LibraryDocument.id).where(LibraryDocument.parent_id.is_(None))))
    page = admin_client.post("/admin/library/bulk", data={"ids": [str(bundle_id)], "action": "split"}).text
    assert "Đã tách được 2 đề." in page
    # Splitting again replaces the parts rather than adding more.
    assert db(lambda s: s.scalars(select(LibraryDocument.title).where(LibraryDocument.parent_id == bundle_id))) == [
        "Đề số 1",
        "Đề số 2",
    ]


def _scanned(texts: list[str]) -> bytes:
    """Pages that are only pictures of text, like a scanned paper."""
    with pymupdf.open() as text_pdf, pymupdf.open() as scan:
        for text in texts:
            page = text_pdf.new_page()
            page.insert_htmlbox(pymupdf.Rect(60, 60, 540, 300), f"<p style='font-size:20px'>{text}</p>")
            picture = page.get_pixmap(dpi=110)
            scan.new_page(width=page.rect.width, height=page.rect.height).insert_image(page.rect, pixmap=picture)
        return scan.tobytes()


TESSDATA = get_settings().tessdata_dir


@pytest.mark.skipif(not (TESSDATA and Path(TESSDATA, "vie.traineddata").exists()), reason="no Tesseract vie data")
def test_scanned_papers_are_read_with_ocr():
    pdf = _scanned(["KỲ THI TỐT NGHIỆP<br>Mã đề thi 101", "Câu 30", "KỲ THI TỐT NGHIỆP<br>Mã đề thi 102"])
    assert find_exams(page_texts(pdf)) == []  # without OCR the pages carry no text at all
    assert find_exams(page_texts(pdf, TESSDATA)) == [Segment("Mã đề 101", 0, 1), Segment("Mã đề 102", 2, 2)]
