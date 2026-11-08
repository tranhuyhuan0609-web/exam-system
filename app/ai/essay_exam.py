"""Essay & Olympiad (HSG) papers in Word (.docx) and PDF:
"Bài 1. (5,0 điểm) – DAO ĐỘNG CƠ HỌC" or "Câu 1 (4,0 điểm)", often followed in the same file by
the marking scheme (HƯỚNG DẪN CHẤM / ĐÁP ÁN).

7-Step Deterministic Extraction Architecture:
1. Regex header matching: `^(Bài|Câu)\\s*(\\d+)[.:]?\\s*\\(([\\d,.]+)\\s*điểm\\)` extracts problem number and points.
   Blocks are cut from one header to the next across pages. For PDFs with text, PyMuPDF (`get_text("dict")`)
   extracts line coordinates sorted in reading order, stripping page headers, footers, and "HẾT".
2. Sub-parts ("1.", "2.", "1.1", "2a)", "3b)", "a)", "b)") stay intact inside the problem's content.
3. Figure attachment by caption & coordinate geometry: "Hình 1.1" in the stem belongs to the whole problem,
   "Hình 1.2" in part 3 belongs to part 3. Uses `page_geometry` and `figures.py` to crop figures cleanly.
4. Self-verification against printed metadata: extracted count must match "Đề thi gồm 05 bài", numbering must
   be contiguous 1..N, and total points must match. Questions mentioning "như hình" without a figure or starting
   with "1." without an introductory stem are flagged and excluded from AI solving. If structural checks fail,
   falls back to Step 6.
5. Formula-only AI transcription: when `_broken()` detects broken math in a PDF block, only that problem's
   cropped image (>= 2000px) is sent to AI at `temperature=0` to transcribe formulas into LaTeX.
6. Fallback for scanned PDFs / photos: vision extraction with explicit prompt rule keeping sub-parts inside Bài N.
7. Difficulty & embedded solutions: difficulty is taken from printed matrix tags e.g. "(NB 0,5 – VDC 1,5)";
   if absent, left for Admin to select (never guessed by AI for HSG essays). When the file includes answers /
   marking scheme, solutions are reconstructed and attached directly without AI solving.
"""

from __future__ import annotations

import io
import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import html as lxml_html
from PIL import Image, ImageDraw

from app.ai.figures import BoundingBox, PageGeometry, cut, is_light_text, page_geometry, remove_light_text, snap_to_graphics
from app.ai.schemas import ExtractedQuestion
from app.pipeline.domain.html_content import to_content

log = logging.getLogger(__name__)

_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
_VN_NUMBERS = {
    "một": 1, "hai": 2, "ba": 3, "bốn": 4, "năm": 5,
    "sáu": 6, "bảy": 7, "tám": 8, "chín": 9, "mười": 10,
}

# Step 1: Primary header regex e.g. "Bài 1. (5,0 điểm) – DAO ĐỘNG CƠ HỌC" or "Câu 1 (4,0 điểm):"
HEADER_WITH_POINTS = re.compile(
    r"^\s*(?P<kind>Bài|Câu)\s*(?P<num>\d{1,2}|I{1,3}|IV|VI{0,3}|IX|X)\s*[.:)]?\s*"
    r"[(\[]\s*(?P<pts>\d+(?:[.,]\d+)?)\s*(?:điểm|đ)\s*[)\]]\s*(?:[-–—.:]\s*(?P<title>.*))?$",
    re.IGNORECASE,
)
# Variant where topic title precedes points on the same line: "Bài 1 – DAO ĐỘNG CƠ HỌC (5,0 điểm)"
HEADER_TITLE_POINTS = re.compile(
    r"^\s*(?P<kind>Bài|Câu)\s*(?P<num>\d{1,2}|I{1,3}|IV|VI{0,3}|IX|X)\b\s*(?:[-–—.:)]\s*)?"
    r"(?P<title>[^\n()]{1,100}?)\s*[(\[]\s*(?P<pts>\d+(?:[.,]\d+)?)\s*(?:điểm|đ)\s*[)\]]\s*[.:)]?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)

_QUESTION = re.compile(r"^\s*(?:câu|bài)\s*(\d{1,2}|I{1,3}|IV|VI{0,3}|IX|X)\b\s*[.:)]?", re.I)
_POINTS = re.compile(r"^\s*[(\[]?\s*(\d+(?:[.,]\d+)?)\s*(?:điểm|đ)\s*[)\]]?\s*(?:[-–—.:]\s*)?", re.I)
_KEY_HEADING = re.compile(
    r"^\s*(?:[IVX\d]+[.\-–—)\s]+|\(\s*)?(hướng dẫn chấm|hướng dẫn giải|đáp án(?: và| -| –|,)? (?:hướng dẫn|thang điểm|biểu điểm)|"
    r"đáp án\s*$|đáp án đề|đáp án chi tiết|lời giải chi tiết|biểu điểm|thang điểm)",
    re.I,
)
# "(NB 0,5 – VDC 1,5)", "(TH 1,0 -- VD 1,0)": matrix tags printed in the exam
_MATRIX_TAG = re.compile(r"\(\s*(?:(?:NB|TH|VDC|VD|HSG)\s*[:=]?\s*\d*(?:[.,]\d+)?\s*(?:điểm|đ)?\s*(?:[-–—]+|;|,)?\s*)+\)", re.I)
_MATRIX_TOKEN = re.compile(r"\b(NB|TH|VDC|VD|HSG)\b(?:\s*[:=]?\s*(\d+(?:[.,]\d+)?))?", re.I)

_LEVEL_MAP = {
    "NB": "RECOGNITION",
    "TH": "UNDERSTANDING",
    "VD": "APPLICATION",
    "VDC": "ADVANCED_APPLICATION",
    "HSG": "HSG",
}
_LEVEL_RANK = {
    "RECOGNITION": 1,
    "UNDERSTANDING": 2,
    "APPLICATION": 3,
    "ADVANCED_APPLICATION": 4,
    "HSG": 5,
}

_NOISE = re.compile(
    r"^\s*[-–—_.*>]*\s*(?:người (?:ra đề|phản biện|duyệt đề|soạn)|giáo viên (?:ra đề|soạn)|gv ra đề|"
    r"họ (?:và )?tên thí sinh|số báo danh|chữ kí|chữ ký|cán bộ coi thi|thí sinh không được|"
    r"\(?\s*đề thi gồm|hết\b|[-–—_. ]*hết[-–—_. ]*$|sđt|số điện thoại|điện thoại|e-?mail|zalo|facebook|"
    r"[|+\-=_.\s]*$)",
    re.I,
)
_END_MARKER = re.compile(r"^\s*[-–—_.*~]*\s*hết\b", re.I)
_PAGE_FOOTER = re.compile(r"^\s*(?:trang|page)\s*\d+(?:\s*/\s*\d+)?\s*$|^\s*[-–—]?\s*\d{1,2}\s*[-–—]?\s*$", re.I)
_DECLARED_COUNT = re.compile(
    r"đề\s*(?:thi\s*)?(?:gồm|có)\s*(?:0?(\d{1,2})|(một|hai|ba|bốn|năm|sáu|bảy|tám|chín|mười))\s*(?:bài|câu)\b",
    re.I,
)
_DECLARED_TOTAL_POINTS = re.compile(
    r"(?:thang\s*điểm|tổng\s*điểm)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*(?:điểm|đ)?",
    re.I,
)
_CONTACT = re.compile(r"(?:\+?84|\b0)\d(?:[\s.]?\d){7,9}\b|\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_PART_LABELS = re.compile(r"(?:\s*(?:\d+(?:\.\d+)*\.?|[a-dA-D]\s*[.)]?)\s*)+")
_SCORE_ONLY = re.compile(r"^\s*\d+(?:[.,]\d+)?\s*(?:điểm|đ)?\s*$", re.I)
_INLINE_PDF_KEY_HDR = re.compile(
    r"^\s*(?:ý|câu|bài|phần)?\s*(?:nội\s*dung|đáp\s*án|hướng\s*dẫn\s*chấm)\s+(?:điểm|thang\s*điểm|biểu\s*điểm)\s*$",
    re.I,
)

# Legacy Windows Symbol / Wingdings Private Use Area (U+F020..U+F0FF) mapping to clean Unicode
_SYMBOL_PUA_MAP = str.maketrans({
    "\uf020": " ",
    "\uf02b": "+",
    "\uf02d": "-",
    "\uf03d": "=",
    "\uf061": "α",
    "\uf062": "β",
    "\uf067": "γ",
    "\uf064": "δ",
    "\uf044": "Δ",
    "\uf06c": "λ",
    "\uf06d": "μ",
    "\uf070": "π",
    "\uf077": "ω",
    "\uf057": "Ω",
    "\uf0a3": "≤",
    "\uf0a5": "∞",
    "\uf0ab": "↔",
    "\uf0ad": "↑",
    "\uf0ae": "→",
    "\uf0af": "↓",
    "\uf0b0": "°",
    "\uf0b1": "±",
    "\uf0b2": "²",
    "\uf0b3": "≥",
    "\uf0b4": "×",
    "\uf0b7": "•",
    "\uf0b8": "÷",
    "\uf0b9": "≠",
    "\uf0ba": "≡",
    "\uf0bb": "≈",
    "\uf0be": "→",
    "\uf0c4": "⊗",
    "\uf0c5": "⊕",
    "\uf0c6": "∅",
    "\uf0c7": "∩",
    "\uf0c8": "∪",
    "\uf0ce": "∈",
    "\uf0cf": "∉",
    "\uf0d5": "∏",
    "\uf0d6": "√",
    "\uf0d7": "⋅",
    "\uf0db": "⇔",
    "\uf0dc": "⇐",
    "\uf0de": "⇒",
    "\uf0e0": "◊",
    "\uf0e5": "∑",
    "\uf0f2": "∫",
})


def clean_symbol_pua(text: str) -> str:
    """Replace legacy Windows Symbol-font Private Use Area chars and MathJax \\dpi{...} tags."""
    if not text:
        return ""
    out = text.translate(_SYMBOL_PUA_MAP)
    out = re.sub(r"\\dpi\{\d+\}\s*", "", out)
    return out

# Step 3 & 4: Figure references, captions, and sub-part markers
_CAPTION_RE = re.compile(r"\b(Hình\s*\d+(?:[.,]\d+)?)\b", re.I)
_FIGURE_MENTION_RE = re.compile(
    r"như\s*hình|hình\s*vẽ|hình\s*bên|hình\s*\d|sơ\s*đồ\s*hình|đồ\s*thị\s*hình",
    re.I,
)
_SUBPART_START_RE = re.compile(
    r"^\s*(?:(?P<num_letter>\d+\s*[a-dA-D])\s*[.)]|(?P<decimal>\d+\.\d+)\s*[.:)]?|(?P<digit>\d+)\s*[.)]|(?P<letter>[a-dA-D])\s*\))\s+",
)
# Step 4: Missing stem check — starts immediately with "1." / "1)" (not "1.1. Topic:") or "a)" without stem
_BARE_FIRST_SUBPART_RE = re.compile(r"^\s*(?:1\s*[.)]\s+(?!\d)|1\s*[a-d]\)\s+|[a-d]\)\s+)", re.I)


@dataclass
class EssayPaper:
    questions: list[ExtractedQuestion]
    explanations: dict[int, str] = field(default_factory=dict)  # question index -> marking scheme
    points: dict[int, float] = field(default_factory=dict)  # question index -> points printed in the paper
    figures: dict[int, bytes] = field(default_factory=dict)  # question index -> cropped figure PNG bytes
    figure_captions: dict[int, dict[str, str]] = field(default_factory=dict)  # question index -> {"Hình 1.1": "stem", "Hình 1.2": "3"}
    broken_crops: dict[int, bytes] = field(default_factory=dict)  # question index -> >=2000px PNG for formula transcription
    matrix_difficulties: dict[int, str] = field(default_factory=dict)  # question index -> difficulty from matrix tag
    question_flags: dict[int, list[str]] = field(default_factory=dict)  # question index -> ["missing_stem", "figure_failed"]
    # question index -> section title printed in the heading ("Câu I. (4,0 điểm) CƠ HỌC CHẤT ĐIỂM"): used to
    # classify the question, never part of its text
    topics: dict[int, str] = field(default_factory=dict)
    key_only: bool = False  # the file is a marking scheme without the questions


def _text(el) -> str:
    return " ".join("".join(el.itertext()).split())


def _number(token: str) -> int:
    return int(token) if token.isdigit() else _ROMAN.get(token.upper(), 0)


def extract_matrix_difficulty(text: str) -> str | None:
    """Step 7: Extract difficulty from printed matrix tags e.g. '(NB 0,5 – VDC 1,5)' -> 'ADVANCED_APPLICATION'.
    Returns None if the text has no printed matrix tag (so Admin chooses the difficulty, never guessed by AI)."""
    if not text:
        return None
    tags = _MATRIX_TAG.findall(text)
    if not tags:
        return None
    best_diff: str | None = None
    best_rank = 0
    for m in _MATRIX_TAG.finditer(text):
        tag_str = m.group(0)
        for tok in _MATRIX_TOKEN.finditer(tag_str):
            code = tok.group(1).upper()
            mapped = _LEVEL_MAP.get(code)
            if mapped and _LEVEL_RANK[mapped] > best_rank:
                best_rank = _LEVEL_RANK[mapped]
                best_diff = mapped
    return best_diff


def match_essay_header(line: str) -> tuple[int, float | None, str] | None:
    """Step 1: Match 'Bài 1. (5,0 điểm) – DAO ĐỘNG CƠ HỌC' or 'Câu 1 (4,0 điểm)'.
    Returns `(question_number, points, trailing_title)` or None."""
    s = line.strip()
    if m := HEADER_WITH_POINTS.match(s):
        num = _number(m.group("num"))
        pts = float(m.group("pts").replace(",", "."))
        title = (m.group("title") or "").strip()
        if num > 0:
            return num, pts, title
    if m := HEADER_TITLE_POINTS.match(s):
        num = _number(m.group("num"))
        pts = float(m.group("pts").replace(",", "."))
        title = " ".join(p for p in ((m.group("title") or "").strip(), (m.group("rest") or "").strip()) if p)
        if num > 0:
            return num, pts, title
    return None


def parse_figure_captions(text: str) -> dict[str, str]:
    """Step 3: Map each figure caption ('Hình 1.1', 'Hình 1.2') to its scope:
    - 'stem' if mentioned in the introductory stem (belongs to the whole problem)
    - sub-part label ('1', '2', '3', '2a', '3b') if mentioned inside that sub-part."""
    mapping: dict[str, str] = {}
    current_part = "stem"
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if sm := _SUBPART_START_RE.match(line):
            raw_label = sm.group("num_letter") or sm.group("decimal") or sm.group("digit") or sm.group("letter") or ""
            current_part = re.sub(r"\s+", "", raw_label)
        for cm in _CAPTION_RE.finditer(line):
            # Normalize "Hình  1.1" -> "Hình 1.1"
            cap = re.sub(r"\s+", " ", cm.group(1).strip())
            cap = cap[0].upper() + cap[1:]
            mapping.setdefault(cap, current_part)
    return mapping


_STEM_OPENING = re.compile(
    r"^(?:cho|một|mot|trong|xét|xet|tính|tinh|tìm|tim|giải|giai|chứng minh|người ta|nguoi ta|có|hai|ba|biết|"
    r"khi|hãy|hay|với|voi|gọi|goi|trên|tren|ở|o|dùng|đặt|đề|nêu|viết|hình|bảng|năm|nghiên cứu|các|những|\d)\b",
    re.IGNORECASE,
)


def is_section_title(text: str) -> bool:
    """Whether the rest of a heading line ("Câu I. (4,0 điểm) CƠ HỌC CHẤT ĐIỂM") is a section title, not the
    first words of the question ("Câu 2 (4,0 điểm). Cho đa thức ...")."""
    text = " ".join(text.split()).strip(" .:-–—")
    letters = [c for c in text if c.isalpha()]
    if not letters or len(text) > 90 or "$" in text:
        return False
    if all(c.isupper() for c in letters):
        return True
    return not _STEM_OPENING.match(text) and not text.endswith(("?", ":"))


def strip_topic(content: str, topic: str | None) -> str:
    """Drop the section title from the start of a question's text (it is kept apart, for classification)."""
    if not topic:
        return content

    def norm(text: str) -> str:
        return " ".join(text.split()).casefold().strip(" .:-–—")

    lines = content.lstrip().split("\n")
    if lines and norm(lines[0]) == norm(topic):
        return "\n".join(lines[1:]).lstrip("\n")
    text = content.lstrip()
    if text.casefold().startswith(topic.strip().casefold()):
        return text[len(topic.strip()) :].lstrip(" -–—.:\n")
    return content


def has_missing_stem(content: str, topic_title: str = "") -> bool:
    """Step 4: Return True when a problem's body starts immediately with a subpart ('1.', 'a)')
    that lacks an introductory stem/scenario (e.g. cut off from previous page).
    Does NOT flag complete multi-part problems (having parts '1.' and '2.', or where '1.' has its own detailed context)."""
    body = content.strip()
    if topic_title and body.startswith(topic_title):
        body = body[len(topic_title) :].lstrip(" -–—.:\n")
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if not lines:
        return True
    first_line = lines[0]
    if len(lines) > 1 and lines[0].isupper() and len(lines[0]) < 80 and not _BARE_FIRST_SUBPART_RE.match(lines[0]):
        first_line = lines[1]

    if not _BARE_FIRST_SUBPART_RE.match(first_line):
        return False

    # If it starts with 'b)', 'c)', 'd)' or '2.', '3.' without previous parts: definitely severed
    if re.match(r"^\s*(?:[b-dB-D]\s*\)|(?:2|3|4)\s*[.)]\s+)", first_line):
        return True

    # If it starts with 'a)' or 'a.':
    if re.match(r"^\s*[a-dA-D]\s*[.)]", first_line):
        # If it has part b) and sufficient context (> 100 chars), it's a self-contained question with items a), b)
        has_part_b = any(re.match(r"^\s*[bB]\s*[.)]\s+", ln) for ln in lines[1:])
        if has_part_b and len(body) > 100:
            return False
        return True

    # If it starts with '1.' or '1)':
    # 1. If the problem has a subsequent part '2.' or '2)', it is a multi-part problem (1., 2.), not missing stem
    has_part_2 = any(re.match(r"^\s*2\s*[.)]\s+|2\s*[a-d]\)\s+", ln, re.I) for ln in lines[1:])
    if has_part_2:
        return False

    # 2. If Part 1 itself has full problem context (long text > 80 chars or internal items like a), b), c))
    has_sub_letters = any(re.match(r"^\s*[a-d]\s*\)\s+", ln, re.I) for ln in lines)
    if len(body) > 80 or has_sub_letters:
        return False

    return True


def references_figure(content: str) -> bool:
    """Step 4: Return True if the question text mentions a figure ('như hình', 'Hình 1.1', 'hình vẽ'...)."""
    return bool(_FIGURE_MENTION_RE.search(content) or _CAPTION_RE.search(content))


def validate_essay_split(
    questions: list[ExtractedQuestion],
    points: dict[int, float],
    *,
    declared_count: int | None = None,
    declared_total_points: float | None = None,
) -> bool:
    """Step 4: Verify extracted problems against printed exam metadata:
    1. At least 1 problem extracted.
    2. Problem numbers are strictly contiguous 1..N.
    3. Number of problems matches 'Đề thi gồm N bài' if printed.
    4. Sum of problem points matches declared total points if printed."""
    if not questions:
        return False
    numbers = [q.number for q in questions]
    if numbers != list(range(1, len(questions) + 1)):
        log.warning("Essay split validation failed: non-contiguous numbers %s", numbers)
        return False
    if declared_count is not None and len(questions) != declared_count:
        log.warning(
            "Essay split validation failed: extracted %s problems but paper declares %s",
            len(questions),
            declared_count,
        )
        return False
    if declared_total_points is not None and points and len(points) == len(questions):
        total_pts = sum(points.values())
        if abs(total_pts - declared_total_points) > 0.15:
            log.warning(
                "Essay split validation failed: sum of points %.2f != declared %.2f",
                total_pts,
                declared_total_points,
            )
            return False
    return True


def _clean_lines(text: str) -> str:
    text = clean_symbol_pua(text)
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
    texts = [_text(p) for p in el.iter("p", "h1", "h2", "h3", "h4", "strong", "th", "em", "td")] or [_text(el)]
    if any(_KEY_HEADING.match(t) and len(t) < 160 for t in texts if t):
        return True
    # Also recognize a marking-scheme table whose header row is "CÂU | NỘI DUNG | ĐIỂM" or "Câu | Ý | Nội dung | Điểm"
    if el.tag == "table":
        first_tr = next(el.iter("tr"), None)
        if first_tr is not None:
            hdr_cells = [_text(c).lower() for c in first_tr.xpath("./td|./th")]
            if len(hdr_cells) >= 2 and hdr_cells[0] in ("câu", "bài") and any("điểm" in c for c in hdr_cells):
                return True
    return False


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
            found = re.match(r"^(?:câu|bài)?\s*(\d{1,2}|I{1,3}|IV|VI{0,3}|IX|X)\s*(?:\([^)]*\))?\s*[.:]?$", first, re.I)
            if found:
                current = _number(found.group(1))
                cells = cells[1:]
            elif re.fullmatch(r"(?:câu|bài|ý|đáp án|điểm|nội dung|thang điểm|biểu điểm)?", first, re.I) and tr.xpath("./th"):
                continue  # the header row
            if not current:
                continue
            parts = []
            for cell in cells:
                content = to_content([cell]).text.strip()
                if not content or _PART_LABELS.fullmatch(content):
                    continue  # empty, or the "Ý" column (1.1 / 1.2, a / b)
                if all(_SCORE_ONLY.match(line) for line in content.split("\n") if line.strip()):
                    continue  # the "Điểm" column: scores for the marker, not part of the model answer
                parts.append(content)
            if parts:
                keys.setdefault(current, []).append(" ".join(parts))
    return keys


def _key_from_paragraphs(blocks: list) -> dict[int, list[str]]:
    """A marking scheme written as "Câu 1." / "Bài 1." paragraphs."""
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


def _img_to_png_bytes(img_el, media_dir: Path | None) -> bytes | None:
    """Convert an HTML <img> element extracted by pandoc into PNG bytes; returns None for 0-width decorative images."""
    style = (img_el.get("style") or "").replace(" ", "").lower()
    if "width:0in" in style or "width:0pt" in style or "width:0px" in style or "height:0in" in style:
        return None
    src = img_el.get("src") or ""
    if not src:
        return None
    candidates = [Path(src)]
    if media_dir is not None:
        candidates.append(media_dir / src)
    for cand in candidates:
        if cand.exists() and cand.is_file():
            try:
                with Image.open(cand) as im:
                    if im.width < 16 or im.height < 16:
                        return None
                    buf = io.BytesIO()
                    im.convert("RGB").save(buf, format="PNG", optimize=True)
                    return buf.getvalue()
            except Exception:
                return None
    return None


def _is_inline_key_table(el) -> bool:
    """Detect an inline marking-scheme table ('Ý | NỘI DUNG | ĐIỂM') placed directly after a question."""
    if el.tag != "table":
        return False
    first_tr = next(el.iter("tr"), None)
    if first_tr is None:
        return False
    hdr = [_text(c).lower() for c in first_tr.xpath("./td|./th")]
    return len(hdr) >= 2 and any("nội dung" in c or "đáp án" in c for c in hdr) and any("điểm" in c for c in hdr)


def _extract_inline_table_solution(tables: list) -> str:
    lines: list[str] = []
    for tbl in tables:
        for tr_idx, tr in enumerate(tbl.iter("tr")):
            cells = tr.xpath("./td|./th")
            if not cells:
                continue
            if tr_idx == 0:
                hdr = [_text(c).lower() for c in cells]
                if any("điểm" in c for c in hdr):
                    continue
            row_parts: list[str] = []
            for cell in cells:
                c_text = to_content([cell]).text.strip()
                if not c_text or _PART_LABELS.fullmatch(c_text):
                    continue
                if all(_SCORE_ONLY.match(ln) for ln in c_text.split("\n") if ln.strip()):
                    continue
                row_parts.append(c_text)
            if row_parts:
                lines.append(" ".join(row_parts))
    return _clean_lines("\n".join(lines))


def parse_essay_html(document: str, media_dir: Path | None = None) -> EssayPaper | None:
    root = lxml_html.fromstring(f"<div>{document}</div>")
    blocks = [b for b in root if isinstance(b.tag, str)]
    starts = [i for i, b in enumerate(blocks) if _question_start(b)]
    key_at = next((i for i, b in enumerate(blocks) if _key_start(b)), None)
    if key_at is not None and (not starts or key_at < starts[0]):
        return EssayPaper([], key_only=True)  # a marking scheme alone
    exam_starts = [i for i in starts if key_at is None or i < key_at]
    if len(exam_starts) < 1:
        return None

    # Extract declared count / total points from preamble before first question
    preamble_text = "\n".join(_text(b) for b in blocks[: exam_starts[0]])
    declared_count = _extract_declared_count(preamble_text)
    declared_total_pts = _extract_declared_points(preamble_text)

    end_of_exam = key_at if key_at is not None else len(blocks)
    questions: list[ExtractedQuestion] = []
    points: dict[int, float] = {}
    figures: dict[int, bytes] = {}
    explanations: dict[int, str] = {}
    matrix_diffs: dict[int, str] = {}
    q_flags: dict[int, list[str]] = {}
    topics: dict[int, str] = {}
    fig_captions: dict[int, dict[str, str]] = {}

    for k, start in enumerate(exam_starts):
        stop = exam_starts[k + 1] if k + 1 < len(exam_starts) else end_of_exam
        raw_segment = blocks[start:stop]
        inline_key_tables = [b for b in raw_segment if _is_inline_key_table(b)]
        segment = [b for b in raw_segment if not _is_inline_key_table(b)]
        seg_imgs = [img for seg in segment for img in seg.xpath(".//img")]
        real_imgs: list[bytes] = []
        for img_el in seg_imgs:
            style = (img_el.get("style") or "").replace(" ", "").lower()
            if "width:0in" in style or "width:0pt" in style or "width:0px" in style or "height:0in" in style:
                continue
            png_data = _img_to_png_bytes(img_el, media_dir)
            if png_data is None:
                return None  # a figure or formula image we could not convert: let page geometry or vision handle it
            real_imgs.append(png_data)

        raw_text = re.sub(r"\[image\]", "", to_content(segment).text)
        head_line = _text(segment[0])
        header_parsed = match_essay_header(head_line)
        head = _question_start(segment[0])
        text = (
            raw_text[head.end() :]
            if head and raw_text.lower().lstrip().startswith(head.group(0).strip().lower())
            else raw_text
        )
        idx = len(questions)
        if m := _POINTS.match(text):
            points[idx] = float(m.group(1).replace(",", "."))
            text = text[m.end() :]
        elif header_parsed and header_parsed[1] is not None:
            points[idx] = header_parsed[1]

        # Step 7: Extract matrix difficulty BEFORE stripping matrix tags
        if m_diff := extract_matrix_difficulty(raw_text):
            matrix_diffs[idx] = m_diff

        content = _clean_lines(text)
        title = header_parsed[2] if header_parsed else ""
        if title and not is_section_title(title):
            title = ""
        if title:
            content = strip_topic(content, title)
        if len(content) < 15:
            continue
        if title:
            topics[idx] = title
        q_num = header_parsed[0] if header_parsed else (_number(head.group(1)) if head else k + 1)

        if real_imgs:
            figures[idx] = real_imgs[0]
        if inline_key_tables:
            inline_sol = _extract_inline_table_solution(inline_key_tables)
            if len(inline_sol) >= 10:
                explanations[idx] = inline_sol

        # Step 3: Caption mapping
        caps = parse_figure_captions(content)
        if caps:
            fig_captions[idx] = caps

        # Step 4: Check per-question flags
        flags: list[str] = []
        if has_missing_stem(content, header_parsed[2] if header_parsed else ""):
            flags.append("missing_stem")
        if references_figure(content) and idx not in figures:
            flags.append("figure_failed")
        if flags:
            q_flags[idx] = flags

        questions.append(
            ExtractedQuestion(
                number=q_num,
                page=1,
                type="ESSAY",
                difficulty=matrix_diffs.get(idx),
                content=content,
                choices=[],
            )
        )
    if len(questions) < 2 and declared_count != 1:
        return None
    if not validate_essay_split(
        questions, points, declared_count=declared_count, declared_total_points=declared_total_pts
    ):
        return None

    if key_at is not None:
        key_blocks = blocks[key_at:]
        keys = _key_from_tables(key_blocks) or _key_from_paragraphs(key_blocks)
        for index, q in enumerate(questions):
            if lines := keys.get(q.number):
                explanation = _clean_lines("\n".join(lines))
                if len(explanation) >= 10:
                    explanations[index] = explanation
    return EssayPaper(
        questions=questions,
        explanations=explanations,
        points=points,
        figures=figures,
        figure_captions=fig_captions,
        matrix_difficulties=matrix_diffs,
        question_flags=q_flags,
        topics=topics,
    )


def read_essay_docx(path: Path) -> EssayPaper | None:
    import pypandoc

    with tempfile.TemporaryDirectory(prefix="docx-essay-") as media:
        media_path = Path(media)
        for track_mode in ("accept", "reject", "all"):
            try:
                document = pypandoc.convert_file(
                    str(path),
                    "html",
                    extra_args=["--wrap=none", f"--track-changes={track_mode}", f"--extract-media={media}"],
                )
            except (RuntimeError, OSError) as exc:
                log.info("pandoc could not read %s: %s", path.name, exc)
                return None
            parsed = parse_essay_html(document, media_path)
            if parsed is not None:
                return parsed
    return None


# ============================================================================
# Coordinate-based PDF Essay & HSG Problem Splitter (Steps 1, 2, 3, 4, 5, 7)
# ============================================================================


@dataclass
class PdfLine:
    page: int  # 1-based page number
    ymin: int  # 0..1000
    xmin: int  # 0..1000
    ymax: int  # 0..1000
    xmax: int  # 0..1000
    text: str
    has_pua: bool = False  # Private Use Area characters (broken symbol fonts)


def _extract_declared_count(text: str) -> int | None:
    if m := _DECLARED_COUNT.search(text):
        if m.group(1):
            return int(m.group(1))
        if m.group(2):
            return _VN_NUMBERS.get(m.group(2).lower())
    return None


def _extract_declared_points(text: str) -> float | None:
    if m := _DECLARED_TOTAL_POINTS.search(text):
        return float(m.group(1).replace(",", "."))
    return None


def _is_broken_text(text: str, has_pua: bool = False) -> bool:
    """Step 5: Detect if a problem block's text has broken formulas (one symbol per line, PUA chars, or formula fragments)."""
    if has_pua or sum(0xE000 <= ord(c) <= 0xF8FF for c in text) > 2:
        return True
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return True
    # 1. Short lines ratio (math/chemical pieces falling onto separate lines: 'RT', 'k -1', '1 2M', etc.)
    short2 = sum(len(ln) <= 2 for ln in lines) / len(lines)
    short6 = sum(len(ln) <= 6 for ln in lines) / len(lines)
    if short2 > 0.08 or short6 > 0.15:
        return True
    # 2. Fragmented reaction arrows or symbols
    if re.search(r"[→←\-=–—>]{3,}|[→]{2,}", text):
        return True
    # 3. Floating reaction / math fragments
    if re.search(r"\b[kK]\s*[-=]\s*\d+|\b(?:RT|\.e|\.10\b|mol\.L|atm\.L)\b", text):
        return True
    # 4. Fused words from font transitions
    if re.search(r"[\u00C0-\u1EF9a-zA-Z]\d+-[a-zA-Z]|\)[a-zA-Z\u00C0-\u1EF9]{3,}", text):
        return True
    return False


def _extract_pdf_lines(doc) -> tuple[list[PdfLine], list[PageGeometry]]:
    """Step 1: Use PyMuPDF `get_text("dict")` to collect positioned lines in reading order,
    stripping headers, footers, and light watermarks."""
    all_lines: list[PdfLine] = []
    geometries: list[PageGeometry] = []

    for page_idx, page in enumerate(doc, start=1):
        width, height = page.rect.width, page.rect.height
        if width <= 0 or height <= 0:
            geometries.append(PageGeometry())
            continue
        geom = page_geometry(page)
        geometries.append(geom)

        raw_dict = page.get_text("dict")
        page_lines: list[PdfLine] = []
        for block in raw_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if s.get("text", "").strip() and not is_light_text(s)]
                if not spans:
                    continue
                bbox = line.get("bbox", (0, 0, 0, 0))
                ymin = round(max(0.0, bbox[1]) / height * 1000)
                xmin = round(max(0.0, bbox[0]) / width * 1000)
                ymax = round(min(height, bbox[3]) / height * 1000)
                xmax = round(min(width, bbox[2]) / width * 1000)
                line_text = clean_symbol_pua("".join(s.get("text", "") for s in spans).strip())
                if not line_text:
                    continue
                has_pua = any(0xE000 <= ord(c) <= 0xF8FF for c in line_text)
                page_lines.append(
                    PdfLine(
                        page=page_idx,
                        ymin=ymin,
                        xmin=xmin,
                        ymax=ymax,
                        xmax=xmax,
                        text=line_text,
                        has_pua=has_pua,
                    )
                )

        # Sort by vertical position (bucketed by 6 units ~ 0.6% page height) then horizontal position
        page_lines.sort(key=lambda l: (l.ymin // 6, l.xmin))

        # Merge horizontally adjacent fragments on the same visual line
        merged: list[PdfLine] = []
        for ln in page_lines:
            if (
                merged
                and abs(ln.ymin - merged[-1].ymin) <= 8
                and abs(ln.ymax - merged[-1].ymax) <= 10
                and ln.xmin >= merged[-1].xmax - 12
            ):
                prev = merged[-1]
                sep = "" if prev.text.endswith("-") or ln.text.startswith((",", ".", ":", ";", ")")) else " "
                prev.text = f"{prev.text}{sep}{ln.text}".strip()
                prev.xmax = max(prev.xmax, ln.xmax)
                prev.ymin = min(prev.ymin, ln.ymin)
                prev.ymax = max(prev.ymax, ln.ymax)
                prev.has_pua = prev.has_pua or ln.has_pua
            else:
                merged.append(ln)

        # Strip running page footers and running headers (on pages > 1)
        for ln in merged:
            if ln.ymin > 925 and _PAGE_FOOTER.match(ln.text):
                continue
            if ln.ymax < 65 and _PAGE_FOOTER.match(ln.text):
                continue
            all_lines.append(ln)

    return all_lines, geometries


def _crop_high_res_region(
    page_images: dict[int, Image.Image],
    slices: list[tuple[int, int, int]],
    min_side: int = 2000,
    context_above: int = 0,
) -> bytes | None:
    """Step 5: Render and crop ONLY the bounding region of a single problem (across 1 or more page slices)
    at >= 2000px resolution for formula transcription at temperature=0."""
    crops: list[Image.Image] = []
    for position, (page_num, ymin, ymax) in enumerate(slices):
        img = page_images.get(page_num)
        if img is None:
            continue
        w, h = img.size
        start = int(ymin * h / 1000) - 12
        # LibreOffice may print a display formula (MathType) above the line it belongs to, i.e. above the
        # question heading: keep a strip above as context, marked off by a red line the prompt refers to.
        top = max(0, start - (int(context_above * h / 1000) if position == 0 else 0))
        bottom = min(h, int(ymax * h / 1000) + 12)
        if bottom - top < 20:
            continue
        crop = img.crop((0, top, w, bottom)).convert("RGB")
        if position == 0 and start - top > 10:
            ImageDraw.Draw(crop).line((0, start - top, w, start - top), fill=(220, 0, 0), width=4)
        crops.append(crop)
    if not crops:
        return None
    if len(crops) == 1:
        combined = crops[0]
    else:
        total_w = max(c.width for c in crops)
        total_h = sum(c.height for c in crops)
        combined = Image.new("RGB", (total_w, total_h), (255, 255, 255))
        y_off = 0
        for c in crops:
            combined.paste(c, (0, y_off))
            y_off += c.height

    # Ensure >= 2000px on the longest side (or width) as required by Step 5
    max_dim = max(combined.width, combined.height)
    if max_dim < min_side:
        scale = min_side / max(max_dim, 1)
        new_w = max(min_side if combined.width >= combined.height else int(combined.width * scale), 1)
        new_h = max(min_side if combined.height > combined.width else int(combined.height * scale), 1)
        combined = combined.resize((new_w, new_h), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    combined.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _has_image_block_in_slices(
    page_blocks: dict[int, list[tuple]],
    slices: list[tuple[int, int, int]],
    page_rects: dict[int, tuple[float, float]],
) -> bool:
    """Detect embedded MathType/Word picture objects inside a problem region.

    PyMuPDF exposes MathType formulas converted by LibreOffice as image blocks,
    while their text layer is empty. Such a problem must go through the
    high-resolution transcription path even when the remaining text looks valid.
    """
    for page_num, ymin, ymax in slices:
        page_w, page_h = page_rects.get(page_num, (0, 0))
        if not page_w or not page_h:
            continue
        top, bottom = ymin * page_h / 1000, ymax * page_h / 1000
        for block in page_blocks.get(page_num, []):
            if len(block) < 7 or block[6] != 1:
                continue
            _, block_top, _, block_bottom = block[:4]
            if block_bottom >= top and block_top <= bottom:
                return True
    return False


CONTEXT_ABOVE = 60  # 0–1000: strip above a question sent with its transcription crop (misplaced formulas)
BARRIER_LINE_WIDTH = 280  # a text line this wide (0–1000) is question text: a figure band never spans it
LABEL_LINE_WIDTH = 150  # a text line up to this wide next to a figure is a label/reagent; wider ones must lie inside it
BAND_GAP = 90  # graphics this close vertically, with no question text between them, are one figure
INLINE_IMAGE_HEIGHT = 45  # a raster this short sitting on a text line is an inline formula (MathType), not a figure
# A raster this short (~1.5 cm) never makes a figure on its own: MathType formulas rendered by LibreOffice are
# images, often shifted off their text line. It still joins a figure built from drawings (atom labels, letters).
FORMULA_IMAGE_HEIGHT = 50


def _line_box(line: list[BoundingBox]) -> BoundingBox:
    return BoundingBox(
        ymin=min(b.ymin for b in line),
        xmin=min(b.xmin for b in line),
        ymax=max(b.ymax for b in line),
        xmax=max(b.xmax for b in line),
    )


def _visual_rows(lines: list[list[BoundingBox]]) -> list[list[BoundingBox]]:
    """Join PDF text lines that sit side by side on one printed line (PDFs split a line at every font change)."""
    rows: list[list[BoundingBox]] = []
    for line in sorted(lines, key=lambda ln: (_line_box(ln).ymin, _line_box(ln).xmin)):
        box = _line_box(line)
        for row in rows:
            r = _line_box(row)
            overlap = min(r.ymax, box.ymax) - max(r.ymin, box.ymin)
            gap = max(box.xmin - r.xmax, r.xmin - box.xmax)  # negative when the pieces overlap
            if overlap >= 0.5 * min(r.ymax - r.ymin, box.ymax - box.ymin) and gap <= 20:
                row.extend(line)
                break
        else:
            rows.append(list(line))
    return rows


def _is_prose(line: list[BoundingBox]) -> bool:
    """A wide line set as running text, not a row of reagents/labels spread across a reaction scheme."""
    box = _line_box(line)
    width = box.xmax - box.xmin
    if width < BARRIER_LINE_WIDTH:
        return False
    inked = sum(b.xmax - b.xmin for b in line)
    return inked / max(width, 1) >= 0.6


def _is_inline_image(box: BoundingBox, lines: list[BoundingBox]) -> bool:
    if box.ymax - box.ymin > INLINE_IMAGE_HEIGHT:
        return False
    centre = (box.ymin + box.ymax) / 2
    return any(
        ln.ymin - 4 <= centre <= ln.ymax + 4
        and max(0, max(box.xmin, ln.xmin) - min(box.xmax, ln.xmax)) <= 25
        for ln in lines
    )


def figure_bands(geom: PageGeometry, slice_ymin: int, slice_ymax: int) -> list[BoundingBox]:
    """Boxes of the uncaptioned figures inside one question slice of a page (0–1000 scale).

    Structural formulas and reaction schemes are drawn as hundreds of short bond strokes with their
    atom labels and reagents as separate text, so per-cluster crops break them into unreadable pieces.
    Here graphics are grouped into horizontal bands: clusters are merged downwards until a wide line of
    question text separates them, and the short text lines inside a band (labels, reagents, compound
    names) are kept with it. Small rasters sitting on a text line are inline formulas and are ignored.
    """
    rows = _visual_rows(geom.lines)
    lines = [_line_box(ln) for ln in rows]
    slice_lines = [ln for ln in lines if slice_ymin - 10 <= (ln.ymin + ln.ymax) / 2 <= slice_ymax + 15]
    inline = {id(b) for b in geom.images if _is_inline_image(b, lines)}
    small = {id(b) for b in geom.images if b.ymax - b.ymin <= FORMULA_IMAGE_HEIGHT}
    graphics = [
        g
        for g in geom.graphics
        if id(g) not in inline
        and (slice_ymin - 10) <= (g.ymin + g.ymax) / 2 <= (slice_ymax + 15)
        and max(g.ymax - g.ymin, g.xmax - g.xmin) >= 3
    ]
    if not graphics:
        return []

    clusters: list[dict[str, Any]] = []
    for g in sorted(graphics, key=lambda b: (b.ymin, b.xmin)):
        near = [
            c
            for c in clusters
            if max(0, max(g.ymin, c["ymin"]) - min(g.ymax, c["ymax"])) <= 30
            and max(0, max(g.xmin, c["xmin"]) - min(g.xmax, c["xmax"])) <= 35
        ]
        item = {"ymin": g.ymin, "xmin": g.xmin, "ymax": g.ymax, "xmax": g.xmax, "n": 1, "solid": id(g) not in small}
        if not near:
            clusters.append(item)
            continue
        head = near[0]
        for c in near[1:]:
            clusters.remove(c)
        for c in [*near[1:], item]:
            head["ymin"], head["xmin"] = min(head["ymin"], c["ymin"]), min(head["xmin"], c["xmin"])
            head["ymax"], head["xmax"] = max(head["ymax"], c["ymax"]), max(head["xmax"], c["xmax"])
            head["n"] += c["n"]
            head["solid"] = head["solid"] or c["solid"]

    def real(c: dict[str, Any]) -> bool:
        h, w = c["ymax"] - c["ymin"], c["xmax"] - c["xmin"]
        return c["solid"] and ((h >= 20 and w >= 20 and c["n"] >= 2) or (h >= 35 and w >= 35))

    clusters = [c for c in clusters if real(c) and c["ymax"] >= slice_ymin]
    barriers = [
        _line_box(ln)
        for ln in rows
        if _is_prose(ln) and slice_ymin - 10 <= (_line_box(ln).ymin + _line_box(ln).ymax) / 2 <= slice_ymax + 15
    ]
    barrier_ids = {(b.ymin, b.xmin, b.ymax, b.xmax) for b in barriers}

    bands: list[dict[str, Any]] = []
    for c in sorted(clusters, key=lambda c: c["ymin"]):
        if bands:
            last = bands[-1]
            gap = c["ymin"] - last["ymax"]
            blocked = any(
                last["ymax"] - 2 <= (b.ymin + b.ymax) / 2 <= c["ymin"] + 2
                and max(b.xmin, min(last["xmin"], c["xmin"])) < min(b.xmax, max(last["xmax"], c["xmax"]))
                for b in barriers
            )
            if gap <= BAND_GAP and not blocked:
                for k in ("ymin", "xmin"):
                    last[k] = min(last[k], c[k])
                for k in ("ymax", "xmax"):
                    last[k] = max(last[k], c[k])
                continue
        bands.append(dict(c))

    boxes: list[BoundingBox] = []
    for band in bands:
        for _ in range(4):  # pull in labels and reagents written as text around the drawing
            grown = False
            for ln in slice_lines:
                if (ln.ymin, ln.xmin, ln.ymax, ln.xmax) in barrier_ids:
                    continue
                centre = (ln.ymin + ln.ymax) / 2
                if not band["ymin"] - 18 <= centre <= band["ymax"] + 18:
                    continue
                if max(ln.xmin, band["xmin"] - 40) >= min(ln.xmax, band["xmax"] + 40):
                    continue
                inside = band["xmin"] - 10 <= ln.xmin and ln.xmax <= band["xmax"] + 10
                if ln.xmax - ln.xmin > LABEL_LINE_WIDTH and not inside:
                    continue
                # The tail of a question line split by inline formula images sits next to the figure on the same
                # row as that line: it is question text, not a label (labels lie inside the drawing).
                if not inside and any(
                    min(b.ymax, ln.ymax) - max(b.ymin, ln.ymin) > 0.5 * (ln.ymax - ln.ymin) for b in barriers
                ):
                    continue
                if ln.ymin < band["ymin"] or ln.xmin < band["xmin"] or ln.ymax > band["ymax"] or ln.xmax > band["xmax"]:
                    band["ymin"], band["xmin"] = min(band["ymin"], ln.ymin), min(band["xmin"], ln.xmin)
                    band["ymax"], band["xmax"] = max(band["ymax"], ln.ymax), max(band["xmax"], ln.xmax)
                    grown = True
            if not grown:
                break
        xmin = band["xmin"]
        for ln in barriers:  # question text running beside the figure: keep it out of the crop
            beside = ln.xmax < (band["xmin"] + band["xmax"]) / 2  # text on the left, figure on the right
            if beside and max(band["ymin"], ln.ymin) < min(band["ymax"], ln.ymax) and ln.xmin < xmin <= ln.xmax + 10:
                xmin = max(xmin, ln.xmax + 4)
        if band["xmax"] - xmin < 20:
            continue
        boxes.append(
            BoundingBox(
                ymin=max(band["ymin"], max(0, slice_ymin - 8)),
                xmin=xmin,
                ymax=min(band["ymax"], min(1000, slice_ymax + 15)),
                xmax=band["xmax"],
            )
        )
    return boxes



def _crop_block_figures(
    clean_images: dict[int, Image.Image],
    geometries: list[PageGeometry],
    slices: list[tuple[int, int, int]],
    target_caps: list[str] | None = None,
    page_blocks: dict[int, list[tuple]] | None = None,
    page_rects: dict[int, tuple[float, float]] | None = None,
) -> tuple[bytes | None, BoundingBox | None]:
    """Step 3: Extract figures falling inside the problem's coordinate slices `(page_num, ymin, ymax)`.
    Uses Caption-Anchored Graphic Clustering when figure labels ('Hình 1', 'Hình 2a'...) are present,
    otherwise falls back to proximity clustering with paragraph margin clamping to prevent text intrusion."""
    cropped_imgs: list[Image.Image] = []
    first_bbox: BoundingBox | None = None

    # Priority 1: Caption-Anchored Graphic Extraction if specific figures are referenced
    if target_caps and page_blocks and page_rects:
        for cap in target_caps:
            cap_rx = re.compile(r"\b" + re.escape(cap) + r"\b", re.I)
            for page_num, s_ymin, s_ymax in slices:
                if not (1 <= page_num <= len(geometries)) or page_num not in page_blocks:
                    continue
                geom = geometries[page_num - 1]
                img = clean_images.get(page_num)
                blocks = page_blocks[page_num]
                page_w, page_h = page_rects[page_num]
                if img is None:
                    continue

                # Locate caption text block on this page
                matched_cap_box = None
                for b in blocks:
                    txt = b[4].strip()
                    if len(txt) <= 35 and cap_rx.search(txt):
                        matched_cap_box = BoundingBox(
                            ymin=int(b[1] / page_h * 1000),
                            xmin=int(b[0] / page_w * 1000),
                            ymax=int(b[3] / page_h * 1000),
                            xmax=int(b[2] / page_w * 1000),
                        )
                        break
                if not matched_cap_box:
                    continue

                # Cluster graphics in this slice
                slice_graphics = [
                    g
                    for g in geom.graphics
                    if (s_ymin - 15) <= (g.ymin + g.ymax) // 2 <= (s_ymax + 20)
                    and max(g.ymax - g.ymin, g.xmax - g.xmin) >= 10
                ]
                clusters: list[dict[str, Any]] = []
                for g in slice_graphics:
                    matched = [
                        c
                        for c in clusters
                        if max(0, max(g.ymin, c["ymin"]) - min(g.ymax, c["ymax"])) <= 35
                        and max(0, max(g.xmin, c["xmin"]) - min(g.xmax, c["xmax"])) <= 40
                    ]
                    if not matched:
                        clusters.append({"ymin": g.ymin, "xmin": g.xmin, "ymax": g.ymax, "xmax": g.xmax, "items": [g]})
                    else:
                        tgt = matched[0]
                        tgt["items"].append(g)
                        tgt["ymin"] = min(tgt["ymin"], g.ymin)
                        tgt["xmin"] = min(tgt["xmin"], g.xmin)
                        tgt["ymax"] = max(tgt["ymax"], g.ymax)
                        tgt["xmax"] = max(tgt["xmax"], g.xmax)
                        for other in matched[1:]:
                            tgt["items"].extend(other["items"])
                            tgt["ymin"] = min(tgt["ymin"], other["ymin"])
                            tgt["xmin"] = min(tgt["xmin"], other["xmin"])
                            tgt["ymax"] = max(tgt["ymax"], other["ymax"])
                            tgt["xmax"] = max(tgt["xmax"], other["xmax"])
                            clusters.remove(other)

                # Find the graphic cluster directly above, below or surrounding this caption
                best_cluster = None
                best_dist = 9999
                for c in clusters:
                    gap_y = max(0, max(c["ymin"], matched_cap_box.ymin) - min(c["ymax"], matched_cap_box.ymax))
                    x_overlap = max(c["xmin"], matched_cap_box.xmin - 120) < min(c["xmax"], matched_cap_box.xmax + 120)
                    if gap_y <= 40 and abs(c["ymin"] - matched_cap_box.ymin) <= 350 and x_overlap:
                        dist = gap_y + abs(c["xmin"] - matched_cap_box.xmin) * 0.1
                        if dist < best_dist:
                            best_dist = dist
                            best_cluster = c

                if best_cluster and (best_cluster["ymax"] - best_cluster["ymin"]) >= 20:
                    crop_box = BoundingBox(
                        ymin=min(best_cluster["ymin"], matched_cap_box.ymin) - 8,
                        xmin=min(best_cluster["xmin"], matched_cap_box.xmin) - 10,
                        ymax=max(best_cluster["ymax"], matched_cap_box.ymax) + 8,
                        xmax=max(best_cluster["xmax"], matched_cap_box.xmax) + 10,
                    )
                else:
                    crop_box = BoundingBox(
                        ymin=matched_cap_box.ymin - 210,
                        xmin=matched_cap_box.xmin - 90,
                        ymax=matched_cap_box.ymax + 8,
                        xmax=matched_cap_box.xmax + 70,
                    )

                # Strict boundary clamps: prevent text intrusion from previous questions or section headers
                crop_box.ymin = max(crop_box.ymin, s_ymin)
                for b in blocks:
                    txt = b[4].strip()
                    if re.match(r"^(?:Câu|Bài)\s+[IVXLCDM\d]+", txt, re.I):
                        b_ymax = int(b[3] / page_h * 1000)
                        if matched_cap_box.ymin >= b_ymax:
                            crop_box.ymin = max(crop_box.ymin, b_ymax + 4)

                if page_num == 1:
                    crop_box.ymin = max(crop_box.ymin, 180)

                crop_box.ymin = max(0, min(1000, crop_box.ymin))
                crop_box.xmin = max(0, min(1000, crop_box.xmin))
                crop_box.ymax = max(0, min(1000, crop_box.ymax))
                crop_box.xmax = max(0, min(1000, crop_box.xmax))

                fig_crop = cut(img, crop_box, pad_x=6, pad_y=6)
                if fig_crop is not None:
                    cropped_imgs.append(fig_crop)
                    if first_bbox is None:
                        first_bbox = crop_box
                    break

        if cropped_imgs:
            if len(cropped_imgs) == 1:
                final_img = cropped_imgs[0]
            else:
                max_w = max(c.width for c in cropped_imgs)
                total_h = sum(c.height for c in cropped_imgs) + 16 * (len(cropped_imgs) - 1)
                final_img = Image.new("RGB", (max_w, total_h), (255, 255, 255))
                y_off = 0
                for c in cropped_imgs:
                    final_img.paste(c, ((max_w - c.width) // 2, y_off))
                    y_off += c.height + 16
            out = io.BytesIO()
            final_img.save(out, format="PNG", optimize=True)
            return out.getvalue(), first_bbox

    # Priority 2: diagrams without a "Hình N" caption (chemistry schemes, structural formulas, unlabeled sketches).
    for page_num, slice_ymin, slice_ymax in slices:
        if not (1 <= page_num <= len(geometries)):
            continue
        img = clean_images.get(page_num)
        if img is None:
            continue
        for band in figure_bands(geometries[page_num - 1], slice_ymin, slice_ymax):
            fig_crop = cut(img, band, pad_x=8, pad_y=8)
            if fig_crop is not None:
                cropped_imgs.append(fig_crop)
                if first_bbox is None:
                    first_bbox = band

    if not cropped_imgs:
        return None, None
    if len(cropped_imgs) == 1:
        final_img = cropped_imgs[0]
    else:
        max_w = max(c.width for c in cropped_imgs)
        total_h = sum(c.height for c in cropped_imgs) + 16 * (len(cropped_imgs) - 1)
        final_img = Image.new("RGB", (max_w, total_h), (255, 255, 255))
        y_off = 0
        for c in cropped_imgs:
            final_img.paste(c, ((max_w - c.width) // 2, y_off))
            y_off += c.height + 16

    out = io.BytesIO()
    final_img.save(out, format="PNG", optimize=True)
    return out.getvalue(), first_bbox


def _parse_pdf_answer_section(lines: list[PdfLine]) -> dict[int, str]:
    """Step 7: Parse the marking scheme / answer section (after HƯỚNG DẪN CHẤM / ĐÁP ÁN) in a PDF
    and reconstruct clean solutions mapped by problem number."""
    keys: dict[int, list[str]] = {}
    current = 0
    for ln in lines:
        txt = ln.text.strip()
        if not txt or _PAGE_FOOTER.match(txt) or _END_MARKER.match(txt):
            continue
        if _KEY_HEADING.match(txt):
            continue
        if re.fullmatch(r"(?:câu|bài|ý|nội dung|đáp án|điểm|thang điểm|hướng dẫn chấm|\s|\|)+", txt, re.I):
            continue
        if hdr := match_essay_header(txt):
            current = hdr[0]
            if hdr[2]:
                keys.setdefault(current, []).append(hdr[2])
            continue
        if m := _QUESTION.match(txt):
            num = _number(m.group(1))
            if num > 0:
                current = num
                rest = txt[m.end() :].strip()
                rest = _POINTS.sub("", rest).strip()
                if rest:
                    keys.setdefault(current, []).append(rest)
                continue
        if current > 0:
            # Strip trailing standalone point scores at the end of table rows (e.g. "0,5" or "0,25 điểm")
            cleaned_row = re.sub(r"\s+\d+(?:[.,]\d+)?\s*(?:điểm|đ)?\s*$", "", txt).strip()
            if cleaned_row and not _SCORE_ONLY.match(cleaned_row):
                keys.setdefault(current, []).append(cleaned_row)

    result: dict[int, str] = {}
    for num, part_lines in keys.items():
        cleaned = _clean_lines("\n".join(part_lines))
        if len(cleaned) >= 10:
            result[num] = cleaned
    return result


def read_essay_pdf(path: Path, dpi: int = 300) -> EssayPaper | None:
    """Steps 1–5 & 7: Coordinate-based PDF splitter for structured Essay / HSG papers
    ('Bài 1. (5,0 điểm) – DAO ĐỘNG CƠ HỌC')."""
    import pymupdf

    try:
        doc = pymupdf.open(path)
    except Exception:
        return None

    with doc:
        if doc.needs_pass or doc.page_count == 0:
            return None
        lines, geometries = _extract_pdf_lines(doc)
        if not lines:
            return None

        # Locate answer key heading ("HƯỚNG DẪN CHẤM", "ĐÁP ÁN") and "HẾT" marker
        key_idx = next((i for i, ln in enumerate(lines) if _KEY_HEADING.match(ln.text) and len(ln.text) < 140), None)
        header_hits: list[tuple[int, int, float | None, str]] = []
        for i, ln in enumerate(lines):
            if key_idx is not None and i >= key_idx:
                break
            if parsed := match_essay_header(ln.text):
                header_hits.append((i, parsed[0], parsed[1], parsed[2]))

        if key_idx is not None and not header_hits:
            return EssayPaper([], key_only=True)
        if not header_hits:
            return None

        first_header_idx = header_hits[0][0]
        end_exam_idx = key_idx if key_idx is not None else len(lines)
        for i in range(header_hits[-1][0] + 1, end_exam_idx):
            if _END_MARKER.match(lines[i].text):
                end_exam_idx = i
                break

        # Extract declared exam metadata ("Đề thi gồm 05 bài", total points) from preamble & footer
        preamble_and_tail = "\n".join(
            ln.text for ln in (*lines[:first_header_idx], *lines[end_exam_idx : key_idx or len(lines)])
        )
        declared_count = _extract_declared_count(preamble_and_tail)
        declared_total_pts = _extract_declared_points(preamble_and_tail)

        # Render pages once on demand for figure cropping (Step 3) and broken-formula crops (Step 5)
        raw_page_imgs: dict[int, Image.Image] = {}
        clean_page_imgs: dict[int, Image.Image] = {}
        page_blocks: dict[int, list[tuple]] = {
            p_idx: page.get_text("blocks") for p_idx, page in enumerate(doc, start=1)
        }
        page_rects: dict[int, tuple[float, float]] = {
            p_idx: (page.rect.width, page.rect.height) for p_idx, page in enumerate(doc, start=1)
        }
        for p_idx, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=dpi)
            raw_page_imgs[p_idx] = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            if remove_light_text(page):
                pix_clean = page.get_pixmap(dpi=dpi)
                clean_page_imgs[p_idx] = Image.open(io.BytesIO(pix_clean.tobytes("png"))).convert("RGB")
            else:
                clean_page_imgs[p_idx] = raw_page_imgs[p_idx]

    questions: list[ExtractedQuestion] = []
    points: dict[int, float] = {}
    figures: dict[int, bytes] = {}
    fig_captions: dict[int, dict[str, str]] = {}
    broken_crops: dict[int, bytes] = {}
    matrix_diffs: dict[int, str] = {}
    q_flags: dict[int, list[str]] = {}
    topics: dict[int, str] = {}
    explanations: dict[int, str] = {}

    for k, (start_line_idx, q_num, q_pts, q_title) in enumerate(header_hits):
        stop_line_idx = header_hits[k + 1][0] if k + 1 < len(header_hits) else end_exam_idx
        block_lines = lines[start_line_idx:stop_line_idx]
        if not block_lines:
            continue

        idx = len(questions)
        if q_pts is not None:
            points[idx] = q_pts

        # Build body lines: keep topic title if present, followed by all lines in the block (including 1., 2a), 3b))
        # If the block contains an inline marking-scheme table header ("Ý NỘI DUNG ĐIỂM"), split stem vs solution.
        body_parts: list[str] = []
        inline_sol_parts: list[str] = []
        in_inline_key = False
        if q_title and is_section_title(q_title):
            topics[idx] = q_title
        elif q_title:
            body_parts.append(q_title)  # the question starts on its heading line
        for ln in block_lines[1:]:
            if _END_MARKER.match(ln.text):
                break
            if not in_inline_key and _INLINE_PDF_KEY_HDR.match(ln.text):
                in_inline_key = True
                continue
            if in_inline_key:
                cleaned_row = re.sub(r"\s+\d+(?:[.,]\d+)?\s*(?:điểm|đ)?\s*$", "", ln.text).strip()
                if cleaned_row and not _SCORE_ONLY.match(cleaned_row):
                    inline_sol_parts.append(cleaned_row)
            else:
                body_parts.append(ln.text)

        raw_block_text = "\n".join(body_parts)
        has_pua = any(ln.has_pua for ln in block_lines)
        if inline_sol_parts:
            inline_sol_clean = _clean_lines("\n".join(inline_sol_parts))
            if len(inline_sol_clean) >= 10:
                explanations[idx] = inline_sol_clean

        # Step 7: Extract difficulty from printed matrix tags BEFORE stripping them
        if m_diff := extract_matrix_difficulty(raw_block_text):
            matrix_diffs[idx] = m_diff

        cleaned_content = _clean_lines(raw_block_text)
        if not cleaned_content:
            cleaned_content = q_title or ln.text

        # Calculate per-page vertical slices (page_num, ymin, ymax) for this problem
        start_page = block_lines[0].page
        end_page = block_lines[-1].page
        next_header_line = lines[stop_line_idx] if stop_line_idx < len(lines) else None

        slices: list[tuple[int, int, int]] = []
        for p_num in range(start_page, end_page + 1):
            p_lines = [l for l in block_lines if l.page == p_num]
            if not p_lines:
                continue
            s_ymin = block_lines[0].ymin if p_num == start_page else max(50, min(l.ymin for l in p_lines) - 10)
            if next_header_line and next_header_line.page == p_num:
                s_ymax = max(s_ymin + 20, next_header_line.ymin - 6)
            elif p_num < end_page:
                s_ymax = 935
            else:
                s_ymax = min(940, max(l.ymax for l in p_lines) + 180)
            slices.append((p_num, s_ymin, s_ymax))

        # Step 3: Crop figures belonging to this problem and map captions ('Hình 1.1' -> stem, 'Hình 1.2' -> part 3)
        caps = parse_figure_captions(cleaned_content)
        if caps:
            fig_captions[idx] = caps
        target_caps = list(caps.keys()) if caps else None
        fig_bytes, fig_bbox = _crop_block_figures(
            clean_page_imgs,
            geometries,
            slices,
            target_caps=target_caps,
            page_blocks=page_blocks,
            page_rects=page_rects,
        )
        if fig_bytes is not None:
            figures[idx] = fig_bytes

        # Step 5: Check if math formulas in this block are broken -> crop >=2000px image of just this problem
        has_embedded_math = _has_image_block_in_slices(page_blocks, slices, page_rects)
        # Word/MathType formulas converted by LibreOffice may become empty
        # text lines rather than image blocks.  Every essay problem therefore
        # gets a high-resolution crop; the transcription model is the authority
        # for formulas while the local parser remains the authority for layout.
        crop_png = _crop_high_res_region(raw_page_imgs, slices, min_side=2000, context_above=CONTEXT_ABOVE)
        if crop_png is not None:
            broken_crops[idx] = crop_png

        # Step 4: Per-question validation flags (do not let AI solve flagged questions)
        flags: list[str] = []
        if has_missing_stem(cleaned_content, q_title):
            flags.append("missing_stem")
        if references_figure(cleaned_content) and idx not in figures:
            flags.append("figure_failed")
        if flags:
            q_flags[idx] = flags

        questions.append(
            ExtractedQuestion(
                number=q_num,
                page=start_page,
                type="ESSAY",
                difficulty=matrix_diffs.get(idx),
                content=cleaned_content,
                choices=[],
                figure_bbox=fig_bbox,
            )
        )

    # Step 4: Whole-paper self-verification (count, contiguous 1..N, total points)
    if not validate_essay_split(
        questions,
        points,
        declared_count=declared_count,
        declared_total_points=declared_total_pts,
    ):
        return None

    # Step 7: Reconstruct answers / marking scheme if included in the same PDF
    if key_idx is not None:
        by_qnum = _parse_pdf_answer_section(lines[key_idx:])
        for index, q in enumerate(questions):
            if sol_text := by_qnum.get(q.number):
                explanations[index] = sol_text

    return EssayPaper(
        questions=questions,
        explanations=explanations,
        points=points,
        figures=figures,
        figure_captions=fig_captions,
        broken_crops=broken_crops,
        matrix_difficulties=matrix_diffs,
        question_flags=q_flags,
        topics=topics,
    )
