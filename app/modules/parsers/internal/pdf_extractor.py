"""Deterministic PDF exam question, figure-box, table, and solution extractor for V11.3 parsers."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from app.infrastructure.pdf_engine import (
    PDFEngineAdapter,
    PDFEngineError,
    PDFPasswordRequired,
    Rect,
    TextLine,
    cluster_rects,
)

# Strict Roman numeral tokens (uppercase only) + Arabic numbers so normal prose like "Câu vì" never splits.
_ROMAN_NUMERALS: dict[str, int] = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "XII": 12,
    "XIII": 13,
    "XIV": 14,
    "XV": 15,
    "XVI": 16,
    "XVII": 17,
    "XVIII": 18,
    "XIX": 19,
    "XX": 20,
}

_QUESTION_START_RE = re.compile(
    r"^\s*(?:(Câu|Cau|Bài|Bai|Question)\s+"
    r"(?:(\d{1,3})|(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV|XVI|XVII|XVIII|XIX|XX)))\b"
    r"\s*(?:([\.:\)\-–])|(?=\s*\(\s*\d|\s*$|\s+[A-ZÀ-Ỹ0-9\[]))(.*)$",
)
_SECTION_HEADER_RE = re.compile(
    r"^\s*(?:PHẦN|PHAN|PART)\s+([IVXLC0-9]+)\s*[\.:\-–]?\s*(.*)$",
    re.IGNORECASE,
)
_STUDY_EXERCISE_ZONE_RE = re.compile(
    r"^\s*(?:BÀI\s+TẬP\s+TỰ\s+LUẬN|BÀI\s+TẬP\s+TRẮC\s+NGHIỆM|BÀI\s+TẬP\s+RÈN\s+LUYỆN)\s*$",
    re.IGNORECASE,
)
_ANSWER_BLOCK_RE = re.compile(
    r"^\s*(?:BẢNG\s+ĐÁP\s+ÁN|BANG\s+DAP\s+AN|ĐÁP\s+ÁN(?:\s+(?:THAM\s+KHẢO|CHI\s+TIẾT|CHÍNH\s+THỨC))?|"
    r"DAP\s+AN|HƯỚNG\s+DẪN\s+CHẤM(?:\s+CHI\s+TIẾT)?|BÀI\s+GIẢI)\s*[:\-–.]?\s*$",
    re.IGNORECASE,
)
_INLINE_ANSWER_LINE_RE = re.compile(
    r"^\s*(?:\[\s*)?(?:Đáp\s*án|Dap\s*an|Key)\s*[:=]\s*([A-D]|[ĐS]{4}|[TF]{4}|-?\d+(?:[.,]\d+)?)\s*(?:\])?\.?\s*$",
    re.IGNORECASE,
)
_ANSWER_PAIR_RE = re.compile(
    r"(?:(?:Câu|Cau|Bài|Bai)?\s*(\d{1,3})\s*[\.:\)\-–]\s*([A-D]|[ĐS]{4}|[TF]{4}|-?\d+(?:[.,]\d+)?))",
    re.IGNORECASE,
)
_END_EXAM_RE = re.compile(
    r"^\s*[-—_=•*]{3,}\s*(?:HẾT|HET)\s*[-—_=•*]{3,}\s*$|^\s*HẾT\s*$",
    re.IGNORECASE,
)
_HEADER_WATERMARK_RE = re.compile(
    r"^\s*(?:PRO\s*:\s*admin@|Trang\s+\d+\s*/\s*\d+|GV\.\s*Phan\s+Nhật\s+Linh|SĐT\s*:\s*0\d+)",
    re.IGNORECASE,
)
_OFFICIAL_HEADER_MARKER_RE = re.compile(r"(?:KỲ\s+THI|ĐỀ\s+CHÍNH\s+THỨC|TRẠI\s+HÈ|SỞ\s+GIÁO\s+DỤC)", re.I)
_POINTS_RE = re.compile(r"\(\s*(\d+(?:[.,]\d+)?)\s*(?:điểm|diem|pt|pts)\s*\)", re.IGNORECASE)
_MCQ_OPTION_SPLIT_RE = re.compile(r"(?:^|\s)([A-D])\s*[\.\)]\s+")
_TF_SUB_RE = re.compile(r"^\s*([a-d])\)\s+(.+)$")
_ESSAY_NUMBERED_SUB_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)?[.)]|[a-d]\.)\s+")
_NUMERIC_ONLY_RE = re.compile(r"-?[0-9]+(?:[.,][0-9]+)?")
_FIG_CAPTION_SHORT_RE = re.compile(
    r"^(?:Hình(?:\s+vẽ)?\s*\d*[a-z]?|[A-Za-zÀ-ỹ0-9'°\(\)/,\.\-\+=\s]{1,18})$",
    re.IGNORECASE,
)

# Private-Use-Area symbols emitted by legacy Word Symbol / MT Extra fonts.
_SYMBOL_PUA_MAP: dict[str, str] = {
    "\uf061": "α",
    "\uf062": "β",
    "\uf063": "χ",
    "\uf064": "δ",
    "\uf065": "ε",
    "\uf066": "φ",
    "\uf067": "γ",
    "\uf068": "η",
    "\uf06a": "φ",
    "\uf06b": "κ",
    "\uf06c": "λ",
    "\uf06d": "μ",
    "\uf06e": "ν",
    "\uf070": "π",
    "\uf071": "θ",
    "\uf072": "ρ",
    "\uf073": "σ",
    "\uf074": "τ",
    "\uf077": "ω",
    "\uf044": "Δ",
    "\uf057": "Ω",
    "\uf0a5": "∞",
    "\uf0a3": "≤",
    "\uf0b3": "≥",
    "\uf0b9": "≠",
    "\uf0bb": "≈",
    "\uf0b1": "±",
    "\uf0b4": "×",
    "\uf0b8": "÷",
    "\uf0ae": "→",
    "\uf0de": "⇒",
    "\uf0db": "⇔",
    "\uf0d6": "√",
    "\uf0b0": "°",
    "\uf02d": "−",
    "\uf03d": "=",
    "\uf02b": "+",
    "\uf03c": "<",
    "\uf03e": ">",
}

# Unmistakable TCVN3 markers (characters outside standard UTF-8 Vietnamese words) and absence of UTF-8 Vietnamese accents.  # noqa: E501
_TCVN3_TRIGGER_RE = re.compile(r"(?:ÑEÀ|KIEÅM|®|[\u00b5-\u00b9\u00a8\u00a9\u00ab\u00ac\u00ae\u00df\u00e4-\u00f9\u00fb\u00fe])")  # noqa: E501
_UTF8_VIET_LOWER_RE = re.compile(r"[àáâãèéêìíòóôõùúýăđĩũơư\u1ea0-\u1ef9]")
_TCVN3_DIGRAPH_MAP: dict[str, str] = {
    "ÑEÀ": "ĐỀ",
    "KIEÅM": "KIỂM",
    "EÀ": "Ề",
    "EÅ": "Ể",
    "EÁ": "Ế",
    "EÄ": "Ệ",
    "EÃ": "Ễ",
    "AØ": "À",
    "AÙ": "Á",
    "AÛ": "Ả",
    "AÕ": "Ã",
    "AÏ": "Ạ",
    "AÂ": "Â",
    "OÂ": "Ô",
    "Ñ": "Đ",
    "®": "đ",
    "¸": "á",
    "µ": "à",
    "¶": "ả",
    "·": "ã",
    "¹": "ạ",
    "¨": "ă",
    "¾": "ắ",
    "»": "ằ",
    "¼": "ẳ",
    "½": "ẵ",
    "Æ": "ặ",
    "©": "â",
    "Ê": "ấ",
    "Ç": "ầ",
    "È": "ẩ",
    "É": "ẫ",
    "Ë": "ậ",
    "Ð": "é",
    "Ì": "è",
    "Î": "ẻ",
    "Ï": "ẽ",
    "Õ": "í",
    "×": "ì",
    "Ø": "ỉ",
    "Ü": "ĩ",
    "Þ": "ị",
    "ã": "ó",
    "ß": "ò",
    "á": "ỏ",
    "â": "õ",
    "ä": "ọ",
    "«": "ô",
    "è": "ố",
    "å": "ồ",
    "æ": "ổ",
    "ç": "ỗ",
    "é": "ộ",
    "¬": "ơ",
    "í": "ớ",
    "ê": "ờ",
    "ë": "ở",
    "ì": "ỡ",
    "î": "ợ",
    "ó": "ú",
    "ï": "ù",
    "ñ": "ủ",
    "ò": "ũ",
    "ô": "ụ",
    "\xad": "ư",
    "ø": "ứ",
    "õ": "ừ",
    "ö": "ử",
    "÷": "ữ",
    "ù": "ự",
    "ý": "ý",
    "ú": "ỳ",
    "û": "ỷ",
    "ü": "ỹ",
    "þ": "ỵ",
}
_TCVN3_SINGLE_PASS_RE = re.compile("|".join(re.escape(k) for k in sorted(_TCVN3_DIGRAPH_MAP, key=len, reverse=True)))


def _normalize_line_text(text: str) -> str:
    if not text:
        return ""
    for pua, repl in _SYMBOL_PUA_MAP.items():
        if pua in text:
            text = text.replace(pua, repl)
    text = text.replace("\xa0", " ").replace("\u200b", "")
    if not _UTF8_VIET_LOWER_RE.search(text) and _TCVN3_TRIGGER_RE.search(text):
        text = _TCVN3_SINGLE_PASS_RE.sub(lambda m: _TCVN3_DIGRAPH_MAP[m.group(0)], text)
    text = unicodedata.normalize("NFC", text)
    return re.sub(r"[ \t]+", " ", text).strip()


@dataclass(frozen=True, slots=True)
class ExtractedPageMetric:
    page_no: int
    width_pt: float
    height_pt: float
    heading: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_no": self.page_no,
            "width_pt": self.width_pt,
            "height_pt": self.height_pt,
            "heading": self.heading,
        }


@dataclass(frozen=True, slots=True)
class ExtractedQuestionDraft:
    question_no: int
    page_number: int
    points: float
    item_type: str
    stem_md: str
    options_md: tuple[str, ...]
    paired_answer: str | None
    bbox_json: dict[str, Any]
    original_question_label: str
    section_code: str = "PART_I"
    section_title: str = "Phần câu hỏi trích xuất"
    figure_bboxes: tuple[dict[str, Any], ...] = ()
    solution_md: str | None = None
    explicit_points: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "question_no": self.question_no,
            "page_number": self.page_number,
            "points": self.points,
            "item_type": self.item_type,
            "stem_md": self.stem_md,
            "options_md": list(self.options_md),
            "paired_answer": self.paired_answer,
            "bbox_json": dict(self.bbox_json),
            "original_question_label": self.original_question_label,
        }
        if self.figure_bboxes:
            payload["figure_bboxes"] = [dict(fb) for fb in self.figure_bboxes]
        if self.solution_md:
            payload["solution_md"] = self.solution_md
        return payload


@dataclass(frozen=True, slots=True)
class ExtractedSectionDraft:
    section_code: str
    title: str
    questions: tuple[ExtractedQuestionDraft, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_code": self.section_code,
            "title": self.title,
            "questions": [q.to_dict() for q in self.questions],
        }


@dataclass(frozen=True, slots=True)
class PdfExtractionArtifact:
    pages: tuple[ExtractedPageMetric, ...]
    sections: tuple[ExtractedSectionDraft, ...]
    raw_line_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "pages": [p.to_dict() for p in self.pages],
            "sections": [s.to_dict() for s in self.sections],
            "raw_line_count": self.raw_line_count,
        }


@dataclass
class _WorkingQuestion:
    question_no: int
    source_qno: int
    label: str
    section_idx: int
    section_code: str
    section_title: str
    page_number: int
    page_width: float
    page_height: float
    points: float | None
    stem_lines: list[str]
    options_by_key: dict[str, str]
    option_order: list[str]
    tf_by_key: dict[str, str]
    paired_answer: str | None
    primary_rects: list[Rect]
    figure_rects: list[tuple[int, Rect]]
    has_numbered_subparts: bool = False
    is_study_exercise_zone: bool = False


@dataclass(frozen=True, slots=True)
class _PageGraphic:
    tag: str
    rect: Rect


@dataclass(frozen=True, slots=True)
class _RowItem:
    y0: float
    y1: float
    x0: float
    x1: float
    text: str
    rect: Rect
    is_table: bool = False


def _is_page_frame(rect: Rect, page_w: float, page_h: float) -> bool:
    return rect.width > page_w * 0.80 and rect.height > page_h * 0.72


def _merge_overlapping_rects(rects: list[Rect], pad: float = 10.0) -> list[Rect]:
    if not rects:
        return []
    merged: list[Rect] = []
    # Run greedy fixed-point pass so chained intersecting rects collapse cleanly.
    pending = sorted(rects, key=lambda r: (r.y0, r.x0))
    while pending:
        cur = pending.pop(0)
        changed = True
        while changed:
            changed = False
            rest: list[Rect] = []
            expanded = cur.expanded(pad)
            for other in pending:
                if expanded.intersects(other):
                    cur = cur.union(other)
                    expanded = cur.expanded(pad)
                    changed = True
                else:
                    rest.append(other)
            pending = rest
        merged.append(cur)
    return sorted(merged, key=lambda r: (r.y0, r.x0))


def _is_real_text_table(table: Any) -> bool:
    rows = getattr(table, "rows", ()) or ()
    if len(rows) < 2:
        return False
    # True multi-column data tables must have at least 2 rows with >= 2 populated distinct columns.
    multi_col_rows = 0
    for r in rows:
        populated_cols = sum(1 for cell in r if (cell or "").strip())
        if populated_cols >= 2:
            multi_col_rows += 1
    if multi_col_rows < 2:
        return False
    preview = " ".join((cell or "").strip() for r in rows[:2] for cell in r)
    return not (len(rows) <= 3 and _OFFICIAL_HEADER_MARKER_RE.search(preview))


def _table_to_markdown(rows: tuple[tuple[str | None, ...], ...]) -> list[str]:
    clean_rows: list[list[str]] = []
    for row in rows:
        cleaned = [_normalize_line_text((c or "").replace("\n", " ")) for c in row]
        if any(cleaned):
            clean_rows.append(cleaned)
    if not clean_rows:
        return []
    width = max(len(r) for r in clean_rows)
    if width < 2:
        return []
    padded = [r + [""] * (width - len(r)) for r in clean_rows]
    md = ["| " + " | ".join(padded[0]) + " |", "| " + " | ".join("---" for _ in range(width)) + " |"]
    for padded_row in padded[1:]:
        md.append("| " + " | ".join(padded_row) + " |")
    return md


def _detect_page_graphics(
    pdf: PDFEngineAdapter,
    page_idx: int,
    *,
    has_official_header_p1: bool,
    table_rects: list[Rect],
) -> tuple[list[_PageGraphic], list[Rect]]:
    w, h = pdf.page_size(page_idx)
    rasters = [
        im.rect
        for im in pdf.images(page_idx)
        if im.rect.width >= 30.0 and im.rect.height >= 26.0 and not _is_page_frame(im.rect, w, h)
    ]
    # Filter out translucent diagonal watermarks (opacity < 0.72) and Word white background rects.
    visible_drawings = [
        d.rect
        for d in pdf.drawings(page_idx)
        if d.opacity >= 0.72
        and not (not d.stroke and d.fill_color and min(d.fill_color) >= 0.88)
        and not _is_page_frame(d.rect, w, h)
        and not any(d.rect.overlap_ratio(tr) >= 0.55 for tr in table_rects)
    ]
    vectors = [
        r
        for r in cluster_rects(visible_drawings, x_tolerance=14.0, y_tolerance=14.0)
        if r.width >= 30.0
        and r.height >= 26.0
        and not _is_page_frame(r, w, h)
        and not any(r.overlap_ratio(tr) >= 0.55 for tr in table_rects)
    ]
    merged = _merge_overlapping_rects(rasters + vectors, pad=10.0)
    graphics: list[_PageGraphic] = []
    fig_rects: list[Rect] = []
    fig_idx = 0
    for r in merged:
        # Skip Page 1 contest crest/logo inside official header box
        if page_idx == 0 and has_official_header_p1 and r.y0 < 150.0 and r.x0 < 245.0:
            continue
        # Clamp strictly within page boundaries
        clamped = Rect(max(0.0, r.x0), max(0.0, r.y0), min(w, r.x1), min(h, r.y1))
        if clamped.width < 28.0 or clamped.height < 24.0:
            continue
        tag = f"[[FIG_P{page_idx + 1}_{fig_idx}]]"
        graphics.append(_PageGraphic(tag=tag, rect=clamped))
        fig_rects.append(clamped)
        fig_idx += 1
    return graphics, fig_rects


def _is_stray_diagram_label(line: TextLine, fig_rects: list[Rect]) -> bool:
    """Drop short labels (`Hình 1`, `A`, `B`, `O`, `10cm`, `t(°C)`) orbiting just outside a figure rect."""
    if not fig_rects:
        return False
    txt = _normalize_line_text(line.text)
    if not txt or len(txt) > 22:
        return False
    if _QUESTION_START_RE.match(txt) or _SECTION_HEADER_RE.match(txt) or _TF_SUB_RE.match(txt):
        return False
    if _MCQ_OPTION_SPLIT_RE.match(txt):
        return False
    cx = (line.rect.x0 + line.rect.x1) / 2.0
    cy = (line.rect.y0 + line.rect.y1) / 2.0
    for fr in fig_rects:
        # Vertical envelope around the diagram + right/inner margin so left-column body text is never touched.
        if fr.x0 - 18.0 <= cx <= fr.x1 + 22.0 and fr.y0 - 20.0 <= cy <= fr.y1 + 22.0:
            if _FIG_CAPTION_SHORT_RE.match(txt):
                return True
    return False


def _merge_visual_rows(lines: list[TextLine]) -> list[TextLine]:
    """Reassemble same-row left/right chunks and small subscript/superscript fragments in reading order."""
    if not lines:
        return []

    sorted_lines = sorted(lines, key=lambda ln: ((ln.rect.y0 + ln.rect.y1) / 2.0, ln.rect.x0))
    rows: list[list[TextLine]] = []

    for ln in sorted_lines:
        txt = _normalize_line_text(ln.text)
        if not txt:
            continue
        ln_norm = TextLine(txt, ln.rect)
        cy = (ln.rect.y0 + ln.rect.y1) / 2.0
        h = max(1.0, ln.rect.height)
        is_tiny_frag = len(txt) <= 8 and h <= 13.0 and not _QUESTION_START_RE.match(txt)

        placed = False
        for row in reversed(rows[-4:]):
            anchor = row[0]
            a_y0 = min(item.rect.y0 for item in row)
            a_y1 = max(item.rect.y1 for item in row)
            a_cy = (a_y0 + a_y1) / 2.0
            a_h = max(1.0, a_y1 - a_y0)

            overlap = min(ln.rect.y1, a_y1) - max(ln.rect.y0, a_y0)
            min_h = min(h, a_h)

            # Same horizontal baseline (fixes 0.8pt subscript jitter flipping right text before left text)
            same_baseline = (overlap / min_h >= 0.45) or (abs(cy - a_cy) <= 4.2 and ln.rect.x0 > anchor.rect.x0 + 20.0)
            if same_baseline:
                row.append(ln_norm)
                placed = True
                break

            # Tiny orphan sub/superscript line within +/- 8.5pt vertically and overlapping horizontally in the row
            if is_tiny_frag and abs(cy - a_cy) <= 8.5:
                row_x0 = min(item.rect.x0 for item in row)
                row_x1 = max(item.rect.x1 for item in row)
                if ln.rect.x0 >= row_x0 - 4.0 and ln.rect.x0 <= row_x1 + 28.0:
                    row.append(ln_norm)
                    placed = True
                    break

        if not placed:
            rows.append([ln_norm])

    merged_lines: list[TextLine] = []
    for row in rows:
        ordered = sorted(row, key=lambda item: (item.rect.x0, item.rect.y0))
        combined_text = ordered[0].text
        combined_rect = ordered[0].rect
        for seg in ordered[1:]:
            gap = seg.rect.x0 - combined_rect.x1
            sep = " " if gap > 1.5 or not (len(seg.text) <= 2 and seg.text.isalnum()) else ""
            # Avoid joining words without space except pure single-token sub/superscript right next to variable
            if sep == "" and combined_text and combined_text[-1] in "=.+-*/(":
                sep = " "
            combined_text = f"{combined_text}{sep}{seg.text}".strip()
            combined_rect = combined_rect.union(seg.rect)
        merged_lines.append(TextLine(_normalize_line_text(combined_text), combined_rect))

    return sorted(merged_lines, key=lambda ln: (ln.rect.y0, ln.rect.x0))


def _extract_mcq_options_from_line(
    line_text: str, *, allow_non_a_start: bool
) -> tuple[str, list[tuple[str, str]]]:
    matches = list(_MCQ_OPTION_SPLIT_RE.finditer(line_text))
    if not matches:
        return line_text.strip(), []

    first_letter = matches[0].group(1).upper()
    if first_letter != "A" and not allow_non_a_start:
        return line_text.strip(), []

    if len(matches) == 1 and first_letter == "A":
        before = line_text[: matches[0].start()].strip()
        if before:
            return line_text.strip(), []

    prefix = line_text[: matches[0].start()].strip()
    extracted: list[tuple[str, str]] = []
    for idx, m in enumerate(matches):
        letter = m.group(1).upper()
        val_start = m.end()
        val_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(line_text)
        value = line_text[val_start:val_end].strip()
        if value:
            extracted.append((letter, value))
    if not extracted:
        return line_text.strip(), []
    return prefix, extracted


def _clamp_bbox(
    rects: list[Rect],
    page_width: float,
    page_height: float,
    *,
    figure_bboxes: tuple[dict[str, Any], ...] = (),
    pad_x: float = 6.0,
    pad_y: float = 4.0,
) -> dict[str, Any]:
    if not rects:
        out: dict[str, Any] = {
            "x0": 36.0,
            "y0": 60.0,
            "x1": round(max(120.0, page_width - 36.0), 1),
            "y1": 160.0,
            "page_width": round(page_width, 1),
            "page_height": round(page_height, 1),
        }
    else:
        merged = rects[0]
        for r in rects[1:]:
            merged = merged.union(r)
        x0 = max(0.0, min(page_width - 12.0, merged.x0 - pad_x))
        y0 = max(0.0, min(page_height - 12.0, merged.y0 - pad_y))
        x1 = min(page_width, max(x0 + 16.0, merged.x1 + pad_x))
        y1 = min(page_height, max(y0 + 14.0, merged.y1 + pad_y))
        out = {
            "x0": round(x0, 1),
            "y0": round(y0, 1),
            "x1": round(x1, 1),
            "y1": round(y1, 1),
            "page_width": round(page_width, 1),
            "page_height": round(page_height, 1),
        }
    if figure_bboxes:
        out["figure_bboxes"] = [dict(fb) for fb in figure_bboxes]
    return out


def _normalize_points(
    drafts: list[ExtractedQuestionDraft],
    expected_total: float = 10.0,
) -> list[ExtractedQuestionDraft]:
    if not drafts:
        return drafts
    if len(drafts) == 1:
        q0 = drafts[0]
        pts = round(q0.points, 2) if q0.explicit_points and q0.points > 0 else round(expected_total, 2)
        return [
            ExtractedQuestionDraft(
                question_no=q0.question_no,
                page_number=q0.page_number,
                points=pts,
                item_type=q0.item_type,
                stem_md=q0.stem_md,
                options_md=q0.options_md,
                paired_answer=q0.paired_answer,
                bbox_json=q0.bbox_json,
                original_question_label=q0.original_question_label,
                section_code=q0.section_code,
                section_title=q0.section_title,
                figure_bboxes=q0.figure_bboxes,
                solution_md=q0.solution_md,
                explicit_points=q0.explicit_points,
            )
        ]

    # Preserve genuine explicit point breakdowns (e.g., 5 x 4.0 = 20.0 on HSG papers, or 10.0 standard).
    all_explicit = all(q.explicit_points and q.points > 0 for q in drafts)
    explicit_sum = sum(q.points for q in drafts)
    if all_explicit or math.isclose(explicit_sum, expected_total, abs_tol=0.01):
        return drafts

    per_item = round(expected_total / len(drafts), 2)
    remainder = round(expected_total - per_item * (len(drafts) - 1), 2)
    out: list[ExtractedQuestionDraft] = []
    for idx, q in enumerate(drafts):
        pts = remainder if idx == len(drafts) - 1 else per_item
        out.append(
            ExtractedQuestionDraft(
                question_no=q.question_no,
                page_number=q.page_number,
                points=pts,
                item_type=q.item_type,
                stem_md=q.stem_md,
                options_md=q.options_md,
                paired_answer=q.paired_answer,
                bbox_json=q.bbox_json,
                original_question_label=q.original_question_label,
                section_code=q.section_code,
                section_title=q.section_title,
                figure_bboxes=q.figure_bboxes,
                solution_md=q.solution_md,
                explicit_points=q.explicit_points,
            )
        )
    return out


def _lookup_answer(
    wq: _WorkingQuestion,
    answer_table: dict[tuple[int | None, int], str],
    detailed_solutions: dict[int, str],
) -> tuple[str | None, str | None]:
    sol_text = detailed_solutions.get(wq.source_qno)
    table_ans = answer_table.get((wq.section_idx, wq.source_qno)) or answer_table.get((None, wq.source_qno))
    is_mcq_or_tf = len(wq.options_by_key) >= 2 or len(wq.tf_by_key) == 4
    if wq.paired_answer:
        short_ans: str | None = wq.paired_answer
    elif is_mcq_or_tf:
        short_ans = table_ans or sol_text
    else:
        short_ans = sol_text or table_ans
    return short_ans, sol_text


def _has_valid_sequential_mcq(wq: _WorkingQuestion) -> bool:
    if len(wq.options_by_key) < 2:
        return False
    expected_seq = tuple("ABCDEFGH"[: len(wq.options_by_key)])
    if tuple(sorted(wq.options_by_key.keys())) != expected_seq:
        return False
    if wq.has_numbered_subparts or len(wq.tf_by_key) >= 2:
        return len(wq.options_by_key) >= 4
    if len(wq.options_by_key) == 2 and (wq.points is not None and wq.points >= 1.5):
        return False
    return True


def _finalize_question(
    wq: _WorkingQuestion,
    answer_table: dict[tuple[int | None, int], str],
    detailed_solutions: dict[int, str],
) -> ExtractedQuestionDraft | None:
    stem_text = "\n".join(line for line in wq.stem_lines if line.strip()).strip()
    if not stem_text and not wq.options_by_key and not wq.tf_by_key:
        return None

    item_type = "ESSAY"
    options: tuple[str, ...] = ()
    paired, sol_md = _lookup_answer(wq, answer_table, detailed_solutions)

    sec_hint = f"{wq.section_code} {wq.section_title}".lower()
    is_short_section = "trả lời ngắn" in sec_hint or "tra loi ngan" in sec_hint or "short" in sec_hint
    is_tf_section = "đúng sai" in sec_hint or "dung sai" in sec_hint or "true_false" in sec_hint

    if len(wq.tf_by_key) == 4 and set(wq.tf_by_key.keys()) == {"a", "b", "c", "d"} and (
        is_tf_section or not wq.has_numbered_subparts
    ):
        item_type = "CLUSTER_TRUE_FALSE"
        options = tuple(wq.tf_by_key[k] for k in ("a", "b", "c", "d"))
    elif _has_valid_sequential_mcq(wq):
        item_type = "SINGLE_CHOICE"
        ordered_keys = sorted(wq.options_by_key.keys())
        options = tuple(wq.options_by_key[k] for k in ordered_keys)
        if paired and len(paired.strip()) <= 4:
            paired = paired.strip().upper()
    elif not wq.has_numbered_subparts and not (wq.points is not None and wq.points >= 1.5) and (
        is_short_section
        or (
            paired is not None
            and len(paired) <= 16
            and _NUMERIC_ONLY_RE.fullmatch(paired.strip()) is not None
        )
    ):
        item_type = "SHORT_NUMERIC"
    else:
        item_type = "ESSAY"
        if wq.options_by_key:
            restored = [f"{k}. {wq.options_by_key[k]}" for k in sorted(wq.options_by_key.keys())]
            stem_text = "\n".join(part for part in (stem_text, *restored) if part).strip()

    fig_bboxes = tuple(
        {
            "page_no": pno,
            "x0": round(r.x0, 1),
            "y0": round(r.y0, 1),
            "x1": round(r.x1, 1),
            "y1": round(r.y1, 1),
        }
        for pno, r in wq.figure_rects
    )
    same_page_figs = [r for pno, r in wq.figure_rects if pno == wq.page_number]
    bbox = _clamp_bbox(
        wq.primary_rects + same_page_figs,
        wq.page_width,
        wq.page_height,
        figure_bboxes=fig_bboxes,
    )

    explicit = wq.points is not None and wq.points > 0
    return ExtractedQuestionDraft(
        question_no=wq.question_no,
        page_number=wq.page_number,
        points=float(wq.points) if explicit and wq.points is not None else 1.0,
        item_type=item_type,
        stem_md=stem_text or wq.label,
        options_md=options,
        paired_answer=paired,
        bbox_json=bbox,
        original_question_label=wq.label,
        section_code=wq.section_code,
        section_title=wq.section_title,
        figure_bboxes=fig_bboxes,
        solution_md=sol_md,
        explicit_points=explicit,
    )


def _parse_question_header(txt: str) -> tuple[str, int, str, str] | None:
    """Match `Câu 1.`, `Câu I.`, `Bài 3:`, `Question 2` -> `(prefix, int_num, label, rest)`."""
    m = _QUESTION_START_RE.match(txt)
    if not m:
        return None
    keyword = m.group(1)
    arabic_raw = m.group(2)
    roman_raw = m.group(3)
    punct = m.group(4) or ""
    rest = (m.group(5) or "").strip()

    if arabic_raw is not None:
        q_num = int(arabic_raw)
        num_token = arabic_raw
    elif roman_raw is not None and roman_raw in _ROMAN_NUMERALS:
        # Avoid matching a standalone Roman-like token mid-sentence unless followed by punct / points / title.
        if not punct and not rest.startswith("(") and not (rest and rest[0].isupper()):
            return None
        q_num = _ROMAN_NUMERALS[roman_raw]
        num_token = roman_raw
    else:
        return None

    label = f"{keyword.capitalize()} {num_token}"
    return keyword, q_num, label, rest


def _should_ignore_line(txt: str) -> bool:
    return bool(_HEADER_WATERMARK_RE.match(txt))


@dataclass(frozen=True, slots=True)
class ExtractedPageInput:
    """Single page text lines & graphics normalized into PDF points."""

    page_no: int
    width_pt: float
    height_pt: float
    row_items: tuple[_RowItem, ...]
    graphics: tuple[_PageGraphic, ...] = ()
    line_count: int = 0


def extract_exam_from_text_lines(
    pages_lines: list[tuple[float, float, list[tuple[str, tuple[float, float, float, float]]]]],
) -> PdfExtractionArtifact:
    """Parse pre-extracted / OCR / DOCX-fallback text lines (with PDF-point bboxes) into structured questions."""
    page_inputs: list[ExtractedPageInput] = []
    for idx, (w_pt, h_pt, raw_lines) in enumerate(pages_lines):
        row_items: list[_RowItem] = []
        for raw_text, bbox in raw_lines:
            txt = _normalize_line_text(raw_text)
            if not txt:
                continue
            rect = Rect(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
            row_items.append(
                _RowItem(
                    y0=rect.y0,
                    y1=rect.y1,
                    x0=rect.x0,
                    x1=rect.x1,
                    text=txt,
                    rect=rect,
                )
            )
        row_items.sort(key=lambda r: (r.y0, r.x0))
        page_inputs.append(
            ExtractedPageInput(
                page_no=idx + 1,
                width_pt=w_pt,
                height_pt=h_pt,
                row_items=tuple(row_items),
                graphics=(),
                line_count=len(row_items),
            )
        )
    return _assemble_exam_artifact(page_inputs)


def extract_pdf_exam_structure(pdf_bytes: bytes) -> PdfExtractionArtifact:
    """Extract pages, sections, questions (MCQ/TF/Numeric/HSG Essay), figures, tables, and answers."""
    if not pdf_bytes or not pdf_bytes.lstrip().startswith(b"%PDF"):
        return PdfExtractionArtifact(pages=(), sections=(), raw_line_count=0)

    try:
        adapter = PDFEngineAdapter.open(pdf_bytes)
    except (PDFEngineError, PDFPasswordRequired):
        return PdfExtractionArtifact(pages=(), sections=(), raw_line_count=0)

    page_inputs: list[ExtractedPageInput] = []
    with adapter as pdf:
        for page_idx in range(pdf.page_count):
            page_no = page_idx + 1
            width_pt, height_pt = pdf.page_size(page_idx)

            raw_peek = pdf.lines(page_idx)
            has_official_header_p1 = (
                page_idx == 0
                and any(_OFFICIAL_HEADER_MARKER_RE.search(_normalize_line_text(ln.text)) for ln in raw_peek[:14])
            )

            page_tables = [t for t in pdf.tables(page_idx) if _is_real_text_table(t)]
            table_rects = [t.rect.expanded(3.0) for t in page_tables]
            page_graphics, fig_rects = _detect_page_graphics(
                pdf,
                page_idx,
                has_official_header_p1=has_official_header_p1,
                table_rects=table_rects,
            )

            exclude_rects = fig_rects + table_rects
            filtered_lines = [
                ln
                for ln in pdf.lines(page_idx, exclude=exclude_rects)
                if not _is_stray_diagram_label(ln, fig_rects)
            ]
            visual_lines = _merge_visual_rows(filtered_lines)

            row_items: list[_RowItem] = [
                _RowItem(
                    y0=ln.rect.y0,
                    y1=ln.rect.y1,
                    x0=ln.rect.x0,
                    x1=ln.rect.x1,
                    text=ln.text,
                    rect=ln.rect,
                )
                for ln in visual_lines
            ]
            for tbl in page_tables:
                md_rows = _table_to_markdown(tbl.rows)
                if md_rows:
                    row_items.append(
                        _RowItem(
                            y0=tbl.rect.y0,
                            y1=tbl.rect.y1,
                            x0=tbl.rect.x0,
                            x1=tbl.rect.x1,
                            text="\n".join(md_rows),
                            rect=tbl.rect,
                            is_table=True,
                        )
                    )
            row_items.sort(key=lambda r: (r.y0, r.x0))
            page_inputs.append(
                ExtractedPageInput(
                    page_no=page_no,
                    width_pt=width_pt,
                    height_pt=height_pt,
                    row_items=tuple(row_items),
                    graphics=tuple(page_graphics),
                    line_count=len(visual_lines),
                )
            )

    return _assemble_exam_artifact(page_inputs)


def _assemble_exam_artifact(page_inputs: list[ExtractedPageInput]) -> PdfExtractionArtifact:
    pages: list[ExtractedPageMetric] = []
    working_questions: list[_WorkingQuestion] = []
    answer_table: dict[tuple[int | None, int], str] = {}
    detailed_solutions: dict[int, str] = {}
    total_lines = 0
    total_pages = len(page_inputs)

    section_idx = 0
    current_section_code = "PART_I_MCQ"
    current_section_title = "Phần nội dung trích xuất"
    current_q: _WorkingQuestion | None = None
    in_answer_section = False
    active_answer_qno: int | None = None
    active_answer_lines: list[str] = []
    exam_ended = False
    global_qno = 0

    def _flush_active_solution() -> None:
        nonlocal active_answer_qno, active_answer_lines
        if active_answer_qno is not None and active_answer_lines:
            joined = "\n".join(active_answer_lines).strip()
            if joined:
                detailed_solutions[active_answer_qno] = joined[:4000]
        active_answer_qno = None
        active_answer_lines = []

    for pin in page_inputs:
        page_no = pin.page_no
        width_pt = pin.width_pt
        height_pt = pin.height_pt
        total_lines += pin.line_count

        heading = f"Trang {page_no} / {total_pages}"
        for item in pin.row_items:
            t = item.text.strip()
            if t and not _should_ignore_line(t) and _parse_question_header(t) is None:
                heading = t[:96]
                break
        pages.append(
            ExtractedPageMetric(
                page_no=page_no,
                width_pt=round(width_pt, 1),
                height_pt=round(height_pt, 1),
                heading=heading,
            )
        )

        if exam_ended:
            continue

        row_items = pin.row_items
        page_graphics = pin.graphics

        q_start_y_on_page: dict[int, float] = {}
        q_end_y_on_page: dict[int, float] = {}
        page_questions_order: list[_WorkingQuestion] = []

        for item in row_items:
            txt = item.text.strip()
            if not txt or _should_ignore_line(txt):
                continue

            if _END_EXAM_RE.match(txt):
                exam_ended = True
                _flush_active_solution()
                if current_q is not None:
                    working_questions.append(current_q)
                    current_q = None
                break

            if _ANSWER_BLOCK_RE.match(txt):
                in_answer_section = True
                _flush_active_solution()
                if current_q is not None:
                    working_questions.append(current_q)
                    current_q = None
                continue

            if _STUDY_EXERCISE_ZONE_RE.match(txt):
                in_answer_section = False
                if current_q is not None:
                    working_questions.append(current_q)
                    current_q = None
                section_idx += 1
                current_section_code = f"PART_EX_{section_idx}"
                current_section_title = txt[:240]
                continue

            sec_match = _SECTION_HEADER_RE.match(txt)
            if sec_match and _parse_question_header(txt) is None:
                in_answer_section = False
                _flush_active_solution()
                if current_q is not None:
                    working_questions.append(current_q)
                    current_q = None
                section_idx += 1
                sec_num = sec_match.group(1).upper()
                sec_rest = sec_match.group(2).strip()
                current_section_code = f"PART_{sec_num}"
                current_section_title = txt[:240] if sec_rest else f"Phần {sec_num}"
                continue

            if in_answer_section:
                parsed_sol_q = _parse_question_header(txt)
                if parsed_sol_q is not None:
                    _flush_active_solution()
                    _, sol_qnum, _, sol_rest = parsed_sol_q
                    active_answer_qno = sol_qnum
                    if sol_rest:
                        active_answer_lines.append(sol_rest)
                    continue
                if active_answer_qno is not None:
                    active_answer_lines.append(txt)
                else:
                    pairs = _ANSWER_PAIR_RE.findall(txt)
                    for qnum_str, ans in pairs:
                        answer_table[(None, int(qnum_str))] = ans.strip()
                continue

            q_parsed = _parse_question_header(txt)
            if q_parsed is not None and not item.is_table:
                if current_q is not None:
                    working_questions.append(current_q)
                _, q_num, label, rest = q_parsed
                global_qno += 1
                pts: float | None = None
                pts_match = _POINTS_RE.search(rest)
                if pts_match:
                    pts = float(pts_match.group(1).replace(",", "."))

                current_q = _WorkingQuestion(
                    question_no=global_qno,
                    source_qno=q_num,
                    label=label,
                    section_idx=section_idx,
                    section_code=current_section_code,
                    section_title=current_section_title,
                    page_number=page_no,
                    page_width=width_pt,
                    page_height=height_pt,
                    points=pts,
                    stem_lines=[],
                    options_by_key={},
                    option_order=[],
                    tf_by_key={},
                    paired_answer=None,
                    primary_rects=[item.rect],
                    figure_rects=[],
                )
                q_start_y_on_page[current_q.question_no] = item.y0
                q_end_y_on_page[current_q.question_no] = item.y1
                page_questions_order.append(current_q)

                if rest:
                    prefix, inline_opts = _extract_mcq_options_from_line(rest, allow_non_a_start=False)
                    if prefix:
                        current_q.stem_lines.append(prefix)
                    for key, val in inline_opts:
                        if key not in current_q.options_by_key:
                            current_q.option_order.append(key)
                        current_q.options_by_key[key] = val
                continue

            if current_q is None:
                continue

            # Register cross-page continuation only when this page actually contains content for current_q.
            if current_q not in page_questions_order:
                q_start_y_on_page[current_q.question_no] = item.y0
                page_questions_order.append(current_q)
            if page_no == current_q.page_number:
                current_q.primary_rects.append(item.rect)
            q_end_y_on_page[current_q.question_no] = max(
                q_end_y_on_page.get(current_q.question_no, 0.0), item.y1
            )

            ans_line = _INLINE_ANSWER_LINE_RE.match(txt)
            if ans_line:
                current_q.paired_answer = ans_line.group(1).strip()
                continue

            if item.is_table:
                current_q.stem_lines.append(txt)
                continue

            if _ESSAY_NUMBERED_SUB_RE.match(txt):
                current_q.has_numbered_subparts = True

            tf_match = _TF_SUB_RE.match(txt)
            if tf_match:
                tf_key = tf_match.group(1).lower()
                current_q.tf_by_key[tf_key] = tf_match.group(2).strip()
                continue

            allow_non_a = len(current_q.options_by_key) > 0
            prefix, inline_opts = _extract_mcq_options_from_line(txt, allow_non_a_start=allow_non_a)
            if inline_opts:
                if prefix:
                    if current_q.option_order:
                        last_key = current_q.option_order[-1]
                        current_q.options_by_key[last_key] = (
                            f"{current_q.options_by_key[last_key]} {prefix}".strip()
                        )
                    else:
                        current_q.stem_lines.append(prefix)
                for key, val in inline_opts:
                    if key not in current_q.options_by_key:
                        current_q.option_order.append(key)
                    current_q.options_by_key[key] = val
            elif current_q.option_order:
                last_key = current_q.option_order[-1]
                current_q.options_by_key[last_key] = (
                    f"{current_q.options_by_key[last_key]} {txt}".strip()
                )
            else:
                current_q.stem_lines.append(txt)

        # Match each diagram to the question on this page with strongest vertical overlap / nearest span.
        if page_graphics and page_questions_order:
            for g in page_graphics:
                g_cy = (g.rect.y0 + g.rect.y1) / 2.0

                def _fig_score(
                    pq: _WorkingQuestion,
                    _starts: dict[int, float] = q_start_y_on_page,
                    _ends: dict[int, float] = q_end_y_on_page,
                    _rect: Rect = g.rect,
                    _cy: float = g_cy,
                ) -> tuple[float, float]:
                    ys = _starts[pq.question_no]
                    ye = _ends[pq.question_no]
                    overlap = min(ye + 6.0, _rect.y1) - max(ys - 6.0, _rect.y0)
                    dist = abs(((ys + ye) / 2.0) - _cy)
                    return (max(0.0, overlap), -dist)

                owner = max(page_questions_order, key=_fig_score)
                owner.figure_rects.append((page_no, g.rect))
                if g.tag not in " ".join(owner.stem_lines):
                    owner.stem_lines.append(g.tag)

    _flush_active_solution()
    if current_q is not None:
        working_questions.append(current_q)

    finalized: list[ExtractedQuestionDraft] = []
    for wq in working_questions:
        drafted = _finalize_question(wq, answer_table, detailed_solutions)
        if drafted is not None:
            finalized.append(drafted)

    normalized = _normalize_points(finalized, expected_total=10.0)

    sections_map: dict[str, tuple[str, list[ExtractedQuestionDraft]]] = {}
    for q in normalized:
        if q.section_code not in sections_map:
            sections_map[q.section_code] = (q.section_title, [])
        sections_map[q.section_code][1].append(q)

    sections = tuple(
        ExtractedSectionDraft(section_code=code, title=title, questions=tuple(items))
        for code, (title, items) in sections_map.items()
    )
    return PdfExtractionArtifact(pages=tuple(pages), sections=sections, raw_line_count=total_lines)
