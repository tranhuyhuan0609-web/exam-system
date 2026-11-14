"""Unified normalisation: every upload becomes a list of page PNGs, so one pipeline handles all formats.

.docx → PDF through LibreOffice (renders MathType objects and Word shapes exactly as printed)
.pdf  → page images with PyMuPDF
photo → EXIF-rotated, size-capped PNG
"""

import asyncio
import io
import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from app.ai.figures import PageGeometry, page_geometry, render_without_light_text
from app.infrastructure.pdf_engine import PDFEngineAdapter, PDFEngineError, PDFPasswordRequired

PDF_EXTENSIONS = (".pdf",)
WORD_EXTENSIONS = (".docx", ".doc")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
ARCHIVE_EXTENSIONS = (".zip", ".rar")
SUPPORTED_EXTENSIONS = PDF_EXTENSIONS + WORD_EXTENSIONS + IMAGE_EXTENSIONS + ARCHIVE_EXTENSIONS

RENDER_DPI = 300  # sharp subscripts and figure crops; ~2480×3508 px for A4 (AI gets a 1600 px copy)
MAX_IMAGE_SIDE = 2400
MAX_PAGES = 30


class NormalizeError(Exception):
    """The file cannot be turned into page images; the message is shown to the admin."""


async def docx_to_pdf(source: Path, soffice: str) -> Path:
    # Check if the file is an encrypted MS Office OLE2 container
    try:
        header = source.read_bytes()[:4096]
        if header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") and b"EncryptedPackage" in header:
            raise NormalizeError("Tệp Word này đã được đặt mật khẩu bảo vệ mã hóa nên không thể mở.")
    except NormalizeError:
        raise
    except Exception:
        pass

    if shutil.which(soffice) is None and not Path(soffice).exists():
        raise NormalizeError(
            "Máy chủ chưa có LibreOffice nên chưa đọc được file Word. "
            "Hãy chạy worker bằng Docker (docker compose up worker) hoặc lưu file Word thành PDF rồi tải lên."
        )
    out_dir = Path(tempfile.mkdtemp(prefix="docx2pdf-"))
    process = await asyncio.create_subprocess_exec(
        soffice,
        "--headless",
        "--norestore",
        f"-env:UserInstallation=file:///{out_dir.as_posix()}/profile",  # isolated profile: parallel-safe
        "--convert-to",
        "pdf",
        "--outdir",
        str(out_dir),
        str(source),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
    except TimeoutError as exc:
        process.kill()
        raise NormalizeError("LibreOffice chuyển file Word quá lâu (quá 3 phút).") from exc
    pdf = out_dir / (source.stem + ".pdf")
    if process.returncode != 0 or not pdf.exists():
        raise NormalizeError(f"LibreOffice không chuyển được file Word: {stderr.decode(errors='replace')[:300]}")
    return pdf


def pdf_to_pages(pdf: Path, out_dir: Path, dpi: int = RENDER_DPI) -> tuple[list[Path], list[PageGeometry]]:
    """Render every page; also return each page's vector graphics/words for exact figure cropping."""
    try:
        reader = PDFEngineAdapter.open(pdf)
    except PDFPasswordRequired as exc:
        raise NormalizeError("File PDF có mật khẩu.") from exc
    except PDFEngineError as exc:
        raise NormalizeError("Không mở được file PDF (file hỏng hoặc có mật khẩu).") from exc
    with reader:
        if reader.page_count > MAX_PAGES:
            raise NormalizeError(f"File có {reader.page_count} trang, vượt giới hạn {MAX_PAGES} trang mỗi lần.")
        paths, geometries = [], []
        for index in range(1, reader.page_count + 1):
            path = out_dir / f"page-{index:03d}.png"
            reader.render(index - 1, dpi=dpi).save(path)  # original, for the AI and the review screen
            paths.append(path)
            geometry = page_geometry(reader, index - 1)
            if geometry.has_light_text:  # figures are cut from a render without watermark text
                try:
                    clean_image = render_without_light_text(reader, index - 1, dpi)
                except PDFEngineError as exc:
                    raise NormalizeError("Không mở được file PDF (file hỏng hoặc có mật khẩu).") from exc
                if clean_image is not None:
                    clean = out_dir / f"page-{index:03d}-clean.png"
                    clean_image.save(clean)
                    geometry.figure_png = clean
            geometries.append(geometry)
    return paths, geometries


def image_to_page(source: Path, out_dir: Path) -> list[Path]:
    try:
        with Image.open(source) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")  # phone photos are often stored rotated
            img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            path = out_dir / "page-001.png"
            img.save(path, optimize=True)
    except OSError as exc:
        raise NormalizeError("Không đọc được file ảnh.") from exc
    return [path]


async def to_page_images(source: Path, out_dir: Path, soffice: str) -> tuple[list[Path], list[PageGeometry | None]]:
    """Page PNGs plus per-page geometry (None for photos, which have no vector information)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    if suffix in WORD_EXTENSIONS:
        pdf = await docx_to_pdf(source, soffice)
        try:
            pages, geometries = await asyncio.to_thread(pdf_to_pages, pdf, out_dir)
            # Copy or preserve the converted PDF next to pages so downstream fallbacks can read structured PDF text
            shutil.copy2(pdf, out_dir / f"{source.stem}.pdf")
            return pages, geometries
        finally:
            shutil.rmtree(pdf.parent, ignore_errors=True)
    if suffix in PDF_EXTENSIONS:
        return await asyncio.to_thread(pdf_to_pages, source, out_dir)
    if suffix in IMAGE_EXTENSIONS:
        pages = await asyncio.to_thread(image_to_page, source, out_dir)
        return pages, [None] * len(pages)
    if suffix in ARCHIVE_EXTENSIONS:
        from app.ai.extract_engine import pair_exam_and_solution_files, unpack_archive
        archive_out = out_dir / "extracted_archive"
        files = unpack_archive(source, archive_out)
        pairs = pair_exam_and_solution_files(files, soffice)
        if not pairs:
            raise NormalizeError("Gói nén không chứa file đề thi (.docx, .doc, .pdf) hợp lệ.")
        primary_exam = pairs[0][0]
        return await to_page_images(primary_exam, out_dir, soffice)
    raise NormalizeError("Định dạng không hỗ trợ. Hãy tải lên PDF, Word (.docx, .doc), file nén (.zip, .rar) hoặc ảnh.")


def png_bytes(path: Path) -> bytes:
    return path.read_bytes()


def thumbnail_bytes(path: Path, max_side: int = 2000) -> bytes:
    with Image.open(path) as img:
        img.thumbnail((max_side, max_side))
        buffer = io.BytesIO()
        img.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
