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
            yield scrapy.Request(
                seed.url,
                callback=self.parse_listing,
                errback=self.on_error,
                cb_kwargs={"label": seed.label},
                dont_filter=True,
            )

    def _follow(self, response, label: str):
        for href in response.css("a::attr(href)").getall():
            url = response.urljoin(href).split("#")[0]
            if urlparse(url).hostname not in self.allowed_domains:
                continue
            rule = self.site.rule_for(url)
            if rule is None:
                continue
            if rule.kind != "listing" and url in self.skip_urls:
                if url not in self._skipped:  # count each page once, however many listings link to it
                    self._skipped.add(url)
                    self.crawler.stats.inc_value("pages/skipped_recent")
                continue
            callback = {"listing": self.parse_listing, "detail": self.parse_detail, "file": self.parse_file}[rule.kind]
            yield scrapy.Request(url, callback=callback, errback=self.on_error, cb_kwargs={"label": label})

    def _page(self, response, role: str, label: str) -> dict:
        return {
            "kind": "html",
            "role": role,
            "url": response.url,
            "body": response.text,
            "title": " ".join((response.css("title::text").get() or "").split()),
            "label": label,
        }

    def parse_listing(self, response, label: str = ""):
        yield self._page(response, "listing", label)  # Bronze keeps listings too; Silver ignores them
        yield from self._follow(response, label)

    def parse_detail(self, response, label: str = ""):
        yield self._page(response, "detail", label)
        yield from self._follow(response, label)

    def parse_file(self, response, label: str = ""):
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
            "label": label,
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
