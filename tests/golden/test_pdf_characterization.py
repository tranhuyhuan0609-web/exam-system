"""Before/after comparison of the PDF structure readers on the golden exam corpus.

Compares the two snapshot folders written by ``python -m tests.golden.snapshot``:

    <GOLDEN_DIR>/_baseline/pymupdf   PyMuPDF readers (reference, captured before the migration)
    <GOLDEN_DIR>/_baseline/pdfium    the migrated readers (PDFEngineAdapter)

The snapshots hold exam text, so they stay in the git-ignored golden folder; this module only reads them and
reports numbers. It needs no database:

    GOLDEN_DIR=... pytest --noconftest -m golden tests/golden/test_pdf_characterization.py

``python -m tests.golden.test_pdf_characterization`` prints the measured value of every check for every file
(numbers only), which is what ``docs/architecture/PDF_GOLDEN_REPORT.md`` is built from.

Every check yields one number where HIGHER IS BETTER, and a threshold. A check that is known to differ for an
inherent, harmless reason is relaxed for that single file through ``tolerances.json`` (file id, check, measured
value, reason); the test then requires the value to stay at least at the recorded measurement.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import unicodedata
from functools import cache
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.golden

REPO = Path(__file__).resolve().parents[2]
GOLDEN_DIR = Path(os.environ.get("GOLDEN_DIR") or REPO / "tests" / "golden" / "files")
BEFORE = GOLDEN_DIR / "_baseline" / "pymupdf"
AFTER = GOLDEN_DIR / "_baseline" / "pdfium"
TOLERANCES = Path(__file__).with_name("tolerances.json")
SLACK = 0.005  # a relaxed check may not fall further than this below its recorded measurement

# check name -> threshold (value is in 0..1, higher is better; equality checks give 1.0 or 0.0;
# box checks give the share of boxes that have a partner with IoU >= 0.90, so 1.0 = same count and all matched)
THRESHOLDS: dict[str, float] = {
    "page_count": 1.0,
    "page_size": 1.0,
    "words_jaccard": 0.99,
    "text_similarity": 0.97,
    "question_count": 1.0,
    "question_labels": 1.0,
    "question_pages": 1.0,
    "question_text": 0.98,
    "question_figures": 1.0,
    "graphics_boxes": 1.0,
    "image_boxes": 1.0,
    "figure_bands": 1.0,
    "unlabeled_diagrams": 1.0,
    "clusters_3": 1.0,
    "clusters_25": 1.0,
    "essay_line_count": 0.98,
    "essay_line_text": 0.97,
}


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def _ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _jaccard(a: str, b: str) -> float:
    # Tokens are runs of word characters: where an engine puts a space between a letter and a following bracket,
    # or between a base and its sub/superscript, is a tokenisation choice, not a difference in the text.
    sa, sb = set(re.findall(r"\w+", a)), set(re.findall(r"\w+", b))
    return len(sa & sb) / len(sa | sb) if sa | sb else 1.0


def _count_ratio(a: int, b: int) -> float:
    return 1.0 if a == b else min(a, b) / max(a, b)


def _iou(a: list[float], b: list[float], pad: float) -> float:
    """IoU of two [y0, x0, y1, x1] boxes, each grown by ``pad`` (boxes are rounded to integers: lines have no area)."""
    ay0, ax0, ay1, ax1 = a[0] - pad, a[1] - pad, a[2] + pad, a[3] + pad
    by0, bx0, by1, bx1 = b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad
    inter = max(0.0, min(ay1, by1) - max(ay0, by0)) * max(0.0, min(ax1, bx1) - max(ax0, bx0))
    union = (ay1 - ay0) * (ax1 - ax0) + (by1 - by0) * (bx1 - bx0) - inter
    return inter / union if union > 0 else 1.0


def _xyxy_to_yxyx(box: list[float]) -> list[float]:
    return [box[1], box[0], box[3], box[2]]


def _boxes_score(ref: list[list[float]], new: list[list[float]], pad: float, min_iou: float = 0.90) -> float:
    """Share of boxes that have a partner with IoU >= min_iou (best pairs first, one-to-one), over the larger
    count. 1.0 means: same count and every box matched; a missing or extra box lowers it."""
    if not ref and not new:
        return 1.0
    pairs = sorted(((_iou(r, n, pad), i, j) for i, r in enumerate(ref) for j, n in enumerate(new)), reverse=True)
    used_r: set[int] = set()
    used_n: set[int] = set()
    matched = 0
    for value, i, j in pairs:
        if value < min_iou:
            break
        if i in used_r or j in used_n:
            continue
        used_r.add(i)
        used_n.add(j)
        matched += 1
    return matched / max(len(ref), len(new))


def _distinct(boxes: list[list[float]]) -> list[list[float]]:
    return [list(b) for b in sorted({tuple(b) for b in boxes})]


def _page_min(ref_pages: list[dict], new_pages: list[dict], fn) -> float:
    values = [fn(r, n) for r, n in zip(ref_pages, new_pages, strict=False)]
    return min(values) if values else 1.0


def _questions(snapshot: dict) -> list[dict]:
    local = snapshot.get("local") or {}
    return local.get("questions") or []


def _attached(snapshot: dict) -> dict[str, bool]:
    return (snapshot.get("local") or {}).get("figures") or {}


def compare(ref: dict[str, Any], new: dict[str, Any]) -> dict[str, float]:
    """The measured value of every check, for one file."""
    out: dict[str, float] = {}
    rp, np_ = ref["pages"], new["pages"]
    out["page_count"] = 1.0 if ref["page_count"] == new["page_count"] else 0.0
    out["page_size"] = (
        1.0
        if all(
            abs(r["size"][0] - n["size"][0]) <= 0.5 and abs(r["size"][1] - n["size"][1]) <= 0.5
            for r, n in zip(rp, np_, strict=False)
        )
        else 0.0
    )

    def words(page: dict) -> str:
        return _norm(" ".join(w[4] for w in page["words"]))

    out["words_jaccard"] = _page_min(rp, np_, lambda r, n: _jaccard(words(r), words(n)))
    out["text_similarity"] = _page_min(rp, np_, lambda r, n: _ratio(words(r), words(n)))

    rq, nq = _questions(ref), _questions(new)
    out["question_count"] = _count_ratio(len(rq), len(nq))
    labels_r, labels_n = [str(q["number"]) for q in rq], [str(q["number"]) for q in nq]
    out["question_labels"] = 1.0 if labels_r == labels_n else _ratio(" ".join(labels_r), " ".join(labels_n))
    paired = list(zip(rq, nq, strict=False))
    out["question_pages"] = sum(r["page"] == n["page"] for r, n in paired) / len(paired) if paired else 1.0
    out["question_text"] = min((_ratio(_norm(r["content"]), _norm(n["content"])) for r, n in paired), default=1.0)
    fr, fn_ = _attached(ref), _attached(new)
    same = sum(bool(fr.get(k)) == bool(fn_.get(k)) for k in set(fr) | set(fn_))
    out["question_figures"] = same / len(set(fr) | set(fn_)) if fr or fn_ else 1.0

    def geometry(key: str):
        # A path that is filled and stroked by two operators is one drawing in one engine and two identical
        # boxes in the other: identical boxes count once.
        return lambda r, n: _boxes_score(_distinct(r["geometry"][key]), _distinct(n["geometry"][key]), 0.5)

    out["graphics_boxes"] = _page_min(rp, np_, geometry("graphics"))
    out["image_boxes"] = _page_min(rp, np_, geometry("images"))
    out["figure_bands"] = _page_min(rp, np_, lambda r, n: _boxes_score(r["figure_bands"], n["figure_bands"], 0.5))
    out["unlabeled_diagrams"] = _page_min(
        rp, np_, lambda r, n: _boxes_score(r["unlabeled_diagrams"], n["unlabeled_diagrams"], 0.5)
    )
    for key in ("clusters_3", "clusters_25"):
        # cluster boxes are in points (x0, y0, x1, y1); convert to the (y, x) order of the other boxes
        out[key] = _page_min(
            rp,
            np_,
            lambda r, n, key=key: _boxes_score(
                [_xyxy_to_yxyx(b) for b in r[key]], [_xyxy_to_yxyx(b) for b in n[key]], 0.5
            ),
        )

    def line_count(r: dict, n: dict) -> float:
        a, b = len(r["essay_lines"]), len(n["essay_lines"])
        return _count_ratio(a, b)

    def line_text(r: dict, n: dict) -> float:
        return _ratio(_norm(" ".join(x[4] for x in r["essay_lines"])), _norm(" ".join(x[4] for x in n["essay_lines"])))

    out["essay_line_count"] = _page_min(rp, np_, line_count)
    out["essay_line_text"] = _page_min(rp, np_, line_text)
    return out


@cache
def tolerances() -> dict[tuple[str, str], dict[str, Any]]:
    if not TOLERANCES.exists():
        return {}
    return {(t["file"], t["check"]): t for t in json.loads(TOLERANCES.read_text(encoding="utf-8"))}


def file_ids() -> list[str]:
    if not (BEFORE.is_dir() and AFTER.is_dir()):
        return []
    return sorted(p.stem for p in BEFORE.glob("g*.json") if (AFTER / p.name).exists())


@cache
def measured(file_id: str) -> dict[str, float]:
    ref = json.loads((BEFORE / f"{file_id}.json").read_text(encoding="utf-8"))
    new = json.loads((AFTER / f"{file_id}.json").read_text(encoding="utf-8"))
    return compare(ref, new)


def verdict(file_id: str, check: str, value: float) -> tuple[bool, str]:
    threshold = THRESHOLDS[check]
    if value >= threshold:
        return True, f"{value:.4f} >= {threshold}"
    entry = tolerances().get((file_id, check))
    if entry is not None and value >= float(entry["measured"]) - SLACK:
        return True, f"{value:.4f} below {threshold}, tolerated down to {entry['measured']} ({entry['reason']})"
    return False, f"{value:.4f} < {threshold}"


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "file_id" in metafunc.fixturenames and "check" in metafunc.fixturenames:
        cases = [(f, c) for f in file_ids() for c in THRESHOLDS]
        metafunc.parametrize(("file_id", "check"), cases or [pytest.param("none", "none", marks=pytest.mark.skip)])


def test_golden_snapshots_are_available():
    if not GOLDEN_DIR.is_dir():
        pytest.skip(f"no golden corpus at {GOLDEN_DIR}")
    if not file_ids():
        pytest.skip("run `python -m tests.golden.snapshot` first: missing _baseline/pymupdf or _baseline/pdfium")


def test_pdf_readers_match_the_pymupdf_baseline(file_id: str, check: str):
    ok, detail = verdict(file_id, check, measured(file_id)[check])
    assert ok, f"{file_id} {check}: {detail}"


def test_every_tolerance_names_a_known_check_and_has_a_reason():
    for (file_id, check), entry in tolerances().items():
        assert check in THRESHOLDS, f"unknown check {check} for {file_id}"
        assert entry.get("reason") and "measured" in entry, f"incomplete tolerance for {file_id} {check}"


def main() -> int:
    ids = file_ids()
    if not ids:
        print("no snapshots found")
        return 1
    print("file  " + " ".join(f"{c[:10]:>10}" for c in THRESHOLDS))
    for f in ids:
        row = measured(f)
        print(f"{f:5} " + " ".join(f"{row[c]:10.4f}" for c in THRESHOLDS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
