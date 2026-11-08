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

import asyncio
import logging
import re
import secrets
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.ai.essay_exam import (
    clean_paper_text,
    extract_matrix_difficulty,
    has_missing_stem,
    parse_figure_captions,
    references_figure,
    strip_topic,
)
from app.ai.figures import crop_figure
from app.ai.local_text import read_locally
from app.ai.normalize import NormalizeError, thumbnail_bytes, to_page_images
from app.ai.provider import AIError, AIProvider, AIQuotaExhausted, FigureImage, PageImage
from app.ai.schemas import ExtractedQuestion, Solution
from app.domain.latex import latex_tables
from app.infrastructure.storage import Storage
from app.question_rules import extract_number

log = logging.getLogger(__name__)

# Free tiers limit requests per DAY, not tokens: as few calls as possible. A whole exam (up to 8 pages) is read
# in one call and up to 25 questions are solved in one; a call that fails (cut-off or broken answer) is split.
EXTRACT_CHUNK_PAGES = 8  # pages per vision call (+1 following page as context for split questions)
SOLVE_BATCH = 25  # questions per solving call
MIN_SOLVE_BATCH = 3

FLAG_LABELS = {
    "unsupported_type": "Câu tự luận chưa dùng được trong đề (chỉ hỗ trợ trắc nghiệm, đúng/sai, trả lời ngắn).",
    "no_match": "Kết quả AI tính ra không khớp lựa chọn nào — hãy kiểm tra lại đáp án.",
    "no_solution": "AI chưa giải được câu này — hãy tự chọn đáp án và viết lời giải.",
    "figure_failed": "Câu có hình vẽ nhưng cắt hình không thành công.",
    "missing_stem": "Câu bắt đầu bằng ý nhỏ (1., a)) mà thiếu đoạn đề dẫn — hãy kiểm tra lại đề bài.",
    "duplicate": "Ngân hàng đã có câu giống hệt (cùng nội dung và lựa chọn).",
    "key_mismatch": "Khác đáp án chính thức của đề: kiểm tra đề bài (AI có thể đã đọc sai) và đáp án.",
}

Progress = Callable[[str, int, int], Awaitable[None]]


def pages_prefix(job_id: int) -> str:
    return f"pages/{job_id}/"


def page_key(job_id: int, page: int) -> str:
    return f"{pages_prefix(job_id)}page-{page:03d}.png"


_QUESTION_LABEL = re.compile(
    r"^\s*(?:Câu|Bài)\s*(?:\d+|[IVXL]+\b)\s*[.:]?\s*(?:\([^)]*điểm\))?\s*[:.)–-]?\s*", re.IGNORECASE
)  # the number is shown by the app
_TRAILING_DOT_IN_MATH = re.compile(r"\.(\s*\}?\s*\$)$")
def clean_choice(text: str) -> str:
    """Drop a leading "A." label and the trailing full stop printed after each option."""
    text = re.sub(r"^\s*[A-F]\s*[.)]\s+", "", text.strip())
    text = _TRAILING_DOT_IN_MATH.sub(r"\1", text)  # "$11\text{ lít.}$" -> "$11\text{ lít}$"
    return text[:-1].rstrip() if text.endswith(".") and not text.endswith("..") else text


def _same_question(a: ExtractedQuestion, b: ExtractedQuestion) -> bool:
    # Numbering restarts per section in the new THPT format, so match on content as well as number.
    return a.number == b.number and a.content[:60] == b.content[:60]


def _tokens(text: str) -> list[str]:
    return [re.sub(r"^[^\w]+|[^\w]+$", "", token.casefold()) for token in text.split()]


def _trim_cross_question_overlap(questions: list[ExtractedQuestion]) -> None:
    """Prevent vision extraction from appending the next essay question to the current one.

    This happens frequently when several HSG problems share one page and the model
    continues reading after a visual boundary.  The next question is already
    present as a separate result, so a long token overlap in the current result's
    tail is safe to remove.
    """
    for current, following in zip(questions, questions[1:]):
        if current.page != following.page or current.number >= following.number:
            continue
        current_tokens = _tokens(current.content)
        next_tokens = _tokens(following.content)
        if len(current_tokens) < 20 or len(next_tokens) < 8:
            continue
        best: tuple[int, int] | None = None
        for start in range(min(8, len(next_tokens))):
            for length in range(min(30, len(next_tokens) - start), 7, -1):
                needle = next_tokens[start : start + length]
                if current_tokens[-length:] == needle:
                    best = (start, length)
                    break
            if best:
                break
        if not best:
            continue
        start, length = best
        overlap_start = len(current_tokens) - length
        # The next question can begin in the middle of a sentence when the
        # model omitted its printed "Câu N" heading, so do not require the
        # overlap to start after the exact midpoint.
        if overlap_start < len(current_tokens) * 0.30:
            continue
        raw_tokens = list(re.finditer(r"\S+", current.content))
        current.content = current.content[: raw_tokens[overlap_start].start()].rstrip()


def _chunks(pages: list[Path], size: int = EXTRACT_CHUNK_PAGES) -> list[list[PageImage]]:
    groups = []
    for start in range(0, len(pages), size):
        chunk = [
            PageImage(number=start + i + 1, png=thumbnail_bytes(p)) for i, p in enumerate(pages[start : start + size])
        ]
        following = start + size
        if following < len(pages):
            chunk.append(PageImage(number=following + 1, png=thumbnail_bytes(pages[following]), context_only=True))
        groups.append(chunk)
    return groups


def _text_contexts(source: Path, page_count: int) -> list[str]:
    """Read an available text layer without making it authoritative over the page image."""
    try:
        if source.suffix.lower() == ".docx":
            from app.ai.extract_engine import docx_to_markdown_latex

            text = docx_to_markdown_latex(source)
            return [text] + [""] * max(0, page_count - 1)
        if source.suffix.lower() == ".pdf":
            import pymupdf

            with pymupdf.open(source) as document:
                return [page.get_text("text")[:24000] for page in document]
    except Exception as exc:
        log.info("Could not read optional text context from %s: %s", source.name, exc)
    return [""] * page_count


def _tables(content: str, figure: str | None) -> str:
    """LaTeX tabulars as table rows; a variation table already drawn in the question's figure is not repeated."""
    is_variation = bool(re.search(r"[ns][ew]arrow|infty", content))
    return latex_tables(content, drop=bool(figure) and is_variation and "tabular" in content)


def _draft(
    q: ExtractedQuestion,
    solution: Solution | None,
    figure: str | None,
    figure_failed: bool,
    default: str,
    key: str | None = None,
    *,
    matrix_difficulty: str | None = None,
    extra_flags: list[str] | None = None,
    figure_captions: dict[str, str] | None = None,
    topic: str | None = None,
):
    """`key`: the answer / marking scheme printed in the file for this question (it becomes the explanation).
    `topic`: the section title of the heading ("CƠ HỌC CHẤT ĐIỂM"): it classifies the question but is not its text."""
    choices = [clean_choice(c.content) for c in q.choices]
    flags: list[str] = []
    if q.type == "ESSAY" and (key or solution is not None):
        pass  # an essay: its marking scheme, or the AI's model answer (no option to match)
    elif q.type != "ESSAY" and key and solution is None:
        pass  # explanation/key came directly from the file
    elif solution is None:
        flags.append("no_solution")
    elif q.type == "TRUE_FALSE":
        truths_list = list(solution.statement_truths or [])
        if len(truths_list) != len(choices):
            flags.append("no_solution")
    elif q.type == "SHORT_ANSWER":
        number = extract_number(solution.short_answer or "") or extract_number(solution.final_value or "")
        if not number:
            flags.append("no_solution")
    else:
        idx = "ABCDEF".index(solution.correct_label) if solution.correct_label is not None else len(choices)
        if not solution.matches_an_option or idx >= len(choices):
            flags.append("no_match")

    correct, truths, answers = None, [], []
    if solution is not None:
        if q.type == "TRUE_FALSE":
            t = list(solution.statement_truths or [])
            truths = t if len(t) == len(choices) else []
        elif q.type == "SHORT_ANSWER":
            num = extract_number(solution.short_answer or "") or extract_number(solution.final_value or "")
            answers = [num] if num else []
        elif q.type != "ESSAY" and solution.correct_label is not None:
            idx = "ABCDEF".index(solution.correct_label)
            correct = idx if idx < len(choices) else None

    if figure_failed and "figure_failed" not in flags:
        flags.append("figure_failed")
    for fl in extra_flags or []:
        if fl not in flags:
            flags.append(fl)

    mat_diff = matrix_difficulty or extract_matrix_difficulty(q.content)
    cleaned_content = _tables(
        strip_topic(clean_paper_text(_QUESTION_LABEL.sub("", q.content.strip(), count=1)), topic), figure
    )
    classified = f"{topic}\n{cleaned_content}" if topic else cleaned_content
    from app.application.knowledge_linker import infer_knowledge_metadata

    if mat_diff:
        # Step 7: Difficulty printed in the exam matrix wins
        final_diff = mat_diff
        difficulty_from_matrix = True
        meta = infer_knowledge_metadata(classified, final_diff)
    elif q.type == "ESSAY":
        # Step 7: Never let AI guess difficulty for HSG / essay questions; leave to Admin's selection
        final_diff = default
        difficulty_from_matrix = False
        meta = infer_knowledge_metadata(classified, final_diff)
    else:
        raw_diff = (solution.difficulty if solution else None) or q.difficulty or default
        meta = infer_knowledge_metadata(classified, raw_diff)
        final_diff = raw_diff if raw_diff == "HSG" else meta["difficulty"]
        difficulty_from_matrix = False

    unit_code = (
        (solution.kntt_unit_code if solution and solution.kntt_unit_code else None)
        or q.kntt_unit_code
        or meta["kntt_unit_code"]
    )
    is_thinking = bool(
        (solution and solution.is_thinking_skill) or q.is_thinking_skill or meta["is_thinking_skill"]
    )
    suitable_targets = list(
        (solution.suitable_targets if solution and solution.suitable_targets else None)
        or q.suitable_targets
        or meta["suitable_targets"]
    )
    captions = figure_captions if figure_captions is not None else parse_figure_captions(cleaned_content)
    expl_text = clean_paper_text(key) if key else (solution.step_by_step_solution.strip() if solution else "")
    from app.ai.extract_engine import extract_short_answer_value
    inferred_val = extract_short_answer_value(expl_text) if expl_text else None
    sol_short = (solution.short_answer if solution else None) or inferred_val
    sol_final = (solution.final_value if solution else None) or inferred_val
    if not answers and inferred_val:
        answers = [inferred_val]

    return {
        "number": q.number,
        "page": q.page,
        "type": q.type,
        "content": cleaned_content,
        "choices": choices,
        "correct": correct,
        "truths": truths,
        "answers": answers,
        "short_answer": sol_short,
        "final_value": sol_final,
        "explanation": latex_tables(expl_text),
        "difficulty": final_diff,
        "difficulty_from_matrix": difficulty_from_matrix,
        "kntt_unit_code": unit_code,
        "is_thinking_skill": is_thinking,
        "suitable_targets": suitable_targets,
        "topic_name": meta.get("topic_name"),
        "topic_title": topic,
        "access_tier": "PRO" if final_diff == "HSG" else "FREE",
        "figure": figure,
        "figure_captions": captions,
        "figure_bbox": q.figure_bbox.model_dump() if q.figure_bbox else None,  # model's box, for re-cropping
        "flags": flags,
        "key_from_file": bool(key),  # the explanation is the answer / marking scheme printed in the file
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
    if "suitable_targets" not in d or "is_thinking_skill" not in d or "kntt_unit_code" not in d or "topic_name" not in d:
        from app.application.knowledge_linker import infer_knowledge_metadata

        meta = infer_knowledge_metadata(d.get("content", ""), d.get("difficulty"))
        d.setdefault("kntt_unit_code", meta["kntt_unit_code"])
        d.setdefault("is_thinking_skill", meta["is_thinking_skill"])
        d.setdefault("suitable_targets", meta["suitable_targets"])
        d.setdefault("topic_name", meta.get("topic_name"))
    return d


def _printed_answer(draft: dict[str, Any], label: str) -> None:
    """The answer printed in the file (answer table, underlined or highlighted option) wins; when the AI found
    another one, the draft is marked so that an admin looks at it."""
    index = "ABCD".index(label)
    if index >= len(draft["choices"]):
        return
    if draft["correct"] is not None and draft["correct"] != index:
        draft["flags"] = [*draft["flags"], "key_mismatch"]
        draft["official"] = label
    else:
        draft["flags"] = [f for f in draft["flags"] if f not in ("no_match", "no_solution")]  # the file settles it
    draft["correct"] = index
    draft["printed"] = label  # the file's own answer: it stands for the official key


async def _extract(provider: AIProvider, chunk: list[PageImage]) -> list[ExtractedQuestion]:
    """Read the pages in one call; if that fails (a cut-off or broken answer), read each half separately."""
    try:
        return await provider.extract(chunk)
    except AIQuotaExhausted:
        raise
    except AIError:
        real = [p for p in chunk if not p.context_only]
        if len(real) < 2:
            raise
        log.warning("Reading %s pages at once failed: reading them in two halves", len(real))
        half = len(real) // 2
        first = [*real[:half], PageImage(number=real[half].number, png=real[half].png, context_only=True)]
        return [
            *await _extract(provider, first),
            *await _extract(provider, [*real[half:], *[p for p in chunk if p.context_only]]),
        ]


async def _solve(
    provider: AIProvider, questions: list[ExtractedQuestion], figures: list[FigureImage]
) -> list[Solution]:
    """Solve the batch in one call; if that fails, solve each half separately (down to a few questions)."""
    try:
        return await provider.solve(questions, figures)
    except AIQuotaExhausted:
        raise
    except AIError:
        if len(questions) < 2 * MIN_SOLVE_BATCH:
            raise
        half = len(questions) // 2
        log.warning("Solving %s questions at once failed: solving them in two halves", len(questions))
        parts = []
        for group in (questions[:half], questions[half:]):
            numbers = {q.number for q in group}
            parts += await _solve(provider, group, [f for f in figures if f.question_number in numbers])
        return parts


async def run_pipeline(
    *,
    job_id: int,
    source_key: str,
    storage: Storage,
    provider: AIProvider,
    soffice: str,
    default_difficulty: str,
    progress: Progress,
    solve: bool = True,
    solution_key: str | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """Process one upload. Returns (page_count, drafts). Raises NormalizeError / AIError on failure.
    `solve=False` saves AI calls: only the questions whose answer is not printed in the file are solved.

    Heavy work (LibreOffice, PyMuPDF, Pillow) runs in a temporary directory; results are written to storage:
    pages/<job>/page-NNN.png for the review screen and figures/<name>.png for questions.
    """
    with tempfile.TemporaryDirectory(prefix=f"job-{job_id}-") as tmp:
        work = Path(tmp)
        await progress("Đang chuyển file thành ảnh từng trang…", 0, 1)
        source = work / ("source" + Path(source_key).suffix.lower())
        source.write_bytes(await storage.get(source_key))

        paired_solution_file: Path | None = None

        # Step 1: Chuẩn hóa file nén (.zip, .rar) tự động bung nén và ghép cặp Đề + Đáp án
        if source.suffix.lower() in (".zip", ".rar"):
            from app.ai.extract_engine import pair_exam_and_solution_files, unpack_archive
            archive_dir = work / "unpacked"
            unpacked_files = unpack_archive(source, archive_dir)
            pairs = pair_exam_and_solution_files(unpacked_files, soffice)
            if not pairs:
                raise NormalizeError("Gói nén không chứa file đề thi (.docx, .doc, .pdf) hợp lệ.")
            source = pairs[0][0]
            if pairs[0][1] and not solution_key:
                paired_solution_file = pairs[0][1]

        # Tải file đáp án đi kèm nếu được chỉ định riêng
        if solution_key:
            sol_ext = Path(solution_key).suffix.lower()
            paired_solution_file = work / f"solution{sol_ext}"
            paired_solution_file.write_bytes(await storage.get(solution_key))

        # Word files and PDFs with a text layer are read on this machine when that works: no AI call to read them.
        from app.ai.extract_engine import has_mathtype_ole

        local = None if source.suffix.lower() == ".docx" and has_mathtype_ole(source) else await asyncio.to_thread(
            read_locally, source
        )
        if local is not None and local.key_only:  # an answer / marking-scheme file: no question to import
            await progress("Tệp hướng dẫn chấm / đáp án: không có câu hỏi để nhập", 1, 1)
            return 0, []
        if local is not None and source.suffix.lower() == ".docx":
            pages, geometries = [], []  # read from its text: page images are not needed
        else:
            try:
                pages, geometries = await to_page_images(source, work / "pages", soffice)
            except NormalizeError:
                if local is None:
                    raise
                pages, geometries = [], []  # no LibreOffice here: the text is enough, only review images are missing

        # Fallback: if pandoc HTML could not parse a complex .docx (e.g. WMF shapes or nested math tables),
        # but LibreOffice successfully converted .docx -> .pdf in to_page_images, re-try read_locally on the generated PDF!
        if local is None and source.suffix.lower() == ".docx" and pages:
            pdf_candidate = work / "pages" / f"{source.stem}.pdf"
            if not pdf_candidate.exists():
                # Locate any generated PDF in the temp directory if available
                candidates = list(work.glob("**/*.pdf"))
                if candidates:
                    pdf_candidate = candidates[0]
            if pdf_candidate.exists():
                local = await asyncio.to_thread(read_locally, pdf_candidate)
        for number, page in enumerate(pages, start=1):
            await storage.put(page_key(job_id, number), page.read_bytes(), "image/png")

        extracted: list[ExtractedQuestion] = []
        figure_keys: dict[int, str] = {}
        figure_bytes: dict[int, bytes] = {}
        failed: set[int] = set()
        if local is not None:
            await progress(f"Đã đọc {len(local.questions)} câu trực tiếp từ file (không cần AI đọc trang)…", 0, 1)
            extracted = local.questions
            for index, png in local.figures.items():
                key = f"figures/{job_id}-{index + 1}-{secrets.token_hex(6)}.png"
                await storage.put(key, png, "image/png")
                figure_bytes[index], figure_keys[index] = png, key
            # Step 5: When _broken() detected broken formulas in specific questions, send only each broken question's
            # >=2000px crop to AI (temperature=0) to transcribe formulas into LaTeX without sending the whole exam.
            for index, crop_png in local.broken_crops.items():
                if index < len(extracted) and hasattr(provider, "transcribe_question"):
                    q_num = extracted[index].number
                    await progress(f"AI đang chép lại công thức câu {q_num} sang LaTeX…", index, len(extracted))
                    try:
                        transcribed = await provider.transcribe_question(crop_png, q_num, extracted[index].content)
                        if transcribed:
                            clean_trans = _QUESTION_LABEL.sub("", transcribed.strip(), count=1).strip()
                            extracted[index].content = strip_topic(clean_trans or transcribed, local.topics.get(index))
                    except AIQuotaExhausted:
                        raise
                    except Exception:
                        log.warning("Could not transcribe broken formulas of question %s in job %s", q_num, job_id)
        chunks = [] if local is not None else _chunks(pages)
        if chunks:
            contexts = await asyncio.to_thread(_text_contexts, source, len(pages))
            for chunk in chunks:
                for page in chunk:
                    if page.number <= len(contexts):
                        page.text_context = contexts[page.number - 1]
        for i, chunk in enumerate(chunks):
            first, last = chunk[0].number, max(p.number for p in chunk if not p.context_only)
            await progress(f"AI đang đọc trang {first}–{last} / {len(pages)}…", i, len(chunks))
            for q in await _extract(provider, chunk):
                if not 1 <= q.page <= len(pages):
                    q.page = first
                if not any(_same_question(q, seen) for seen in extracted):
                    extracted.append(q)
        if not extracted:
            return len(pages), []
        _trim_cross_question_overlap(extracted)

        for index, q in enumerate(extracted):
            if index in figure_bytes:
                continue  # Đã có hình chính xác từ local.figures (read_essay_pdf), giữ nguyên không ghi đè
            if q.figure_bbox is None or not pages:
                continue
            crop = work / "figures" / f"{index + 1}.png"
            if crop_figure(pages[q.page - 1], q.figure_bbox, crop, geometries[q.page - 1]):
                key = f"figures/{job_id}-{index + 1}-{secrets.token_hex(6)}.png"  # unguessable: figures are public
                figure_bytes[index] = crop.read_bytes()
                await storage.put(key, figure_bytes[index], "image/png")
                figure_keys[index] = key
            else:
                failed.add(index)

        # Step 3: Quét hình vẽ theo caption "Hình X" trên PDF (file PDF gốc hoặc PDF do LibreOffice sinh ra)
        pdf_target = None
        if source.suffix.lower() == ".pdf" and source.exists():
            pdf_target = source
        elif (work / "pages" / f"{source.stem}.pdf").exists():
            pdf_target = work / "pages" / f"{source.stem}.pdf"
        else:
            pdf_list = list(work.glob("**/*.pdf"))
            if pdf_list:
                pdf_target = pdf_list[0]

        if pdf_target and pdf_target.exists():
            from app.ai.extract_engine import extract_chemistry_schemes, extract_figures_by_caption
            try:
                caption_figs = await asyncio.to_thread(extract_figures_by_caption, pdf_target)
                for index, q in enumerate(extracted):
                    if index in figure_bytes:
                        continue
                    cleaned_body = q.content
                    for fig_label, fig_png in caption_figs.items():
                        if re.search(rf"\b{re.escape(fig_label)}\b", cleaned_body, re.I):
                            key = f"figures/{job_id}-{index + 1}-{secrets.token_hex(6)}.png"
                            figure_bytes[index] = fig_png
                            await storage.put(key, fig_png, "image/png")
                            figure_keys[index] = key
                            if index in failed:
                                failed.remove(index)
                            break
                # Chemistry schemes without a "Hình N" caption: the local reader crops them per question
                # (figure_bands). Only the AI-read path, which has no question regions, falls back to a page scan.
                source_name = Path(source_key).stem.casefold()
                is_chemistry_source = any(token in source_name for token in ("hoa", "hóa", "chem"))
                if is_chemistry_source and local is None:
                    schemes = await asyncio.to_thread(extract_chemistry_schemes, pdf_target)
                    by_page: dict[int, list[bytes]] = {}
                    for page_number, _, png in schemes:
                        by_page.setdefault(page_number, []).append(png)
                    for index, q in enumerate(extracted):
                        if index in figure_bytes or q.page not in by_page:
                            continue
                        figure_bytes[index] = by_page[q.page][0]
                        key = f"figures/{job_id}-{index + 1}-{secrets.token_hex(6)}.png"
                        await storage.put(key, figure_bytes[index], "image/png")
                        figure_keys[index] = key
            except Exception as exc:
                log.warning("extract_figures_by_caption failed: %s", exc)

        # Step 5: Nếu có file đáp án đi kèm, đọc lời giải trực tiếp để ghép vào đề
        paired_keys: dict[int, str] = {}
        paired_keys_by_index: list[str] = []
        if paired_solution_file and paired_solution_file.exists():
            sol_local = await asyncio.to_thread(read_locally, paired_solution_file)
            if sol_local and sol_local.explanations:
                paired_keys = sol_local.explanations
                paired_keys_by_index = [
                    sol_local.explanations[number]
                    for number in sorted(sol_local.explanations)
                ]
            elif sol_local and sol_local.questions:
                paired_keys = {q.number: q.content for q in sol_local.questions}
                paired_keys_by_index = [q.content for q in sol_local.questions]
            else:
                try:
                    from app.ai.extract_engine import (
                        docx_to_markdown_latex,
                        pdf_to_continuous_text,
                        split_into_structured_questions,
                    )
                    if paired_solution_file.suffix.lower() == ".docx":
                        sol_text = await asyncio.to_thread(docx_to_markdown_latex, paired_solution_file)
                    elif paired_solution_file.suffix.lower() == ".pdf":
                        sol_text = await asyncio.to_thread(pdf_to_continuous_text, paired_solution_file)
                    else:
                        sol_text = ""
                    if sol_text:
                        parsed_ans = split_into_structured_questions(sol_text)
                        paired_keys_by_index = [a.content for a in parsed_ans]
                        for a in parsed_ans:
                            paired_keys.setdefault(a.number, a.content)
                except Exception as exc:
                    log.warning("Could not parse paired solution file %s: %s", paired_solution_file.name, exc)

        # Step 4: Flag questions mentioning "như hình" without a figure, or starting with "1." without a stem,
        # and exclude flagged questions from AI solving.
        pre_flags: dict[int, list[str]] = {}
        for i, q in enumerate(extracted):
            q_flags = list((local.question_flags.get(i) if local else None) or [])
            cleaned_q = clean_paper_text(_QUESTION_LABEL.sub("", q.content.strip(), count=1))
            if references_figure(cleaned_q) and i not in figure_bytes:
                failed.add(i)
                if "figure_failed" not in q_flags:
                    q_flags.append("figure_failed")
            if q.type == "ESSAY" and has_missing_stem(cleaned_q):
                if "missing_stem" not in q_flags:
                    q_flags.append("missing_stem")
            if q_flags:
                pre_flags[i] = q_flags

        keys = {}
        for i, q in enumerate(extracted):
            local_key = local.explanations.get(i) if local else None
            paired_key = paired_keys.get(q.number)
            if not local_key and i < len(paired_keys_by_index):
                paired_key = paired_keys_by_index[i]
            keys[i] = local_key or paired_key or paired_keys.get(i) or q.key
        solutions: dict[int, Solution] = {}
        # Step 4 & Step 7: Questions with embedded answers/marking schemes in the file, or flagged with
        # missing_stem / figure_failed, are NOT sent to AI to solve.
        to_solve = [
            i
            for i, q in enumerate(extracted)
            if not keys.get(i) and i not in failed and not pre_flags.get(i)
        ]
        if not solve:  # the file's own answers are enough where it has them
            printed_in_file = set(local.answers if local else {}) | {
                i for i, q in enumerate(extracted) if q.key_label or (q.type != "MULTIPLE_CHOICE" and keys.get(i))
            }
            to_solve = [i for i in to_solve if i not in printed_in_file]
        batches = [to_solve[s : s + SOLVE_BATCH] for s in range(0, len(to_solve), SOLVE_BATCH)]
        for b, indexes in enumerate(batches):
            questions = [extracted[i] for i in indexes]
            await progress(
                f"AI đang giải câu {questions[0].number}–{questions[-1].number} ({len(extracted)} câu)…",
                b,
                len(batches),
            )
            figures = [FigureImage(extracted[i].number, figure_bytes[i]) for i in indexes if i in figure_bytes]
            try:
                answers = await _solve(provider, questions, figures)
            except AIQuotaExhausted:  # the whole job waits for tomorrow's quota rather than half-solved drafts
                raise
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
            _draft(
                q,
                solutions.get(i),
                figure_keys.get(i),
                i in failed,
                default_difficulty,
                keys.get(i),
                matrix_difficulty=(local.matrix_difficulties.get(i) if local else None),
                extra_flags=pre_flags.get(i),
                figure_captions=(local.figure_captions.get(i) if local else None),
                topic=(local.topics.get(i) if local else None),
            )
            for i, q in enumerate(extracted)
        ]
        printed = dict(local.answers) if local else {}
        for index, q in enumerate(extracted):  # answers the AI read in the file's answer table
            label = (q.key_label or "").strip().upper()[:1]
            if index not in printed and q.type == "MULTIPLE_CHOICE" and label in ("A", "B", "C", "D"):
                printed[index] = label
        for index, label in printed.items():
            _printed_answer(drafts[index], label)
        for index, points in (local.points if local else {}).items():
            drafts[index]["points"] = points
        await progress("Hoàn tất", 1, 1)
        return len(pages), drafts
