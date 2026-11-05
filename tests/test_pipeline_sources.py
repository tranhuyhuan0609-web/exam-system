"""hoc247 and tuyensinh247: parsers, id-based link extraction, answer verification, LaTeX alt text.

HTML fixtures are hand-written in the shape of the source sites (no copied content).
"""

from datetime import UTC, datetime

from scrapy.http import HtmlResponse, Request
from sqlalchemy import select

from app.infrastructure.storage import get_bronze_storage, get_storage
from app.models import CrawledQuestion, CrawlerSite, CrawlItemStatus, Difficulty
from app.pipeline.adapters.memory import MemoryDownloader, MemoryRawStore
from app.pipeline.crawl.spider import QuestionSpider
from app.pipeline.domain.models import RawDocument, SiteConfig, doc_id
from app.pipeline.parsers.base import all_parsers
from app.pipeline.ports import EnrichResult
from app.pipeline.use_cases.silver import ANSWER_DOUBT, SilverDeps, ai_sweep, ingest_document
from tests.factories import make_tree

HOC_QUESTION = r"""<html><body><div id="cauhoi"><ul class="dsch"><li class="lch">
<strong>Câu hỏi:</strong> <!--<p>old</p>--> <p>Góc có số đo \(150^\circ \) đổi sang radian là?</p>
<ul class="339 dstl" id="dstl900"><li><input type="radio"><span class="cautl">A.</span> <span>\(\frac{5\pi }{6}\)</span></li>
<li><input type="radio"><span class="cautl">B.</span> <span>\(\frac{2\pi }{3}\)</span></li>
<li><input type="radio"><span class="cautl">C.</span> <span>\(\frac{3\pi }{4}\)</span></li>
<li><input type="radio"><span class="cautl">D.</span> <span>\(\frac{\pi }{2}\)</span></li></ul>
<div class="_traloi displaynone" id="answer_900"><p><b>Lời giải tham khảo:</b></p>
<p><strong>Đáp án đúng: A</strong></p><div class="loigiai"><p>Ta có \(150^\circ = \frac{5\pi}{6}\).</p><p><strong>Đáp án </strong><strong>A</strong></p></div></div>
</li></ul>
<ul class="dsch"><li class="lch"><p>Câu liên quan khác</p><ul class="dstl" id="dstl901"><li><span>X</span></li></ul></li></ul>
</div></body></html>"""

TS_QUESTION = """<html><body><div class="extra-question-item">
<div class="extra-question-title"><div class="extra-question-number">Câu hỏi số 555:</div>
<span class="level-2 level-question">Thông hiểu</span></div>
<div class="extra-question-body"><div class="question-desc"><p>Năng lượng của phản ứng
<img src="http://img.example/555_1.gif" alt="_{1}^{2}\\textrm{D} + _{1}^{2}\\textrm{D}\\rightarrow _{2}^{3}\\textrm{He} + n"> là</p></div>
<div class="mab5 right-anwser"><label class="checkcontainer"><strong> A.</strong> 3,26MeV <input type="radio"><span class="radiobtn"></span></label></div>
<div class="mab5"><label class="checkcontainer"><strong> B.</strong> 0,25MeV <input type="radio"></label></div>
<div class="mab5"><label class="checkcontainer"><strong> C.</strong> 0,32MeV <input type="radio"></label></div>
<div class="mab5"><label class="checkcontainer"><strong> D.</strong> 1,55MeV <input type="radio"></label></div>
<div class="answer-result"><p><span>Đáp án đúng là: A</span></p></div></div>
<div class="extra-question-guide" id="qg_555">
<div class="extra-question-answer-vip-main"><div class="extra-title-info-box"><span>Phương pháp giải</span></div>
<div class="extra-answer-vip-detail"><p>Dùng năng lượng liên kết.</p></div></div>
<div class="extra-question-answer-vip-main"><div class="extra-title-info-box"><span>Giải chi tiết</span></div>
<div class="extra-answer-vip-detail"><p>W<sub>tỏa</sub> = 7,62 – 2.2,18 = 3,26 MeV</p></div></div></div>
</div></body></html>"""

TS_TOPIC = """<html><body><h1>Luyện chuyên đề Vật lý hạt nhân</h1>
<div><input onclick="checkOption(555,1);"><input onclick="checkOption(555,1);"><input onclick="checkOption(556,1);"></div>
<a href="/luyen-chuyen-de-vat-ly-hat-nhan-tq6-page2.html?tab_id=0&q_level=0">2</a>
<a href="/luyen-chuyen-de-song-anh-sang-tq1-page2.html">khác</a></body></html>"""


def test_hoc247_question_page():
    page = all_parsers()["hoc247_question"](HOC_QUESTION, "https://hoc247.net/cau-hoi-goc-co-so-do-qid900.html")
    (q,) = page.questions
    assert (q.external_id, q.content) == ("900", r"Góc có số đo \(150^\circ \) đổi sang radian là?")
    assert [c["content"] for c in q.choices][0] == r"\(\frac{5\pi }{6}\)" and q.choices[0]["is_correct"]
    assert q.explanation == r"Ta có \(150^\circ = \frac{5\pi}{6}\)." and not q.issues


def test_tuyensinh247_question_page_with_level_and_latex_alt():
    page = all_parsers()["tuyensinh247_question"](TS_QUESTION, "https://tuyensinh247.com/bai-tap-555.html")
    (q,) = page.questions
    assert (
        q.content
        == r"Năng lượng của phản ứng $_{1}^{2}\textrm{D} + _{1}^{2}\textrm{D}\rightarrow _{2}^{3}\textrm{He} + n$ là"
    )
    assert [c["content"] for c in q.choices] == ["3,26MeV", "0,25MeV", "0,32MeV", "1,55MeV"] and q.choices[0][
        "is_correct"
    ]
    assert q.difficulty == "UNDERSTANDING" and q.images == []  # the formula image became LaTeX: no download, no AI
    assert (
        q.explanation
        == "Phương pháp giải: Dùng năng lượng liên kết.\nGiải chi tiết: W$_{\\text{tỏa}}$ = 7,62 – 2.2,18 = 3,26 MeV"
    )


def test_topic_page_yields_question_urls_from_ids_and_its_own_pages():
    config = {
        "site_id": "tuyensinh247",
        "name": "t",
        "base_url": "https://tuyensinh247.com",
        "parser": "tuyensinh247_question",
        "auto_toc": True,
        "seeds": [{"url": "https://tuyensinh247.com/x"}],
        "follow": [
            {
                "pattern": r"^https://tuyensinh247\.com/luyen-chuyen-de-[a-z0-9-]+-tq\d+-page\d+\.html$",
                "kind": "listing",
                "strip_query": True,
            },
            {
                "pattern": r"^https://tuyensinh247\.com/bai-tap-\d+\.html$",
                "extract": r"checkOption\((\d+),",
                "url": "https://tuyensinh247.com/bai-tap-{id}.html",
            },
        ],
    }
    spider = QuestionSpider(config=config)
    spider.crawler = type("C", (), {"stats": type("S", (), {"inc_value": lambda *a, **k: None})()})()
    url = "https://tuyensinh247.com/luyen-chuyen-de-vat-ly-hat-nhan-tq6.html"
    response = HtmlResponse(url=url, body=TS_TOPIC.encode(), encoding="utf-8", request=Request(url))
    context = {
        "root": "Ôn thi THPT – Vật lí",
        "seed_chapter": "Vật lý hạt nhân",
        "chapter": "Vật lý hạt nhân",
        "prefix": "https://tuyensinh247.com/luyen-chuyen-de-vat-ly-hat-nhan-tq6-",
    }
    requests = list(spider.parse_listing(response, context=context))[1:]
    assert (
        [r.url for r in requests]
        == [
            "https://tuyensinh247.com/luyen-chuyen-de-vat-ly-hat-nhan-tq6-page2.html",  # filters stripped, other topic ignored
            "https://tuyensinh247.com/bai-tap-555.html",
            "https://tuyensinh247.com/bai-tap-556.html",
        ]
    )
    assert all(r.cb_kwargs["context"]["chapter"] == "Vật lý hạt nhân" for r in requests)


def test_verify_answers_sends_doubtful_keys_to_review(db):
    class Checker:
        async def enrich(self, items):
            return [
                EnrichResult(
                    i.ref, i.content, i.choices, "Đáp án đúng là B.", False, "RECOGNITION", "groq", answer_ok=False
                )
                for i in items
            ]

    async def setup(s):
        await make_tree(s, {"Toán 11": {}})
        config = SiteConfig.model_validate(
            {
                "site_id": "hoc247",
                "name": "h",
                "base_url": "https://hoc247.net",
                "parser": "hoc247_question",
                "auto_toc": True,
                "verify_answers": True,
                "seeds": [{"url": "https://hoc247.net/x"}],
                "follow": [{"pattern": "x"}],
            }
        )
        s.add(CrawlerSite(site_id="hoc247", name="h", config=config.model_dump(mode="json")))

    db(setup)
    url = "https://hoc247.net/cau-hoi-goc-co-so-do-qid900.html"
    doc = RawDocument(
        doc_id("hoc247", url, "h"),
        "hoc247",
        url,
        "html",
        datetime.now(UTC),
        "h",
        html=HOC_QUESTION,
        context={"root": "Toán 11", "chapter": "Đề thi giữa học kì 1"},
    )
    deps = SilverDeps(
        store=MemoryRawStore(),
        storage=get_storage(),
        bronze_storage=get_bronze_storage(),
        downloader=MemoryDownloader(),
        enricher=Checker(),
    )
    db(lambda s: ingest_document(s, deps, doc))
    item = db(lambda s: s.scalar(select(CrawledQuestion)))
    assert item.status == CrawlItemStatus.PENDING_AI  # it has a solution, but this site's keys get checked
    assert db(lambda s: ai_sweep(s, deps)) == 1
    item = db(lambda s: s.scalar(select(CrawledQuestion)))
    assert item.status == CrawlItemStatus.NEEDS_REVIEW and ANSWER_DOUBT in item.issues
    assert item.ai_provider == "groq" and item.difficulty == Difficulty.RECOGNITION
