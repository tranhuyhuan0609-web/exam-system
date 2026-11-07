"""Essay papers in Word (HSG, Ngữ văn, Địa lí …): "Câu 1 (4,0 điểm)" questions, often followed in the same file by
the marking scheme (HƯỚNG DẪN CHẤM / ĐÁP ÁN, a table Câu | Ý | Đáp án | Điểm).

Only the questions are imported; the marking scheme of each question becomes its solution. School headers, the
matrix tags of each part ("(NB 0,5 – VDC 1,5)"), the setter's name and exam-room lines are left out. A file that
holds only a marking scheme gives no question at all.
"""

import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import html as lxml_html

from app.ai.schemas import ExtractedQuestion
from app.pipeline.domain.html_content import to_content

log = logging.getLogger(__name__)

_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
_QUESTION = re.compile(r"^\s*(?:câu|bài)\s*(\d{1,2}|I{1,3}|IV|VI{0,3}|IX|X)\b\s*[.:)]?", re.I)
_POINTS = re.compile(r"^\s*[(\[]?\s*(\d+(?:[.,]\d+)?)\s*điểm\s*[)\]]?\s*[.:]?\s*", re.I)
_KEY_HEADING = re.compile(
    r"^\s*(hướng dẫn chấm|hướng dẫn giải|đáp án(?: và| -| –|,)? (?:hướng dẫn|thang điểm|biểu điểm)|"
    r"đáp án\s*$|đáp án đề|đáp án chi tiết|lời giải chi tiết|biểu điểm)",
    re.I,
)
# "(NB 0,5 – VDC 1,5)", "(TH 1,0 -- VD 1,0)": where the part sits in the matrix, not for students
_MATRIX_TAG = re.compile(r"\(\s*(?:(?:NB|TH|VDC|VD)\s*\d+(?:[.,]\d+)?\s*(?:[-–—]+|;|,)?\s*)+\)", re.I)
_NOISE = re.compile(
    r"^\s*[-–—_.*>]*\s*(?:người (?:ra đề|phản biện|duyệt đề|soạn)|giáo viên (?:ra đề|soạn)|gv ra đề|"
    r"họ (?:và )?tên thí sinh|số báo danh|chữ kí|chữ ký|cán bộ coi thi|thí sinh không được|"
    r"\(?\s*đề thi gồm|hết\b|[-–—_. ]*hết[-–—_. ]*$|sđt|số điện thoại|điện thoại|e-?mail|zalo|facebook|"
    r"[|+\-=_.\s]*$)",
    re.I,
)
_CONTACT = re.compile(r"(?:\+?84|\b0)\d(?:[\s.]?\d){7,9}\b|\b[\w.+-]+@[\w-]+\.[\w.]+\b")  # phones, e-mails
_PART_LABELS = re.compile(r"(?:\s*(?:\d+(?:\.\d+)*\.?|[a-dA-D]\s*[.)]?)\s*)+")  # "1.1 1.2", "a b"
_SCORE_ONLY = re.compile(r"^\s*\d+(?:[.,]\d+)?\s*(?:điểm|đ)?\s*$", re.I)


@dataclass
class EssayPaper:
    questions: list[ExtractedQuestion]
    explanations: dict[int, str] = field(default_factory=dict)  # question index -> marking scheme
    points: dict[int, float] = field(default_factory=dict)  # question index -> points printed in the paper
    key_only: bool = False  # the file is a marking scheme without the questions


def _text(el) -> str:
    return " ".join("".join(el.itertext()).split())


def _number(token: str) -> int:
    return int(token) if token.isdigit() else _ROMAN.get(token.upper(), 0)


def _clean_lines(text: str) -> str:
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"\s+:", ":", _MATRIX_TAG.sub("", line)).strip()
        if not line or _NOISE.match(line) or _CONTACT.search(line):
            continue  # setters' names and contacts, exam-room lines: not part of the question
        lines.append(" ".join(line.split()))
    return "\n".join(lines).strip()


def clean_paper_text(text: str | None) -> str:
    """Question or answer text without matrix tags, setters' names and contacts, exam-room lines."""
    return _clean_lines(text or "")


def _question_start(el) -> re.Match | None:
    return _QUESTION.match(_text(el))


def _key_start(el) -> bool:
    texts = [_text(p) for p in el.iter("p", "h1", "h2", "h3", "h4", "strong", "th")] or [_text(el)]
    return any(_KEY_HEADING.match(t) and len(t) < 160 for t in texts if t)


def _key_from_tables(blocks: list) -> dict[int, list[str]]:
    """Table rows "Câu | Ý | Đáp án | Điểm": the first cell names the question for the rows that follow."""
    keys: dict[int, list[str]] = {}
    current = 0
    for block in blocks:
        for tr in block.iter("tr"):
            cells = tr.xpath("./td|./th")
            if not cells:
                continue
            first = _text(cells[0])
            found = re.match(r"^(?:câu|bài)?\s*(\d{1,2}|I{1,3}|IV|VI{0,3}|IX|X)\s*[.:]?$", first, re.I)
            if found:
                current = _number(found.group(1))
                cells = cells[1:]
            elif re.fullmatch(r"(?:câu|ý|đáp án|điểm|nội dung)?", first, re.I) and tr.xpath("./th"):
                continue  # the header row
            if not current:
                continue
            parts = []
            for cell in cells:
                content = to_content([cell]).text.strip()
                if not content or _PART_LABELS.fullmatch(content):
                    continue  # empty, or the "Ý" column (1.1 / 1.2, a / b): the answer text names its parts
                if all(_SCORE_ONLY.match(line) for line in content.split("\n") if line.strip()):
                    continue  # the "Điểm" column: scores for the marker, not part of the model answer
                parts.append(content)
            if parts:
                keys.setdefault(current, []).append(" ".join(parts))
    return keys


def _key_from_paragraphs(blocks: list) -> dict[int, list[str]]:
    """A marking scheme written as "Câu 1." paragraphs."""
    keys: dict[int, list[str]] = {}
    current = 0
    for block in blocks:
        if block.tag == "table":
            continue
        if m := _question_start(block):
            current = _number(m.group(1))
        if current:
            content = to_content([block]).text.strip()
            if content:
                keys.setdefault(current, []).append(content)
    return keys


def parse_essay_html(document: str) -> EssayPaper | None:
    root = lxml_html.fromstring(f"<div>{document}</div>")
    blocks = [b for b in root if isinstance(b.tag, str)]
    starts = [i for i, b in enumerate(blocks) if _question_start(b)]
    key_at = next((i for i, b in enumerate(blocks) if _key_start(b)), None)
    if key_at is not None and (not starts or key_at < starts[0]):
        return EssayPaper([], key_only=True)  # a marking scheme alone
    exam_starts = [i for i in starts if key_at is None or i < key_at]
    if len(exam_starts) < 2:
        return None
    end_of_exam = key_at if key_at is not None else len(blocks)
    questions, points = [], {}
    for k, start in enumerate(exam_starts):
        stop = exam_starts[k + 1] if k + 1 < len(exam_starts) else end_of_exam
        segment = blocks[start:stop]
        if any(seg.xpath(".//img") for seg in segment):
            return None  # a figure or a map: the page images (AI) show it
        text = to_content(segment).text
        head = _question_start(segment[0])
        text = text[head.end() :] if head and text.lower().lstrip().startswith(head.group(0).strip().lower()) else text
        if m := _POINTS.match(text):
            points[len(questions)] = float(m.group(1).replace(",", "."))
            text = text[m.end() :]
        content = _clean_lines(text)
        if len(content) < 15:
            continue
        questions.append(
            ExtractedQuestion(
                number=_number(head.group(1)) if head else k + 1, page=1, type="ESSAY", content=content, choices=[]
            )
        )
    if len(questions) < 2:
        return None
    explanations = {}
    if key_at is not None:
        key_blocks = blocks[key_at:]
        keys = _key_from_tables(key_blocks) or _key_from_paragraphs(key_blocks)
        for index, q in enumerate(questions):
            if lines := keys.get(q.number):
                explanation = _clean_lines("\n".join(lines))
                if len(explanation) >= 15:
                    explanations[index] = explanation
    return EssayPaper(questions, explanations, points)


def read_essay_docx(path: Path) -> EssayPaper | None:
    import pypandoc

    with tempfile.TemporaryDirectory(prefix="docx-essay-") as media:
        try:
            document = pypandoc.convert_file(str(path), "html", extra_args=["--wrap=none", f"--extract-media={media}"])
        except (RuntimeError, OSError) as exc:
            log.info("pandoc could not read %s: %s", path.name, exc)
            return None
    return parse_essay_html(document)
