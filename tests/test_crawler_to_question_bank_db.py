"""POST /api/v1/crawler/sources/{id}/fetch-and-import: one permitted page -> solved, tagged DRAFTs in the bank.

Offline: the DNS resolver, the web (an httpx MockTransport) and the AI (a scripted Gemini) are injected through the
route's dependencies. What is checked: every crawl gate holds before a byte is fetched (SSRF on the DNS answer,
allowlist, https only, robots.txt), the page's questions are extracted (HTML and Open Trivia JSON), solved where the
page gives no key, deduplicated on a second fetch, and the fetch is recorded for the drift monitor.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app
from app.modules.crawler import router as crawler_router
from app.modules.crawler.internal import fetch_import
from app.modules.crawler.internal.politeness import DomainPolitenessLimiter
from app.ops.seed_demo import seed_demo
from tests.ai_fakes import fake_solver, no_ai_settings
from tests.test_question_bank_api_db import _login, _make_tenant_with_author

HOST = "de.example.edu.vn"
PAGE = f"https://{HOST}/de-thi/toan-12.html"
HTML = """<html><head><script>var x = "Câu 99. not a question";</script><style>p{}</style></head><body>
<nav>Trang chủ</nav>
<h1>Đề kiểm tra Toán 12</h1>
<p>Câu 1. Đạo hàm của hàm số y = x^3 - 3x là</p>
<p>A. 3x^2 - 3</p><p>B. 3x^2 + 3</p><p>C. x^2 - 3</p><p>D. 3x</p>
<p>Câu 2: Giá trị log2(8) bằng</p>
<p>A. 2 B. 3 C. 4 D. 8</p>
<p>Đáp án: B</p>
<p>Câu 3. Trình bày cách tìm cực trị của hàm số bậc ba.</p>
<footer>Bản quyền</footer>
</body></html>"""
TRIVIA = {
    "response_code": 0,
    "results": [
        {"type": "multiple", "question": "What is 2 &amp; 2 added?", "correct_answer": "4",
         "incorrect_answers": ["3", "5", "22"]},
        {"type": "boolean", "question": "Is 7 a prime number?", "correct_answer": "True",
         "incorrect_answers": ["False"]},
    ],
}  # fmt: skip


class Web:
    """The internet of the test: pages by URL, robots.txt by host, and every request it was asked for."""

    def __init__(self) -> None:
        self.pages: dict[str, httpx.Response] = {}
        self.robots: dict[str, str | None] = {}
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if request.url.path == "/robots.txt":
            body = self.robots.get(request.url.host)
            return httpx.Response(404) if body is None else httpx.Response(200, text=body)
        return self.pages.get(url, httpx.Response(404))


@pytest.fixture
def world(client, monkeypatch):
    seeded = asyncio.run(seed_demo())
    web = Web()
    web.pages[PAGE] = httpx.Response(200, text=HTML, headers={"content-type": "text/html; charset=utf-8"})
    dns = {HOST: ["93.184.216.34"], "opentdb.com": ["104.21.0.10"]}
    solver, gemini = fake_solver(
        {
            "Đạo hàm của hàm số": {"answer": "A", "solution_steps": "y' = 3x^2 - 3", "difficulty_level": "NB"},
            "cực trị": {"answer": "1 điểm: tính y'; 1 điểm: xét dấu y'", "difficulty_level": "TH"},
        }
    )
    state = {"solver": solver}
    app.dependency_overrides[crawler_router.get_crawl_network] = lambda: crawler_router.CrawlNetwork(
        resolve_host=lambda host: dns.get(host, []), transport=httpx.MockTransport(web)
    )
    app.dependency_overrides[crawler_router.get_crawl_solver] = lambda: state["solver"]
    monkeypatch.setattr(fetch_import, "_POLITENESS", DomainPolitenessLimiter(min_interval_sec=0))
    author = _login(client, "content_author@h2edu.demo")
    source = client.post(
        "/api/v1/crawler/sources",
        headers=author,
        json={"name": "Đề thi mở", "base_uri": f"https://{HOST}/de-thi/", "allowed_domains": [HOST, "opentdb.com"],
              "allowed_path_prefixes": ["/de-thi", "/api.php"], "rights_status": "LICENSED"},
    )  # fmt: skip
    assert source.status_code == 201, source.text
    yield {
        "tenant_id": uuid.UUID(seeded["tenant"]["id"]),
        "author": author,
        "teacher": _login(client, "teacher@h2edu.demo"),
        "learner": _login(client, "learner@h2edu.demo"),
        "source_id": source.json()["id"],
        "web": web,
        "dns": dns,
        "gemini": gemini,
        "state": state,
    }
    app.dependency_overrides.pop(crawler_router.get_crawl_network, None)
    app.dependency_overrides.pop(crawler_router.get_crawl_solver, None)


def _fetch(client, w, url=PAGE, headers=None, **body):
    return client.post(
        f"/api/v1/crawler/sources/{w['source_id']}/fetch-and-import",
        headers=headers or w["author"],
        json={"url": url, **body},
    )


async def _rows(sql: str, **params):
    async with SessionLocal() as session:
        return (await session.execute(text(sql), params)).mappings().all()


def _bank(w):
    return asyncio.run(
        _rows(
            "SELECT q.id, q.item_type, q.publication_status, q.rights_status, q.difficulty_level, "
            "       q.primary_taxonomy_node_id, v.stem_json->>'text' AS stem, a.answer_schema, a.rationale_json "
            "FROM questions q JOIN question_versions v ON v.question_id = q.id "
            "JOIN question_version_answers a ON a.question_version_id = v.id "
            "WHERE q.tenant_id = :t AND q.stable_code LIKE 'CRAWL-%' ORDER BY q.stable_code",
            t=w["tenant_id"],
        )
    )


def test_a_page_becomes_solved_tagged_drafts_and_a_second_fetch_adds_nothing(client, world):
    first = _fetch(client, world)
    assert first.status_code == 200, first.text
    body = first.json()
    assert (body["extracted_count"], body["created_count"], body["duplicate_count"]) == (3, 3, 0), body
    assert body["skipped"] == [] and body["drift_detected"] is False and body["source_policy_status"] == "ACTIVE"

    rows = {r["stem"][:12]: r for r in _bank(world)}
    derivative, log, essay = rows["Đạo hàm của "], rows["Giá trị log2"], rows["Trình bày cá"]
    assert all(r["publication_status"] == "DRAFT" and r["rights_status"] == "LICENSED" for r in rows.values())
    # the page printed the key of question 2; the AI solved question 1 and wrote the rubric of question 3
    assert log["answer_schema"] == {"correct_option": "B"} and log["rationale_json"]["answer_source"] == "EXISTING"
    assert derivative["answer_schema"] == {"correct_option": "A"} and derivative["rationale_json"]["ai_solved"] is True
    assert derivative["rationale_json"]["solution_steps"] == "y' = 3x^2 - 3" and derivative["difficulty_level"] == "NB"
    assert essay["item_type"] == "ESSAY" and essay["answer_schema"]["rubric"].startswith("1 điểm")
    assert derivative["rationale_json"]["provenance"]["url"] == PAGE
    # nothing from the <script> block became a question
    assert not any("99" in stem for stem in rows)

    again = _fetch(client, world)
    assert again.status_code == 200 and again.json()["created_count"] == 0, again.text
    assert again.json()["duplicate_count"] == 3 and len(_bank(world)) == 3, "dedup on the content hash"
    fetches = asyncio.run(_rows("SELECT http_status FROM crawl_fetches WHERE tenant_id = :t", t=world["tenant_id"]))
    assert [f["http_status"] for f in fetches] == [200, 200]


def test_open_trivia_json_is_read_with_a_stable_option_order(client, world):
    url = "https://opentdb.com/api.php?amount=2"
    world["web"].pages[url] = httpx.Response(200, json=TRIVIA)
    done = _fetch(client, world, url)
    assert done.status_code == 200 and done.json()["created_count"] == 2, done.text
    rows = {r["stem"]: r for r in _bank(world)}
    sums = rows["What is 2 & 2 added?"]
    assert sums["answer_schema"] == {"correct_option": "C"}, "options sorted: 22, 3, 4, 5 -> 4 is C"
    assert rows["Is 7 a prime number?"]["answer_schema"] == {"correct_option": "A"}


def test_no_request_leaves_for_a_private_address_a_foreign_domain_plain_http_or_a_robots_refusal(client, world):
    web = world["web"]
    world["dns"][HOST] = ["10.0.0.5"]  # a DNS answer pointing inside
    blocked = _fetch(client, world)
    assert blocked.status_code == 403 and blocked.json()["policy_code"] == "SSRF_BLOCKED", blocked.text
    assert web.requests == [], "not even robots.txt was asked for"

    world["dns"][HOST] = ["93.184.216.34"]
    for url in ("https://evil.example.com/de-thi/x.html", f"http://{HOST}/de-thi/toan-12.html"):
        refused = _fetch(client, world, url)
        assert refused.status_code == 403, (url, refused.text)
    assert web.requests == []

    web.robots[HOST] = "User-agent: *\nDisallow: /de-thi/"
    robots = _fetch(client, world)
    assert robots.status_code == 403 and robots.json()["policy_code"] == "ROBOTS_TXT_DISALLOWED", robots.text
    assert web.requests == [f"https://{HOST}/robots.txt"], "robots.txt was read, the page was not"
    assert _bank(world) == []


def test_an_upstream_error_or_a_redirect_imports_nothing_and_answers_502(client, world):
    world["web"].pages[PAGE] = httpx.Response(500, text="oops")
    failed = _fetch(client, world)
    assert failed.status_code == 502 and failed.json()["http_status"] == 500, failed.text
    recorded = asyncio.run(_rows("SELECT http_status FROM crawl_fetches WHERE tenant_id = :t", t=world["tenant_id"]))
    assert [r["http_status"] for r in recorded] == [500], "the failed fetch is recorded for the drift monitor"

    world["web"].pages[PAGE] = httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})
    moved = _fetch(client, world)
    assert moved.status_code == 502 and moved.json()["policy_code"] == "UPSTREAM_FETCH_FAILED", moved.text
    assert "169.254.169.254" not in " ".join(world["web"].requests), "a redirect is never followed"
    assert _bank(world) == []


def test_without_an_ai_a_question_the_page_gives_no_key_for_is_skipped_with_its_reason(client, world):
    world["state"]["solver"], _ = fake_solver(settings=no_ai_settings())
    done = _fetch(client, world)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["created_count"] == 1, "only the question whose key the page printed"
    reasons = {s["label"]: s["reason"] for s in body["skipped"]}
    assert set(reasons) == {"Câu 1", "Câu 3"} and all(r.startswith("NO_ANSWER") for r in reasons.values())


def test_only_authors_and_administrators_crawl_and_a_source_is_per_tenant(client, world):
    for who in ("teacher", "learner"):
        assert _fetch(client, world, headers=world[who]).status_code == 403, who
    assert (
        client.post(f"/api/v1/crawler/sources/{world['source_id']}/fetch-and-import", json={"url": PAGE}).status_code
        == 401
    )
    asyncio.run(_make_tenant_with_author("other-school"))
    other = _login(client, "author@other-school.test", "other-school")
    assert _fetch(client, world, headers=other).status_code == 404, "another school cannot use our source"
    assert world["web"].requests == []
    bad = client.post(
        f"/api/v1/crawler/sources/{world['source_id']}/fetch-and-import",
        headers=world["author"],
        data=json.dumps({"url": PAGE, "max_questions": 0}),
    )
    assert bad.status_code == 422


def test_no_database_transaction_is_open_while_the_web_or_the_ai_answers(client, world, monkeypatch):
    from tests.ai_fakes import watch_transactions

    watch = watch_transactions(monkeypatch, fetch_import)
    world["gemini"].watch = watch
    original = world["web"].__call__

    def probing(request):
        watch.probe()
        return original(request)

    monkeypatch.setattr(Web, "__call__", lambda self, request: probing(request))
    done = _fetch(client, world)
    assert done.status_code == 200 and done.json()["created_count"] == 3, done.text
    assert world["gemini"].calls and world["web"].requests, "the AI and the web were really used"
    assert watch.io_inside_transaction == 0, "no HTTP call went out while a transaction was open"
    assert watch.opened == 2, "one short transaction before the network, one after"
