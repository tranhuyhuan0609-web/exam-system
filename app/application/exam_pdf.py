"""Typeset an exam as clean PDFs: the paper (questions only) and the solutions (answer key + worked solutions).

HTML from templates/pdf/exam.html, formulas by KaTeX (as on the site), printed to A4 by headless Chromium
(Playwright). Figures are embedded as data URIs, so the page needs nothing but the KaTeX CDN.
"""

import base64
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.application.paper import SECTION_ORDER
from app.infrastructure.storage import Storage, StorageError
from app.models import Question, QuestionType
from app.templating import templates

log = logging.getLogger(__name__)

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
    """Print HTML to A4 with headless Chromium, once KaTeX has typeset the formulas."""
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
