"""Crawler expansion: table of contents from the source (auto_toc), book scoping, admin pause and stats.

HTML fixtures are hand-written in the shape of the source sites.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from scrapy.http import HtmlResponse, Request
from sqlalchemy import select

from app.infrastructure.storage import get_bronze_storage, get_storage
from app.models import Category, CrawledQuestion, CrawlerSite, CrawlItemStatus, Question
from app.pipeline.adapters.memory import MemoryDownloader, MemoryRawStore
from app.pipeline.crawl.spider import QuestionSpider
from app.pipeline.domain.models import RawDocument, SiteConfig, TocRules, doc_id
from app.pipeline.use_cases.silver import SilverDeps, ingest_document
from app.pipeline.use_cases.sync_configs import sync_configs
from app.pipeline.use_cases.toc import adopt_series, ensure_path, number_key
from tests.factories import make_tree
from tests.test_pipeline import VJ_PAGE

VJ_LISTING = """<html><body>
<div class="left-menu"><a href="/toan-11-kn/trac-nghiem-bai-1-gia-tri.jsp">Bài 1 (menu)</a></div>
<div class="middle-col"><h2>1000 Bài tập trắc nghiệm Toán 11 Kết nối tri thức (có đáp án)</h2>
<h3><a href="/toan-11-kn/trac-nghiem-chuong-1-ham-so.jsp">Trắc nghiệm Chương 1: Hàm số lượng giác</a></h3>
<p><a href="/toan-11-kn/trac-nghiem-bai-1-gia-tri.jsp">Trắc nghiệm Bài 1: Giá trị lượng giác của góc lượng giác</a></p>
<p><a href="/toan-11-kn/trac-nghiem-bai-2-cong-thuc.jsp">Trắc nghiệm Bài 2: Công thức lượng giác</a></p>
<p><a href="/toan-11-kn/trac-nghiem-bai-tap-cuoi-chuong-1.jsp">Bài tập trắc nghiệm tổng hợp Toán 11 Chương 1</a></p>
<h3><a href="/toan-11-kn/trac-nghiem-chuong-2-day-so.jsp">Trắc nghiệm Chương 2: Dãy số</a></h3>
<p><a href="/toan-11-kn/trac-nghiem-bai-5-day-so.jsp">Trắc nghiệm Bài 5: Dãy số</a></p>
<p><a href="/toan-10-kn/trac-nghiem-bai-1-menh-de.jsp">Trắc nghiệm Bài 1: Mệnh đề (sách khác)</a></p>
</div></body></html>"""

LGH_CHAPTER = """<html><body>
<div class="n-left-menu"><a href="/trac-nghiem-vat-li-10-bai-1-chu-de-5-canh-dieu-co-dap-an-a9.html">Bài 1. Chuyển động tròn</a></div>
<div class="left left-3col"><h1>Bài tập trắc nghiệm Chủ đề 1. Mô tả chuyển động</h1>
<div class="n-form-exercise"><a href="/trac-nghiem-vat-li-10-bai-1-canh-dieu-co-dap-an-a1.html">Bài 1. Tốc độ, độ dịch chuyển và vận tốc Luyện tập và củng cố kiến thức Bài 1</a></div>
<div class="n-form-exercise"><a href="/trac-nghiem-vat-li-10-bai-2-canh-dieu-co-dap-an-a2.html">Bài 2. Đồ thị độ dịch chuyển - thời gian Luyện tập và củng cố</a></div>
</div></body></html>"""


class _Stats:
    def inc_value(self, *args, **kwargs):
        pass


def _spider(config: dict) -> QuestionSpider:
    spider = QuestionSpider(config=config)
    spider.crawler = type("C", (), {"stats": _Stats()})()
    return spider


def _crawl(spider, url, body, context):
    response = HtmlResponse(url=url, body=body.encode(), encoding="utf-8", request=Request(url))
    return [r for r in spider.parse_listing(response, context=context)][1:]


# ---------------------------------------------------------------- pure rules


def test_toc_rules_and_number_keys():
    toc = TocRules()
    assert toc.chapter_of("Trắc nghiệm Chương 1: Hàm số lượng giác") == "Chương 1: Hàm số lượng giác"
    assert toc.chapter_of("Bài tập trắc nghiệm Chủ đề 1. Mô tả chuyển động") == "Chủ đề 1. Mô tả chuyển động"
    assert toc.lesson_of("Bài 2. Đồ thị độ dịch chuyển - thời gian Luyện tập và củng cố") == (
        "Bài 2. Đồ thị độ dịch chuyển - thời gian"
    )
    assert (
        toc.lesson_of("Trắc nghiệm Bài 4. Chuyển động biến đổi - Vật Lí 10 Cánh diều") == "Bài 4. Chuyển động biến đổi"
    )
    assert toc.lesson_of("Bài tập trắc nghiệm tổng hợp Toán 11 Chương 1") is None
    assert number_key("Chương IV. Dòng điện") == number_key("Chương 4: Dòng điện, mạch điện") == ("chương", 4)
    assert number_key("Bài 12: Giao thoa") == ("bài", 12) and number_key("Ôn tập") is None


# ---------------------------------------------------------------- spider context


def test_vietjack_listing_gives_chapter_and_lesson_context_within_the_book():
    config = {
        "site_id": "vietjack",
        "name": "v",
        "base_url": "https://vietjack.com",
        "parser": "vietjack_quiz",
        "auto_toc": True,
        "toc": {"scope": ["//div[contains(@class, 'middle-col')]"]},
        "seeds": [{"url": "https://vietjack.com/toan-11-kn/index.jsp"}],
        "follow": [{"pattern": r"^https://vietjack\.com/toan-1[012]-kn/trac-nghiem-(?!chuong)[a-z0-9-]+\.jsp$"}],
    }
    context = {"root": "Toán 11", "series": "Kết nối tri thức", "prefix": "https://vietjack.com/toan-11-kn/"}
    requests = _crawl(_spider(config), "https://vietjack.com/toan-11-kn/trac-nghiem-toan-11.jsp", VJ_LISTING, context)
    got = [
        (r.url.rsplit("/", 1)[1], r.cb_kwargs["context"]["chapter"], r.cb_kwargs["context"]["lesson"]) for r in requests
    ]
    assert got == [
        (
            "trac-nghiem-bai-1-gia-tri.jsp",
            "Chương 1: Hàm số lượng giác",
            "Bài 1: Giá trị lượng giác của góc lượng giác",
        ),
        ("trac-nghiem-bai-2-cong-thuc.jsp", "Chương 1: Hàm số lượng giác", "Bài 2: Công thức lượng giác"),
        ("trac-nghiem-bai-tap-cuoi-chuong-1.jsp", "Chương 1: Hàm số lượng giác", ""),  # end-of-chapter quiz
        ("trac-nghiem-bai-5-day-so.jsp", "Chương 2: Dãy số", "Bài 5: Dãy số"),
    ]  # the left menu and the other book's link are ignored
    assert requests[0].cb_kwargs["context"]["root"] == "Toán 11"


def test_loigiaihay_chapter_page_uses_h1_and_skips_sidebars():
    config = {
        "site_id": "loigiaihay",
        "name": "l",
        "base_url": "https://loigiaihay.com",
        "parser": "loigiaihay_quiz",
        "auto_toc": True,
        "toc": {"scope": ["//div[contains(@class, 'left-3col')]"]},
        "seeds": [{"url": "https://loigiaihay.com/c1.html"}],
        "follow": [{"pattern": r"^https://loigiaihay\.com/trac-nghiem-vat-li-10-bai-[a-z0-9-]+-a\d+\.html$"}],
    }
    requests = _crawl(
        _spider(config), "https://loigiaihay.com/e1.html", LGH_CHAPTER, {"root": "Vật lí 10", "series": "Cánh diều"}
    )
    assert [(r.cb_kwargs["context"]["chapter"], r.cb_kwargs["context"]["lesson"]) for r in requests] == [
        ("Chủ đề 1. Mô tả chuyển động", "Bài 1. Tốc độ, độ dịch chuyển và vận tốc"),
        ("Chủ đề 1. Mô tả chuyển động", "Bài 2. Đồ thị độ dịch chuyển - thời gian"),
    ]


# ---------------------------------------------------------------- table of contents in the database


def test_ensure_path_reuses_numbered_nodes_and_adopt_series(db):
    async def run(s):
        t = await make_tree(s, {"Vật lí 11": {"Chương 1: Dao động": {"Bài 1: Dao động điều hoà": {}}}})
        moved = await adopt_series(s, "Vật lí 11", "Kết nối tri thức")
        lesson = await ensure_path(
            s, ["Vật lí 11", "Kết nối tri thức", "Chương I. Dao động", "Bài 1. Dao động điều hòa"]
        )
        new = await ensure_path(s, ["Vật lí 11", "Kết nối tri thức", "Chương II. Sóng", "Bài 8. Mô tả sóng"])
        other = await ensure_path(s, ["Vật lí 11", "Cánh diều", "Chủ đề 1. Dao động", "Bài 1. Mô tả dao động"])
        toan = await ensure_path(s, ["Toán 10", "Kết nối tri thức", "Chương 1: Mệnh đề và tập hợp", "Bài 1: Mệnh đề"])
        return moved, t["Bài 1: Dao động điều hoà"].id, lesson, new, other, toan

    moved, old_lesson, lesson, new, other, toan = db(run)
    assert moved == 1 and lesson.id == old_lesson  # old KNTT chapter moved under the series and reused
    assert lesson.path.count("/") == 5 and lesson.depth == 3  # root / series / chapter / lesson
    nodes = {(c.name, c.root_id): c for c in db(lambda s: s.scalars(select(Category)))}
    physics = lesson.root_id
    kntt = nodes[("Kết nối tri thức", physics)]
    assert nodes[("Chương II. Sóng", physics)].parent_id == kntt.id
    assert nodes[("Chương 1: Dao động", physics)].parent_id == kntt.id  # existing name kept
    assert new.name == "Bài 8. Mô tả sóng" and other.id not in (lesson.id, new.id)
    assert db(lambda s: s.get(Category, toan.root_id)).slug == "toan-10"
    names = sorted(c.name for c in db(lambda s: s.scalars(select(Category).where(Category.parent_id.is_(None)))))
    assert names == ["Toán 10", "Vật lí 11"]


def test_auto_toc_labels_crawled_questions(db):
    config = SiteConfig.model_validate(
        {
            "site_id": "vietjack",
            "name": "v",
            "base_url": "https://vietjack.com",
            "parser": "vietjack_quiz",
            "auto_toc": True,
            "seeds": [{"url": "https://vietjack.com/x"}],
            "follow": [{"pattern": "x"}],
        }
    )
    db(lambda s: _add_site(s, config))
    url = "https://vietjack.com/vat-li-10-cd/trac-nghiem-bai-1-toc-do.jsp"
    html = VJ_PAGE.replace('<p><img src="images/hinh-1.png" alt="hình"></p>', "")
    doc = RawDocument(
        doc_id("vietjack", url, "h"),
        "vietjack",
        url,
        "html",
        datetime.now(UTC),
        "h",
        html=html,
        context={
            "root": "Vật lí 10",
            "series": "Cánh diều",
            "chapter": "Chủ đề 1. Mô tả chuyển động",
            "lesson": "Bài 1. Tốc độ, độ dịch chuyển và vận tốc",
        },
    )
    deps = SilverDeps(
        store=MemoryRawStore(),
        storage=get_storage(),
        bronze_storage=get_bronze_storage(),
        downloader=MemoryDownloader(),
    )
    db(lambda s: ingest_document(s, deps, doc))
    statuses = db(lambda s: s.scalars(select(CrawledQuestion.status)))
    assert set(statuses) == {CrawlItemStatus.PUBLISHED}
    lesson = db(lambda s: s.scalar(select(Category).where(Category.name == "Bài 1. Tốc độ, độ dịch chuyển và vận tốc")))
    assert set(db(lambda s: s.scalars(select(Question.category_id)))) == {lesson.id}
    trail = [c.name for c in db(lambda s: s.scalars(select(Category).order_by(Category.depth)))]
    assert trail == [
        "Vật lí 10",
        "Cánh diều",
        "Chủ đề 1. Mô tả chuyển động",
        "Bài 1. Tốc độ, độ dịch chuyển và vận tốc",
    ]
    item = db(lambda s: s.scalar(select(CrawledQuestion).limit(1)))
    assert item.source_context["series"] == "Cánh diều"


# ---------------------------------------------------------------- admin


def test_pause_survives_sync_and_stats_page(admin_client, db, tmp_path):
    yaml_text = (
        "site_id: demo\nname: Demo\nbase_url: https://x.test\nparser: vietjack_quiz\n"
        "seeds: [{url: 'https://x.test/a'}]\nfollow: [{pattern: 'x'}]\n"
    )
    (tmp_path / "demo.yaml").write_text(yaml_text, encoding="utf-8")
    db(lambda s: sync_configs(s, tmp_path))
    admin_client.post("/admin/crawler/demo/toggle")
    assert db(lambda s: s.get(CrawlerSite, "demo")).enabled is False
    (tmp_path / "demo.yaml").write_text(yaml_text.replace("Demo", "Demo 2"), encoding="utf-8")
    db(lambda s: sync_configs(s, tmp_path))
    site = db(lambda s: s.get(CrawlerSite, "demo"))
    assert (site.name, site.enabled) == ("Demo 2", False)  # config updated, pause kept
    page = admin_client.get("/admin/crawler/stats")
    assert page.status_code == 200 and "Chất lượng dữ liệu thu thập" in page.text
    assert "Bật lại lịch" in admin_client.get("/admin/crawler").text


async def _add_site(session, config: SiteConfig):
    session.add(CrawlerSite(site_id=config.site_id, name=config.name, config=config.model_dump(mode="json")))


@pytest.fixture(autouse=True)
def _no_loop_leak():
    yield
    asyncio.set_event_loop(None)


def test_waiting_questions_are_labelled_when_a_recrawl_brings_context(db):
    config = SiteConfig.model_validate(
        {
            "site_id": "vietjack",
            "name": "v",
            "base_url": "https://vietjack.com",
            "parser": "vietjack_quiz",
            "auto_toc": True,
            "seeds": [{"url": "https://vietjack.com/x"}],
            "follow": [{"pattern": "x"}],
        }
    )
    db(lambda s: _add_site(s, config))
    url = "https://vietjack.com/toan-11-kn/trac-nghiem-bai-1-gia-tri.jsp"
    html = VJ_PAGE.replace('<p><img src="images/hinh-1.png" alt="hình"></p>', "")
    store = MemoryRawStore()
    deps = SilverDeps(
        store=store, storage=get_storage(), bronze_storage=get_bronze_storage(), downloader=MemoryDownloader()
    )
    first = RawDocument(doc_id("vietjack", url, "h"), "vietjack", url, "html", datetime.now(UTC), "h", html=html)
    assert asyncio.run(store.save(first))
    db(lambda s: ingest_document(s, deps, first))
    assert set(db(lambda s: s.scalars(select(CrawledQuestion.status)))) == {CrawlItemStatus.NEEDS_REVIEW}

    again = RawDocument(
        first.id,
        "vietjack",
        url,
        "html",
        datetime.now(UTC),
        "h",
        html=html,
        context={
            "root": "Toán 11",
            "series": "Kết nối tri thức",
            "chapter": "Chương 1: Hàm số lượng giác",
            "lesson": "Bài 1: Giá trị lượng giác của góc lượng giác",
        },
    )
    assert asyncio.run(store.save(again))  # same bytes, new context: announced again
    db(lambda s: ingest_document(s, deps, again))
    assert set(db(lambda s: s.scalars(select(CrawledQuestion.status)))) == {CrawlItemStatus.PUBLISHED}
    assert not asyncio.run(store.save(again))  # nothing new the third time


def test_ai_quota_errors_leave_questions_waiting(db):
    from app.pipeline.use_cases.silver import AIUnavailable, ai_sweep

    class QuotaExceeded:
        async def enrich(self, items):
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

    config = SiteConfig.model_validate(
        {
            "site_id": "loigiaihay",
            "name": "l",
            "base_url": "https://loigiaihay.com",
            "parser": "loigiaihay_quiz",
            "seeds": [{"url": "https://loigiaihay.com/x"}],
            "follow": [{"pattern": "x"}],
        }
    )
    db(lambda s: _add_site(s, config))
    from tests.test_pipeline import LGH_PAGE, LGH_URL

    doc = RawDocument(
        doc_id("loigiaihay", LGH_URL, "h"), "loigiaihay", LGH_URL, "html", datetime.now(UTC), "h", html=LGH_PAGE
    )
    deps = SilverDeps(
        store=MemoryRawStore(),
        storage=get_storage(),
        bronze_storage=get_bronze_storage(),
        downloader=MemoryDownloader(),
        enricher=QuotaExceeded(),
    )
    db(lambda s: ingest_document(s, deps, doc))
    for _ in range(4):
        with pytest.raises(AIUnavailable):
            db(lambda s: ai_sweep(s, deps))
    waiting = db(
        lambda s: s.scalars(select(CrawledQuestion).where(CrawledQuestion.status == CrawlItemStatus.PENDING_AI))
    )
    assert waiting and all(i.ai_tries == 0 and not i.issues for i in waiting)


def test_admin_can_run_one_book(admin_client, db):
    from app.models import CrawlRun

    config = SiteConfig.model_validate(
        {
            "site_id": "vietjack",
            "name": "v",
            "base_url": "https://vietjack.com",
            "parser": "vietjack_quiz",
            "seeds": [{"url": "https://vietjack.com/toan-11-kn/index.jsp", "label": "Toán 11 – Kết nối tri thức"}],
            "follow": [{"pattern": "x"}],
        }
    )
    db(lambda s: _add_site(s, config))
    assert "Toán 11 – Kết nối tri thức" in admin_client.get("/admin/crawler").text
    admin_client.post("/admin/crawler/vietjack/run", data={"seed": "Toán 11 – Kết nối tri thức", "max_pages": "20"})
    run = db(lambda s: s.scalar(select(CrawlRun)))
    assert (run.seed_filter, run.max_pages) == ("Toán 11 – Kết nối tri thức", 20)


VJ_TOAN = """<html><body><div class="middle-col"><div class="content">
<p><strong>PHẦN I. TRẮC NGHIỆM NHIỀU LỰA CHỌN</strong></p>
<p><strong>Câu 1. </strong>Chu kì của hàm số y = cos3x là</p>
<p><strong>A. </strong>2π/3.</p><p><strong>B. </strong>π.</p><p><strong>C. </strong>3π.</p><p><strong>D. </strong>2π.</p>
<section class="toggle"><label>Hiển thị đáp án</label><div class="toggle-content"><p><strong>Đáp án đúng là: A</strong></p></div></section>
<p><strong>PHẦN II. TRẮC NGHIỆM ĐÚNG – SAI</strong></p>
<p><strong>Câu hỏi. </strong>Cho hàm số f(x) = tan 2x - 1.</p>
<p><strong>a)</strong> f(π/8) = 0.</p><p><strong>b)</strong> f là hàm số chẵn.</p>
<p><strong>c)</strong> f xác định trên ℝ.</p><p><strong>d)</strong> f tuần hoàn.</p>
<section class="toggle"><label>Hiển thị đáp án</label><div class="toggle-content">
<p>Ta có f(-x) = -tan 2x - 1.</p>
<p><em><strong>Đáp án:</strong></em> <strong>a)</strong> <strong>Đúng</strong>, <strong>b) Sai</strong>, <strong>c) Sai</strong>, <strong>d) Đúng</strong>.</p>
</div></section>
<p><strong>PHẦN III. TRẢ LỜI NGẮN</strong></p>
<p><strong>Câu 1. </strong>Mẫu số liệu có bao nhiêu nhóm?</p>
<section class="toggle"><label>Hiển thị đáp án</label><div class="toggle-content"><p>Đếm các nhóm.</p><p>Trả lời: 5.</p></div></section>
</div></div></body></html>"""


def test_vietjack_toan_layout_with_inline_true_false():
    from app.pipeline.parsers.base import all_parsers

    page = all_parsers()["vietjack_quiz"](VJ_TOAN, "https://vietjack.com/toan-11-kn/trac-nghiem-bai-3-ham-so.jsp")
    mc, tf, sa = page.questions
    assert mc.question_type == "MULTIPLE_CHOICE" and mc.choices[0]["is_correct"] and not mc.issues
    assert tf.question_type == "TRUE_FALSE" and not tf.issues
    assert [c["content"] for c in tf.choices] == [
        "f(π/8) = 0.",
        "f là hàm số chẵn.",
        "f xác định trên ℝ.",
        "f tuần hoàn.",
    ]
    assert [c["is_correct"] for c in tf.choices] == [True, False, False, True]
    assert tf.explanation == "Ta có f(-x) = -tan 2x - 1."
    assert (sa.question_type, sa.accepted_answers, sa.explanation) == ("SHORT_ANSWER", ["5"], "Đếm các nhóm.")


def test_questions_gone_after_a_reparse_are_dropped(db):
    config = SiteConfig.model_validate(
        {
            "site_id": "vietjack",
            "name": "v",
            "base_url": "https://vietjack.com",
            "parser": "vietjack_quiz",
            "seeds": [{"url": "https://vietjack.com/x"}],
            "follow": [{"pattern": "x"}],
        }
    )
    db(lambda s: _add_site(s, config))
    url = "https://vietjack.com/toan-11-kn/trac-nghiem-bai-3-ham-so.jsp"
    deps = SilverDeps(
        store=MemoryRawStore(),
        storage=get_storage(),
        bronze_storage=get_bronze_storage(),
        downloader=MemoryDownloader(),
    )
    old_parse = VJ_TOAN.replace("PHẦN II. TRẮC NGHIỆM ĐÚNG – SAI", "Ghi chú")  # the true/false part read as MC
    for html, digest in ((old_parse, "a"), (VJ_TOAN, "b")):
        doc = RawDocument(
            doc_id("vietjack", url, digest), "vietjack", url, "html", datetime.now(UTC), digest, html=html
        )
        db(lambda s, d=doc: ingest_document(s, deps, d))
    rows = {i.external_id: i for i in db(lambda s: s.scalars(select(CrawledQuestion)))}
    assert "trac-nghiem-bai-3-ham-so#mu2" not in rows  # the old reading is gone, not left "rejected" in the list
    assert rows["trac-nghiem-bai-3-ham-so#tr1"].question_type.value == "TRUE_FALSE"


def test_a_better_parse_that_finds_the_answer_takes_the_question_again(db):
    config = SiteConfig.model_validate(
        {
            "site_id": "vietjack",
            "name": "v",
            "base_url": "https://vietjack.com",
            "parser": "vietjack_quiz",
            "seeds": [{"url": "https://vietjack.com/x"}],
            "follow": [{"pattern": "x"}],
        }
    )
    db(lambda s: _add_site(s, config))
    url = "https://vietjack.com/toan-11-kn/trac-nghiem-bai-3-ham-so.jsp"
    deps = SilverDeps(
        store=MemoryRawStore(),
        storage=get_storage(),
        bronze_storage=get_bronze_storage(),
        downloader=MemoryDownloader(),
    )
    # The first parse missed the answer (same text, same options); a later one finds it.
    no_answer = VJ_TOAN.replace("<strong>Đáp án đúng là: A</strong>", "<strong>Xem lời giải</strong>", 1)
    statuses = []
    for html, digest in ((no_answer, "a"), (VJ_TOAN, "b")):
        doc = RawDocument(
            doc_id("vietjack", url, digest), "vietjack", url, "html", datetime.now(UTC), digest, html=html
        )
        db(lambda s, d=doc: ingest_document(s, deps, d))
        first = db(lambda s: s.scalar(select(CrawledQuestion).where(CrawledQuestion.external_id.endswith("#mu1"))))
        statuses.append((first.status, first.issues))
    assert statuses[0] == (CrawlItemStatus.NEEDS_REVIEW, ["Không tìm thấy đáp án."])
    assert "Không tìm thấy đáp án." not in statuses[1][1]  # taken again: the answer is there now
    first = db(lambda s: s.scalar(select(CrawledQuestion).where(CrawledQuestion.external_id.endswith("#mu1"))))
    assert [c["is_correct"] for c in first.choices][0] is True
