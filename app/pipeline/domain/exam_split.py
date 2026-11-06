"""Find where each exam starts in a PDF that bundles many ("Tuyển tập 20 đề", all codes of an official paper).

Signals, read near the top (and the page-number line at the bottom) of every page:
- an exam marker: "ĐỀ SỐ 12", "ĐỀ THI THỬ SỐ 3", "Đề 5" — or a paper code: "Mã đề 101". Official papers repeat
  the code in every page header, so the same marker again means the same exam. One document uses one kind:
  the one that tells more exams apart (a numbered exam may also print its code);
- page numbering starting again ("Trang 1/6", "Trang: 1"): a new document glued after the previous one.
"ĐÁP ÁN…", "HƯỚNG DẪN GIẢI…", "LỜI GIẢI…" in the first lines make the part an answers part.
Pages before the first boundary (cover, table of contents) are left out: they stay in the original file.
"""

import re
import unicodedata
from dataclasses import dataclass

HEAD_LINES = 25  # markers are looked for near the top of the page only
_SOLUTION = re.compile(r"đáp\s*án|hướng\s*dẫn\s*(?:giải|chấm)|lời\s*giải|giải\s*chi\s*tiết", re.IGNORECASE)
# OCR of scans mixes up tone marks ("đềthị", "để", "sổ"): the patterns accept the usual confusions.
_DE = r"đ[ềểếệễe]"
_NUMBERED = re.compile(  # "đề số 5" anywhere, "Đề 5" only at the start of a line; never "mã đề 105"
    rf"(?<!mã\s)(?<!mã)\b{_DE}\s*(?:th[iịí]\s*)?(?:thử\s*)?s[ốổố]\s*(?P<num>\d{{1,3}})\b|^\W*{_DE}\s*(?P<bare>\d{{1,3}})\b",
    re.IGNORECASE,
)
_CODE = re.compile(rf"\bm[ãaạ]\s*{_DE}(?:\s*th[iịí])?\s*(?:s[ốổ]\s*)?[:.]?\s*(?P<num>\d{{3,4}})\b", re.IGNORECASE)
_FIRST_PAGE = re.compile(r"\btrang\s*:?\s*0?1\s*(?:/\s*\d+|\.|$|\s*[-–])", re.IGNORECASE)


@dataclass(frozen=True)
class Segment:
    label: str  # "Đề số 12", "Mã đề 101", "Đáp án mã đề 101", "Phần 3"
    first: int  # 0-based page indexes, inclusive
    last: int


@dataclass
class _Page:
    number: str | None  # "Đề số N"
    code: str | None  # "Mã đề N"
    restarts: bool  # "Trang 1/…"
    solution: bool


def _read(text: str) -> _Page:
    lines = [line.strip() for line in unicodedata.normalize("NFC", text).splitlines() if line.strip()]
    head = lines[:HEAD_LINES]
    number = next((str(int(m["num"] or m["bare"])) for line in head if (m := _NUMBERED.search(line))), None)
    code = next((m["num"] for line in head if (m := _CODE.search(line))), None)
    edges = lines[:4] + lines[-4:]  # page numbers live in the header or the footer
    restarts = any(_FIRST_PAGE.search(line) for line in edges)
    solution = any(_SOLUTION.search(line) for line in lines[:6])
    return _Page(number, code, restarts, solution)


def find_exams(pages: list[str], min_parts: int = 2) -> list[Segment]:
    """The exams in a bundle; [] when the file holds fewer than `min_parts` (nothing to split)."""
    read = [_read(text) for text in pages]
    numbers = {p.number for p in read if p.number}
    codes = {p.code for p in read if p.code}
    kind = "code" if len(codes) >= len(numbers) else "number"

    def marker(page: _Page) -> str | None:
        return page.code if kind == "code" else page.number

    parts: list[dict] = []  # {"first", "mark", "solution", "restarted"}
    for index, page in enumerate(read):
        mark = marker(page)
        last = parts[-1] if parts else None
        if page.restarts and not (last and last["first"] == index - 1 and not last["restarted"]):
            parts.append({"first": index, "mark": mark, "solution": page.solution, "restarted": True})
        elif mark is None:
            continue
        elif last and last["mark"] is None and last["restarted"]:  # a glued file names itself further down
            last["mark"], last["solution"] = mark, last["solution"] or page.solution
        elif last is None or mark != last["mark"]:
            parts.append({"first": index, "mark": mark, "solution": page.solution, "restarted": False})
    if kind == "code" and (grid := _regular_grid(read, len(pages))):
        parts = grid
    if len(parts) < min_parts:
        return []
    segments, seen = [], {}
    ends = [p["first"] - 1 for p in parts[1:]] + [len(pages) - 1]
    for n, (part, end) in enumerate(zip(parts, ends, strict=True), 1):
        mark = part["mark"]
        if mark is None:  # name an unnamed part by a code printed lower on its first pages
            text = unicodedata.normalize("NFC", "\n".join(pages[part["first"] : part["first"] + 2]))
            found = (_CODE if kind == "code" else _NUMBERED).search(text)
            mark = found and (found["num"] or found.groupdict().get("bare"))
            mark = str(int(mark)) if mark and kind == "number" else mark
        if mark is None:
            label = f"Phần {n}"
        else:
            name = f"mã đề {mark}" if kind == "code" else f"đề số {mark}"
            label = f"Đáp án {name}" if part["solution"] else name[0].upper() + name[1:]
        seen[label] = seen.get(label, 0) + 1
        if seen[label] > 1:  # the same exam named again further on (e.g. a second copy): keep both
            label = f"{label} ({seen[label]})"
        segments.append(Segment(label, part["first"], end))
    return segments


def _regular_grid(read: list[_Page], total: int) -> list[dict] | None:
    """An official set prints every code on the same number of pages, codes counting up (101, 102, …).
    OCR misses or misreads a few codes on scans; when most codes that were read agree with
    "code = first code + page // L" for one length L, cut every L pages and fill in the codes."""
    seen = [(i, int(p.code)) for i, p in enumerate(read) if p.code]
    best = None
    for length in range(2, total // 2 + 1):
        if total % length:
            continue
        bases = {}
        for index, code in seen:
            bases.setdefault(code - index // length, set()).add(code)
        base, codes = max(bases.items(), key=lambda item: len(item[1]))
        exams = total // length
        distinct = len({code for _, code in seen})
        agrees = len(codes) >= max(3, 0.35 * exams) and len(codes) >= 0.75 * distinct  # few OCR misreads
        if agrees and (best is None or len(codes) > best[2]):
            best = (length, base, len(codes))
    if best is None:
        return None
    length, base, _ = best
    width = max(len(p.code) for p in read if p.code)
    return [
        {"first": k * length, "mark": str(base + k).zfill(width), "solution": False, "restarted": False}
        for k in range(total // length)
    ]
