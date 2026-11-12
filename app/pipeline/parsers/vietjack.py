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

from app.pipeline.domain.html_content import BLANK
from app.pipeline.domain.models import ParsedPage, ParsedQuestion
from app.pipeline.parsers.base import Images, document, letter_choices, page_title, register, text_of, tf_choices

_PART = re.compile(r"^Phần\s+[IVX]+\b", re.I)
_QUESTION = re.compile(r"^(Câu\s*\d+|Câu hỏi|Bài\s*\d+|Question\s*\d+)\s*[:.]", re.I)
_ENGLISH = re.compile(r"^Question\s*\d+", re.I)  # English quizzes: a passage / an instruction leads a group
_INSTRUCTION = re.compile(  # "Read the following passage …", also after "Questions 11-15."
    r"^(?:questions?\s*\d+\s*(?:[-–]\s*\d+)?\s*[.:]\s*)?"
    r"(read|mark|choose|find|select|fill|complete|rewrite|look|listen|indicate|circle|match|put|give|write|use)\b",
    re.I,
)


_NOISE = re.compile(r"^(quảng cáo|trắc nghiệm online)$", re.I)  # labels between questions, not passage text


def _from_instruction(blocks: list) -> list:
    """The blocks from the last instruction on ("Read the following passage …" and the passage); [] if there is
    none — page headers and the "Quảng cáo" label between two questions are not a new passage."""
    starts = [i for i, el in enumerate(blocks) if _INSTRUCTION.match(text_of(el))]
    return blocks[starts[-1] :] if starts else []


_OPTION = re.compile(r"^([A-Da-d])\s*[.)]\s*")  # English quizzes letter options "a. r<ins>e</ins>member"
_INLINE_OPTIONS = re.compile(r"(?:^|\s)([A-D])\s*\.\s+")
# "Đáp án đúng là C", "Chọn đáp án: C", "Đáp án cần chọn là: d"
_MC_ANSWER = re.compile(r"(?i:đáp án)(?: đúng)?(?: cần chọn)?(?: là)?\s*:?\s*([A-Da-d])\b")
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
    if _tag(el) == "ins":  # an ad slot has attributes; a bare <ins> with text is underlining
        return bool(el.attrib) or not text_of(el)
    return "ads_" in (el.get("class") or "") or _tag(el) in ("script", "style")


@dataclass
class _Draft:
    part: str  # MULTIPLE_CHOICE | TRUE_FALSE | SHORT_ANSWER
    number: int
    stem: list = field(default_factory=list)
    options: list = field(default_factory=list)
    table: object | None = None
    answer: object | None = None  # the toggle <section>
    context: list = field(default_factory=list)  # the passage / instruction shared by a group of questions
    answer_end: int | None = None  # the answer's blocks stop here: the next passage was nested inside it


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
    between: list = []  # blocks after an answer and before the next question: a new passage or instruction
    group: list = []  # the passage / instruction of the questions being read
    for el in root:
        if not isinstance(el.tag, str) or _is_ad(el):
            continue
        tag, text = _tag(el), text_of(el)
        if tag == "p" and _PART.match(text):
            part = _part_of(text)
            current = None
            continue
        if (
            tag == "p"
            and _ENGLISH.match(text)
            and current is not None
            and len(current.stem) == 1
            and not current.options
            and not _ENGLISH.match(text_of(current.stem[0]))
        ):  # "Câu 6. Choose the best answer:" then "Question 6. She has been ___": one question
            current.stem.append(el)
            # Numbered as when the two lines were read as two questions (the second one held the options and
            # the answer), so the questions already in the bank keep their ids
            counters[part] += 1
            current.number = counters[part]
            continue
        if tag == "p" and _QUESTION.match(text):
            if fresh := _from_instruction(between):
                group = fresh
            between = []
            counters[part] = counters.get(part, 0) + 1
            current = _Draft(part, counters[part], stem=[el], context=group if _ENGLISH.match(text) else [])
            drafts.append(current)
            continue
        if current is None:
            if tag in ("p", "div", "figure", "center", "table", "h2", "h3", "h4") and text and not _NOISE.match(text):
                between.append(el)  # "Read the following passage …", the passage itself, "Mark the letter …"
            continue
        if tag == "section" and "toggle" in (el.get("class") or ""):
            current.answer = el
            if current.stem and _ENGLISH.match(text_of(current.stem[0])):
                # Some pages nest the next group's instruction and passage inside this answer: cut them out
                inside = _toggle_blocks(el)
                starts = [i for i, b in enumerate(inside) if _INSTRUCTION.match(text_of(b))]
                if starts:
                    current.answer_end = starts[0]
                    between = inside[starts[0] :]
            current = None  # the answer closes the question
        elif tag == "table":
            if current.part == "TRUE_FALSE" and current.table is None and _table_rows(el):
                current.table = el
            elif not current.options:
                current.stem.append(el)
        elif tag == "p" and current.part == "TRUE_FALSE" and (_STATEMENT.match(text) or current.options):
            current.options.append(el)  # statements written as "a) …" paragraphs instead of a table
        elif tag == "p" and current.part == "MULTIPLE_CHOICE" and (_OPTION.match(text) or current.options):
            current.options.append(el)
        elif tag in ("p", "div", "figure", "center", "table") and not current.options:
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


_QUESTION_LINE = re.compile(r"(?m)^Question\s*\d+\s*[.:]\s*")
_BLANK_LINE = re.compile(r".*(_{4,}|\.{5,}|…{2,})")  # the question repeated with its gap, atop the answer
_WORD_ANSWER = re.compile(r"^Đáp án\s*:\s*(.{1,40}?)\s*\.?$")


def _filled_word(blocks: list) -> str | None:
    """The word written in the gap: "Đáp án: adolescence", or the word underlined (<ins>/<u>) in the worked
    sentence ("Đáp án: … more <ins>responsibility</ins> …", "=> … <ins>worried</ins> …")."""
    for node in blocks:
        found = _WORD_ANSWER.match(text_of(node))
        if found and len(found.group(1).split()) <= 4 and not _MC_ANSWER.fullmatch(text_of(node).rstrip(".")):
            return found.group(1)
    for starts in ("Đáp án", "=>"):
        for node in blocks:
            if text_of(node).startswith(starts):
                marked = [text_of(m) for m in node.xpath(".//ins|.//u") if text_of(m)]
                if marked:
                    return " ".join(marked)
    return None


def _build(draft: _Draft, url: str, slug: str) -> ParsedQuestion:
    images = Images(url)
    stem = images.convert(draft.stem)
    stem = _QUESTION.sub("", stem, count=1).strip()
    stem = _QUESTION_LINE.sub("", stem).strip()  # "Question 6." under "Câu 6."
    if draft.context:  # a reading question carries its passage (and the instruction): it is read on its own
        context = images.convert(draft.context).strip()
        number = re.search(r"\d+", text_of(draft.stem[0]))
        if not stem and number and f"({number.group(0)})" in context:
            # a gap in a cloze passage: "Question 11." alone, the blank "(11) ____" is in the passage
            stem = f"Chọn đáp án đúng cho chỗ trống ({number.group(0)})."
        stem = f"{context}\n\n{stem}".strip()  # alone, the instruction is the question ("Choose the word …")
    q = ParsedQuestion(
        external_id=f"{slug}#{draft.part[:2].lower()}{draft.number}", question_type=draft.part, content=stem
    )
    answer_blocks = _toggle_blocks(draft.answer)[: draft.answer_end] if draft.answer is not None else []
    if draft.answer is None:
        q.issues.append("Không tìm thấy phần đáp án.")

    explanation_nodes = []
    if draft.part == "MULTIPLE_CHOICE":
        correct = None
        for node in answer_blocks:
            found = _MC_ANSWER.search(text_of(node))
            if found and correct is None and _tag(node) == "p" and len(text_of(node)) < 40:
                correct = found.group(1).upper()
                continue
            explanation_nodes.append(node)
        options = _options_from(images, draft.options)
        if not options and BLANK in stem and (word := _filled_word(answer_blocks)):
            # "Use the correct form of the words … ________ (responsible)": the answer is the word underlined
            # in the worked sentence; students type it
            q.question_type, q.accepted_answers = "SHORT_ANSWER", [word]
            q.explanation = images.convert([n for n in answer_blocks if not _BLANK_LINE.match(text_of(n))])
            q.images = images.urls
            return q
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
