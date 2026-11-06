"""Academic and Open Book Crawler Pipeline.

Crawls scholarly articles and electronic textbooks from open academic APIs:
- OpenAlex (Academic papers & research)
- Semantic Scholar (AI/Science papers)
- Crossref (DOI publications)
- Open Library (Public domain books & textbooks)
- Gutendex (Project Gutenberg classical literature & texts)

Data is saved to:
1. Bronze Store (raw JSON response for reproducibility and audit)
2. PostgreSQL `library_documents` for instant search, reading, and access in the Library portal.
"""

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LibraryDocument, LibraryKind, LibraryStatus
from app.pipeline.domain.models import RawDocument, doc_id
from app.pipeline.ports import RawStore

log = logging.getLogger(__name__)

USER_AGENT = "ExamSystemAcademicCrawler/1.0 (mailto:admin@exam-systems.local; Educational Project)"


async def crawl_openalex(query: str, subject: str = "Khoa học & Giáo dục", limit: int = 15) -> list[dict[str, Any]]:
    """Fetch research papers from OpenAlex REST API."""
    url = f"https://api.openalex.org/works?search={httpx.URL('', params={'q': query}).params['q']}&per-page={limit}"
    headers = {"User-Agent": USER_AGENT}
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                log.warning("OpenAlex error %d: %s", resp.status_code, resp.text[:200])
                return []
            data = resp.json()
    except Exception as exc:
        log.warning("OpenAlex fetch error: %s", exc)
        return []

    results = []
    for item in data.get("results", []):
        title = item.get("display_name") or item.get("title") or ""
        if not title:
            continue
        doi = item.get("doi") or ""
        open_access = item.get("open_access", {})
        pdf_url = open_access.get("oa_url") or doi or item.get("id") or ""
        year = item.get("publication_year")

        authorships = item.get("authorships", [])
        authors = [a.get("author", {}).get("display_name", "") for a in authorships[:4] if a.get("author")]
        author_str = ", ".join(filter(None, authors))

        results.append({
            "title": title[:480],
            "kind": LibraryKind.TOPIC,
            "folder_path": ["Nghiên cứu khoa học", subject],
            "source": "openalex",
            "source_file_id": item.get("id") or doi,
            "source_url": pdf_url,
            "mime": "application/pdf" if (pdf_url.endswith(".pdf") or "pdf" in pdf_url) else "text/html",
            "authors": author_str,
            "year": year,
            "raw_payload": item,
        })
    return results


async def crawl_openlibrary(query: str, subject: str = "Sách tham khảo", limit: int = 15) -> list[dict[str, Any]]:
    """Fetch books from Open Library API."""
    url = f"https://openlibrary.org/search.json?q={httpx.URL('', params={'q': query}).params['q']}&limit={limit}"
    headers = {"User-Agent": USER_AGENT}
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                log.warning("Open Library error %d: %s", resp.status_code, resp.text[:200])
                return []
            data = resp.json()
    except Exception as exc:
        log.warning("Open Library fetch error: %s", exc)
        return []

    results = []
    for doc in data.get("docs", []):
        title = doc.get("title", "")
        if not title:
            continue
        authors = doc.get("author_name", [])
        author_str = ", ".join(authors[:3]) if authors else "Tác giả mở"
        key = doc.get("key", "")
        read_url = f"https://openlibrary.org{key}" if key else f"https://openlibrary.org/search?q={query}"
        year = doc.get("first_publish_year")

        results.append({
            "title": f"{title} ({author_str})"[:480],
            "kind": LibraryKind.BOOK,
            "folder_path": ["Sách giáo trình điện tử", subject],
            "source": "openlibrary",
            "source_file_id": key or f"ol-{hashlib.sha1(title.encode()).hexdigest()[:12]}",
            "source_url": read_url,
            "mime": "text/html",
            "authors": author_str,
            "year": year,
            "raw_payload": doc,
        })
    return results


async def crawl_gutendex(query: str, subject: str = "Văn học & Tư liệu cổ điển", limit: int = 15) -> list[dict[str, Any]]:
    """Fetch public domain classical books from Project Gutenberg (Gutendex)."""
    url = f"https://gutendex.com/books/?search={httpx.URL('', params={'q': query}).params['q']}"
    headers = {"User-Agent": USER_AGENT}
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                log.warning("Gutendex error %d: %s", resp.status_code, resp.text[:200])
                return []
            data = resp.json()
    except Exception as exc:
        log.warning("Gutendex fetch error: %s", exc)
        return []

    results = []
    for item in data.get("results", [])[:limit]:
        title = item.get("title", "")
        if not title:
            continue
        authors = [a.get("name", "") for a in item.get("authors", [])]
        author_str = ", ".join(authors[:2]) if authors else "Project Gutenberg"
        formats = item.get("formats", {})
        read_url = (
            formats.get("text/html")
            or formats.get("application/epub+zip")
            or formats.get("text/plain; charset=utf-8")
            or f"https://www.gutenberg.org/ebooks/{item.get('id')}"
        )
        gutenberg_id = str(item.get("id", ""))

        results.append({
            "title": f"{title} – {author_str}"[:480],
            "kind": LibraryKind.BOOK,
            "folder_path": ["Tủ sách Gutenberg", subject],
            "source": "gutendex",
            "source_file_id": f"gutenberg-{gutenberg_id}",
            "source_url": read_url,
            "mime": "text/html" if "html" in read_url else "application/epub+zip",
            "authors": author_str,
            "year": None,
            "raw_payload": item,
        })
    return results


async def crawl_semanticscholar(query: str, subject: str = "Nghiên cứu AI & Khoa học", limit: int = 15) -> list[dict[str, Any]]:
    """Fetch research papers from Allen Institute Semantic Scholar API."""
    url = (
        f"https://api.semanticscholar.org/graph/v1/paper/search?"
        f"query={httpx.URL('', params={'q': query}).params['q']}&limit={limit}&fields=title,authors,abstract,openAccessPdf,year"
    )
    headers = {"User-Agent": USER_AGENT}
    try:
        async with httpx.AsyncClient(timeout=12.0, headers=headers) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                log.warning("Semantic Scholar error %d: %s", resp.status_code, resp.text[:200])
                return []
            data = resp.json()
    except Exception as exc:
        log.warning("Semantic Scholar fetch error: %s", exc)
        return []

    results = []
    for item in data.get("data", []):
        title = item.get("title", "")
        if not title:
            continue
        paper_id = item.get("paperId", "")
        oa = item.get("openAccessPdf") or {}
        pdf_url = oa.get("url") or f"https://www.semanticscholar.org/paper/{paper_id}"
        authors = [a.get("name", "") for a in item.get("authors", []) if a.get("name")]
        author_str = ", ".join(authors[:3])

        results.append({
            "title": title[:480],
            "kind": LibraryKind.TOPIC,
            "folder_path": ["Semantic Scholar Research", subject],
            "source": "semanticscholar",
            "source_file_id": paper_id or f"ss-{hashlib.sha1(title.encode()).hexdigest()[:12]}",
            "source_url": pdf_url,
            "mime": "application/pdf" if (pdf_url.endswith(".pdf") or "pdf" in pdf_url) else "text/html",
            "authors": author_str,
            "year": item.get("year"),
            "raw_payload": item,
        })
    return results


async def ingest_academic_items(
    session: AsyncSession,
    raw_store: RawStore | None,
    items: list[dict[str, Any]],
) -> dict[str, int]:
    """Save crawled items to Bronze store (MongoDB) and PostgreSQL LibraryDocument."""
    stats = {"new": 0, "existing": 0, "errors": 0}

    for item in items:
        source = item["source"]
        source_id = str(item["source_file_id"])
        url = item.get("source_url") or ""
        title = item["title"]

        # 1. Check if already exists in PostgreSQL
        existing = await session.scalar(
            select(LibraryDocument).where(
                LibraryDocument.source == source,
                LibraryDocument.source_file_id == source_id,
            )
        )
        if existing:
            stats["existing"] += 1
            continue

        # 2. Save Bronze raw document to MongoDB if raw_store is available
        payload_bytes = json.dumps(item.get("raw_payload", {}), ensure_ascii=False).encode("utf-8")
        digest = hashlib.sha256(payload_bytes).hexdigest()

        if raw_store is not None:
            raw_doc = RawDocument(
                id=doc_id(source, url or source_id, digest),
                site_id=source,
                url=url or source_id,
                kind="json",
                fetched_at=datetime.now(UTC),
                content_hash=digest,
                title=title,
                html=json.dumps(item.get("raw_payload", {}), ensure_ascii=False),
                context={"authors": item.get("authors"), "year": item.get("year")},
            )
            await raw_store.save(raw_doc)

        # 3. Create LibraryDocument in PostgreSQL
        doc = LibraryDocument(
            title=title,
            kind=item["kind"],
            status=LibraryStatus.PUBLISHED,
            folder_path=item["folder_path"],
            source=source,
            source_file_id=source_id,
            source_url=url,
            checksum=digest,
            storage_key=f"academic/{source}/{digest}.json",
            mime=item.get("mime", "text/html"),
            size=len(payload_bytes),
        )
        session.add(doc)
        stats["new"] += 1

    await session.commit()
    return stats
