"""Characterisation snapshot of the PDF structure/geometry readers on the golden exam corpus.

Runs the CURRENT code (no AI, no network) on every file of the golden corpus and writes one JSON per file to
``<GOLDEN_DIR>/_baseline/<engine>/<file id>.json``. The JSON holds extracted exam text, so it must stay in the
git-ignored golden folder; the comparison test only commits numbers.

    python -m tests.golden.snapshot                 # all files
    python -m tests.golden.snapshot --only g01,g04  # a subset

``GOLDEN_DIR`` defaults to ``<repo>/tests/golden/files``. Word files are converted to PDF once with LibreOffice
(the same way the import pipeline does) and the converted PDF is kept under ``_baseline/_converted`` so that the
before and after runs read the very same bytes.

This version drives the PyMuPDF-based readers (engine ``pymupdf``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
ENGINE = "pymupdf"


def golden_dir() -> Path:
    return Path(os.environ.get("GOLDEN_DIR") or REPO / "tests" / "golden" / "files")


def baseline_dir(engine: str) -> Path:
    return golden_dir() / "_baseline" / engine


def golden_files() -> list[Path]:
    folder = golden_dir()
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".pdf", ".docx"))


def file_id(path: Path) -> str:
    return path.name.split("_", 1)[0]


def git_commit() -> dict[str, Any]:
    def run(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    dirty = run("status", "--porcelain", "--", "app")
    return {"commit": run("rev-parse", "HEAD"), "branch": run("rev-parse", "--abbrev-ref", "HEAD"), "app_dirty": bool(dirty)}


def _r(values: Any, nd: int = 2) -> list[float]:
    return [round(float(v), nd) for v in values]


def _box(b: Any) -> list[int]:
    return [int(b.ymin), int(b.xmin), int(b.ymax), int(b.xmax)]


def converted_pdf(docx: Path) -> Path:
    """The LibreOffice PDF of a Word file, converted once and reused by every later run."""
    target = golden_dir() / "_baseline" / "_converted" / (docx.stem + ".pdf")
    if target.exists():
        return target
    from app.ai.extract_engine import find_soffice
    from app.ai.normalize import docx_to_pdf

    pdf = asyncio.run(docx_to_pdf(docx, find_soffice("soffice")))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf, target)
    shutil.rmtree(pdf.parent, ignore_errors=True)
    return target


# -- recorders -------------------------------------------------------------------------------------


@contextmanager
def patched(module: Any, name: str, wrapper_factory):
    original = getattr(module, name)
    setattr(module, name, wrapper_factory(original))
    try:
        yield
    finally:
        setattr(module, name, original)


@contextmanager
def record_renders(log: list[dict[str, Any]]):
    """Log the clip rectangle of every page render (figure crops are renders of a clip)."""
    import pymupdf

    def factory(original):
        def get_pixmap(self, *args, **kwargs):
            clip = kwargs.get("clip")
            log.append({"page": self.number + 1, "clip": _r(pymupdf.Rect(clip)) if clip is not None else None})
            return original(self, *args, **kwargs)

        return get_pixmap

    with patched(pymupdf.Page, "get_pixmap", factory):
        yield


# -- per-page readings -----------------------------------------------------------------------------


def read_pages(pdf: Path) -> list[dict[str, Any]]:
    import pymupdf

    from app.ai import essay_exam
    from app.ai.figures import extract_unlabeled_diagrams, page_geometry

    pages: list[dict[str, Any]] = []
    with pymupdf.open(pdf) as doc:
        lines, geometries = essay_exam._extract_pdf_lines(doc)
        for index, page in enumerate(doc):
            geom = page_geometry(page)
            try:
                tables = [_r(t.bbox) for t in page.find_tables().tables]
            except Exception as exc:  # noqa: BLE001 - recorded, not fatal
                tables = [f"error: {exc}"]
            pages.append(
                {
                    "index": index,
                    "size": _r((page.rect.width, page.rect.height)),
                    "essay_lines": [
                        [ln.ymin, ln.xmin, ln.ymax, ln.xmax, ln.text] for ln in lines if ln.page == index + 1
                    ],
                    "blocks": [[*_r(b[:4]), b[4], int(b[6])] for b in page.get_text("blocks")],
                    "words": [[*_r(w[:4]), w[4]] for w in page.get_text("words")],
                    "geometry": {
                        "graphics": [_box(b) for b in geom.graphics],
                        "images": [_box(b) for b in geom.images],
                        "lines": [[_box(b) for b in line] for line in geom.lines],
                    },
                    "essay_geometry_graphics": len(geometries[index].graphics),
                    "figure_bands": [_box(b.box) for b in essay_exam.figure_band_crops(geom, 0, 1000)],
                    "tables": tables,
                    "clusters_3": [_r(r) for r in page.cluster_drawings()],
                    "clusters_25": [_r(r) for r in page.cluster_drawings(x_tolerance=25, y_tolerance=25)],
                    "unlabeled_diagrams": [_box(b) for b in extract_unlabeled_diagrams(page)],
                }
            )
    return pages


# -- per-document readings -------------------------------------------------------------------------


def _question(q: Any) -> dict[str, Any]:
    return {
        "number": q.number,
        "type": q.type,
        "page": q.page,
        "content": q.content,
        "choices": len(q.choices or []),
        "figure_bbox": _box(q.figure_bbox) if q.figure_bbox is not None else None,
    }


def _local(exam: Any) -> dict[str, Any] | None:
    if exam is None:
        return None
    return {
        "source": exam.source,
        "key_only": exam.key_only,
        "questions": [_question(q) for q in exam.questions],
        "figures": {str(k): len(v) > 0 for k, v in sorted(exam.figures.items())},
        "explanations": {str(k): len(v) for k, v in sorted(exam.explanations.items())},
        "points": {str(k): v for k, v in sorted(exam.points.items())},
        "flags": {str(k): v for k, v in sorted(exam.question_flags.items())},
        "broken_crops": sorted(exam.broken_crops),
    }


def read_document(pdf: Path) -> dict[str, Any]:
    from app.ai import essay_exam, extract_engine, local_text

    out: dict[str, Any] = {}
    timing: dict[str, float] = {}

    t = time.perf_counter()
    text = extract_engine.pdf_to_continuous_text(pdf)
    timing["continuous_text"] = time.perf_counter() - t
    out["continuous_text"] = text
    out["structured"] = [
        {"number": q.number, "header": q.header, "content": q.content, "figures": q.attached_figures}
        for q in extract_engine.split_into_structured_questions(text)
    ]

    renders: list[dict[str, Any]] = []
    t = time.perf_counter()
    with record_renders(renders):
        figs = extract_engine.extract_figures_by_caption(pdf)
    timing["caption_figures"] = time.perf_counter() - t
    out["caption_figures"] = {"labels": list(figs), "renders": renders}

    renders = []
    t = time.perf_counter()
    with record_renders(renders):
        schemes = extract_engine.extract_chemistry_schemes(pdf)
    timing["chemistry_schemes"] = time.perf_counter() - t
    out["chemistry_schemes"] = [{"page": p, "y0": round(y0, 2)} for p, y0, _ in schemes]
    out["chemistry_scheme_renders"] = renders

    # read_locally() is the non-AI import path; read_essay_pdf() is its first step. Record both, and every
    # figure band / crop box the essay reader computes.
    essay_runs: list[dict[str, Any]] = []
    bands: list[dict[str, Any]] = []
    cuts: list[list[int]] = []

    def essay_factory(original):
        def read_essay_pdf(path, *a, **kw):
            paper = original(path, *a, **kw)
            essay_runs.append(
                None
                if paper is None
                else {
                    "key_only": paper.key_only,
                    "questions": [_question(q) for q in paper.questions],
                    "figures": sorted(paper.figures),
                    "figure_captions": {str(k): v for k, v in sorted(paper.figure_captions.items())},
                    "explanations": {str(k): len(v) for k, v in sorted(paper.explanations.items())},
                    "points": {str(k): v for k, v in sorted(paper.points.items())},
                    "flags": {str(k): v for k, v in sorted(paper.question_flags.items())},
                    "topics": {str(k): v for k, v in sorted(paper.topics.items())},
                }
            )
            return paper

        return read_essay_pdf

    def bands_factory(original):
        def figure_band_crops(geom, slice_ymin, slice_ymax):
            result = original(geom, slice_ymin, slice_ymax)
            bands.append({"slice": [slice_ymin, slice_ymax], "boxes": [_box(b.box) for b in result]})
            return result

        return figure_band_crops

    def cut_factory(original):
        def cut(img, box, *a, **kw):
            cuts.append(_box(box))
            return original(img, box, *a, **kw)

        return cut

    t = time.perf_counter()
    with (
        patched(essay_exam, "read_essay_pdf", essay_factory),
        patched(essay_exam, "figure_band_crops", bands_factory),
        patched(essay_exam, "cut", cut_factory),
    ):
        local = local_text.read_locally(pdf)
    timing["read_locally"] = time.perf_counter() - t
    out["essay"] = essay_runs[0] if essay_runs else None
    out["essay_figure_bands"] = bands
    out["essay_cuts"] = cuts
    out["local"] = _local(local)
    out["timing"] = {k: round(v, 3) for k, v in timing.items()}
    return out


def snapshot(path: Path) -> dict[str, Any]:
    started = time.perf_counter()
    result: dict[str, Any] = {"file": path.name, "id": file_id(path), "engine": ENGINE, **git_commit()}
    pdf = path
    if path.suffix.lower() == ".docx":
        from app.ai import local_text

        result["docx_local"] = _local(local_text.read_locally(path))
        pdf = converted_pdf(path)
        result["converted_pdf"] = str(pdf.relative_to(golden_dir()))
    t = time.perf_counter()
    result["pages"] = read_pages(pdf)
    result["page_count"] = len(result["pages"])
    pages_time = time.perf_counter() - t
    result.update(read_document(pdf))
    result["timing"]["pages"] = round(pages_time, 3)
    result["timing"]["total"] = round(time.perf_counter() - started, 3)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", default="", help="comma-separated file ids (g01,g04,...)")
    parser.add_argument("--out", default="", help="output folder (default <GOLDEN_DIR>/_baseline/<engine>)")
    args = parser.parse_args(argv)

    files = golden_files()
    if not files:
        print(f"No golden files in {golden_dir()}", file=sys.stderr)
        return 1
    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    out_dir = Path(args.out) if args.out else baseline_dir(ENGINE)
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in files:
        if wanted and file_id(path) not in wanted:
            continue
        data = snapshot(path)
        (out_dir / f"{data['id']}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        local = data["local"]
        found = len(local["questions"]) if local else 0
        print(f"{data['id']}: {data['page_count']} pages, {found} questions, {data['timing']['total']} s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
