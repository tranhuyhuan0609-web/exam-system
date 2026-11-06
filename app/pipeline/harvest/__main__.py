"""Harvest open sources into the library and the question bank (plan: configs/open_sources.yaml).

    python -m app.pipeline.harvest run                     # every source, pages_per_run pages per plan item
    python -m app.pipeline.harvest run --source openalex --pages 50
    python -m app.pipeline.harvest files --limit 500       # store open PDFs of listed works
    python -m app.pipeline.harvest trivia [--force]        # Open Trivia DB -> question bank (via Silver)
    python -m app.pipeline.harvest status
    python -m app.pipeline.harvest anthologies              # reports of a field from open papers
    python -m app.pipeline.harvest books [--only "Vật lí 10"]   # admin: review books from the question bank
    python -m app.pipeline.harvest loop                    # run at the plan's schedule_times (Vietnam time)

Each run continues where the last one stopped (cursors in MongoDB harvest_state). Optional keys in .env:
OPENALEX_API_KEY (openalex.org/settings/api: own daily budget), SEMANTIC_SCHOLAR_API_KEY, CROSSREF_MAILTO.
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import LibraryDocument
from app.pipeline.harvest.files import store_files
from app.pipeline.harvest.sources import CLIENTS, http_for
from app.pipeline.harvest.store import Plan, StateStore, load_plan, save_records

log = logging.getLogger("app.pipeline.harvest")
VN = timezone(timedelta(hours=7))


async def harvest(plan: Plan, states: StateStore, source: str | None = None, pages: int | None = None) -> dict:
    totals: dict[str, int] = {}
    for name, items in plan.sources.items():
        if source and name != source or name not in CLIENTS:
            continue
        fetch, http = CLIENTS[name][0], http_for(name)
        try:
            for item in items:
                key = f"{name}:{item.name}"
                state = await states.get(key)
                if state.get("done"):
                    continue
                cursor, records, new = state.get("cursor"), state.get("records", 0), 0
                for _ in range(pages or item.pages or plan.pages_per_run):
                    try:
                        found, cursor = await fetch(http, item, cursor)
                    except Exception as exc:  # the next run starts again from the same cursor
                        log.warning("%s: %s", key, exc)
                        break
                    async with SessionLocal() as session:
                        new += await save_records(session, found)
                    records += len(found)
                    await states.put(key, cursor=cursor, done=cursor is None, records=records)
                    if cursor is None:
                        break
                log.info("%s: %s new documents (%s read in all)", key, new, records)
                totals[name] = totals.get(name, 0) + new
        finally:
            await http.close()
    return totals


async def trivia(plan: Plan, states: StateStore, force: bool = False) -> dict:
    from app.pipeline.adapters.kafka import KafkaBus
    from app.pipeline.adapters.mongo import MongoRawStore
    from app.pipeline.harvest.trivia import harvest_trivia

    raw_store, bus = MongoRawStore(), await KafkaBus().start()
    try:
        return await harvest_trivia(plan.opentrivia, raw_store, bus, states, force=force)
    finally:
        await bus.stop()
        await raw_store.close()


async def run_all(plan: Plan, states: StateStore) -> None:
    log.info("Harvest: %s", await harvest(plan, states))
    log.info("Files: %s", await files_batch(plan))
    try:
        log.info("Open Trivia: %s", await trivia(plan, states))
    except Exception:
        log.exception("Open Trivia failed")
    try:
        log.info("Anthologies: %s", await anthologies())
    except Exception:
        log.exception("Binding anthologies failed")


async def anthologies() -> dict:
    """Reports of one field (Giáo dục học, Toán học …) bound from open papers, twenty at a time."""
    from app.pipeline.harvest.anthologies import compile_anthologies

    async with SessionLocal() as session:
        return await compile_anthologies(session)


async def books(only: str | None = None) -> dict:
    """Review books typeset from the question bank: run by an admin, not on a schedule."""
    from app.application.books import compile_books

    async with SessionLocal() as session:
        return await compile_books(session, only=only)


async def files_batch(plan: Plan) -> dict:
    return await store_files(plan.files_per_run, plan.max_file_mb, plan.file_concurrency, plan.max_storage_gb)


async def loop() -> None:
    """Harvest runs at the plan's times; between them, open PDFs are fetched batch after batch."""
    done: set[str] = set()
    while True:
        plan = load_plan()  # edits to the plan apply at the next slot
        now = datetime.now(VN)
        for time_of_day in plan.schedule_times:
            hour, minute = (int(part) for part in time_of_day.split(":"))
            start, slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0), f"{now:%F} {time_of_day}"
            if start <= now < start + timedelta(hours=1) and slot not in done:  # within the hour after the slot
                done.add(slot)
                states = StateStore()
                try:
                    await run_all(plan, states)
                except Exception:
                    log.exception("Harvest run failed")
                finally:
                    await states.close()
                break
        else:
            try:
                stats = await files_batch(plan)
            except Exception:
                log.exception("Fetching files failed")
                stats = {}
            if stats.get("stored") or stats.get("failed"):
                log.info("Files: %s", stats)
                continue  # more waiting: the next batch at once
        await asyncio.sleep(60)


async def status() -> None:
    states = StateStore()
    try:
        for row in await states.all():
            mark = "xong" if row.get("done") else "đang chạy"
            print(f"{row['_id']:<45} {row.get('records', 0):>9} bản ghi  {mark}")
    finally:
        await states.close()
    async with SessionLocal() as session:
        rows = await session.execute(
            select(
                LibraryDocument.source,
                func.count(),
                func.count().filter(LibraryDocument.storage_key != ""),
            ).group_by(LibraryDocument.source)
        )
        for source, total, stored in rows:
            print(f"{source:<25} {total:>9} tài liệu  {stored:>7} có file")


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="python -m app.pipeline.harvest")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--source", choices=sorted(CLIENTS))
    run.add_argument("--pages", type=int, help="pages per plan item (default: the plan's pages_per_run)")
    files = sub.add_parser("files")
    files.add_argument("--limit", type=int, default=200)
    tr = sub.add_parser("trivia")
    tr.add_argument("--force", action="store_true", help="also categories done recently")
    sub.add_parser("status")
    bd = sub.add_parser("books", help="admin: typeset review books from the question bank")
    bd.add_argument("--only", help="only books whose title contains this")
    sub.add_parser("anthologies", help="bind new anthologies of open papers (also after each harvest run)")
    sub.add_parser("loop")
    args = parser.parse_args(argv)
    if sys.platform == "win32":  # aiokafka/asyncpg are happiest on the selector loop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    async def with_states(job):
        states = StateStore()
        try:
            return await job(states)
        finally:
            await states.close()

    plan = load_plan()
    if args.command == "run":
        print(asyncio.run(with_states(lambda s: harvest(plan, s, args.source, args.pages))))
    elif args.command == "files":
        print(asyncio.run(store_files(args.limit, plan.max_file_mb, plan.file_concurrency, plan.max_storage_gb)))
    elif args.command == "trivia":
        print(asyncio.run(with_states(lambda s: trivia(plan, s, args.force))))
    elif args.command == "status":
        asyncio.run(status())
    elif args.command == "books":
        print(asyncio.run(books(args.only)))
    elif args.command == "anthologies":
        print(asyncio.run(anthologies()))
    elif args.command == "loop":
        try:
            asyncio.run(loop())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
