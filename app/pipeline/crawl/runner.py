"""Run one crawl (a Scrapy process) and record it in crawl_runs.

Scrapy's reactor cannot be restarted inside a process, so the scheduler starts each run as
`python -m app.pipeline crawl <site> --run-id N` in a subprocess.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, engine
from app.models import CrawlerSite, CrawlRun, CrawlRunStatus
from app.pipeline.domain.models import SiteConfig


async def _prepare(site_id: str, run_id: int | None, max_pages: int | None, force: bool, trigger: str):
    from app.pipeline.adapters.mongo import MongoRawStore

    async with SessionLocal() as session:
        site = await session.get(CrawlerSite, site_id)
        if site is None:
            raise SystemExit(f"Unknown site {site_id!r}: run `python -m app.pipeline sync-configs` first")
        config = SiteConfig.model_validate(site.config)
        run = await session.get(CrawlRun, run_id) if run_id else None
        if run is None:
            run = CrawlRun(site_id=site_id, trigger=trigger, max_pages=max_pages, force=force)
            session.add(run)
        run.status = CrawlRunStatus.RUNNING
        run.started_at = datetime.now(UTC)
        await session.commit()
        run_id, max_pages, force = run.id, run.max_pages or max_pages, run.force or force
    skip: set[str] = set()
    if not force:
        store = MongoRawStore()
        skip = await store.fetched_since(site_id, datetime.now(UTC) - timedelta(days=config.refresh_days))
        await store.close()
    await engine.dispose()  # the Scrapy reactor runs its own event loop
    return config, run_id, max_pages, skip


async def _finish(run_id: int, stats: dict, error: str | None) -> None:
    async with SessionLocal() as session:
        run = await session.get(CrawlRun, run_id)
        run.pages_fetched = int(stats.get("response_received_count", 0))
        run.pages_skipped = int(stats.get("pages/skipped_recent", 0)) + int(stats.get("bronze/unchanged", 0))
        run.pages_failed = int(stats.get("pages/failed", 0))
        run.docs_published = int(stats.get("bronze/published", 0))
        run.finished_at = datetime.now(UTC)
        run.error = error
        run.status = CrawlRunStatus.FAILED if error else CrawlRunStatus.DONE
        site = await session.get(CrawlerSite, run.site_id)
        site.last_run_at = run.finished_at
        await session.commit()
    await engine.dispose()


def run_crawl(
    site_id: str, run_id: int | None = None, max_pages: int | None = None, force: bool = False, trigger: str = "cli"
) -> int:
    from scrapy.crawler import CrawlerProcess
    from scrapy.utils.reactor import install_reactor

    from app.pipeline.crawl.spider import QuestionSpider, scrapy_settings

    config, run_id, max_pages, skip = asyncio.run(_prepare(site_id, run_id, max_pages, force, trigger))
    install_reactor("twisted.internet.asyncioreactor.AsyncioSelectorReactor")
    process = CrawlerProcess(scrapy_settings(config, get_settings().crawler_user_agent, max_pages))
    crawler = process.create_crawler(QuestionSpider)
    error = None
    try:
        process.crawl(crawler, config=config.model_dump(mode="json"), run_id=run_id, skip_urls=sorted(skip))
        process.start()
        reason = crawler.stats.get_value("finish_reason")
        if reason not in ("finished", "closespider_pagecount"):
            error = f"Scrapy stopped: {reason}"
    except Exception as exc:  # noqa: BLE001
        error = repr(exc)
    stats = crawler.stats.get_stats() if crawler.stats else {}
    asyncio.run(_finish(run_id, stats, error))
    return run_id


async def queued_runs() -> list[CrawlRun]:
    async with SessionLocal() as session:
        return list(
            (
                await session.scalars(
                    select(CrawlRun).where(CrawlRun.status == CrawlRunStatus.QUEUED).order_by(CrawlRun.id)
                )
            ).all()
        )
