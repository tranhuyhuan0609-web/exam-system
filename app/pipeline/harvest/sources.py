"""Open sources: one page of records per call, and where the next page starts (a cursor, a page number).

Every client returns `(records, next_state)`; `next_state` is None once the query has no more results.
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings
from app.models import LibraryKind

log = logging.getLogger(__name__)

USER_AGENT = "ExamSystemCrawler/0.1 (educational project; open-access metadata)"


def env(name: str) -> str:
    """An optional key from .env (OPENALEX_API_KEY …) without declaring it in Settings."""
    extra = get_settings().model_extra or {}
    return os.environ.get(name) or str(extra.get(name.lower(), "") or "")


@dataclass
class OpenRecord:
    key: str  # the same work from two sources has one key (its DOI)
    source: str
    title: str
    kind: LibraryKind
    folder: list[str]
    url: str  # where to read it at the source
    file_url: str = ""  # an open PDF to keep in our storage ("ia:<identifier>" for the Internet Archive)
    authors: str = ""
    year: int | None = None
    language: str = ""
    license: str = ""


class Http:
    """One polite client per source: a minimum interval between calls, backoff on 429 and 5xx."""

    def __init__(self, min_interval: float, headers: dict[str, str] | None = None, retries: int = 4):
        self.min_interval, self.retries, self._last = min_interval, retries, 0.0
        self.client = httpx.AsyncClient(
            timeout=60, follow_redirects=True, headers={"User-Agent": USER_AGENT, **(headers or {})}
        )

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        delay = max(self.min_interval, 2.0)
        for attempt in range(self.retries + 1):
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
            try:
                response = await self.client.get(url, params=params)
            except httpx.TransportError as exc:
                if attempt == self.retries:
                    raise
                log.warning("%s: %s, retrying", url, exc.__class__.__name__)
            else:
                if response.status_code != 429 and response.status_code < 500:
                    response.raise_for_status()
                    return response.json()
                if attempt == self.retries:
                    response.raise_for_status()
                retry_after = response.headers.get("retry-after", "")
                delay = float(retry_after) if retry_after.isdigit() else delay
            await asyncio.sleep(min(delay, 120))
            delay *= 2

    async def close(self) -> None:
        await self.client.aclose()


def _doi_key(doi: str | None) -> str:
    doi = (doi or "").strip().lower().removeprefix("https://doi.org/").removeprefix("http://dx.doi.org/")
    return f"doi:{doi}" if doi else ""


def _authors(names: list[str], most: int = 5) -> str:
    names = [n for n in names if n]
    return ", ".join(names[:most]) + (" và cộng sự" if len(names) > most else "")


def _key(value: str) -> str:
    return value if len(value) <= 200 else value[:150] + ":" + hashlib.sha1(value.encode()).hexdigest()[:40]


# ---------------------------------------------------------------- papers

# OpenAlex fields (primary_topic.field.id) in Vietnamese: the folder of a paper when a query covers every field.
FIELDS_VI = {
    11: "Nông nghiệp & Sinh học",
    12: "Nghệ thuật & Nhân văn",
    13: "Hoá sinh & Di truyền",
    14: "Kinh doanh & Quản trị",
    15: "Kỹ thuật hoá học",
    16: "Hoá học",
    17: "Khoa học máy tính",
    18: "Khoa học quyết định",
    19: "Khoa học Trái Đất",
    20: "Kinh tế học",
    21: "Năng lượng",
    22: "Kỹ thuật",
    23: "Khoa học môi trường",
    24: "Miễn dịch & Vi sinh",
    25: "Khoa học vật liệu",
    26: "Toán học",
    27: "Y học",
    28: "Thần kinh học",
    29: "Điều dưỡng",
    30: "Dược học",
    31: "Vật lí & Thiên văn",
    32: "Tâm lí học",
    33: "Khoa học xã hội & Giáo dục",
    34: "Thú y",
    35: "Nha khoa",
    36: "Khoa học sức khoẻ",
}


OPENALEX_PAGES_WITHOUT_KEY = 50  # 10,000 results per query: paging further needs a cursor, and a cursor a key


async def openalex(http: Http, item, state: str | None) -> tuple[list[OpenRecord], str | None]:
    """State: "p:<n>" (page number, without a key) or a cursor (with OPENALEX_API_KEY)."""
    params = {
        "filter": item.filter or "is_oa:true,has_content.pdf:true",
        "sort": item.sort or "cited_by_count:desc",
        "per-page": 200,
        "select": "id,doi,title,publication_year,language,type,best_oa_location,open_access,authorships,primary_topic",
    }
    if item.search:
        params["search"] = item.search
        params.pop("sort")  # relevance first when searching
    key = env("OPENALEX_API_KEY")
    page = 0
    if key and not (state or "").startswith("p:"):
        params["api_key"], params["cursor"] = key, state or "*"
    else:
        if key:
            params["api_key"] = key
        # a cursor saved before (first page read without a key): go on from page 2
        page = int(state[2:]) if (state or "").startswith("p:") else (2 if state else 1)
        params["page"] = page
    data = await http.get_json("https://api.openalex.org/works", params)
    records = []
    for work in data.get("results", []):
        title = " ".join((work.get("title") or "").split())
        if not title:
            continue
        location = work.get("best_oa_location") or {}
        pdf = location.get("pdf_url") or ""
        field = ((work.get("primary_topic") or {}).get("field") or {}).get("id", "")
        folder = list(item.folder)
        if item.by_field and field:
            folder.append(FIELDS_VI.get(int(str(field).rsplit("/", 1)[-1]), "Lĩnh vực khác"))
        openalex_id = str(work.get("id", "")).rsplit("/", 1)[-1]
        records.append(
            OpenRecord(
                key=_key(_doi_key(work.get("doi")) or f"openalex:{openalex_id}"),
                source="openalex",
                title=title,
                kind=LibraryKind.BOOK if work.get("type") == "book" else LibraryKind.PAPER,
                folder=folder,
                url=location.get("landing_page_url") or work.get("doi") or str(work.get("id", "")),
                file_url=pdf,
                authors=_authors(
                    [(a.get("author") or {}).get("display_name", "") for a in work.get("authorships", [])]
                ),
                year=work.get("publication_year"),
                language=work.get("language") or "",
                license=location.get("license") or "",
            )
        )
    if not data.get("results"):
        return records, None
    if page:
        return records, f"p:{page + 1}" if page < OPENALEX_PAGES_WITHOUT_KEY else None
    cursor = (data.get("meta") or {}).get("next_cursor")
    return records, cursor or None


async def semanticscholar(http: Http, item, state: str | None) -> tuple[list[OpenRecord], str | None]:
    params = {
        "query": item.search or "*",
        "fields": "title,year,externalIds,openAccessPdf,authors,publicationTypes",
        "openAccessPdf": "",
    }
    if item.filter:  # e.g. fieldsOfStudy=Mathematics
        name, _, value = item.filter.partition("=")
        params[name] = value
    if state:
        params["token"] = state
    data = await http.get_json("https://api.semanticscholar.org/graph/v1/paper/search/bulk", params)
    records = []
    for paper in data.get("data") or []:
        title = " ".join((paper.get("title") or "").split())
        pdf = paper.get("openAccessPdf") or {}
        if not title or not pdf.get("url"):
            continue
        records.append(
            OpenRecord(
                key=_key(_doi_key((paper.get("externalIds") or {}).get("DOI")) or f"s2:{paper.get('paperId')}"),
                source="semanticscholar",
                title=title,
                kind=LibraryKind.PAPER,
                folder=list(item.folder),
                url=f"https://www.semanticscholar.org/paper/{paper.get('paperId')}",
                file_url=pdf["url"],
                authors=_authors([a.get("name", "") for a in paper.get("authors") or []]),
                year=paper.get("year"),
                license=pdf.get("license") or "",
            )
        )
    token = data.get("token")
    return records, token if data.get("data") and token else None


# Creative Commons licences (Crossref filters on the exact URL; the same filter repeated means "any of")
CC_LICENSES = [
    f"http://creativecommons.org/licenses/{kind}/{version}/"
    for kind in ("by", "by-sa", "by-nc", "by-nc-sa", "by-nd", "by-nc-nd")
    for version in ("4.0", "3.0")
]


async def crossref(http: Http, item, state: str | None) -> tuple[list[OpenRecord], str | None]:
    filters = [f"license.url:{url}" for url in CC_LICENSES]
    if item.filter:
        filters.append(item.filter)
    params = {
        "filter": ",".join(filters),
        "rows": 500,
        "cursor": state or "*",
        "select": "DOI,title,author,issued,link,license,type",
    }
    if item.search:
        params["query.bibliographic"] = item.search
    if mailto := env("CROSSREF_MAILTO"):
        params["mailto"] = mailto
    message = (await http.get_json("https://api.crossref.org/works", params)).get("message") or {}
    records = []
    for work in message.get("items", []):
        title = " ".join(" ".join(work.get("title") or []).split())
        if not title or not work.get("DOI"):
            continue
        pdf = next(
            (link["URL"] for link in work.get("link") or [] if link.get("content-type") == "application/pdf"), ""
        )
        licence = next(
            (lic.get("URL", "") for lic in work.get("license") or [] if "creativecommons" in lic.get("URL", "")), ""
        )
        year = ((work.get("issued") or {}).get("date-parts") or [[None]])[0][0]
        records.append(
            OpenRecord(
                key=_key(_doi_key(work["DOI"])),
                source="crossref",
                title=title,
                kind=LibraryKind.BOOK if work.get("type") in ("book", "monograph") else LibraryKind.PAPER,
                folder=list(item.folder),
                url=f"https://doi.org/{work['DOI']}",
                file_url=pdf,
                authors=_authors(
                    [" ".join(filter(None, (a.get("given"), a.get("family")))) for a in work.get("author") or []]
                ),
                year=year if isinstance(year, int) else None,
                language=work.get("language") or "",
                license=licence.replace("http://creativecommons.org/licenses/", "CC ").rstrip("/").upper(),
            )
        )
    cursor = message.get("next-cursor")
    return records, cursor if message.get("items") and cursor else None


# ---------------------------------------------------------------- books


async def openlibrary(http: Http, item, state: str | None) -> tuple[list[OpenRecord], str | None]:
    page = int(state or 1)
    query = f"{item.search} ebook_access:public".strip()
    params = {
        "q": query,
        "page": page,
        "limit": 100,
        "fields": "key,title,author_name,first_publish_year,ia,language,ebook_access",
    }
    data = await http.get_json("https://openlibrary.org/search.json", params)
    records = []
    for book in data.get("docs", []):
        identifiers = book.get("ia") or []
        if book.get("ebook_access") != "public" or not identifiers or not book.get("title"):
            continue
        records.append(
            OpenRecord(
                key=_key(f"openlibrary:{book['key'].rsplit('/', 1)[-1]}"),
                source="openlibrary",
                title=" ".join(book["title"].split()),
                kind=LibraryKind.BOOK,
                folder=list(item.folder),
                url=f"https://archive.org/details/{identifiers[0]}",
                file_url=f"ia:{identifiers[0]}",
                authors=_authors(book.get("author_name") or []),
                year=book.get("first_publish_year"),
                language=(book.get("language") or [""])[0][:10],
                license="Public domain · Internet Archive",
            )
        )
    more = data.get("docs") and page * 100 < int(data.get("numFound", 0))
    return records, str(page + 1) if more else None


async def gutendex(http: Http, item, state: str | None) -> tuple[list[OpenRecord], str | None]:
    page = int(state or 1)
    params: dict[str, Any] = {"page": page}
    if item.search:
        params["topic"] = item.search
    if item.filter:  # e.g. languages=en,fr
        name, _, value = item.filter.partition("=")
        params[name] = value
    data = await http.get_json("https://gutendex.com/books/", params)
    records = []
    for book in data.get("results", []):
        formats = book.get("formats") or {}
        records.append(
            OpenRecord(
                key=f"gutenberg:{book['id']}",
                source="gutendex",
                title=" ".join((book.get("title") or "").split())[:500],
                kind=LibraryKind.BOOK,
                folder=list(item.folder),
                url=f"https://www.gutenberg.org/ebooks/{book['id']}",
                file_url=formats.get("application/pdf", ""),
                authors=_authors([a.get("name", "") for a in book.get("authors") or []]),
                language=(book.get("languages") or [""])[0][:10],
                license="Public domain · Project Gutenberg",
            )
        )
    return [r for r in records if r.title], str(page + 1) if data.get("next") else None


CLIENTS = {
    "openalex": (openalex, 0.2),
    "semanticscholar": (semanticscholar, 1.2),  # one request a second without a key
    "crossref": (crossref, 0.5),
    "openlibrary": (openlibrary, 1.0),
    "gutendex": (gutendex, 1.0),
}


def http_for(source: str) -> Http:
    headers = {}
    if source == "semanticscholar" and (key := env("SEMANTIC_SCHOLAR_API_KEY")):
        headers["x-api-key"] = key
    return Http(CLIENTS[source][1], headers)


def describe(record: OpenRecord) -> str:
    return json.dumps(record.__dict__, ensure_ascii=False, default=str)
