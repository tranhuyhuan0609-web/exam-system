"""Background worker: AI import jobs, plus a once-a-minute sweep that auto-submits expired attempts.

    python -m app.worker          # run forever
    python -m app.worker --once   # auto-submit expired attempts, process queued jobs, then exit

Jobs are claimed with FOR UPDATE SKIP LOCKED, so several workers can run side by side.
"""

import argparse
import asyncio
import logging
import sys
from datetime import timedelta

from sqlalchemy import func, select, update

from app.ai.normalize import NormalizeError
from app.ai.pipeline import run_pipeline
from app.ai.provider import AIError, AIProvider, GeminiProvider
from app.application.essay_grading import grade_pending_essays
from app.application.library_publish import auto_publish
from app.attempts import finalize_expired
from app.config import get_settings
from app.db import SessionLocal
from app.infrastructure.storage import StorageError, get_storage
from app.models import ImportJob, JobStatus

log = logging.getLogger("app.worker")

POLL_SECONDS = 3
AUTO_SUBMIT_SECONDS = 60
ESSAY_SECONDS = 5
# A PROCESSING job untouched for this long belongs to a worker that died; put it back in the queue.
STALE_AFTER = timedelta(minutes=30)


def default_provider() -> AIProvider:
    settings = get_settings()
    return GeminiProvider(settings.gemini_api_key, settings.gemini_model_list)


async def claim_next() -> int | None:
    async with SessionLocal() as session:
        job_id = await session.scalar(
            select(ImportJob.id)
            .where(ImportJob.status == JobStatus.QUEUED)
            .order_by(ImportJob.id)
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


async def requeue_stale() -> int:
    async with SessionLocal() as session:
        result = await session.execute(
            update(ImportJob)
            .where(ImportJob.status == JobStatus.PROCESSING, ImportJob.started_at < func.now() - STALE_AFTER)
            .values(status=JobStatus.QUEUED, stage="Xếp hàng lại sau khi worker bị dừng")
        )
        await session.commit()
        return result.rowcount


async def process(job_id: int, provider: AIProvider | None = None) -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        job = await session.get(ImportJob, job_id)

        async def progress(stage: str, done: int, total: int) -> None:
            job.stage, job.progress_done, job.progress_total = stage, done, total
            await session.commit()

        try:
            provider = provider or default_provider()
            page_count, drafts = await run_pipeline(
                job_id=job.id,
                source_key=job.stored_path,
                storage=get_storage(),
                provider=provider,
                soffice=settings.soffice_path,
                default_difficulty=job.default_difficulty.value,
                progress=progress,
            )
        except (NormalizeError, AIError, StorageError) as exc:
            job.status, job.error, job.stage = JobStatus.FAILED, str(exc), "Lỗi"
        except Exception as exc:  # unexpected: keep the worker alive and record what happened
            log.exception("Job %s crashed", job_id)
            job.status, job.error, job.stage = JobStatus.FAILED, f"Lỗi hệ thống: {exc}", "Lỗi"
        else:
            job.status, job.page_count, job.drafts = JobStatus.DONE, page_count, drafts
            job.stage = f"Đã đọc {len(drafts)} câu từ {page_count} trang" if drafts else "Không tìm thấy câu hỏi nào"
            job.models_used = ", ".join(sorted(provider.models_used))[:200] or None
        job.finished_at = await session.scalar(select(func.now()))
        await session.commit()
        log.info("Job %s finished: %s", job_id, job.status)
        if job.status == JobStatus.DONE:
            try:  # a library file: publish it and typeset clean PDFs when the drafts pass the checks
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
    if once:
        await auto_submit_once()
        await essay_grading_once()
        await ai_jobs_loop(once=True)
        return
    # Independent loops: AI import jobs, auto-submit sweep every minute, AI essay grading.
    await asyncio.gather(ai_jobs_loop(), auto_submit_loop(), essay_grading_loop())


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
