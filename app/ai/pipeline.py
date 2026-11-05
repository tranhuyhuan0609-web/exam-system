"""Job processing: pages → extraction (vision) → figure crops → solving (code execution) → drafts.

A draft is a plain dict stored in ImportJob.drafts and edited during review:

    {
        "number": 5, "page": 1, "type": "MULTIPLE_CHOICE",
        "content": "...", "choices": ["...", "..."], "correct": 2,         # MULTIPLE_CHOICE: index, or None
        "truths": [True, False, ...], "answers": ["30"],                    # TRUE_FALSE / SHORT_ANSWER
        "short_answer": None, "final_value": "25 N", "explanation": "...",
        "difficulty": "APPLICATION", "figure": "figures/12-ab12cd.png",  # relative to <data_dir>/media
        "flags": ["no_match"], "question_id": None,                         # set once published
    }
"""

import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.ai.normalize import crop_figure, thumbnail_bytes, to_page_images
from app.ai.provider import AIProvider, FigureImage, PageImage
from app.ai.schemas import ExtractedQuestion, Solution
from app.question_rules import extract_number

log = logging.getLogger(__name__)

EXTRACT_CHUNK_PAGES = 3  # pages per vision call (+1 following page as context for split questions)
SOLVE_BATCH = 6  # questions per solving call

FLAG_LABELS = {
    "unsupported_type": "Câu tự luận chưa dùng được trong đề (chỉ hỗ trợ trắc nghiệm, đúng/sai, trả lời ngắn).",
    "no_match": "Kết quả AI tính ra không khớp lựa chọn nào — hãy kiểm tra lại đáp án.",
    "no_solution": "AI chưa giải được câu này — hãy tự chọn đáp án và viết lời giải.",
    "figure_failed": "Câu có hình vẽ nhưng cắt hình không thành công.",
}

Progress = Callable[[str, int, int], Awaitable[None]]


def job_dirs(data_dir: Path, job_id: int) -> tuple[Path, Path]:
    return data_dir / "jobs" / str(job_id) / "pages", data_dir / "media" / "figures"


def page_path(data_dir: Path, job_id: int, page: int) -> Path:
    return job_dirs(data_dir, job_id)[0] / f"page-{page:03d}.png"


_TRAILING_DOT_IN_MATH = re.compile(r"\.(\s*\}?\s*\$)$")


def clean_choice(text: str) -> str:
    """Drop a leading "A." label and the trailing full stop printed after each option."""
    text = re.sub(r"^\s*[A-F]\s*[.)]\s+", "", text.strip())
    text = _TRAILING_DOT_IN_MATH.sub(r"\1", text)  # "$11\text{ lít.}$" -> "$11\text{ lít}$"
    return text[:-1].rstrip() if text.endswith(".") and not text.endswith("..") else text


def _same_question(a: ExtractedQuestion, b: ExtractedQuestion) -> bool:
    # Numbering restarts per section in the new THPT format, so match on content as well as number.
    return a.number == b.number and a.content[:60] == b.content[:60]


def _chunks(pages: list[Path]) -> list[list[PageImage]]:
    groups = []
    for start in range(0, len(pages), EXTRACT_CHUNK_PAGES):
        chunk = [
            PageImage(number=start + i + 1, png=thumbnail_bytes(p))
            for i, p in enumerate(pages[start : start + EXTRACT_CHUNK_PAGES])
        ]
        following = start + EXTRACT_CHUNK_PAGES
        if following < len(pages):
            chunk.append(PageImage(number=following + 1, png=thumbnail_bytes(pages[following]), context_only=True))
        groups.append(chunk)
    return groups


def _draft(q: ExtractedQuestion, solution: Solution | None, figure: str | None, figure_failed: bool, default: str):
    choices = [clean_choice(c.content) for c in q.choices]
    flags: list[str] = []
    correct, truths, answers = None, [], []
    if q.type == "ESSAY":
        flags.append("unsupported_type")
    if solution is None:
        flags.append("no_solution")
    elif q.type == "TRUE_FALSE":
        truths = list(solution.statement_truths or [])
        if len(truths) != len(choices):
            flags.append("no_solution")
            truths = []
    elif q.type == "SHORT_ANSWER":
        number = extract_number(solution.short_answer or "") or extract_number(solution.final_value or "")
        answers = [number] if number else []
        if not answers:
            flags.append("no_solution")
    else:
        if solution.correct_label is not None:
            index = "ABCDEF".index(solution.correct_label)
            correct = index if index < len(choices) else None
        if not solution.matches_an_option or correct is None:
            flags.append("no_match")
    if figure_failed:
        flags.append("figure_failed")
    return {
        "number": q.number,
        "page": q.page,
        "type": q.type,
        "content": q.content.strip(),
        "choices": choices,
        "correct": correct,
        "truths": truths,
        "answers": answers,
        "short_answer": solution.short_answer if solution else None,
        "final_value": solution.final_value if solution else None,
        "explanation": solution.step_by_step_solution.strip() if solution else "",
        "difficulty": solution.difficulty if solution else default,
        "figure": figure,
        "flags": flags,
        "question_id": None,
    }


def normalize_draft(draft: dict[str, Any]) -> dict[str, Any]:
    """Fill fields added after a draft was stored (drafts from older versions of the pipeline)."""
    d = dict(draft)
    d.setdefault("truths", [])
    if not d.get("answers"):
        number = extract_number(d.get("short_answer") or "") or extract_number(d.get("final_value") or "")
        d["answers"] = [number] if d.get("type") == "SHORT_ANSWER" and number else []
    if d.get("type") in ("TRUE_FALSE", "SHORT_ANSWER"):
        d["flags"] = [f for f in d.get("flags", []) if f != "unsupported_type"]
    return d


async def run_pipeline(
    *,
    job_id: int,
    source: Path,
    data_dir: Path,
    provider: AIProvider,
    soffice: str,
    default_difficulty: str,
    progress: Progress,
) -> tuple[int, list[dict[str, Any]]]:
    """Process one upload. Returns (page_count, drafts). Raises NormalizeError / AIError on failure."""
    pages_dir, figures_dir = job_dirs(data_dir, job_id)

    await progress("Đang chuyển file thành ảnh từng trang…", 0, 1)
    pages = await to_page_images(source, pages_dir, soffice)

    chunks = _chunks(pages)
    extracted: list[ExtractedQuestion] = []
    for i, chunk in enumerate(chunks):
        first, last = chunk[0].number, max(p.number for p in chunk if not p.context_only)
        await progress(f"AI đang đọc trang {first}–{last} / {len(pages)}…", i, len(chunks))
        for q in await provider.extract(chunk):
            if not 1 <= q.page <= len(pages):
                q.page = first
            if not any(_same_question(q, seen) for seen in extracted):
                extracted.append(q)
    if not extracted:
        return len(pages), []

    figure_paths: dict[int, str | None] = {}
    failed: set[int] = set()
    for index, q in enumerate(extracted):
        if q.figure_bbox is None:
            continue
        name = f"{job_id}-{index + 1}-{secrets.token_hex(6)}.png"  # unguessable: figures are served publicly
        if crop_figure(pages[q.page - 1], q.figure_bbox, figures_dir / name):
            figure_paths[index] = f"figures/{name}"
        else:
            failed.add(index)

    solutions: dict[int, Solution] = {}
    batches = [list(range(s, min(s + SOLVE_BATCH, len(extracted)))) for s in range(0, len(extracted), SOLVE_BATCH)]
    for b, indexes in enumerate(batches):
        questions = [extracted[i] for i in indexes]
        await progress(
            f"AI đang giải câu {questions[0].number}–{questions[-1].number} ({len(extracted)} câu)…", b, len(batches)
        )
        figures = [
            FigureImage(extracted[i].number, (data_dir / "media" / figure_paths[i]).read_bytes())
            for i in indexes
            if figure_paths.get(i)
        ]
        try:
            answers = await provider.solve(questions, figures)
        except Exception:  # keep the extracted questions even if solving one batch fails
            log.exception("Solving batch %s of job %s failed", b, job_id)
            continue
        by_number: dict[int, list[Solution]] = {}
        for s in answers:
            by_number.setdefault(s.number, []).append(s)
        for position, i in enumerate(indexes):
            candidates = by_number.get(extracted[i].number) or []
            if len(candidates) == 1:
                solutions[i] = candidates[0]
            elif position < len(answers):  # duplicate or missing numbers: fall back to order
                solutions[i] = answers[position]

    drafts = [
        _draft(q, solutions.get(i), figure_paths.get(i), i in failed, default_difficulty)
        for i, q in enumerate(extracted)
    ]
    await progress("Hoàn tất", 1, 1)
    return len(pages), drafts
