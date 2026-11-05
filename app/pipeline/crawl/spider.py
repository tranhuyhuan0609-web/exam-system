"""One generic Scrapy spider driven by the site's YAML config: seeds -> listing pages -> detail pages / files.

Politeness comes from the config (download delay, one request at a time, robots.txt, AutoThrottle) and from
refresh_days: detail pages and files fetched recently are not requested again.
"""

from urllib.parse import urlparse

import scrapy

from app.pipeline.domain.models import SiteConfig

FILE_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "image/png": "image",
    "image/jpeg": "image",
}


class QuestionSpider(scrapy.Spider):
    name = "questions"

    def __init__(self, config: dict, run_id: int | None = None, skip_urls: list[str] | None = None, **kwargs):
        super().__init__(**kwargs)
        self.site = SiteConfig.model_validate(config)
        self.run_id = run_id
        self.skip_urls = set(skip_urls or [])
        self._skipped: set[str] = set()
        self.allowed_domains = [urlparse(self.site.base_url).hostname]

    async def start(self):
        for seed in self.site.seeds:
            context = {"label": seed.label, "root": seed.root, "series": seed.series, "prefix": seed.scope_prefix}
            yield scrapy.Request(
                seed.url,
                callback=self.parse_listing,
                errback=self.on_error,
                cb_kwargs={"context": context},
                dont_filter=True,
            )

    def _request(self, url: str, context: dict):
        rule = self.site.rule_for(url)
        if rule is None or urlparse(url).hostname not in self.allowed_domains:
            return None
        if rule.kind != "listing" and url in self.skip_urls:
            if url not in self._skipped:  # count each page once, however many listings link to it
                self._skipped.add(url)
                self.crawler.stats.inc_value("pages/skipped_recent")
            return None
        callback = {"listing": self.parse_listing, "detail": self.parse_detail, "file": self.parse_file}[rule.kind]
        return scrapy.Request(url, callback=callback, errback=self.on_error, cb_kwargs={"context": context})

    def _follow(self, response, context: dict):
        """Walk headings and links in page order: a chapter heading (or the page's own h1) sets the chapter of
        the links below it, a link's text names its lesson. The context travels with each request."""
        toc = self.site.toc
        chapter = context.get("chapter")
        h1 = " ".join(" ".join(response.css("h1 ::text").getall()).split())
        chapter = toc.chapter_of(h1) or chapter
        area = next((found for xp in toc.scope if (found := response.xpath(xp))), None) or response.xpath("//body")
        for node in area.xpath(".//*[self::h1 or self::h2 or self::h3 or self::h4 or self::a]"):
            tag = node.root.tag
            text = " ".join(" ".join(node.xpath(".//text()").getall()).split())
            if tag != "a":
                chapter = toc.chapter_of(text) or chapter
                continue
            href = node.attrib.get("href")
            if not href:
                continue
            url = response.urljoin(href).split("#")[0]
            link_chapter = toc.chapter_of(text)
            child = {
                **context,
                "chapter": link_chapter or chapter or "",
                "lesson": "" if link_chapter else (toc.lesson_of(text) or ""),
            }
            rule = self.site.rule_for(url)
            if context.get("prefix") and not url.startswith(context["prefix"]):
                continue  # another book linked from this one
            if rule and rule.kind == "listing":
                child["chapter"], child["lesson"] = link_chapter or "", ""  # a listing names its own chapters
            if self.site.auto_toc and rule and rule.kind == "detail" and not child["chapter"]:
                continue  # reached again from its chapter page, with the right context
            request = self._request(url, child)
            if request is not None:
                yield request

    def _page(self, response, role: str, context: dict) -> dict:
        return {
            "kind": "html",
            "role": role,
            "url": response.url,
            "body": response.text,
            "title": " ".join((response.css("title::text").get() or "").split()),
            "label": context.get("label", ""),
            "context": {k: v for k, v in context.items() if k not in ("label", "prefix") and v},
        }

    def parse_listing(self, response, context: dict | None = None):
        context = context or {}
        yield self._page(response, "listing", context)  # Bronze keeps listings too; Silver ignores them
        yield from self._follow(response, context)

    def parse_detail(self, response, context: dict | None = None):
        # No link following from lesson pages: their sidebars link other lessons without chapter context.
        yield self._page(response, "detail", context or {})

    def parse_file(self, response, context: dict | None = None):
        context = context or {}
        content_type = response.headers.get("Content-Type", b"").decode().split(";")[0].strip()
        kind = FILE_TYPES.get(content_type)
        if kind is None:
            self.logger.warning("Skipping %s (%s)", response.url, content_type)
            return
        yield {
            "kind": kind,
            "role": "file",
            "url": response.url,
            "body": response.body,
            "content_type": content_type,
            "title": response.url.rsplit("/", 1)[-1],
            "label": context.get("label", ""),
            "context": {k: v for k, v in context.items() if k not in ("label", "prefix") and v},
        }

    def on_error(self, failure):
        request = failure.request
        self.crawler.stats.inc_value("pages/failed")
        yield {"kind": "error", "url": request.url, "error": repr(failure.value)[:2000]}


def scrapy_settings(site: SiteConfig, user_agent: str, max_pages: int | None) -> dict:
    limit = site.rate_limit
    return {
        "BOT_NAME": "exam_crawler",
        "USER_AGENT": user_agent,
        "ROBOTSTXT_OBEY": site.respect_robots,
        "DOWNLOAD_DELAY": limit.delay_sec,
        "RANDOMIZE_DOWNLOAD_DELAY": True,
        "CONCURRENT_REQUESTS": limit.concurrency,
        "CONCURRENT_REQUESTS_PER_DOMAIN": limit.concurrency,
        "DOWNLOAD_TIMEOUT": limit.timeout_sec,
        "RETRY_TIMES": limit.retries,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": limit.delay_sec,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1.0,
        "CLOSESPIDER_PAGECOUNT": max_pages or site.max_pages,
        # Breadth-first, in page order: lessons are fetched in the order the table of contents lists them.
        "DEPTH_PRIORITY": 1,
        "SCHEDULER_DISK_QUEUE": "scrapy.squeues.PickleFifoDiskQueue",
        "SCHEDULER_MEMORY_QUEUE": "scrapy.squeues.FifoMemoryQueue",
        "COOKIES_ENABLED": False,
        "TELNETCONSOLE_ENABLED": False,
        "LOG_LEVEL": "INFO",
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        "ITEM_PIPELINES": {"app.pipeline.crawl.bronze.BronzePipeline": 100},
        "REQUEST_FINGERPRINTER_IMPLEMENTATION": "2.7",
        "DOWNLOAD_MAXSIZE": 30 * 1024 * 1024,
    }
