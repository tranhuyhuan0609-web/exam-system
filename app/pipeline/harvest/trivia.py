"""Open Trivia DB: every question of each category, 50 at a time, into the question bank through Bronze -> Silver.

A session token makes the API return each question once; "token empty" (code 4) means the category is done.
Each batch becomes a Bronze document of the `opentrivia` site, parsed by app/pipeline/parsers/opentrivia.py.
"""

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta

from app.pipeline.domain.models import RawDocument, doc_id
from app.pipeline.harvest.sources import Http
from app.pipeline.harvest.store import StateStore, TriviaPlan
from app.pipeline.ports import RAW_TOPIC

log = logging.getLogger(__name__)
API = "https://opentdb.com"
SITE = "opentrivia"


async def harvest_trivia(plan: TriviaPlan, raw_store, bus, states: StateStore, force: bool = False) -> dict[str, int]:
    stats = {"batches": 0, "questions": 0}
    http = Http(5.5)  # the API allows one request every 5 seconds
    try:
        token = (await http.get_json(f"{API}/api_token.php", {"command": "request"}))["token"]
        for category, name in plan.categories.items():
            key = f"{SITE}:{category}"
            state = await states.get(key)
            fresh = state.get("updated_at") and state["updated_at"] > datetime.now(UTC) - timedelta(plan.refresh_days)
            if state.get("done") and fresh and not force:
                continue
            count, amount = 0, 50
            while True:
                data = await http.get_json(f"{API}/api.php", {"amount": amount, "category": category, "token": token})
                code = data.get("response_code")
                if code == 1 and amount > 1:  # fewer questions left than asked for
                    amount = 10 if amount > 10 else 1
                    continue
                if code == 5:  # too fast
                    await asyncio.sleep(6)
                    continue
                if code == 3:  # token expired (6 hours idle)
                    token = (await http.get_json(f"{API}/api_token.php", {"command": "request"}))["token"]
                    continue
                if code != 0 or not data.get("results"):
                    break
                body = json.dumps(data, ensure_ascii=False)
                digest = hashlib.sha256(body.encode()).hexdigest()
                url = f"{API}/api.php?category={category}#{digest[:16]}"  # one Bronze page per batch
                doc = RawDocument(
                    id=doc_id(SITE, url, digest),
                    site_id=SITE,
                    url=url,
                    kind="html",
                    fetched_at=datetime.now(UTC),
                    content_hash=digest,
                    html=body,
                    title=f"Open Trivia – {name}",
                    seed_label=f"Open Trivia – {name}",
                    context={"root": plan.root, "chapter": name},
                )
                if await raw_store.save(doc):
                    await bus.publish(RAW_TOPIC, doc.event(), key=doc.id)
                count += len(data["results"])
                stats["batches"] += 1
            stats["questions"] += count
            await states.put(key, done=True, records=state.get("records", 0) + count)
            log.info("Open Trivia %s (%s): %s questions", category, name, count)
    finally:
        await http.close()
    return stats
