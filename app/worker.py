"""Background worker: AI import jobs, plus a once-a-minute sweep that auto-submits expired attempts.

    python -m app.worker          # run forever
    python -m app.worker --once   # auto-submit expired attempts, process queued jobs, then exit

Jobs are claimed with FOR UPDATE SKIP LOCKED, so several workers can run side by side.
"""

import argparse
import asyncio
import logging
import sys
import time
from datetime import timedelta

from sqlalchemy import func, select, update

from app.ai.cache import DbCache
from app.ai.frugal import ChatModel, FrugalProvider, bank_lookup
from app.ai.normalize import NormalizeError
from app.ai.pipeline import run_pipeline
from app.ai.provider import AIError, AIProvider, AIQuotaExhausted, GeminiProvider
from app.application.answer_keys import is_answer_key, read_answer_key_document
from app.application.essay_grading import grade_pending_essays
from app.application.library_publish import auto_publish, library_document, recheck_waiting
from app.application.similar import make_variants
from app.attempts import finalize_expired
from app.config import get_settings
from app.db import SessionLocal
from app.infrastructure.storage import StorageError, get_storage
from app.models import ImportJob, JobStatus
from app.pipeline.adapters.ai_chain import DbUsage

log = logging.getLogger("app.worker")

POLL_SECONDS = 2
QUOTA_PAUSE_SECONDS = 60  # all AI models out of quota: retry after 60s instead of waiting 30 minutes
_paused_until = 0.0
AUTO_SUBMIT_SECONDS = 60
ESSAY_SECONDS = 5
# A PROCESSING job untouched for this long belongs to a worker that died; put it back in the queue.
STALE_AFTER = timedelta(minutes=15)

_shared_provider: GeminiProvider | None = None
_shared_provider_sig: tuple | None = None


def default_provider() -> AIProvider:
    global _shared_provider, _shared_provider_sig
    settings = get_settings()
    sig = (
        tuple(settings.gemini_api_key_list),
        tuple(settings.gemini_model_list),
        tuple(settings._names(settings.gemini_extract_models)),
        settings.gemini_extract_thinking,
    )
    if _shared_provider is None or _shared_provider_sig != sig:
        _shared_provider = GeminiProvider(
            settings.gemini_api_key_list,
            settings.gemini_model_list,
            extract_models=settings._names(settings.gemini_extract_models),
            extract_thinking=settings.gemini_extract_thinking,
        )
        _shared_provider_sig = sig
    return _shared_provider


def chat_models() -> tuple[list[ChatModel], list[ChatModel]]:
    """(text solvers, vision readers) on free tiers, used before Gemini (app/ai/frugal.py)."""
    s, usage = get_settings(), DbUsage()
    limits, sizes = s.ai_daily_limit_map, s.ai_batch_size_map
    text, vision = [], []
    if s.groq_api_key_list and s.groq_model:
        for idx, key in enumerate(s.groq_api_key_list, start=1):
            label = f"groq-{idx}" if len(s.groq_api_key_list) > 1 else "groq"
            text.append(
                ChatModel(
                    label,
                    s.groq_base_url,
                    key,
                    s.groq_model,
                    usage,
                    limits.get("groq", 0),
                    batch_size=sizes.get("groq", 6),
                )
            )
    if s.openrouter_api_key:
        common = {
            "usage": usage,
            "daily_limit": limits.get("openrouter", 0),
            "min_interval": s.openrouter_min_interval,
            "batch_size": sizes.get("openrouter", 8),
        }
        key, url = s.openrouter_api_key, s.openrouter_base_url
        text += [ChatModel("openrouter", url, key, m, **common) for m in s._names(s.openrouter_model)]
        vision += [
            ChatModel("openrouter", url, key, m, vision=True, **common) for m in s._names(s.openrouter_vision_models)
        ]
    for provider in s.extra_providers:  # AI_PROVIDERS in .env (Azure for Students, Cerebras, …)
        extra = {
            "usage": usage,
            "daily_limit": limits.get(provider.name, 0),
            "batch_size": sizes.get(provider.name, 10),
            "headers": provider.headers,
        }
        url = provider.base_url
        text += [ChatModel(provider.name, url, provider.api_key, m, **extra) for m in provider.models]
        # Paid or credited vision models read better than the free ones: tried first for pages and figures
        vision = [
            *(ChatModel(provider.name, url, provider.api_key, m, vision=True, **extra) for m in provider.vision_models),
            *vision,
        ]
    return text, vision


async def claim_next() -> int | None:
    async with SessionLocal() as session:
        job_id = await session.scalar(
            select(ImportJob.id)
            .where(ImportJob.status == JobStatus.QUEUED)
            .order_by(
                (ImportJob.purpose == "SIMILAR").desc(),
                ImportJob.original_filename.ilike("%.docx").desc(),  # .docx files read locally in 0.05s first!
                ImportJob.id,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job_id is None:
            return None
        await session.execute(
            update(ImportJob)
            .where(ImportJob.id == job_id)
            .values(status=JobStatus.PROCESSING, started_at=func.now(), error=None, stage="Bắt đầu xử lý…")
        )
        await session.commit()
        return job_id


async def requeue_stale(all_processing: bool = False) -> int:
    async with SessionLocal() as session:
        cond = [ImportJob.status == JobStatus.PROCESSING]
        if not all_processing:
            cond.append(ImportJob.started_at < func.now() - STALE_AFTER)
        result = await session.execute(
            update(ImportJob).where(*cond).values(status=JobStatus.QUEUED, stage="Xếp hàng lại sau khi worker bị dừng")
        )
        await session.commit()
        return result.rowcount


async def process(job_id: int, provider: AIProvider | None = None) -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        job = await session.get(ImportJob, job_id)
        progress_lock = asyncio.Lock()

        async def progress(stage: str, done: int, total: int) -> None:
            async with progress_lock:
                job.stage = stage
                job.progress_done = done
                job.progress_total = total
                await session.commit()

        try:
            if provider is None:  # bank answers and free text models first; Gemini reads the pages
                text, vision = chat_models()
                lookup = await bank_lookup(session, job.root_id)
                provider = FrugalProvider(default_provider(), lookup, text, vision, cache=DbCache())
            doc = await library_document(session, job)
            codes = None
            if doc is not None and is_answer_key(doc):  # an answer table, not an exam: read the official answers
                await progress("AI đang đọc bảng đáp án chính thức…", 0, 1)
                data = await get_storage().get(job.stored_path)
                codes = await read_answer_key_document(session, job, doc, data, provider)
                page_count, drafts = 0, []
            else:
                page_count, drafts = await run_pipeline(
                    job_id=job.id,
                    source_key=job.stored_path,
                    solution_key=job.solution_stored_path,
                    storage=get_storage(),
                    provider=provider,
                    soffice=settings.effective_soffice_path,
                    default_difficulty=job.default_difficulty.value,
                    progress=progress,
                    solve=(job.purpose == "SIMILAR" or getattr(provider, "is_fake", False)),
                )
                if job.purpose == "SIMILAR":  # a teacher's paper: similar papers, nothing for the bank
                    text, _ = chat_models()
                    try:
                        gemini = default_provider()
                    except AIError:
                        gemini = None
                    job.variants = await make_variants(drafts, job.versions, text, gemini, progress)
        except AIQuotaExhausted as exc:  # back in the queue; the loop pauses until quotas may have refilled
            global _paused_until
            _paused_until = time.monotonic() + QUOTA_PAUSE_SECONDS
            job.status, job.error, job.stage = JobStatus.QUEUED, None, f"Chờ quota AI: {exc}"[:200]
        except (NormalizeError, AIError, StorageError) as exc:
            job.status, job.error, job.stage = JobStatus.FAILED, str(exc), "Lỗi"
        except Exception as exc:  # unexpected: keep the worker alive and record what happened
            log.exception("Job %s crashed", job_id)
            job.status, job.error, job.stage = JobStatus.FAILED, f"Lỗi hệ thống: {exc}", "Lỗi"
        else:
            job.status, job.error, job.page_count, job.drafts = JobStatus.DONE, None, page_count, drafts
            job.stage = f"Đã đọc {len(drafts)} câu từ {page_count} trang" if drafts else "Không tìm thấy câu hỏi nào"
            if codes is not None:
                job.stage = f"Đã đọc đáp án chính thức của {codes} mã đề"
            if job.purpose == "SIMILAR":
                job.stage = f"Đã viết {len(job.variants)} câu cho {job.versions} đề tương tự từ {len(drafts)} câu"
            job.models_used = ", ".join(sorted(provider.models_used))[:200] or None
            if getattr(provider, "reused", 0):
                job.stage = f"{job.stage} · {provider.reused} câu lấy đáp án từ ngân hàng"[:200]
            if getattr(provider, "cached", 0):
                job.stage = f"{job.stage} · {provider.cached} phần lấy từ bộ nhớ đệm"[:200]
        job.finished_at = await session.scalar(select(func.now()))
        await session.commit()
        log.info("Job %s finished: %s", job_id, job.status)
        if job.status == JobStatus.DONE and job.purpose != "SIMILAR":
            try:  # a library file: publish it and typeset clean PDFs when the drafts pass the checks
                if codes:  # exams of this paper that were waiting for their official answers
                    await recheck_waiting(session, job.category_id)
                else:
                    await auto_publish(session, job)
            except Exception:  # the drafts stay for the admin's review screen
                log.exception("Job %s: automatic publication failed", job_id)
                await session.rollback()


async def auto_submit_once(batch: int = 500) -> int:
    """Grade every attempt whose deadline (+ grace) has passed, using the answers auto-saved so far."""
    total = 0
    while True:
        async with SessionLocal() as session:
            done = await finalize_expired(session, limit=batch)
        total += done
        if done < batch:
            return total


async def auto_submit_loop() -> None:
    while True:
        try:
            if graded := await auto_submit_once():
                log.info("Auto-submitted %s expired attempt(s)", graded)
        except Exception:  # never let a DB hiccup stop the loop
            log.exception("Auto-submit failed")
        await asyncio.sleep(AUTO_SUBMIT_SECONDS)


async def essay_grading_once(provider: AIProvider | None = None) -> int:
    try:
        provider = provider or default_provider()
    except AIError as exc:  # no API key: essays stay pending until one is configured
        log.warning("Essay grading disabled: %s", exc)
        return 0
    return await grade_pending_essays(provider)


async def essay_grading_loop() -> None:
    while True:
        try:
            if graded := await essay_grading_once():
                log.info("AI graded essays of %s attempt(s)", graded)
        except Exception:
            log.exception("Essay grading loop failed")
        await asyncio.sleep(ESSAY_SECONDS)


async def ai_jobs_loop(once: bool = False) -> None:
    if requeued := await requeue_stale():
        log.info("Requeued %s stale job(s)", requeued)
    while True:
        if time.monotonic() < _paused_until:  # every AI model is out of quota for now
            if once:
                return
            await asyncio.sleep(POLL_SECONDS)
            continue
        job_id = await claim_next()
        if job_id is not None:
            log.info("Processing job %s", job_id)
            await process(job_id)
            continue
        if once:
            return
        await asyncio.sleep(POLL_SECONDS)


async def run(once: bool = False) -> None:
    log.info("Worker started (models: %s)", get_settings().gemini_models)
    if requeued := await requeue_stale(all_processing=True):
        log.info("Requeued %s interrupted PROCESSING job(s) on startup", requeued)
    if once:
        await auto_submit_once()
        await essay_grading_once()
        await ai_jobs_loop(once=True)
        return
    # Independent loops: 2 parallel AI import workers, auto-submit sweep every minute, AI essay grading.
    await asyncio.gather(ai_jobs_loop(), ai_jobs_loop(), auto_submit_loop(), essay_grading_loop())


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    parser.add_argument("--once", action="store_true", help="exit when the queue is empty")
    args = parser.parse_args()
    try:
        asyncio.run(run(once=args.once))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
