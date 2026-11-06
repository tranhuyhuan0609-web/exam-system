"""Long-running pipeline processes: scheduler, Silver consumer (+ AI sweep), DLQ consumer; and DLQ replay."""

import asyncio
import logging
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.infrastructure.storage import get_bronze_storage, get_storage
from app.models import CrawlerSite, CrawlRun, CrawlRunStatus
from app.pipeline.domain.models import SiteConfig
from app.pipeline.ports import DLQ_TOPIC, LIBRARY_TOPIC, RAW_TOPIC, AIUnavailable
from app.pipeline.use_cases.rescue import place_uncategorised, rescue_waiting
from app.pipeline.use_cases.silver import SilverDeps, ai_sweep, backfill_embeddings, ingest_document, retry_ready

log = logging.getLogger(__name__)
VN = timezone(timedelta(hours=7))


def silver_deps(store=None) -> SilverDeps:
    from app.pipeline.adapters.http import HttpDownloader
    from app.pipeline.adapters.mongo import MongoRawStore

    settings = get_settings()
    from app.pipeline.adapters.ai_chain import build_chain

    enricher = build_chain()  # Gemini -> Groq -> OpenRouter, whichever have keys
    embedder = None
    if settings.gemini_api_key:
        from app.pipeline.adapters.gemini import GeminiEmbedder

        embedder = GeminiEmbedder()
    else:
        log.warning("GEMINI_API_KEY is not set: no semantic dedup")
    if enricher is None:
        log.warning("No AI provider configured: questions needing AI wait for review")
    return SilverDeps(
        store=store or MongoRawStore(),
        storage=get_storage(),
        bronze_storage=get_bronze_storage(),
        downloader=HttpDownloader(),
        enricher=enricher,
        embedder=embedder,
    )


# ---------------------------------------------------------------- Silver


async def handle_raw_event(deps: SilverDeps, event: dict) -> None:
    doc = await deps.store.get(event["doc_id"])
    if doc is None:
        raise LookupError(f"Bronze document {event['doc_id']} not found")
    async with SessionLocal() as session:
        await ingest_document(session, deps, doc)


RESCUE_EVERY = 30 * 60  # seconds between passes over the questions waiting for review


def rescue_models() -> list:
    """Free text models for completing questions (app/pipeline/use_cases/rescue.py)."""
    from app.worker import chat_models

    text, _ = chat_models()
    return text


async def run_silver(stop: asyncio.Event | None = None) -> None:
    from app.pipeline.adapters.kafka import KafkaBus, consume

    deps = silver_deps()
    bus = await KafkaBus().start()

    async def to_dlq(event: dict, error: str) -> None:
        await bus.publish(DLQ_TOPIC, {"stage": "silver", "event": event, "error": error})

    async def sweep() -> None:
        next_rescue = 0.0
        while not (stop and stop.is_set()):
            pause = 15
            try:
                async with SessionLocal() as session:
                    handled = await ai_sweep(session, deps)
                async with SessionLocal() as session:
                    handled += await retry_ready(session, deps)
                if not handled:  # idle: catch up on embeddings skipped while they were at quota
                    async with SessionLocal() as session:
                        await backfill_embeddings(session, deps)
                    if time.monotonic() >= next_rescue:  # and finish what waits for review, when a machine can
                        next_rescue = time.monotonic() + RESCUE_EVERY
                        models = rescue_models()
                        async with SessionLocal() as session:
                            log.info("Rescue: %s", await rescue_waiting(session, models))
                        async with SessionLocal() as session:
                            log.info("Placed: %s", await place_uncategorised(session, models))
                pause = 1 if handled else 15
            except AIUnavailable:
                pause = 120  # quota / overload: wait for it to recover
            except Exception:  # noqa: BLE001
                log.exception("AI sweep failed")
            await asyncio.sleep(pause)

    await asyncio.gather(
        consume(RAW_TOPIC, "silver", lambda e: handle_raw_event(deps, e), on_error=to_dlq, stop=stop), sweep()
    )


# ---------------------------------------------------------------- library (Drive files)


async def handle_library_event(event: dict) -> None:
    from app.pipeline.use_cases.drive_sync import ingest_library_event, load_sources

    settings = get_settings()
    source = load_sources(settings.drive_configs_dir).get(event["source_id"])
    if source is None:
        log.warning("Library event for unknown Drive source %s", event["source_id"])
        return
    async with SessionLocal() as session:
        await ingest_library_event(session, event, source, get_bronze_storage(), get_storage(), settings.soffice_path)


async def run_library(stop: asyncio.Event | None = None) -> None:
    """raw.library.ingestion -> library documents (+ split, answer tables, AI import jobs)."""
    from app.pipeline.adapters.kafka import KafkaBus, consume

    bus = await KafkaBus().start()

    async def to_dlq(event: dict, error: str) -> None:
        await bus.publish(DLQ_TOPIC, {"stage": "library", "event": event, "error": error})

    await consume(LIBRARY_TOPIC, "library", handle_library_event, on_error=to_dlq, stop=stop)


async def drive_crawl_once(source, bus, drive_factory=None) -> dict:
    """One run of a Drive source: fetch into Bronze and announce each file on Kafka."""
    from app.pipeline.adapters.drive import DriveClient
    from app.pipeline.adapters.mongo import MongoRawStore
    from app.pipeline.use_cases.drive_sync import crawl_drive, known_in

    drive = (drive_factory or (lambda: DriveClient(get_settings().google_drive_api_key)))()
    store = MongoRawStore()

    async def emit(event: dict) -> None:
        await bus.publish(LIBRARY_TOPIC, event, key=event["file_id"])

    try:
        async with SessionLocal() as session:
            return await crawl_drive(source, drive, get_bronze_storage(), store, emit, known_in(session))
    finally:
        await drive.close()
        await store.close()


async def run_drive_schedule(poll_seconds: int = 60, stop: asyncio.Event | None = None) -> None:
    """Every Drive source in configs/drive runs every `every_hours`; a run fetches at most `max_files` files and
    stops at Google's block page, so a large folder is taken a little at a time, never waiting for the AI."""
    from app.pipeline.adapters.kafka import KafkaBus
    from app.pipeline.use_cases.drive_sync import load_sources

    settings = get_settings()
    if not settings.google_drive_api_key:
        log.info("No GOOGLE_DRIVE_API_KEY: Drive sources are not crawled")
        return
    bus = await KafkaBus().start()
    next_run: dict[str, datetime] = {}
    while not (stop and stop.is_set()):
        now = datetime.now(UTC)
        for source in load_sources(settings.drive_configs_dir).values():
            if next_run.get(source.source_id, now) > now:
                continue
            try:
                stats = await drive_crawl_once(source, bus)
                log.info("Drive %s: %s", source.source_id, stats)
                # More files waiting and not blocked: come back soon; otherwise after the usual interval.
                wait = timedelta(minutes=20) if stats.get("more") and not stats.get("blocked") else None
            except Exception:  # noqa: BLE001 - a bad run must not stop the schedule
                log.exception("Drive %s run failed", source.source_id)
                wait = None
            next_run[source.source_id] = datetime.now(UTC) + (wait or timedelta(hours=source.every_hours))
        await asyncio.sleep(poll_seconds)


# ---------------------------------------------------------------- DLQ


async def run_dlq(stop: asyncio.Event | None = None) -> None:
    from app.pipeline.adapters.kafka import consume
    from app.pipeline.adapters.mongo import MongoRawStore

    store = MongoRawStore()

    async def archive(event: dict) -> None:
        await store.save_dead_letter(event.get("event", event), event.get("stage", "unknown"), event.get("error", ""))

    await consume(DLQ_TOPIC, "dlq-archiver", archive, stop=stop)


async def replay_dlq(limit: int = 100) -> int:
    """Re-send archived Silver failures to raw.exam.ingestion (after a parser fix, for example)."""
    from app.pipeline.adapters.kafka import KafkaBus
    from app.pipeline.adapters.mongo import MongoRawStore

    store, bus = MongoRawStore(), await KafkaBus().start()
    count = 0
    try:
        for row in await store.dead_letters(limit, stage="silver"):
            if "doc_id" in row["event"]:
                await bus.publish(RAW_TOPIC, row["event"], key=row["event"]["doc_id"])
                await store.mark_replayed(row["id"])
                count += 1
    finally:
        await bus.stop()
        await store.close()
    return count


# ---------------------------------------------------------------- scheduler


def _due(config: SiteConfig, last_run_at: datetime | None, now: datetime) -> bool:
    local = now.astimezone(VN)
    for slot in config.schedule_times:
        hour, minute = (int(x) for x in slot.split(":"))
        at = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at <= local and (last_run_at is None or last_run_at.astimezone(VN) < at):
            return True
    return False


async def _next_run() -> int | None:
    async with SessionLocal() as session:
        run = await session.scalar(
            select(CrawlRun).where(CrawlRun.status == CrawlRunStatus.QUEUED).order_by(CrawlRun.id).limit(1)
        )
        if run is not None:
            return run.id
        now = datetime.now(UTC)
        for site in (await session.scalars(select(CrawlerSite).where(CrawlerSite.enabled))).all():
            if _due(SiteConfig.model_validate(site.config), site.last_run_at, now):
                run = CrawlRun(site_id=site.site_id, trigger="schedule")
                session.add(run)
                site.last_run_at = now  # claim the slot even if the run fails
                await session.commit()
                return run.id
    return None


async def run_scheduler(poll_seconds: int = 20) -> None:
    """Website crawls (one at a time, in subprocesses) and Drive sources, side by side."""
    await asyncio.gather(_run_site_schedule(poll_seconds), run_drive_schedule())


async def _run_site_schedule(poll_seconds: int = 20) -> None:
    log.info("Crawler scheduler started")
    while True:
        run_id = await _next_run()
        if run_id is None:
            await asyncio.sleep(poll_seconds)
            continue
        async with SessionLocal() as session:
            run = await session.get(CrawlRun, run_id)
            site_id = run.site_id
        log.info("Starting crawl run %s (%s)", run_id, site_id)
        # A thread waits on a plain subprocess: asyncio subprocesses need the Proactor loop, not available on
        # Windows with the selector loop the database driver uses (Docker / Linux would be fine either way).
        command = [sys.executable, "-m", "app.pipeline", "crawl", site_id, "--run-id", str(run_id)]
        process = await asyncio.to_thread(subprocess.run, command, check=False)
        if process.returncode:
            async with SessionLocal() as session:
                run = await session.get(CrawlRun, run_id)
                if run.status in (CrawlRunStatus.QUEUED, CrawlRunStatus.RUNNING):
                    run.status, run.error = CrawlRunStatus.FAILED, f"crawler exited with {process.returncode}"
                    run.finished_at = datetime.now(UTC)
                    await session.commit()
