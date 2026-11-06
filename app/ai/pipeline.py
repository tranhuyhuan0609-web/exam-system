"""Job processing: pages → extraction (vision) → figure crops → solving (code execution) → drafts.

A draft is a plain dict stored in ImportJob.drafts and edited during review:

    {
        "number": 5, "page": 1, "type": "MULTIPLE_CHOICE",
        "content": "...", "choices": ["...", "..."], "correct": 2,         # MULTIPLE_CHOICE: index, or None
        "truths": [True, False, ...], "answers": ["30"],                    # TRUE_FALSE / SHORT_ANSWER
        "short_answer": None, "final_value": "25 N", "explanation": "...",
        "difficulty": "APPLICATION", "figure": "figures/12-ab12cd.png",  # storage key (public)
        "flags": ["no_match"], "question_id": None,                         # set once published
    }
"""

import logging
import re
import secrets
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.ai.figures import crop_figure
from app.ai.normalize import thumbnail_bytes, to_page_images
from app.ai.provider import AIProvider, FigureImage, PageImage
from app.ai.schemas import ExtractedQuestion, Solution
from app.infrastructure.storage import Storage
from app.question_rules import extract_number

log = logging.getLogger(__name__)

EXTRACT_CHUNK_PAGES = 3  # pages per vision call (+1 following page as context for split questions)
SOLVE_BATCH = 6  # questions per solving call

FLAG_LABELS = {
    "unsupported_type": "Câu tự luận chưa dùng được trong đề (chỉ hỗ trợ trắc nghiệm, đúng/sai, trả lời ngắn).",
    "no_match": "Kết quả AI tính ra không khớp lựa chọn nào — hãy kiểm tra lại đáp án.",
    "no_solution": "AI chưa giải được câu này — hãy tự chọn đáp án và viết lời giải.",
    "figure_failed": "Câu có hình vẽ nhưng cắt hình không thành công.",
    "duplicate": "Ngân hàng đã có câu giống hệt (cùng nội dung và lựa chọn).",
}

Progress = Callable[[str, int, int], Awaitable[None]]


def pages_prefix(job_id: int) -> str:
    return f"pages/{job_id}/"


def page_key(job_id: int, page: int) -> str:
    return f"{pages_prefix(job_id)}page-{page:03d}.png"


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
        "figure_bbox": q.figure_bbox.model_dump() if q.figure_bbox else None,  # model's box, for re-cropping
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
    if d.get("type") in ("TRUE_FALSE", "SHORT_ANSWER", "ESSAY"):
        d["flags"] = [f for f in d.get("flags", []) if f != "unsupported_type"]
    return d


async def run_pipeline(
    *,
    job_id: int,
    source_key: str,
    storage: Storage,
    provider: AIProvider,
    soffice: str,
    default_difficulty: str,
    progress: Progress,
) -> tuple[int, list[dict[str, Any]]]:
    """Process one upload. Returns (page_count, drafts). Raises NormalizeError / AIError on failure.

    Heavy work (LibreOffice, PyMuPDF, Pillow) runs in a temporary directory; results are written to storage:
    pages/<job>/page-NNN.png for the review screen and figures/<name>.png for questions.
    """
    with tempfile.TemporaryDirectory(prefix=f"job-{job_id}-") as tmp:
        work = Path(tmp)
        await progress("Đang chuyển file thành ảnh từng trang…", 0, 1)
        source = work / ("source" + Path(source_key).suffix.lower())
        source.write_bytes(await storage.get(source_key))
        pages, geometries = await to_page_images(source, work / "pages", soffice)
        for number, page in enumerate(pages, start=1):
            await storage.put(page_key(job_id, number), page.read_bytes(), "image/png")

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

        figure_keys: dict[int, str] = {}
        figure_bytes: dict[int, bytes] = {}
        failed: set[int] = set()
        for index, q in enumerate(extracted):
            if q.figure_bbox is None:
                continue
            crop = work / "figures" / f"{index + 1}.png"
            if crop_figure(pages[q.page - 1], q.figure_bbox, crop, geometries[q.page - 1]):
                key = f"figures/{job_id}-{index + 1}-{secrets.token_hex(6)}.png"  # unguessable: figures are public
                figure_bytes[index] = crop.read_bytes()
                await storage.put(key, figure_bytes[index], "image/png")
                figure_keys[index] = key
            else:
                failed.add(index)

        solutions: dict[int, Solution] = {}
        batches = [list(range(s, min(s + SOLVE_BATCH, len(extracted)))) for s in range(0, len(extracted), SOLVE_BATCH)]
        for b, indexes in enumerate(batches):
            questions = [extracted[i] for i in indexes]
            await progress(
                f"AI đang giải câu {questions[0].number}–{questions[-1].number} ({len(extracted)} câu)…",
                b,
                len(batches),
            )
            figures = [FigureImage(extracted[i].number, figure_bytes[i]) for i in indexes if i in figure_bytes]
            try:
                answers = await provider.solve(questions, figures)
            except Exception:  # keep the extracted questions even if solving one batch fails
                log.exception("Solving batch %s of job %s failed", b, job_id)
                continue
            by_number: dict[int, list[Solution]] = {}
            for sol in answers:
                by_number.setdefault(sol.number, []).append(sol)
            for position, i in enumerate(indexes):
                candidates = by_number.get(extracted[i].number) or []
                if len(candidates) == 1:
                    solutions[i] = candidates[0]
                elif len(answers) == len(indexes):  # duplicate numbers in a complete answer: by order
                    solutions[i] = answers[position]

        drafts = [
            _draft(q, solutions.get(i), figure_keys.get(i), i in failed, default_difficulty)
            for i, q in enumerate(extracted)
        ]
        await progress("Hoàn tất", 1, 1)
        return len(pages), drafts
