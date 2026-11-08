"""Read an exam on this machine, without any AI call, when the file carries its text.

- Word (.docx): pandoc turns text and Equation objects (OMML) into Markdown with LaTeX, and gives the pictures.
  Old MathType objects come out as WMF/EMF pictures, not text: such a file is left to the vision path.
- PDF with a text layer: PyMuPDF. Formulas often fall apart there (one symbol per line); such pages, and
  scans without text, are left to the vision path.

The text is then cut into questions by the shapes every Vietnamese paper uses — "Câu 5:", options "A." to "D.",
statements "a)" to "d)", parts "PHẦN I/II/III" — and answers printed in the file are picked up: an answer table
at the end ("1.A 2.C …", "1A 2C …") or an underlined option. Only a confident reading is used (enough
questions, nearly all multiple-choice ones with their four options); otherwise the caller reads page images.
"""

import io
import logging
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from app.ai.schemas import ExtractedChoice, ExtractedQuestion

log = logging.getLogger(__name__)
MIN_QUESTIONS = 5
MIN_COMPLETE = 0.8  # share of multiple-choice questions whose four options were found

_MATH = re.compile(r"(\$\$.+?\$\$|\$[^$\n]+?\$)", re.S)
_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)[^)]*\)(\{[^}]*\})?")
_MARKED = re.compile(r"\]\{\.(?:underline|mark)\}")  # the end of an underlined / highlighted span
_SPAN = re.compile(r"\[([^\]]*)\]\{[^}]*\}")
_QUESTION = re.compile(r"(?im)^[\s>#]*c[âa]u\s*(\d{1,3})\s*[.:)]\s*")
_OPTION = re.compile(r"(?<![\w\\$])([A-D])\s*[.)]\s*(?=\S)")
_LOOSE_OPTION = re.compile(r"(?<![\w\\$])([A-D])(?:\s*[.)]\s*|\s+)(?=\S)")  # "A giải thể …": no full stop
_STATEMENT = re.compile(r"(?<![\w\\$])([a-d])\)\s+")
_PART = re.compile(r"(?im)^[\s#]*ph[aầ]n\s+(i{1,3}|[123])\b[.:\s]*(.*)$")
_ANSWERS = re.compile(r"(?im)^[\s#|]*(?:b[ảa]ng\s*)?(?:đ|d)[áa]p\s*[áa]n\b")
_KEY_PAIR = re.compile(r"(?<!\d)(\d{1,2})\s*[.:\-–)]?\s*([A-D])(?![A-Za-z])")
_FIGURE_WORDS = re.compile(r"h[iì]nh\s*v[eẽ]|đ[ồo]\s*th[ịi]|b[ảa]ng\s*bi[ếe]n\s*thi[êe]n|như\s*h[iì]nh", re.I)
_END = re.compile(r"(?im)^[\s\\\-–—*]*h[ếe]t[\s\\\-–—*]*$")


@dataclass
class LocalExam:
    questions: list[ExtractedQuestion]
    answers: dict[int, str] = field(default_factory=dict)  # question index -> option label printed in the file
    figures: dict[int, bytes] = field(default_factory=dict)  # question index -> PNG
    source: str = ""  # "docx" | "pdf-text"
    explanations: dict[int, str] = field(default_factory=dict)  # question index -> marking scheme in the file
    points: dict[int, float] = field(default_factory=dict)  # question index -> points printed in the paper
    figure_captions: dict[int, dict[str, str]] = field(default_factory=dict)  # question index -> {"Hình 1.1": "stem"}
    broken_crops: dict[int, bytes] = field(default_factory=dict)  # question index -> >=2000px PNG for formula OCR
    matrix_difficulties: dict[int, str] = field(default_factory=dict)  # question index -> difficulty from matrix tag
    question_flags: dict[int, list[str]] = field(default_factory=dict)  # question index -> ["missing_stem", ...]
    topics: dict[int, str] = field(default_factory=dict)  # question index -> section title (classification only)
    key_only: bool = False  # a marking scheme / answer file without the questions: nothing to import


def _clean(text: str) -> str:
    """Pandoc Markdown -> plain text, LaTeX kept as is: emphasis and escapes are dropped outside formulas."""
    out = []
    for i, part in enumerate(_MATH.split(text)):
        if i % 2:  # a formula
            out.append(part)
            continue
        part = re.sub(r"\*{1,3}|(?<!\w)_{1,3}|_{1,3}(?!\w)", "", part)
        part = re.sub(r"\\([^\w\s])", r"\1", part)  # \" \- \( ... -> the character
        part = part.replace("\\\n", "\n")
        out.append(part)
    return unicodedata.normalize("NFC", "".join(out))


def _options(block: str) -> tuple[str, list[str], int | None] | None:
    """(stem, the four options A–D, index of the one underlined or highlighted) when the block has all four.
    Two-column layouts read "A … C … B … D": each text is taken up to the next mark, whatever its letter."""
    for pattern in (_OPTION, _LOOSE_OPTION):
        marks = list(pattern.finditer(block))
        for first in (k for k, m in enumerate(marks) if m.group(1) == "A"):
            chosen, seen = [], set()
            for m in marks[first:]:
                if m.group(1) in seen:
                    break
                chosen.append(m)
                seen.add(m.group(1))
                if len(seen) == 4:
                    break
            if len(seen) < 4:
                continue
            stem = block[: chosen[0].start()]
            ends = [m.start() for m in chosen[1:]] + [len(block)]
            texts = {m.group(1): block[m.end() : end] for m, end in zip(chosen, ends, strict=True)}
            options = [texts[label] for label in "ABCD"]
            marked = next((k for k, o in enumerate(options) if _MARKED.search(o) or "<u>" in o), None)
            return stem, [_tidy_option(o) for o in options], marked
    return None


def _tidy_option(text: str) -> str:
    text = _SPAN.sub(r"\1", text)
    text = re.sub(r"\]\{[^}]*\}|\[\s*$|^\s*\[", "", text.strip())  # pieces of a span split by option marks
    text = " ".join(text.split())
    return text[:-1].rstrip() if text.endswith((".", ";")) and not text.endswith("..") else text


def _statements(block: str) -> tuple[str, list[str]] | None:
    marks = [m for m in _STATEMENT.finditer(block)]
    labels = [m.group(1) for m in marks]
    if labels[:4] != ["a", "b", "c", "d"]:
        return None
    ends = [m.start() for m in marks[1:4]] + [len(block)]
    return block[: marks[0].start()], [
        " ".join(block[m.end() : e].split()) for m, e in zip(marks[:4], ends, strict=True)
    ]


def _answer_table(text: str) -> dict[int, str]:
    pairs: dict[int, str] = {}
    for number, label in _KEY_PAIR.findall(text):
        pairs.setdefault(int(number), label)
    return pairs


def _answer_explanations(text: str) -> dict[int, str]:
    """Extract detailed solutions written after the answer table ('Câu 1: ...', 'Lời giải:...')."""
    from app.ai.essay_exam import clean_paper_text

    starts = list(_QUESTION.finditer(text))
    if not starts:
        return {}
    explanations: dict[int, str] = {}
    for k, m in enumerate(starts):
        end = starts[k + 1].start() if k + 1 < len(starts) else len(text)
        q_num = int(m.group(1))
        body = text[m.end() : end].strip()
        body = re.sub(r"^\s*(?:chọn|đáp án)\s*[A-D]\s*[.:,-]?\s*", "", body, flags=re.I)
        body = re.sub(r"^\s*(?:lời giải|hướng dẫn giải)\s*[:.]?\s*", "", body, flags=re.I)
        cleaned = clean_paper_text(body)
        if len(cleaned) >= 12:
            explanations[q_num] = cleaned
    return explanations


def _from_essay(essay, source: str) -> LocalExam:
    return LocalExam(
        questions=essay.questions,
        figures=dict(essay.figures),
        source=source,
        explanations=dict(essay.explanations),
        points=dict(essay.points),
        figure_captions=dict(essay.figure_captions),
        broken_crops=dict(essay.broken_crops),
        matrix_difficulties=dict(essay.matrix_difficulties),
        question_flags=dict(essay.question_flags),
        topics=dict(essay.topics),
        key_only=essay.key_only,
    )


def parse_text_exam(markdown: str, images: dict[str, bytes] | None = None) -> LocalExam | None:
    """Cut a paper's text into questions; None when the reading is not confident enough to be used."""
    images = images or {}
    text = _clean(markdown)
    starts = list(_QUESTION.finditer(text))
    if len(starts) < MIN_QUESTIONS:
        return None
    numbers = [int(m.group(1)) for m in starts]
    if any(n == 1 and k > 0 and numbers[k - 1] > 2 for k, n in enumerate(numbers)):
        # Numbering starts again: several papers (codes) in one file. They are cut apart first
        # (library_split), or read from the page images; one exam must not get the questions of two.
        return None
    # Answers printed after the questions (after "HẾT" or under a "ĐÁP ÁN" heading)
    tail_at = len(text)
    for pattern in (_END, _ANSWERS):
        found = [m for m in pattern.finditer(text) if m.start() > starts[-1].start()]
        if found:
            tail_at = min(tail_at, found[0].start())
    answer_table = _answer_table(text[tail_at:]) if tail_at < len(text) else {}
    answer_explanations = _answer_explanations(text[tail_at:]) if tail_at < len(text) else {}
    parts = [(m.start(), (m.group(2) or "").casefold()) for m in _PART.finditer(text)]

    from app.ai.essay_exam import extract_matrix_difficulty

    questions, answers, figures = [], {}, {}
    explanations: dict[int, str] = {}
    matrix_difficulties: dict[int, str] = {}
    mc_total = mc_complete = 0
    for k, start in enumerate(starts):
        end = starts[k + 1].start() if k + 1 < len(starts) else tail_at
        block = text[start.end() : end]
        section = next((name for at, name in reversed(parts) if at < start.start()), "")
        pictures = [path for path, _ in _IMAGE.findall(block)]
        block = _IMAGE.sub(" ", block)
        number = int(start.group(1))
        mat_diff = extract_matrix_difficulty(block)
        option = _options(block)
        statements = None if option else _statements(block)
        if option:
            stem, choices, underlined = option
            kind = "MULTIPLE_CHOICE"
        elif statements or "đúng" in section and "sai" in section:
            stem, choices, underlined = (*statements, None) if statements else (block, [], None)
            kind = "TRUE_FALSE"
        elif "ngắn" in section:
            stem, choices, underlined, kind = block, [], None, "SHORT_ANSWER"
        else:
            stem, choices, underlined, kind = block, [], None, "MULTIPLE_CHOICE"  # options not found
        if kind == "MULTIPLE_CHOICE":
            mc_total += 1
            mc_complete += bool(choices)
        stem = " ".join(_SPAN.sub(r"\1", stem).split())
        if not stem:
            continue
        index = len(questions)
        questions.append(
            ExtractedQuestion(
                number=number,
                page=1,
                type=kind,
                content=stem,
                difficulty=mat_diff,
                choices=[ExtractedChoice(label="ABCD"[i], content=c) for i, c in enumerate(choices)],
            )
        )
        if mat_diff:
            matrix_difficulties[index] = mat_diff
        if kind == "MULTIPLE_CHOICE" and underlined is not None:
            answers[index] = "ABCD"[underlined]
        elif kind == "MULTIPLE_CHOICE" and number in answer_table and len(questions) <= len(starts):
            answers[index] = answer_table[number]
        if number in answer_explanations and len(questions) <= len(starts):
            explanations[index] = answer_explanations[number]
        picture = next((images[p] for p in pictures if p in images), None)
        if picture is not None:
            figures[index] = picture
        elif pictures or _FIGURE_WORDS.search(stem):
            return None  # a figure we could not take: the page images will show it
    if not mc_total or mc_complete / mc_total < MIN_COMPLETE:
        return None
    return LocalExam(
        questions,
        answers,
        figures,
        explanations=explanations,
        matrix_difficulties=matrix_difficulties,
    )


def _to_png(data: bytes) -> bytes | None:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as picture:
            out = io.BytesIO()
            picture.convert("RGB").save(out, "PNG")
            return out.getvalue()
    except Exception:  # noqa: BLE001 - WMF/EMF and broken pictures: not usable here
        return None


def read_docx(path: Path) -> LocalExam | None:
    import pypandoc

    with tempfile.TemporaryDirectory(prefix="docx-text-") as media:
        try:
            markdown = pypandoc.convert_file(
                str(path),
                to="markdown-simple_tables-multiline_tables-grid_tables",
                extra_args=["--wrap=none", f"--extract-media={media}"],
            )
        except (RuntimeError, OSError) as exc:
            log.info("pandoc could not read %s: %s", path.name, exc)
            return None
        referenced = [p for p, _ in _IMAGE.findall(markdown)]
        if any(p.lower().endswith((".wmf", ".emf")) for p in referenced):
            from app.ai.essay_exam import read_essay_docx

            essay = read_essay_docx(path)
            if essay is not None:
                return _from_essay(essay, "docx")
            return None  # MathType formulas are pictures here: the text alone would miss them
        images = {}
        for ref in referenced:
            file = Path(ref)
            if file.exists() and (png := _to_png(file.read_bytes())) is not None:
                images[ref] = png
    exam = parse_text_exam(markdown, images)
    if exam is None:  # an essay paper ("Bài 1 (5,0 điểm)" / "Câu 1 (4,0 điểm)"), possibly with marking scheme
        from app.ai.essay_exam import read_essay_docx

        essay = read_essay_docx(path)
        if essay is not None:
            exam = _from_essay(essay, "docx")
    if exam:
        exam.source = "docx"
    return exam


def _broken(text: str) -> bool:
    """Formulas fallen apart: many one- or two-character lines, or symbol-font characters."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return True
    short = sum(len(line) <= 2 for line in lines) / len(lines)
    private = sum(0xE000 <= ord(c) <= 0xF8FF for c in text)
    return short > 0.15 or private > 5


def read_pdf_text(path: Path) -> LocalExam | None:
    import pymupdf
    from app.ai.essay_exam import read_essay_pdf

    # Step 1-5: Try structured essay PDF extraction with PyMuPDF get_text("dict") + geometry first
    try:
        essay = read_essay_pdf(path)
    except Exception as exc:  # noqa: BLE001
        log.info("read_essay_pdf failed on %s: %s", path.name, exc)
        essay = None
    if essay is not None:
        return _from_essay(essay, "pdf-text")

    with pymupdf.open(path) as pdf:
        pages = [page.get_text("text") for page in pdf]
    if not pages or any(len(p.strip()) < 30 for p in pages) or any(_broken(p) for p in pages):
        return None  # a scan, or formulas that did not survive: the page images are read instead
    exam = parse_text_exam("\n".join(pages))
    if exam:
        exam.source = "pdf-text"
    return exam


def read_locally(path: Path) -> LocalExam | None:
    suffix = path.suffix.lower()
    if suffix == ".doc":
        from app.ai.extract_engine import convert_doc_to_docx
        try:
            with tempfile.TemporaryDirectory(prefix="doc2docx-") as tmp:
                converted = convert_doc_to_docx(path, tmp)
                return read_docx(converted)
        except Exception as exc:
            log.warning("Could not convert .doc %s: %s", path.name, exc)
            return None
    if suffix == ".docx":
        return read_docx(path)
    if suffix == ".pdf":
        return read_pdf_text(path)
    return None
