"""vietjack.com quiz lessons ("Trắc nghiệm Vật Lí 11 ... Bài N (có đáp án)").

The article is a flat run of blocks inside the content column:

    <p>Phần I. Trắc nghiệm nhiều lựa chọn</p>
    <p><strong>Câu 1:</strong> stem</p>   <p>A. ...</p> ... <p>D. ...</p>
    <section class="toggle"> ... <p>Đáp án đúng là C.</p> [explanation] </section>
    <p>Phần II. Câu trắc nghiệm đúng sai</p>
    <p>Câu hỏi: stem</p> <table> a | statement | | </table>
    <section class="toggle"> <table> a | statement | Đ | </table> [explanation] </section>
    <p>Phần III. Câu trắc nghiệm trả lời ngắn</p>
    <p>Câu 1: stem</p> <section class="toggle"> [explanation] <p>Đáp án: 20</p> </section>

Ads (<div class="ads_...">) sit between the blocks and are skipped.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from lxml import etree

from app.pipeline.domain.models import ParsedPage, ParsedQuestion
from app.pipeline.parsers.base import Images, document, letter_choices, page_title, register, text_of, tf_choices

_PART = re.compile(r"^Phần\s+[IVX]+\b", re.I)
_QUESTION = re.compile(r"^(Câu\s*\d+|Câu hỏi|Bài\s*\d+)\s*[:.]", re.I)
_OPTION = re.compile(r"^([A-D])\s*[.)]\s*")
_INLINE_OPTIONS = re.compile(r"(?:^|\s)([A-D])\s*\.\s+")
_MC_ANSWER = re.compile(r"Đáp án(?: đúng)?(?: là)?\s*:?\s*([A-D])\b")
_SA_ANSWER = re.compile(r"^(?:Đáp (?:án|số)|Trả lời)\s*:?\s*(.+?)\s*\.?$")
_STATEMENT = re.compile(r"^([a-d])\s*\)\s*")
_TF_KEY = re.compile(r"\b([a-d])\s*\)\s*[-–:]?\s*(Đúng|Sai|Đ|S)\b", re.I)


def _part_of(text: str) -> str:
    low = re.sub(r"[-–/]", " ", text.lower())
    low = " ".join(low.split())
    if "đúng sai" in low:
        return "TRUE_FALSE"
    if "trả lời ngắn" in low:
        return "SHORT_ANSWER"
    return "MULTIPLE_CHOICE"


def _tag(el) -> str:
    return etree.QName(el.tag).localname.lower() if isinstance(el.tag, str) else ""


def _is_ad(el) -> bool:
    return "ads_" in (el.get("class") or "") or _tag(el) in ("script", "ins", "style")


@dataclass
class _Draft:
    part: str  # MULTIPLE_CHOICE | TRUE_FALSE | SHORT_ANSWER
    number: int
    stem: list = field(default_factory=list)
    options: list = field(default_factory=list)
    table: object | None = None
    answer: object | None = None  # the toggle <section>


def _content_root(doc):
    """The element whose children are the article blocks (parent of the first "Câu ..." paragraph)."""
    for p in doc.xpath("//p"):
        if _QUESTION.match(text_of(p)):
            return p.getparent()
    return None


def _drafts(root) -> list[_Draft]:
    drafts: list[_Draft] = []
    part = "MULTIPLE_CHOICE"
    counters: dict[str, int] = {}
    current: _Draft | None = None
    for el in root:
        if not isinstance(el.tag, str) or _is_ad(el):
            continue
        tag, text = _tag(el), text_of(el)
        if tag == "p" and _PART.match(text):
            part = _part_of(text)
            current = None
            continue
        if tag == "p" and _QUESTION.match(text):
            counters[part] = counters.get(part, 0) + 1
            current = _Draft(part, counters[part], stem=[el])
            drafts.append(current)
            continue
        if current is None:
            continue
        if tag == "section" and "toggle" in (el.get("class") or ""):
            current.answer = el
            current = None  # the answer closes the question
        elif tag == "table" and current.part == "TRUE_FALSE" and current.table is None:
            current.table = el
        elif tag == "p" and current.part == "TRUE_FALSE" and (_STATEMENT.match(text) or current.options):
            current.options.append(el)  # statements written as "a) …" paragraphs instead of a table
        elif tag == "p" and current.part == "MULTIPLE_CHOICE" and (_OPTION.match(text) or current.options):
            current.options.append(el)
        elif tag in ("p", "div", "figure", "center") and not current.options:
            current.stem.append(el)
    return drafts


def _toggle_blocks(section) -> list:
    body = section.xpath('.//div[contains(@class, "toggle-content")]')
    return [
        c for c in (body[0] if body else section) if isinstance(c.tag, str) and _tag(c) != "label" and not _is_ad(c)
    ]


def _table_rows(table) -> list[list]:
    rows = []
    for tr in table.xpath(".//tr"):
        cells = tr.xpath("./td|./th")
        if len(cells) >= 2 and re.fullmatch(r"[a-dA-D]\)?", text_of(cells[0])):
            rows.append(cells)
    return rows


def _options_from(images: Images, nodes: list) -> list[str]:
    texts = []
    for node in nodes:
        text = images.convert([node])
        pieces = [m for m in _INLINE_OPTIONS.finditer(text)]
        if len(pieces) > 1:  # "A. 1 B. 2 C. 3 D. 4" on one line
            for i, m in enumerate(pieces):
                end = pieces[i + 1].start() if i + 1 < len(pieces) else len(text)
                texts.append(text[m.end() : end].strip())
        else:
            texts.append(_OPTION.sub("", text, count=1).strip())
    return [t for t in texts if t]


def _build(draft: _Draft, url: str, slug: str) -> ParsedQuestion:
    images = Images(url)
    stem = images.convert(draft.stem)
    stem = _QUESTION.sub("", stem, count=1).strip()
    q = ParsedQuestion(
        external_id=f"{slug}#{draft.part[:2].lower()}{draft.number}", question_type=draft.part, content=stem
    )
    answer_blocks = _toggle_blocks(draft.answer) if draft.answer is not None else []
    if draft.answer is None:
        q.issues.append("Không tìm thấy phần đáp án.")

    explanation_nodes = []
    if draft.part == "MULTIPLE_CHOICE":
        correct = None
        for node in answer_blocks:
            found = _MC_ANSWER.search(text_of(node))
            if found and correct is None and _tag(node) == "p" and len(text_of(node)) < 40:
                correct = found.group(1)
                continue
            explanation_nodes.append(node)
        options = _options_from(images, draft.options)
        if len(options) < 2:
            q.issues.append("Không tách được các lựa chọn A–D.")
        q.choices = letter_choices(options, correct)
        if correct is None:
            q.issues.append("Không tìm thấy đáp án.")
    elif draft.part == "TRUE_FALSE":
        if draft.table is not None:
            statements = [images.convert([row[1]]) for row in _table_rows(draft.table)]
        else:
            statements = [_STATEMENT.sub("", images.convert([n]), count=1).strip() for n in draft.options]
        truth: list[bool | None] = [None] * len(statements)
        key_table = next((n for n in answer_blocks if _tag(n) == "table"), None)
        key_line = None
        if key_table is not None:
            for i, row in enumerate(_table_rows(key_table)[: len(statements)]):
                marks = [text_of(c).upper() for c in row[2:4]]
                truth[i] = (
                    True
                    if marks and marks[0].startswith("Đ")
                    else (False if len(marks) > 1 and marks[1].startswith("S") else None)
                )
        else:  # "Đáp án: a) Đúng, b) Sai, c) Sai, d) Đúng."
            for node in answer_blocks:
                marks = _TF_KEY.findall(text_of(node))
                if len(marks) >= 2:
                    key_line = node
                    for letter, mark in marks:
                        i = "abcd".index(letter.lower())
                        if i < len(truth):
                            truth[i] = mark.upper().startswith("Đ")
        explanation_nodes = [n for n in answer_blocks if n is not key_table and n is not key_line]
        if len(statements) != 4:
            q.issues.append(f"Câu đúng/sai có {len(statements)} ý (cần 4).")
        if any(t is None for t in truth):
            q.issues.append("Thiếu đáp án Đúng/Sai cho một số ý.")
        q.choices = tf_choices(statements, truth)
    else:  # SHORT_ANSWER
        answer = None
        for node in answer_blocks:
            text = text_of(node)
            found = _SA_ANSWER.match(text) if text.startswith(("Đáp", "Trả lời")) else None
            if found and answer is None:
                answer = found.group(1).strip()
                continue
            explanation_nodes.append(node)
        if answer:
            q.accepted_answers = [answer]
        else:
            q.issues.append("Không tìm thấy đáp số.")
    q.explanation = images.convert(explanation_nodes)
    q.images = images.urls
    return q


@register("vietjack_quiz")
def parse(html: str, url: str) -> ParsedPage:
    doc = document(html)
    root = _content_root(doc)
    if root is None:
        return ParsedPage(title=page_title(doc), questions=[], issues=["Không tìm thấy câu hỏi nào trên trang."])
    slug = urlparse(url).path.rsplit("/", 1)[-1].removesuffix(".jsp")
    questions = [_build(d, url, slug) for d in _drafts(root)]
    return ParsedPage(title=page_title(doc), questions=questions)
