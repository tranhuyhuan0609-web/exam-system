"""Value objects that flow through the crawler: site configs (YAML), Bronze documents, parsed questions."""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

DocKind = Literal["html", "pdf", "docx", "image"]


# ---------------------------------------------------------------- site config (configs/sites/<site_id>.yaml)


class FollowRule(BaseModel):
    pattern: str  # regex on the absolute URL
    kind: Literal["listing", "detail", "file"] = "detail"

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        re.compile(value)
        return value


class Seed(BaseModel):
    url: str
    label: str = ""  # human-readable source area, e.g. "Vật lí 11 – Kết nối tri thức"
    # Where this seed's lessons go in our table of contents (auto_toc): subject root and textbook series.
    root: str = ""  # e.g. "Vật lí 11"
    series: str = ""  # e.g. "Kết nối tri thức"; empty = chapters directly under the root
    scope_prefix: str = ""  # only follow URLs starting with this (one book's folder); empty = whole site


class TocRules(BaseModel):
    """How listing pages name chapters and lessons, for building our table of contents from the source."""

    chapter: str = r"^(Chương|Chủ đề|Phần)\s*[IVXLC\d]+"  # heading / link text of a chapter
    lesson: str = r"^Bài\s*\d+"  # link text of a lesson
    strip: str = (
        r"^(Bài tập\s*)?[Tt]rắc nghiệm\s*|\s*Luyện tập và củng cố.*$|\s*[-–]\s*(Vật Lí|Vật lí|Toán)\s*1[012].*$"
        r"|\s*\(có đáp án\).*$"
    )
    # Where the table of contents lives on listing pages (first XPath that matches; else the whole body).
    # Menus and sidebars outside it would attach lessons to the wrong chapter.
    scope: list[str] = []

    @field_validator("chapter", "lesson", "strip")
    @classmethod
    def _compiles(cls, value: str) -> str:
        re.compile(value)
        return value

    def clean(self, text: str) -> str:
        return " ".join(re.sub(self.strip, "", " ".join(text.split()), flags=re.I).split())

    def chapter_of(self, text: str) -> str | None:
        text = self.clean(text)
        return text if re.match(self.chapter, text, re.I) else None

    def lesson_of(self, text: str) -> str | None:
        text = self.clean(text)
        return text if re.match(self.lesson, text, re.I) else None


class RateLimit(BaseModel):
    delay_sec: float = Field(2.5, ge=0.5)  # politeness: never faster than 2 requests per second
    timeout_sec: float = 20
    retries: int = 2
    concurrency: int = Field(1, ge=1, le=4)


class CategoryRule(BaseModel):
    """Automatic labelling: a source page matching `pattern` goes under `root` (subject path in our tree);
    `lesson` (regex with one group, e.g. 'bài (\\d+)') picks the child whose name starts with 'Bài <n>'."""

    pattern: str
    root: str  # category path in our table of contents, e.g. "Vật lí 11"
    lesson: str | None = None


class SiteConfig(BaseModel):
    site_id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    enabled: bool = True
    base_url: str
    parser: str  # registered parser for detail pages (app/pipeline/parsers)
    renderer: Literal["http", "playwright"] = "http"  # playwright: JS-rendered sources (not used yet)
    schedule_times: list[str] = []  # "HH:MM" Vietnam time; empty = manual runs only
    refresh_days: int = 30  # pages fetched more recently than this are skipped
    max_pages: int = 300  # per run (listing + detail + files)
    respect_robots: bool = True
    auto_publish: bool = True  # questions that pass dedup and labelling go straight to the bank
    default_difficulty: str = "UNDERSTANDING"
    rate_limit: RateLimit = RateLimit()
    seeds: list[Seed]
    follow: list[FollowRule]
    categories: list[CategoryRule] = []
    # Build missing chapters / lessons in our table of contents from the source's listing pages.
    auto_toc: bool = False
    toc: TocRules = TocRules()

    def rule_for(self, url: str) -> FollowRule | None:
        return next((r for r in self.follow if re.search(r.pattern, url)), None)


# ---------------------------------------------------------------- Bronze


def doc_id(site_id: str, url: str, content_hash: str) -> str:
    """Bronze document id: same URL and same bytes = same document (re-crawls of unchanged pages are no-ops)."""
    return f"{site_id}:{hashlib.sha1(url.encode()).hexdigest()[:16]}:{content_hash[:16]}"


@dataclass
class RawDocument:
    id: str
    site_id: str
    url: str
    kind: DocKind
    fetched_at: datetime
    content_hash: str
    run_id: int | None = None
    html: str | None = None  # html documents
    storage_key: str | None = None  # files: key in the bronze-raw bucket
    title: str = ""
    seed_label: str = ""
    # Where the page sits in the source's table of contents: {"root", "series", "chapter", "lesson"}.
    context: dict[str, str] = field(default_factory=dict)

    def event(self) -> dict[str, Any]:
        """Kafka message on raw.exam.ingestion: the id only, the payload stays in MongoDB."""
        return {"doc_id": self.id, "site_id": self.site_id, "kind": self.kind, "url": self.url, "run_id": self.run_id}


# ---------------------------------------------------------------- parsed (input of Silver)


@dataclass
class ParsedQuestion:
    external_id: str  # stable id on the source site (question id, or page url + number)
    question_type: str  # MULTIPLE_CHOICE | TRUE_FALSE | SHORT_ANSWER
    content: str
    choices: list[dict[str, Any]] = field(default_factory=list)  # {"id", "content", "is_correct"}
    accepted_answers: list[str] = field(default_factory=list)
    explanation: str = ""
    images: list[str] = field(default_factory=list)  # absolute URLs, referenced as [[img:N]] in the texts
    issues: list[str] = field(default_factory=list)  # parser warnings shown in review

    @property
    def has_answer(self) -> bool:
        if self.question_type == "SHORT_ANSWER":
            return bool(self.accepted_answers)
        return any(c.get("is_correct") for c in self.choices)


@dataclass
class ParsedPage:
    title: str
    questions: list[ParsedQuestion]
    issues: list[str] = field(default_factory=list)
