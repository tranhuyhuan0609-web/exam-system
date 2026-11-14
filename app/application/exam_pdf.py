"""Typeset an exam as clean PDFs: the paper (questions only) and the solutions (answer key + worked solutions).

HTML from templates/pdf/exam.html, formulas by KaTeX (as on the site), printed to A4 by headless Chromium
(Playwright). Figures are embedded as data URIs, so the page needs nothing but the KaTeX CDN.
"""

import asyncio
import base64
import logging
import os
import sys
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.application.paper import SECTION_ORDER
from app.infrastructure.pdf_engine import (
    ImageStamp,
    LineStamp,
    PDFEngineAdapter,
    Rect,
    Stamp,
    TextStamp,
    extend_page_top,
    redact_pdf,
    stamp_pages,
)
from app.infrastructure.storage import Storage, StorageError
from app.models import Question, QuestionType
from app.templating import templates

log = logging.getLogger(__name__)


def docx_has_mathtype_or_shapes(docx_path: str | Path) -> bool:
    """Phát hiện file .docx có công thức MathType OLE (như toan11_de_goc.docx) hoặc hình vẽ Word Shapes."""
    try:
        with zipfile.ZipFile(str(docx_path), "r") as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
            return any(t in xml for t in ("<w:object", "oleObject", "<v:shape", "<wps:wsp"))
    except Exception:
        return False


def rebrand_pdf_preserve_exam_title(
    input_pdf: str | Path,
    output_pdf: str | Path,
    logo_path: str | Path | None = None,
    brand_footer: str = "Hệ thống Luyện thi & Ngân hàng Đề PRO",
) -> str:
    """
    - Xóa watermark 'PRO: admin@...' ở đỉnh trang và Footer tên GV/SĐT cũ ở đáy trang.
    - Nếu Trang 1 có Header kỳ thi ('KỲ THI...'): chỉ xóa góc trái (Logo + Đơn vị cũ), giữ nguyên tên kỳ thi bên phải.
    - Nếu Trang 1 vào thẳng 'Câu 1' ở sát mép trên (như HSG 1.1.pdf): dịch trang xuống 70pt để gắn Banner + Logo mới.

    PDF engine adapter (ADR-010): text/images under the removed bands are taken out of the content stream and the
    bands painted white (``redact_pdf``); logo, banner and footer are an overlay in DejaVu Sans (``stamp_pages``).
    """
    data = Path(input_pdf).read_bytes()
    with PDFEngineAdapter.open(data) as src:
        page_count = src.page_count
        ky_thi_rects = src.find_text(0, "KỲ THI")
        cau1_rects = src.find_text(0, "Câu 1") + src.find_text(0, "Câu I") + src.find_text(0, "Chương ")
        has_header = bool(ky_thi_rects) or bool(cau1_rects and cau1_rects[0].y0 > 110)
        regions: dict[int, list[Rect]] = {}
        for idx in range(page_count):
            w, h = src.page_size(idx)
            areas = regions.setdefault(idx, [])
            # 1. Xóa dòng "PRO: admin@..." ở mép trên
            areas += [Rect(0, 0, w, r.y1 + 3) for r in src.find_text(idx, "PRO:")]
            # 2. Xóa Footer tên GV / SĐT cũ ở đáy trang (VD: "GV. Phan Nhật Linh - SĐT...")
            for kw in ("GV.", "SĐT:", "0817"):
                areas += [Rect(0, r.y0 - 4, w, h) for r in src.find_text(idx, kw) if r.y0 > h - 65]
            # 3. Ở Trang 1 có Header kỳ thi: Xóa góc trái, giữ nguyên khối "KỲ THI..." bên phải
            if idx == 0 and has_header and ky_thi_rects:
                chinh_thuc = src.find_text(idx, "CHÍNH THỨC")
                bottom_limit = chinh_thuc[0].y0 - 4 if chinh_thuc else 130
                areas.append(Rect(0, 0, ky_thi_rects[0].x0 - 8, bottom_limit))

    pdf = redact_pdf(data, lambda idx, _w, _h: regions.get(idx, []))
    if not has_header:  # Phiếu bài tập không có Header -> trang 1 cao thêm 70pt, nội dung dịch xuống
        pdf = extend_page_top(pdf, 0, 70)
    logo = Path(logo_path) if logo_path and os.path.exists(str(logo_path)) else None
    right_limit = ky_thi_rects[0].x0 - 8 if ky_thi_rects else 220

    def brand(idx: int, w: float, h: float) -> list[Stamp]:
        stamps: list[Stamp] = []
        if idx == 0 and has_header:
            cx = (30 + right_limit) / 2
            if logo:
                stamps.append(ImageStamp(logo, Rect(cx - 30, 35, cx + 30, 95)))
        elif idx == 0:
            if logo:
                stamps.append(ImageStamp(logo, Rect(40, 12, 90, 62)))
            stamps.append(TextStamp.at(105, 42, brand_footer.upper(), 10, max_width=w - 145))
            stamps.append(LineStamp(40, 68, w - 40, 68, (0.2, 0.3, 0.6), 1.0))
        stamps.append(TextStamp.at(40, h - 14, f"{brand_footer} • Trang {idx + 1}/{page_count}", 8, (0.45, 0.45, 0.45)))
        return stamps

    Path(output_pdf).write_bytes(stamp_pages(pdf, brand))
    return str(output_pdf)


PART_TITLES = {
    QuestionType.MULTIPLE_CHOICE: "Câu trắc nghiệm nhiều phương án lựa chọn",
    QuestionType.TRUE_FALSE: "Câu trắc nghiệm đúng sai",
    QuestionType.SHORT_ANSWER: "Câu trắc nghiệm trả lời ngắn",
    QuestionType.ESSAY: "Tự luận",
}
ROMAN = ["I", "II", "III", "IV"]
Renderer = Callable[[str, str], Awaitable[bytes]]  # (html, footer text) -> PDF bytes


class PdfError(Exception):
    pass


@dataclass
class PrintedQuestion:
    number: int
    kind: str
    content: str
    choices: list[str]
    columns: int  # choices per row: short options sit side by side as in printed papers
    figure: str | None
    answer: str
    truths: list[bool] = field(default_factory=list)
    explanation: str = ""


@dataclass
class PrintedPart:
    title: str
    questions: list[PrintedQuestion]


def _columns(choices: list[str]) -> int:
    longest = max((len(c) for c in choices), default=0)
    return 4 if longest <= 22 else 2 if longest <= 48 else 1


def _answer(q: Question) -> str:
    if q.question_type == QuestionType.SHORT_ANSWER:
        return " hoặc ".join(q.accepted_answers or [])
    if q.question_type == QuestionType.TRUE_FALSE:
        return " ".join(f"{'abcd'[i]}) {'Đ' if c.get('is_correct') else 'S'}" for i, c in enumerate(q.choices))
    return next(("ABCDEF"[i] for i, c in enumerate(q.choices) if c.get("is_correct")), "")


async def _figure(storage: Storage, key: str | None) -> str | None:
    if not key:
        return None
    try:
        data = await storage.get(key)
    except StorageError:
        log.warning("Figure %s is missing; printed without it", key)
        return None
    return "data:image/png;base64," + base64.b64encode(data).decode()


async def build_parts(questions: list[Question], storage: Storage) -> list[PrintedPart]:
    """Questions grouped like the new THPT paper (Part I multiple choice, II true/false, III short answer);
    numbering restarts in every part."""
    groups: dict[QuestionType, list[Question]] = {}
    for q in questions:
        groups.setdefault(q.question_type, []).append(q)
    parts = []
    for kind in sorted(groups, key=SECTION_ORDER.get):
        printed = []
        for number, q in enumerate(groups[kind], start=1):
            texts = [c.get("content", "") for c in q.choices or []]
            printed.append(
                PrintedQuestion(
                    number=number,
                    kind=kind.value,
                    content=q.content,
                    choices=texts,
                    columns=_columns(texts),
                    figure=await _figure(storage, q.image_path),
                    answer=_answer(q),
                    truths=[bool(c.get("is_correct")) for c in q.choices or []],
                    explanation=q.explanation or "",
                )
            )
        title = PART_TITLES[kind]
        parts.append(PrintedPart(f"PHẦN {ROMAN[len(parts)]}. {title}" if len(groups) > 1 else title, printed))
    return parts


def exam_html(*, title: str, subtitle: str, minutes: int, source: str, parts: list[PrintedPart], solutions: bool):
    return templates.get_template("pdf/exam.html").render(
        title=title, subtitle=subtitle, minutes=minutes, source=source, parts=parts, solutions=solutions
    )


async def chromium_pdf(html: str, footer: str) -> bytes:
    """Print HTML to A4 with headless Chromium, once KaTeX has typeset the formulas. Playwright starts Chromium
    as a subprocess, which the selector event loop (Windows, aiokafka/asyncpg processes) cannot do: there the
    printing runs in a thread with a loop of its own."""
    if sys.platform == "win32" and not isinstance(asyncio.get_running_loop(), asyncio.ProactorEventLoop):

        def in_thread() -> bytes:
            loop = asyncio.ProactorEventLoop()
            try:
                return loop.run_until_complete(_chromium_pdf(html, footer))
            finally:
                loop.close()

        return await asyncio.to_thread(in_thread)
    return await _chromium_pdf(html, footer)


async def _chromium_pdf(html: str, footer: str) -> bytes:
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import async_playwright

    footer_html = (
        '<div style="width:100%;font-size:8px;color:#555;text-align:center;font-family:serif">'
        f'Trang <span class="pageNumber"></span>/<span class="totalPages"></span> · {footer}</div>'
    )
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page()
                await page.set_content(html, wait_until="networkidle", timeout=60_000)
                try:
                    await page.wait_for_function("window.mathDone === true", timeout=20_000)
                except PlaywrightError:
                    log.warning("KaTeX did not load; formulas are printed as typed")
                return await page.pdf(
                    format="A4",
                    margin={"top": "14mm", "bottom": "16mm", "left": "15mm", "right": "15mm"},
                    print_background=True,
                    display_header_footer=True,
                    header_template="<div></div>",
                    footer_template=footer_html,
                )
            finally:
                await browser.close()
    except PlaywrightError as exc:
        raise PdfError(f"Không in được PDF (Chromium): {exc}") from exc
