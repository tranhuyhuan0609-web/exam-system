"""Crawler pipeline: MathML/HTML conversion, site parsers, Bronze writer, Silver/Gold with dedup and labelling.

HTML fixtures below are hand-written in the shape of the source sites (no copied content). Kafka and MongoDB
are replaced by the in-memory adapters; PostgreSQL (with pgvector) is real.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from scrapy.http import HtmlResponse, Request
from sqlalchemy import func, select

from app.infrastructure.storage import get_bronze_storage, get_storage
from app.models import (
    CrawlCategoryMap,
    CrawledQuestion,
    CrawlerSite,
    CrawlItemStatus,
    Difficulty,
    Question,
    QuestionType,
    SourceType,
)
from app.pipeline.adapters.memory import EchoEnricher, HashEmbedder, MemoryBus, MemoryDownloader, MemoryRawStore
from app.pipeline.crawl.bronze import BronzePipeline
from app.pipeline.crawl.spider import QuestionSpider
from app.pipeline.domain.html_content import to_content
from app.pipeline.domain.mathml import mathml_to_latex
from app.pipeline.domain.models import RawDocument, SiteConfig, doc_id
from app.pipeline.parsers.base import all_parsers
from app.pipeline.ports import DLQ_TOPIC, RAW_TOPIC
from app.pipeline.use_cases.silver import SilverDeps, ai_sweep, has_plain_math, ingest_document, same_question
from app.pipeline.use_cases.sync_configs import load_configs, sync_configs
from app.pipeline.workers import _due
from tests.factories import make_tree

ROOT = Path(__file__).resolve().parents[1]

LGH_PAGE = """<html><head><title>Trắc nghiệm vật lí 11 bài 1 kết nối tri thức có đáp án</title></head><body>
<div id="sub-question-1"><p>Đề bài</p></div>
<div id="sub-question-2"><p id="question-title-idx-2"><strong>Lời giải và đáp án</strong></p>
 <div class="question c1"><div class="question-content"><div class="question-content-inner">
   <strong class="question-index-number"><a href="/bai-tap-900001.html">Câu 1</a> : </strong>
   <p>Chu kì của con lắc phụ thuộc vào</p></div></div>
  <ul class="dapan rows-4">
   <li><span class="span-answer">A.</span><div> khối lượng.</div></li>
   <li><span class="span-answer">B.</span><div> chiều dài dây.</div></li>
   <li><span class="span-answer">C.</span><div> biên độ.</div></li>
   <li><span class="span-answer">D.</span><div> pha ban đầu.</div></li></ul>
  <div class="loigiai"><p><b>Đáp án : B</b></p>
   <div class="solution-suggesstion"><div class="solution-suggesstion-title">Phương pháp giải : </div>
     <div><p>Dùng công thức chu kì.</p></div></div>
   <div class="solution-detail"><div class="solution-detail-title">Lời giải chi tiết : </div>
     <div><p>Chu kì chỉ phụ thuộc chiều dài và gia tốc trọng trường.</p></div></div></div></div>
 <div class="question c2"><div class="question-content"><div class="question-content-inner">
   <strong class="question-index-number"><a href="/bai-tap-900002.html">Câu 2</a> : </strong>
   <p>Một vật dao động với biên độ 4 cm. Quãng đường trong một chu kì là</p></div></div>
  <ul class="dapan rows-4">
   <li><span class="span-answer">A.</span><div>4 cm</div></li><li><span class="span-answer">B.</span><div>8 cm</div></li>
   <li><span class="span-answer">C.</span><div>12 cm</div></li><li><span class="span-answer">D.</span><div>16 cm</div></li></ul>
  <div class="loigiai"><p><b>Đáp án : D</b></p>
   <div class="solution-detail"><div class="solution-detail-title">Lời giải chi tiết : </div><div><p>Đáp án: D</p></div></div>
  </div></div>
</div></body></html>"""

VJ_PAGE = """<html><head><title>Trắc nghiệm Vật Lí 11 Kết nối tri thức Bài 1 (có đáp án): Dao động</title></head>
<body><div class="middle-col"><div class="content">
<p><strong>Phần I. Trắc nghiệm nhiều lựa chọn</strong></p>
<p><strong>Câu 1: </strong>Pha của dao động <math><mfrac><mi>π</mi><mn>2</mn></mfrac></math> rad nghĩa là</p>
<div class="ads_ads ads_1"><script>ads()</script></div>
<p>A. vật ở biên.</p><p>B. vật ở vị trí cân bằng.</p><p>C. vật dừng lại.</p><p>D. vật đổi chiều.</p>
<section class="toggle"><label>Hiển thị đáp án</label><div class="toggle-content">
<p><strong>Đáp án đúng là B.</strong></p><p>Khi pha bằng π/2 thì li độ bằng 0.</p></div></section>
<p><strong>Câu 2:</strong> Đơn vị của tần số góc là</p>
<p>A. rad/s           B. Hz       C. s         D. m</p>
<section class="toggle"><label>Hiển thị đáp án</label><div class="toggle-content">
<p><strong>Đáp án đúng là A.</strong></p></div></section>
<p><strong>Phần II. Câu trắc nghiệm đúng sai</strong></p>
<p><strong>Câu hỏi:</strong> Một vật dao động theo phương trình x = 5cos(2πt) cm.</p>
<table><tr><td></td><td>Phát biểu</td><td>Đúng</td><td>Sai</td></tr>
<tr><td>a</td><td>Biên độ là 5 cm</td><td></td><td></td></tr><tr><td>b</td><td>Chu kì là 2 s</td><td></td><td></td></tr>
<tr><td>c</td><td>Tần số là 1 Hz</td><td></td><td></td></tr><tr><td>d</td><td>Pha ban đầu là π</td><td></td><td></td></tr></table>
<section class="toggle"><label>Hiển thị đáp án</label><div class="toggle-content">
<table><tr><td></td><td>Phát biểu</td><td>Đúng</td><td>Sai</td></tr>
<tr><td>a</td><td>Biên độ là 5 cm</td><td>Đ</td><td></td></tr><tr><td>b</td><td>Chu kì là 2 s</td><td></td><td>S</td></tr>
<tr><td>c</td><td>Tần số là 1 Hz</td><td>Đ</td><td></td></tr><tr><td>d</td><td>Pha ban đầu là π</td><td></td><td>S</td></tr></table>
<p>T = 2π/ω = 1 s.</p></div></section>
<p><strong>Phần III. Câu trắc nghiệm trả lời ngắn</strong></p>
<p><strong>Câu 1:</strong> Tính chu kì (s) của vật có ω = 4π rad/s.</p>
<p><img src="images/hinh-1.png" alt="hình"></p>
<section class="toggle"><label>Hiển thị đáp án</label><div class="toggle-content">
<p>T = 2π/ω</p><p><strong>Đáp án: 0,5</strong></p></div></section>
</div></div></body></html>"""

LGH_URL = "https://loigiaihay.com/trac-nghiem-vat-li-11-bai-1-ket-noi-tri-thuc-co-dap-an-a1.html"
VJ_URL = "https://vietjack.com/vat-li-11-kn/trac-nghiem-bai-1-dao-dong.jsp"


# ---------------------------------------------------------------- pure domain


def test_mathml_to_latex():
    assert mathml_to_latex("<math><mfrac><mi>π</mi><mn>2</mn></mfrac></math>") == r"\dfrac{\pi }{2}"
    assert mathml_to_latex("<math><msub><mi>v</mi><mi>max</mi></msub><mo>=</mo><mi>ω</mi><mi>A</mi></math>") == (
        r"v_{\max}=\omega A"
    )
    assert mathml_to_latex("<math><msqrt><mn>2</mn></msqrt><mo>≤</mo><mn>0,5</mn></math>") == r"\sqrt{2}\le 0{,}5"
    assert mathml_to_latex("<math><mover><mi>F</mi><mo>→</mo></mover></math>") == r"\vec{F}"
    assert mathml_to_latex("<math><msup><mi>s</mi><mn>2</mn></msup><mtext>cm</mtext></math>") == r"s^2\text{cm}"


def test_html_to_content_handles_scripts_images_and_ads():
    content = to_content(
        '<p>a<sup>2</sup> + b<sub>1</sub> <math><mi>π</mi></math></p><div class="ads_x">QC</div>'
        '<p><img src="/x.png"></p><script>bad()</script>',
        "https://site.test/page.html",
    )
    assert content.text == "a$^{2}$ + b$_{1}$ $\\pi$\n[[img:0]]"
    assert content.images[0].src == "https://site.test/x.png"
    assert has_plain_math("x = 4cos(3πt)") and not has_plain_math("Tính $x = 4\\cos(3\\pi t)$ theo cm")


def test_loigiaihay_parser():
    page = all_parsers()["loigiaihay_quiz"](LGH_PAGE, LGH_URL)
    assert [q.external_id for q in page.questions] == ["900001", "900002"]
    q1, q2 = page.questions
    assert q1.content == "Chu kì của con lắc phụ thuộc vào"
    assert [c["content"] for c in q1.choices] == ["khối lượng.", "chiều dài dây.", "biên độ.", "pha ban đầu."]
    assert [c["is_correct"] for c in q1.choices] == [False, True, False, False]
    assert q1.explanation == (
        "Phương pháp giải: Dùng công thức chu kì.\nLời giải chi tiết: Chu kì chỉ phụ thuộc chiều dài và gia tốc trọng trường."
    )
    assert q2.choices[3]["is_correct"] and q2.explanation == "" and not q2.issues  # "Đáp án: D" alone is dropped


def test_vietjack_parser_reads_all_three_parts():
    page = all_parsers()["vietjack_quiz"](VJ_PAGE, VJ_URL)
    mc1, mc2, tf, sa = page.questions
    assert (mc1.question_type, mc1.content) == ("MULTIPLE_CHOICE", r"Pha của dao động $\dfrac{\pi }{2}$ rad nghĩa là")
    assert [c["is_correct"] for c in mc1.choices] == [False, True, False, False]
    assert mc1.explanation == "Khi pha bằng π/2 thì li độ bằng 0."
    assert [c["content"] for c in mc2.choices] == ["rad/s", "Hz", "s", "m"] and mc2.choices[0]["is_correct"]
    assert tf.question_type == "TRUE_FALSE" and [c["is_correct"] for c in tf.choices] == [True, False, True, False]
    assert [c["content"] for c in tf.choices][1] == "Chu kì là 2 s" and tf.explanation == "T = 2π/ω = 1 s."
    assert (sa.question_type, sa.accepted_answers, sa.explanation) == ("SHORT_ANSWER", ["0,5"], "T = 2π/ω")
    assert sa.images == ["https://vietjack.com/vat-li-11-kn/images/hinh-1.png"] and "[[img:0]]" in sa.content
    assert len({q.external_id for q in page.questions}) == 4 and not any(q.issues for q in page.questions)


def test_site_configs_in_repo_are_valid_and_sync_is_idempotent(db):
    configs = load_configs(ROOT / "configs" / "sites")
    assert {c.site_id for c in configs} >= {"vietjack", "loigiaihay"}
    for c in configs:
        assert c.rate_limit.delay_sec >= 2 and c.respect_robots and c.parser in all_parsers()
    assert set(db(lambda s: sync_configs(s, ROOT / "configs" / "sites"))) >= {"vietjack", "loigiaihay"}
    assert db(lambda s: sync_configs(s, ROOT / "configs" / "sites")) == []


def test_schedule_slots():
    config = SiteConfig.model_validate({**_site("x", "loigiaihay_quiz"), "schedule_times": ["02:30"]})
    vn = datetime(2026, 11, 5, 2, 40, tzinfo=UTC) - timedelta(hours=7)  # 02:40 Vietnam time
    assert _due(config, None, vn)
    assert not _due(config, vn - timedelta(minutes=5), vn)  # already ran after 02:30
    assert not _due(config, None, vn - timedelta(hours=1))  # 01:40: not yet


# ---------------------------------------------------------------- Bronze (Scrapy side)


def test_spider_follows_rules_and_skips_recent_pages():
    config = _site("loigiaihay", "loigiaihay_quiz")
    spider = QuestionSpider(
        config=config, run_id=1, skip_urls=["https://loigiaihay.com/trac-nghiem-vat-li-11-bai-2-x-a2.html"]
    )
    spider.crawler = type("C", (), {"stats": _Stats()})()
    body = (
        '<a href="/trac-nghiem-vat-li-11-bai-1-x-a1.html">1</a><a href="/trac-nghiem-vat-li-11-bai-2-x-a2.html">2</a>'
        '<a href="/khac.html">x</a><a href="https://evil.test/trac-nghiem-vat-li-11-bai-3-x-a3.html">3</a>'
        '<a href="/bai-tap-trac-nghiem-chuong-ii-song-e2.html">ch</a>'
    )
    response = HtmlResponse(
        url="https://loigiaihay.com/c1.html",
        body=body.encode(),
        encoding="utf-8",
        request=Request("https://loigiaihay.com/c1.html"),
    )
    results = list(spider.parse_listing(response, context={"label": "L"}))
    page, requests = results[0], results[1:]
    assert page["role"] == "listing"
    assert [r.url for r in requests] == [
        "https://loigiaihay.com/trac-nghiem-vat-li-11-bai-1-x-a1.html",
        "https://loigiaihay.com/bai-tap-trac-nghiem-chuong-ii-song-e2.html",
    ]
    assert spider.crawler.stats.values["pages/skipped_recent"] == 1


def test_bronze_pipeline_stores_raw_html_and_publishes_ids_only():
    store, bus = MemoryRawStore(), MemoryBus()
    pipeline = BronzePipeline(store=store, bus=bus, storage=get_bronze_storage())
    spider = type(
        "S",
        (),
        {
            "site": SiteConfig.model_validate(_site("loigiaihay", "loigiaihay_quiz")),
            "run_id": 7,
            "crawler": type("C", (), {"stats": _Stats()})(),
        },
    )()

    def item(role, url, body="<html>x</html>"):
        return {"kind": "html", "role": role, "url": url, "body": body, "title": "T", "label": "L"}

    async def run():
        await pipeline.process_item(item("listing", "https://loigiaihay.com/c.html"), spider)
        await pipeline.process_item(item("detail", LGH_URL), spider)
        await pipeline.process_item(item("detail", LGH_URL), spider)  # unchanged re-crawl
        await pipeline.process_item(item("detail", LGH_URL, "<html>changed</html>"), spider)
        await pipeline.process_item({"kind": "error", "url": "https://loigiaihay.com/x", "error": "timeout"}, spider)
        await pipeline.process_item(
            {
                "kind": "pdf",
                "role": "file",
                "url": "https://loigiaihay.com/de.pdf",
                "body": b"%PDF-1",
                "content_type": "application/pdf",
                "title": "de.pdf",
                "label": "",
            },
            spider,
        )

    asyncio.run(run())
    assert len(store.docs) == 4  # listing, 2 versions of the detail page, the pdf
    raw = [m for t, m in bus.messages if t == RAW_TOPIC]
    assert [m["kind"] for m in raw] == ["html", "html", "pdf"] and all(
        set(m) == {"doc_id", "site_id", "kind", "url", "run_id"} for m in raw
    )
    assert [m["stage"] for t, m in bus.messages if t == DLQ_TOPIC] == ["fetch"]
    pdf = next(d for d in store.docs.values() if d.kind == "pdf")
    assert asyncio.run(get_bronze_storage().get(pdf.storage_key)) == b"%PDF-1"


# ---------------------------------------------------------------- Silver / Gold


@pytest.fixture
def physics(db):
    async def _make(s):
        t = await make_tree(
            s, {"Vật lí 11": {"Chương 1: Dao động": {"Bài 1: Dao động điều hoà": {}, "Bài 2: Mô tả": {}}}}
        )
        for site_id, parser in (("loigiaihay", "loigiaihay_quiz"), ("vietjack", "vietjack_quiz")):
            config = SiteConfig.model_validate(_site(site_id, parser)).model_dump(mode="json")
            s.add(CrawlerSite(site_id=site_id, name=site_id, config=config))
        return {name: c.id for name, c in t.items()}

    return db(_make)


def _deps(store, enricher=None, embedder=None, files=None, threshold=0.92):
    return SilverDeps(
        store=store,
        storage=get_storage(),
        bronze_storage=get_bronze_storage(),
        downloader=MemoryDownloader(files or {}),
        enricher=enricher,
        embedder=embedder,
        threshold=threshold,
    )


def _doc(site_id, url, html):
    digest = str(abs(hash(html)))
    return RawDocument(
        doc_id(site_id, url, digest), site_id, url, "html", datetime.now(UTC), digest, html=html, title=""
    )


def test_html_document_goes_to_the_bank_with_label_dedup_and_rehosted_images(db, physics):
    store = MemoryRawStore()
    deps = _deps(store, embedder=HashEmbedder(), files={"https://vietjack.com/vat-li-11-kn/images/hinh-1.png": b"PNG"})
    doc = _doc("vietjack", VJ_URL, VJ_PAGE)
    asyncio.run(store.save(doc))
    db(lambda s: ingest_document(s, deps, doc))

    items = db(lambda s: s.scalars(select(CrawledQuestion).order_by(CrawledQuestion.id)))
    assert [i.status for i in items] == [CrawlItemStatus.PUBLISHED] * 4  # no AI configured: published as parsed
    questions = db(lambda s: s.scalars(select(Question).order_by(Question.id)))
    assert {q.category_id for q in questions} == {physics["Bài 1: Dao động điều hoà"]}  # "Bài 1" in the title
    assert all(q.source_type == SourceType.CRAWLER and q.source_url == VJ_URL and q.is_verified for q in questions)
    sa = next(q for q in questions if q.question_type == QuestionType.SHORT_ANSWER)
    assert sa.image_path.startswith("figures/crawl/") and "[[img" not in sa.content
    assert asyncio.run(get_storage().get(sa.image_path)) == b"PNG"  # re-hosted: no dead links
    assert (
        db(lambda s: s.scalar(select(func.count()).select_from(Question).where(Question.embedding.is_not(None)))) == 4
    )
    assert (
        db(lambda s: s.get(CrawlCategoryMap, ("vietjack", VJ_URL))).category_id == physics["Bài 1: Dao động điều hoà"]
    )

    # Same document again: nothing new. The same page on another site: exact / semantic duplicates.
    db(lambda s: ingest_document(s, deps, doc))
    assert db(lambda s: s.scalar(select(func.count()).select_from(Question))) == 4
    copy = _doc("loigiaihay", LGH_URL, VJ_PAGE.replace("Câu 2:", "Câu 2 :"))
    site = SiteConfig.model_validate(_site("loigiaihay", "vietjack_quiz"))  # parse the copy with the vietjack parser
    db(lambda s: _set_config(s, "loigiaihay", site))
    db(lambda s: ingest_document(s, deps, copy))
    dupes = db(
        lambda s: s.scalars(
            select(CrawledQuestion.status).where(CrawledQuestion.site_id == "loigiaihay").order_by(CrawledQuestion.id)
        )
    )
    # The short-answer copy points at an image on the other host that cannot be fetched: it waits for review.
    assert dupes == [CrawlItemStatus.DUPLICATE] * 3 + [CrawlItemStatus.NEEDS_REVIEW]


def test_semantic_duplicates_are_skipped(db, physics):
    class SameVector:
        async def embed(self, texts):
            return [[1.0] + [0.0] * 767 for _ in texts]

    async def seed(s):
        s.add(
            Question(
                root_id=physics["Vật lí 11"],
                category_id=physics["Vật lí 11"],
                difficulty=Difficulty.RECOGNITION,
                content="Chu kì của con lắc đơn phụ thuộc vào",  # reworded copy of crawled question 1
                choices=[
                    {"id": "a", "content": "Khối lượng", "is_correct": False},
                    {"id": "b", "content": "Chiều dài dây", "is_correct": True},
                ],
                embedding=[1.0] + [0.0] * 767,
            )
        )

    db(seed)
    store = MemoryRawStore()
    doc = _doc("loigiaihay", LGH_URL, LGH_PAGE)
    # Every embedding is identical (cosine 1.0): only the candidate with the same stem and answer is a duplicate.
    db(lambda s: ingest_document(s, _deps(store, embedder=SameVector()), doc))
    items = db(lambda s: s.scalars(select(CrawledQuestion).order_by(CrawledQuestion.id)))
    assert [i.status for i in items] == [CrawlItemStatus.SIMILAR, CrawlItemStatus.PUBLISHED]
    assert all(float(i.similarity) == 1.0 for i in items)
    assert db(lambda s: s.scalar(select(func.count()).select_from(Question))) == 2


def test_semantic_candidates_need_the_same_answer():
    def q(content, correct):
        return Question(
            question_type=QuestionType.MULTIPLE_CHOICE,
            content=content,
            choices=[
                {"id": "a", "content": correct, "is_correct": True},
                {"id": "b", "content": "khác", "is_correct": False},
            ],
        )

    base = q("Cho vật dao động điều hòa. Li độ đạt giá trị cực tiểu khi vật qua vị trí", "biên âm")
    assert not same_question(
        q("Cho vật dao động điều hòa. Gia tốc đạt giá trị cực tiểu khi vật qua vị trí", "biên dương"), base
    )
    assert same_question(
        q("Cho vật dao động điều hoà. Li độ đạt giá trị cực tiểu khi vật qua vị trí:", "Biên âm."), base
    )
    # Only the numbers change: another exercise, even with the same wording and the same answer text.
    proton = q("Một nguyên tử có 40 proton. Hạt nào có số lượng bằng số proton?", "electron")
    assert not same_question(q("Một nguyên tử có 19 proton. Hạt nào có số lượng bằng số proton?", "electron"), proton)
    fraction = q(r"Tính $\dfrac{3}{5}+\dfrac{1}{5}$", "đáp số khác 1")
    assert not same_question(q(r"Tính $\dfrac{3}{5}+\dfrac{2}{5}$", "đáp số khác 1"), fraction)
    assert same_question(q("Một nguyên tử có 40 proton . Hạt nào có số lượng bằng số proton", "Electron."), proton)
    # The same numbers written differently, or a number word written as a digit: still the same question.
    power = q("Nhà máy có công suất $P = 6.10^{5}$ kW, hiệu suất 20%, hoạt động trong 1 năm", "4616 kg")
    assert same_question(
        q("Nhà máy có công suất $P = 6{,}10^{5}$ kW, hiệu suất 20%, hoạt động trong một năm", "4616 kg"), power
    )


def test_ai_normalises_and_writes_missing_solutions(db, physics):
    store, enricher = MemoryRawStore(), EchoEnricher()
    deps = _deps(store, enricher=enricher)
    doc = _doc("loigiaihay", LGH_URL, LGH_PAGE)
    db(lambda s: ingest_document(s, deps, doc))
    statuses = db(lambda s: s.scalars(select(CrawledQuestion.status).order_by(CrawledQuestion.id)))
    # Question 1 has a solution and no plain-text formula: straight to the bank. Question 2 lacks a solution.
    assert statuses == [CrawlItemStatus.PUBLISHED, CrawlItemStatus.PENDING_AI]
    assert db(lambda s: ai_sweep(s, deps)) >= 1
    item = db(lambda s: s.scalar(select(CrawledQuestion).where(CrawledQuestion.external_id == "900002")))
    assert item.status == CrawlItemStatus.PUBLISHED and item.explanation_by_ai
    assert item.explanation == f"Lời giải AI cho câu {item.id}." and item.difficulty == Difficulty.APPLICATION
    assert db(lambda s: s.get(Question, item.question_id)).explanation == item.explanation


def test_unlabelled_pages_wait_for_an_admin(admin_client, db, physics):
    store = MemoryRawStore()
    html = LGH_PAGE.replace("bài 1 kết nối", "bài 7 kết nối")  # our tree has no "Bài 7"
    url = LGH_URL.replace("bai-1", "bai-7")
    db(lambda s: ingest_document(s, _deps(store), _doc("loigiaihay", url, html)))
    assert set(db(lambda s: s.scalars(select(CrawledQuestion.status)))) == {CrawlItemStatus.NEEDS_REVIEW}
    page = admin_client.get("/admin/crawler/items?status=NEEDS_REVIEW").text
    assert "Chưa gắn được chương/bài." in page and "Chu kì của con lắc" in page
    target = physics["Bài 2: Mô tả"]
    admin_client.post(
        "/admin/crawler/map", data={"site_id": "loigiaihay", "source_url": url, "category_id": str(target)}
    )
    assert set(db(lambda s: s.scalars(select(CrawledQuestion.status)))) == {CrawlItemStatus.PUBLISHED}
    assert set(db(lambda s: s.scalars(select(Question.category_id)))) == {target}
    assert db(lambda s: s.get(CrawlCategoryMap, ("loigiaihay", url))).by_admin
    assert "Đã vào ngân hàng: 2" in admin_client.get("/admin/crawler").text


def test_admin_queues_a_run_and_rejects_items(admin_client, db, physics):
    admin_client.post("/admin/crawler/loigiaihay/run", data={"max_pages": "5"})
    admin_client.post("/admin/crawler/loigiaihay/run", data={"max_pages": "5"})  # already queued: refused
    from app.models import CrawlRun

    runs = db(lambda s: s.scalars(select(CrawlRun)))
    assert len(runs) == 1 and runs[0].max_pages == 5 and runs[0].trigger == "manual"
    store = MemoryRawStore()
    db(
        lambda s: ingest_document(
            s, _deps(store), _doc("loigiaihay", LGH_URL.replace("bai-1", "bai-9"), LGH_PAGE.replace("bài 1", "bài 9"))
        )
    )
    ids = [str(i) for i in db(lambda s: s.scalars(select(CrawledQuestion.id)))]
    admin_client.post("/admin/crawler/items/action", data={"action": "reject", "ids": ids})
    assert set(db(lambda s: s.scalars(select(CrawledQuestion.status)))) == {CrawlItemStatus.REJECTED}


# ---------------------------------------------------------------- helpers


class _Stats:
    def __init__(self):
        self.values: dict[str, int] = {}

    def inc_value(self, key, count=1):
        self.values[key] = self.values.get(key, 0) + count


def _site(site_id: str, parser: str) -> dict:
    host = {"loigiaihay": "https://loigiaihay.com", "vietjack": "https://vietjack.com"}.get(site_id, "https://x.test")
    return {
        "site_id": site_id,
        "name": site_id,
        "base_url": host,
        "parser": parser,
        "seeds": [{"url": host + "/c1.html"}],
        "follow": [
            {"pattern": r"/bai-tap-trac-nghiem-chuong-[a-z-]+-e\d+\.html$", "kind": "listing"},
            {"pattern": r"/trac-nghiem-vat-li-11-bai-\d+-[a-z-]+-a\d+\.html$", "kind": "detail"},
        ],
        "categories": [{"pattern": "trac-nghiem", "root": "Vật lí 11", "lesson": r"bài\s*(\d+)"}],
    }


async def _set_config(session, site_id, config: SiteConfig):
    site = await session.get(CrawlerSite, site_id)
    site.config = config.model_dump(mode="json")
