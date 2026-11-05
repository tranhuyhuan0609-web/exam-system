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

from app.ai.schemas import BoundingBox

PDF_EXTENSIONS = (".pdf",)
WORD_EXTENSIONS = (".docx", ".doc")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
SUPPORTED_EXTENSIONS = PDF_EXTENSIONS + WORD_EXTENSIONS + IMAGE_EXTENSIONS

RENDER_DPI = 300  # sharp subscripts and figure crops; ~2480×3508 px for A4 (AI gets a 1600 px copy)
MAX_IMAGE_SIDE = 2400
MAX_PAGES = 30
FIGURE_PADDING = 0.012  # extra margin around model-reported figure boxes (fraction of the page)


class NormalizeError(Exception):
    """The file cannot be turned into page images; the message is shown to the admin."""


async def docx_to_pdf(source: Path, soffice: str) -> Path:
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


def pdf_to_pages(pdf: Path, out_dir: Path, dpi: int = RENDER_DPI) -> list[Path]:
    import pymupdf

    try:
        doc = pymupdf.open(pdf)
    except Exception as exc:
        raise NormalizeError("Không mở được file PDF (file hỏng hoặc có mật khẩu).") from exc
    with doc:
        if doc.needs_pass:
            raise NormalizeError("File PDF có mật khẩu.")
        if doc.page_count > MAX_PAGES:
            raise NormalizeError(f"File có {doc.page_count} trang, vượt giới hạn {MAX_PAGES} trang mỗi lần.")
        paths = []
        for index, page in enumerate(doc, start=1):
            path = out_dir / f"page-{index:03d}.png"
            page.get_pixmap(dpi=dpi).save(path)
            paths.append(path)
    return paths


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


async def to_page_images(source: Path, out_dir: Path, soffice: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    if suffix in WORD_EXTENSIONS:
        pdf = await docx_to_pdf(source, soffice)
        try:
            return await asyncio.to_thread(pdf_to_pages, pdf, out_dir)
        finally:
            shutil.rmtree(pdf.parent, ignore_errors=True)
    if suffix in PDF_EXTENSIONS:
        return await asyncio.to_thread(pdf_to_pages, source, out_dir)
    if suffix in IMAGE_EXTENSIONS:
        return await asyncio.to_thread(image_to_page, source, out_dir)
    raise NormalizeError("Định dạng không hỗ trợ. Hãy tải lên PDF, Word (.docx) hoặc ảnh (PNG/JPG).")


def crop_figure(page_png: Path, box: BoundingBox, target: Path) -> bool:
    """Cut a model-reported 0–1000 box out of the page. Returns False for degenerate boxes."""
    with Image.open(page_png) as img:
        width, height = img.size
        pad_x, pad_y = int(width * FIGURE_PADDING), int(height * FIGURE_PADDING)
        left = max(0, min(box.xmin, box.xmax) * width // 1000 - pad_x)
        right = min(width, max(box.xmin, box.xmax) * width // 1000 + pad_x)
        top = max(0, min(box.ymin, box.ymax) * height // 1000 - pad_y)
        bottom = min(height, max(box.ymin, box.ymax) * height // 1000 + pad_y)
        if right - left < 20 or bottom - top < 20:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        img.crop((left, top, right, bottom)).save(target, optimize=True)
    return True


def png_bytes(path: Path) -> bytes:
    return path.read_bytes()


def thumbnail_bytes(path: Path, max_side: int = 1600) -> bytes:
    with Image.open(path) as img:
        img.thumbnail((max_side, max_side))
        buffer = io.BytesIO()
        img.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
