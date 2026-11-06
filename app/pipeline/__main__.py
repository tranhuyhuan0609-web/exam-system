"""Crawler pipeline CLI.

python -m app.pipeline sync-configs          # configs/sites/*.yaml -> crawler_sites
python -m app.pipeline crawl loigiaihay [--max-pages 20] [--force]
python -m app.pipeline scheduler             # runs queued (admin "Chạy ngay") and scheduled crawls
python -m app.pipeline silver                # raw.exam.ingestion -> crawled_questions -> questions
python -m app.pipeline dlq                   # dead letters -> MongoDB dlq_events
python -m app.pipeline replay-dlq [--limit 100]
python -m app.pipeline reparse loigiaihay    # Bronze -> Silver again, without crawling
python -m app.pipeline embed-questions       # backfill embeddings of existing questions (semantic dedup)
python -m app.pipeline recheck SIMILAR       # dedup + labelling again for the items in a status
python -m app.pipeline recheck READY --no-semantic   # publish without the cosine check (embeddings at quota)
python -m app.pipeline renormalize           # rule-based formula conversion for AI-queued items
python -m app.pipeline adopt-series "Vật lí 11" "Kết nối tri thức"
python -m app.pipeline drive-sync thptqg2026 [--max-files 50]   # Google Drive folder -> library
"""

import argparse
import asyncio
import logging
import sys

from app.config import get_settings


async def _sync() -> None:
    from app.db import SessionLocal
    from app.pipeline.use_cases.sync_configs import sync_configs

    async with SessionLocal() as session:
        changed = await sync_configs(session, get_settings().crawler_configs_dir)
    print("Updated:", ", ".join(changed) if changed else "nothing (already in sync)")


async def _reparse(site_id: str) -> None:
    from app.db import SessionLocal
    from app.pipeline.use_cases.silver import reparse_site
    from app.pipeline.workers import silver_deps

    deps = silver_deps()
    async with SessionLocal() as session:
        print("Questions (re)parsed:", await reparse_site(session, deps, site_id))


async def _recheck(status: str, no_semantic: bool = False) -> None:
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import CrawledQuestion, CrawlItemStatus
    from app.pipeline.use_cases.silver import reprocess
    from app.pipeline.workers import silver_deps

    deps = silver_deps()
    if no_semantic:  # embeddings out of quota: exact (SHA-256) dedup only; run embed-questions later
        deps.embedder = None
    async with SessionLocal() as session:
        items = (
            await session.scalars(
                select(CrawledQuestion)
                .where(CrawledQuestion.status == CrawlItemStatus(status))
                .order_by(CrawledQuestion.id)
            )
        ).all()
        for item in items:
            await reprocess(session, deps, item)
            await session.flush()
        await session.commit()
        print(f"Rechecked {len(items)}:", {s.value: sum(1 for i in items if i.status == s) for s in CrawlItemStatus})


async def _renormalize() -> None:
    from app.db import SessionLocal
    from app.pipeline.use_cases.silver import renormalize_waiting
    from app.pipeline.workers import silver_deps

    deps = silver_deps()
    async with SessionLocal() as session:
        print("Questions that were waiting for the AI:", await renormalize_waiting(session, deps))


async def _drive_sync(source_id: str, max_files: int | None) -> None:
    from app.db import SessionLocal
    from app.infrastructure.storage import get_bronze_storage, get_storage
    from app.pipeline.adapters.drive import DriveClient
    from app.pipeline.adapters.mongo import MongoRawStore
    from app.pipeline.use_cases.drive_sync import load_sources, sync_drive

    settings = get_settings()
    source = load_sources(settings.drive_configs_dir)[source_id]
    drive, store = DriveClient(settings.google_drive_api_key), MongoRawStore()
    try:
        async with SessionLocal() as session:
            stats = await sync_drive(session, source, drive, get_bronze_storage(), store, get_storage(), max_files)
    finally:
        await drive.close()
        await store.close()
    print("Drive sync:", stats)


async def _adopt(root: str, series: str) -> None:
    from app.db import SessionLocal
    from app.pipeline.use_cases.toc import adopt_series

    async with SessionLocal() as session:
        moved = await adopt_series(session, root, series)
        await session.commit()
    print(f"Moved {moved} chapters of {root} under {series}")


async def _embed(batch: int = 100) -> None:
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Question
    from app.pipeline.adapters.gemini import GeminiEmbedder

    embedder, total = GeminiEmbedder(), 0
    while True:
        async with SessionLocal() as session:
            rows = (await session.scalars(select(Question).where(Question.embedding.is_(None)).limit(batch))).all()
            if not rows:
                break
            vectors = await embedder.embed(
                [" ".join([q.content, *(c["content"] for c in q.choices or [])]) for q in rows]
            )
            for q, v in zip(rows, vectors, strict=True):
                q.embedding = v
            await session.commit()
            total += len(rows)
            print("Embedded", total)
    print("Done:", total)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="python -m app.pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync-configs")
    crawl = sub.add_parser("crawl")
    crawl.add_argument("site_id")
    crawl.add_argument("--max-pages", type=int)
    crawl.add_argument("--force", action="store_true", help="ignore refresh_days")
    crawl.add_argument("--run-id", type=int)
    crawl.add_argument("--seed", help="only seeds whose label contains this, e.g. 'Toán 11 – Kết nối'")
    sub.add_parser("scheduler")
    sub.add_parser("silver")
    sub.add_parser("dlq")
    replay = sub.add_parser("replay-dlq")
    replay.add_argument("--limit", type=int, default=100)
    reparse = sub.add_parser("reparse")
    reparse.add_argument("site_id")
    sub.add_parser("embed-questions")
    sub.add_parser("renormalize")
    drive = sub.add_parser("drive-sync")
    drive.add_argument("source_id")
    drive.add_argument("--max-files", type=int)
    adopt = sub.add_parser("adopt-series")
    adopt.add_argument("root")
    adopt.add_argument("series")
    recheck = sub.add_parser("recheck")
    recheck.add_argument("status", choices=["SIMILAR", "NEEDS_REVIEW", "READY", "DUPLICATE"])
    recheck.add_argument("--no-semantic", action="store_true", help="skip the embedding (cosine) duplicate check")
    args = parser.parse_args(argv)

    if sys.platform == "win32" and args.command != "crawl":  # aiokafka/asyncpg are happiest on the selector loop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    if args.command == "sync-configs":
        asyncio.run(_sync())
    elif args.command == "crawl":
        from app.pipeline.crawl.runner import run_crawl

        run_id = run_crawl(
            args.site_id,
            args.run_id,
            args.max_pages,
            args.force,
            trigger="cli" if not args.run_id else "queued",
            seed_filter=args.seed,
        )
        print("Crawl run", run_id, "finished")
    elif args.command == "scheduler":
        from app.pipeline.workers import run_scheduler

        asyncio.run(run_scheduler())
    elif args.command == "silver":
        from app.pipeline.workers import run_silver

        asyncio.run(run_silver())
    elif args.command == "dlq":
        from app.pipeline.workers import run_dlq

        asyncio.run(run_dlq())
    elif args.command == "replay-dlq":
        from app.pipeline.workers import replay_dlq

        print("Replayed:", asyncio.run(replay_dlq(args.limit)))
    elif args.command == "reparse":
        asyncio.run(_reparse(args.site_id))
    elif args.command == "embed-questions":
        asyncio.run(_embed())
    elif args.command == "drive-sync":
        asyncio.run(_drive_sync(args.source_id, args.max_files))
    elif args.command == "renormalize":
        asyncio.run(_renormalize())
    elif args.command == "adopt-series":
        asyncio.run(_adopt(args.root, args.series))
    elif args.command == "recheck":
        asyncio.run(_recheck(args.status, args.no_semantic))


if __name__ == "__main__":
    main()
