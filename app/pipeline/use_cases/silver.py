"""Silver and Gold: Bronze document -> cleaned, re-hosted, AI-normalised, deduplicated, labelled question.

raw.exam.ingestion {doc_id}
  html  -> parse -> re-host images -> crawled_questions (PENDING_AI | READY | NEEDS_REVIEW)
           AI sweep (Gemini Flash): formulas -> LaTeX, missing solutions written      -> READY
           READY -> exact dedup (SHA-256) -> semantic dedup (pgvector cosine) -> labelling -> questions (Gold)
  file  -> import_jobs (the existing AI PDF/Word pipeline)
"""

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domain.question_hash import content_hash, normalize_text
from app.infrastructure.storage import Storage
from app.models import (
    Category,
    CrawlCategoryMap,
    CrawledQuestion,
    CrawlerSite,
    CrawlItemStatus,
    Difficulty,
    ImportJob,
    JobStatus,
    Question,
    QuestionType,
    SourceType,
)
from app.pipeline.domain.models import ParsedQuestion, RawDocument, SiteConfig
from app.pipeline.domain.plain_math import normalize_plain_math
from app.pipeline.parsers.base import all_parsers
from app.pipeline.ports import AIUnavailable, Downloader, Embedder, Enricher, EnrichItem, RawStore
from app.pipeline.use_cases.toc import ensure_path

log = logging.getLogger(__name__)

_IMG = re.compile(r"\[\[img:(\d+)\]\]")
_MATH = re.compile(r"\$\$.*?\$\$|\$[^$]*\$|\\\(.*?\\\)|\\\[.*?\\\]", re.S)
# Signs of a formula written as plain text (outside $...$): Greek letters, roots, comparison signs, functions.
_PLAIN_MATH = re.compile(r"[πωλφΦαβγΔδθμρσε√≤≥±≈∞]|\b(?:sin|cos|tan|cot|log|ln)\s*\(|\d\s*[\^_]|\b[a-zA-Z]\s*=\s*-?\d")
MAX_AI_TRIES = 3
NO_CATEGORY = "Chưa gắn được chương/bài."
STALE = "Không còn trên trang nguồn khi bóc lại."
ANSWER_DOUBT = "AI nghi đáp án của nguồn sai — kiểm tra lại."


EXT_BY_TYPE = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}


@dataclass
class SilverDeps:
    store: RawStore
    storage: Storage
    bronze_storage: Storage
    downloader: Downloader
    enricher: Enricher | None = None
    embedder: Embedder | None = None
    threshold: float | None = None

    @property
    def similarity_threshold(self) -> float:
        return self.threshold if self.threshold is not None else get_settings().semantic_duplicate_threshold


def has_plain_math(text: str) -> bool:
    return bool(_PLAIN_MATH.search(_MATH.sub(" ", text or "")))


def _texts(item: CrawledQuestion) -> list[str]:
    return [item.content, *(c["content"] for c in item.choices or []), item.explanation or ""]


def _inline_images(text: str) -> set[int]:
    """Placeholders sharing a line with other text: formulas drawn as images (the AI transcribes them)."""
    found = set()
    for line in (text or "").split("\n"):
        if _IMG.search(line) and _IMG.sub("", line).strip():
            found |= {int(n) for n in _IMG.findall(line)}
    return found


def _answer_of(item: CrawledQuestion) -> str:
    if item.question_type == QuestionType.SHORT_ANSWER:
        return ", ".join(item.accepted_answers or [])
    if item.question_type == QuestionType.TRUE_FALSE:
        return " ".join(f"{c['id']}) {'Đúng' if c['is_correct'] else 'Sai'}" for c in item.choices)
    return ", ".join(c["id"].upper() for c in item.choices if c.get("is_correct"))


async def _config(session: AsyncSession, site_id: str) -> SiteConfig:
    site = await session.get(CrawlerSite, site_id)
    if site is None:
        raise LookupError(f"Unknown site {site_id}; run sync-configs")
    return SiteConfig.model_validate(site.config)


# ---------------------------------------------------------------- Bronze -> Silver


async def _rehost_images(deps: SilverDeps, urls: list[str]) -> tuple[list[dict], list[str]]:
    images, issues = [], []
    for number, url in enumerate(urls):
        try:
            data, mime = await deps.downloader.download(url)
            ext = EXT_BY_TYPE.get(mime) or PurePosixPath(url.split("?")[0]).suffix.lower() or ".png"
            key = f"figures/crawl/{hashlib.sha1(data).hexdigest()}{ext}"
            if not await deps.storage.exists(key):
                await deps.storage.put(key, data, mime)
            images.append({"n": number, "src": url, "key": key, "mime": mime})
        except Exception as exc:  # noqa: BLE001 - a dead image must not lose the question
            images.append({"n": number, "src": url, "key": None, "mime": None})
            issues.append(f"Không tải được ảnh {url}: {exc}")
    return images, issues


def _place_figures(item: CrawledQuestion) -> None:
    """A placeholder alone on its line is a figure: the first one in the stem becomes the question image."""
    figures = [int(m.group(1)) for line in item.content.split("\n") if (m := _IMG.fullmatch(line.strip()))]
    by_n = {img["n"]: img for img in item.images}
    if figures and by_n.get(figures[0], {}).get("key"):
        item.image_path = by_n[figures[0]]["key"]
        item.content = "\n".join(
            line for line in item.content.split("\n") if line.strip() != f"[[img:{figures[0]}]]"
        ).strip()
    if item.explanation:
        cleaned = "\n".join(line for line in item.explanation.split("\n") if not _IMG.fullmatch(line.strip()))
        if cleaned != item.explanation:
            item.explanation = cleaned.strip()
            item.issues = [*item.issues, "Lời giải gốc có hình minh hoạ (không đưa vào)."]


async def ingest_document(session: AsyncSession, deps: SilverDeps, doc: RawDocument) -> list[CrawledQuestion]:
    config = await _config(session, doc.site_id)
    if doc.kind != "html":
        await ingest_file(session, deps, doc, config)
        return []
    parser = all_parsers()[config.parser]
    page = parser(doc.html or "", doc.url)
    items = []
    for parsed in page.questions:
        item = await _upsert(session, deps, doc, config, parsed, page.title)
        if item is not None:
            items.append(item)
    # Questions an earlier (worse) parse found on this page but the current one does not, e.g. a true/false
    # question once read as multiple choice under another id: drop them unless they are already in the bank.
    current = {parsed.external_id for parsed in page.questions}
    for stale in (
        await session.scalars(
            select(CrawledQuestion).where(
                CrawledQuestion.site_id == doc.site_id,
                CrawledQuestion.source_url == doc.url,
                CrawledQuestion.status != CrawlItemStatus.PUBLISHED,
                CrawledQuestion.status != CrawlItemStatus.REJECTED,
            )
        )
    ).all():
        if stale.external_id not in current:
            stale.status = CrawlItemStatus.REJECTED
            stale.issues = [*stale.issues, STALE]
    await session.flush()
    for item in items:
        if item.status == CrawlItemStatus.READY:
            await advance(session, deps, item, config)
    await session.commit()
    return items


async def _upsert(
    session: AsyncSession, deps: SilverDeps, doc: RawDocument, config: SiteConfig, parsed: ParsedQuestion, title: str
) -> CrawledQuestion | None:
    if parsed.question_type not in QuestionType.__members__:
        return None
    source_hash = content_hash(parsed.question_type, parsed.content, (c["content"] for c in parsed.choices))
    item = await session.scalar(
        select(CrawledQuestion).where(
            CrawledQuestion.site_id == doc.site_id, CrawledQuestion.external_id == parsed.external_id
        )
    )
    if item is not None and item.source_hash == source_hash and doc.context and item.source_context != doc.context:
        item.source_context = doc.context  # same question, now with its place in the source's table of contents
        if item.status == CrawlItemStatus.NEEDS_REVIEW and NO_CATEGORY in item.issues:
            item.issues = [i for i in item.issues if i != NO_CATEGORY]
            item.status = CrawlItemStatus.READY if not item.issues else item.status
            return item if item.status == CrawlItemStatus.READY else None
        return None
    if item is not None and (item.source_hash == source_hash or item.status == CrawlItemStatus.PUBLISHED):
        return None  # unchanged (or already in the bank: edits there win)
    if item is None:
        item = CrawledQuestion(site_id=doc.site_id, external_id=parsed.external_id)
        session.add(item)
    images, image_issues = await _rehost_images(deps, parsed.images)
    item.doc_id = doc.id
    item.source_url = doc.url
    item.source_title = title[:500]
    item.source_context = doc.context or {}
    item.question_type = QuestionType(parsed.question_type)
    # Plain-text formulas become LaTeX here (no AI); only what this cannot handle is left for Gemini.
    item.content = normalize_plain_math(parsed.content)
    item.choices = [{**c, "content": normalize_plain_math(c["content"])} for c in parsed.choices]
    item.accepted_answers = parsed.accepted_answers or None
    item.explanation = normalize_plain_math(parsed.explanation) or None
    item.explanation_by_ai = False
    item.images = images
    item.image_path = None
    item.difficulty = Difficulty(
        parsed.difficulty if parsed.difficulty in Difficulty.__members__ else config.default_difficulty
    )
    item.source_hash = source_hash
    item.content_hash = source_hash
    item.issues = [*parsed.issues, *image_issues]
    item.ai_tries = 0
    item.similarity = None
    item.duplicate_of = None
    item.updated_at = datetime.now(UTC)
    _place_figures(item)

    inline = set().union(*(_inline_images(t) for t in _texts(item)))
    blocking = parsed.issues or image_issues or not parsed.has_answer
    needs_ai = (
        bool(inline)
        or not item.explanation
        or config.verify_answers  # this source's answer keys contain mistakes: have them checked
        or any(has_plain_math(t) for t in _texts(item))
    )
    if blocking:
        item.status = CrawlItemStatus.NEEDS_REVIEW
    elif needs_ai and deps.enricher is not None:
        item.status = CrawlItemStatus.PENDING_AI
    elif inline:  # formula images and no AI configured: a human has to fix them
        item.status = CrawlItemStatus.NEEDS_REVIEW
        item.issues = [*item.issues, "Có công thức dạng ảnh nhưng chưa cấu hình AI."]
    else:
        item.status = CrawlItemStatus.READY
    return item


async def ingest_file(
    session: AsyncSession, deps: SilverDeps, doc: RawDocument, config: SiteConfig
) -> ImportJob | None:
    """PDF / Word / image from a source: hand it to the existing AI import pipeline (draft review)."""
    category = await label(session, config, doc.url, doc.title or doc.url, doc.context)
    if category is None:
        raise LookupError(f"No category for file {doc.url}; add a categories rule to {config.site_id}.yaml")
    data = await deps.bronze_storage.get(doc.storage_key)
    suffix = PurePosixPath(doc.storage_key).suffix
    key = f"raw/crawl-{doc.id.replace(':', '-')}{suffix}"
    await deps.storage.put(key, data)
    job = ImportJob(
        original_filename=(doc.title or PurePosixPath(doc.url).name)[:300],
        stored_path=key,
        status=JobStatus.QUEUED,
        stage=f"Thu thập từ {config.name}",
        root_id=category.root_id,
        category_id=category.id,
        default_difficulty=Difficulty(config.default_difficulty),
    )
    session.add(job)
    await session.commit()
    return job


# ---------------------------------------------------------------- AI enrichment


async def ai_sweep(session: AsyncSession, deps: SilverDeps, batch: int = 8) -> int:
    """Send PENDING_AI items to Gemini in small batches; returns how many were handled."""
    if deps.enricher is None:
        return 0
    items = list(
        (
            await session.scalars(
                select(CrawledQuestion)
                .where(CrawledQuestion.status == CrawlItemStatus.PENDING_AI)
                .order_by(CrawledQuestion.id)
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    if not items:
        return 0
    requests = []
    for item in items:
        inline = set().union(*(_inline_images(t) for t in _texts(item)))
        attached = []
        for img in item.images:
            if img["n"] in inline and img.get("key"):
                attached.append((img["n"], await deps.storage.get(img["key"]), img["mime"] or "image/png"))
        requests.append(
            EnrichItem(
                item.id,
                item.question_type.value,
                item.content,
                [c["content"] for c in item.choices],
                _answer_of(item),
                item.explanation or "",
                attached,
            )
        )
    try:
        results = {r.ref: r for r in await deps.enricher.enrich(requests)}
    except Exception as exc:  # noqa: BLE001
        # Quota / overload (429, 5xx after the provider's own retries): not the questions' fault. Leave them
        # waiting, untouched, and tell the caller to back off instead of burning their tries.
        log.warning("AI enrichment unavailable, backing off: %s", exc)
        await session.rollback()
        raise AIUnavailable(str(exc)) from exc
    for item in items:
        result = results.get(item.id)
        if result is None:
            item.ai_tries += 1
            if item.ai_tries >= MAX_AI_TRIES:
                item.status = CrawlItemStatus.NEEDS_REVIEW
            continue
        item.content = result.content
        item.choices = [{**c, "content": text} for c, text in zip(item.choices, result.choices, strict=True)]
        item.explanation = result.explanation or item.explanation
        item.explanation_by_ai = result.explanation_written
        item.ai_provider = result.provider or None
        config = await _config(session, item.site_id)
        if result.difficulty in Difficulty.__members__ and item.difficulty == Difficulty(config.default_difficulty):
            item.difficulty = Difficulty(result.difficulty)  # the source's own level label wins
        item.updated_at = datetime.now(UTC)
        if result.answer_ok is False:
            item.status = CrawlItemStatus.NEEDS_REVIEW
            item.issues = [*item.issues, ANSWER_DOUBT]
            continue
        if set().union(*(_inline_images(t) for t in _texts(item))):
            item.status = CrawlItemStatus.NEEDS_REVIEW
            item.issues = [*item.issues, "AI chưa chuyển hết công thức dạng ảnh."]
            continue
        item.status = CrawlItemStatus.READY
        await advance(session, deps, item, await _config(session, item.site_id))
    await session.commit()
    return len(items)


# ---------------------------------------------------------------- dedup, labelling, Gold


def _embedding_text(item: CrawledQuestion | Question) -> str:
    return " ".join([item.content, *(c["content"] for c in item.choices or [])])


TEXT_RATIO = 0.85  # character-level similarity of the normalised stems for a semantic duplicate


def _answer_key(q: CrawledQuestion | Question) -> tuple:
    if q.question_type == QuestionType.SHORT_ANSWER:
        return tuple(sorted(normalize_text(a) for a in q.accepted_answers or []))
    if q.question_type == QuestionType.TRUE_FALSE:
        return tuple((normalize_text(c["content"]), bool(c["is_correct"])) for c in q.choices or [])
    return tuple(sorted(normalize_text(c["content"]) for c in q.choices or [] if c.get("is_correct")))


def same_question(a: CrawledQuestion | Question, b: CrawledQuestion | Question) -> bool:
    """Confirmation of a cosine candidate: same type, nearly the same stem, same correct answer."""
    if a.question_type != b.question_type:
        return False
    ratio = SequenceMatcher(None, normalize_text(a.content), normalize_text(b.content)).ratio()
    return ratio >= TEXT_RATIO and _answer_key(a) == _answer_key(b)


async def advance(session: AsyncSession, deps: SilverDeps, item: CrawledQuestion, config: SiteConfig) -> None:
    """READY item: exact dedup -> semantic dedup -> labelling -> Gold (when the site auto-publishes)."""
    item.duplicate_of = None  # a re-check starts from scratch
    item.content_hash = content_hash(item.question_type.value, item.content, (c["content"] for c in item.choices))
    # Step 1: exact match with the bank, or with an item already taken from another site.
    same = await session.scalar(select(Question.id).where(Question.content_hash == item.content_hash).limit(1))
    if same is None:
        same_item = await session.scalar(
            select(CrawledQuestion.question_id)
            .where(
                CrawledQuestion.source_hash == item.source_hash,
                CrawledQuestion.id != item.id,
                CrawledQuestion.status == CrawlItemStatus.PUBLISHED,
            )
            .limit(1)
        )
        same = same_item
    if same is not None:
        item.status, item.duplicate_of, item.similarity = CrawlItemStatus.DUPLICATE, same, 1
        return
    # Step 2: semantic match (cosine similarity on Gemini embeddings, pgvector HNSW index). Embeddings of short
    # physics questions on one topic are all close (~0.92+), and "gia tốc cực tiểu" vs "li độ cực tiểu" scores 0.97
    # with a different answer, so cosine only nominates candidates: a candidate is a duplicate when the texts are
    # nearly identical and the answer is the same.
    vector = None
    if deps.embedder is not None:
        try:
            vector = (await deps.embedder.embed([_embedding_text(item)]))[0]
        except Exception as exc:  # noqa: BLE001 - embeddings out of quota: retry later (retry_ready)
            log.warning("Embedding unavailable, item %s waits: %s", item.id, exc)
            item.status = CrawlItemStatus.READY
            return
        distance = Question.embedding.cosine_distance(vector)
        candidates = (
            await session.execute(
                select(Question, (1 - distance).label("similarity"))
                .where(Question.embedding.is_not(None))
                .order_by(distance)
                .limit(5)
            )
        ).all()
        if candidates:
            item.similarity = round(float(candidates[0].similarity), 4)
        for question, similarity in candidates:
            if similarity < deps.similarity_threshold:
                break
            if same_question(item, question):
                item.status, item.duplicate_of = CrawlItemStatus.SIMILAR, question.id
                item.similarity = round(float(similarity), 4)
                return
    # Labelling: which chapter / lesson of our table of contents.
    category = await session.get(Category, item.category_id) if item.category_id else None
    if category is None:
        category = await label(session, config, item.source_url, item.source_title, item.source_context)
    if category is None:
        item.status = CrawlItemStatus.NEEDS_REVIEW
        if NO_CATEGORY not in item.issues:
            item.issues = [*item.issues, NO_CATEGORY]
        return
    item.category_id = category.id
    if config.auto_publish:
        await publish(session, item, category, vector)


async def publish(
    session: AsyncSession, item: CrawledQuestion, category: Category, vector: list[float] | None = None
) -> Question:
    question = Question(
        root_id=category.root_id,
        category_id=category.id,
        difficulty=item.difficulty,
        question_type=item.question_type,
        content=item.content,
        choices=item.choices,
        accepted_answers=item.accepted_answers,
        explanation=item.explanation,
        image_path=item.image_path,
        source_type=SourceType.CRAWLER,
        source_url=item.source_url,
        content_hash=item.content_hash,
        is_verified=True,
        embedding=vector,
    )
    session.add(question)
    await session.flush()
    item.question_id = question.id
    item.category_id = category.id
    item.status = CrawlItemStatus.PUBLISHED
    return question


async def _category_by_path(session: AsyncSession, path: str) -> Category | None:
    names = [n.strip() for n in path.split("›") if n.strip()]
    parent: Category | None = None
    for name in names:
        cond = Category.parent_id == parent.id if parent else Category.parent_id.is_(None)
        parent = await session.scalar(select(Category).where(cond, func.lower(Category.name) == name.lower()))
        if parent is None:
            return None
    return parent


async def label(
    session: AsyncSession, config: SiteConfig, url: str, title: str, context: dict | None = None
) -> Category | None:
    """Category of a source page: an admin's (or earlier) choice; else the source's own table of contents
    (auto_toc: root › series › chapter › lesson, missing nodes created); else the site's rules ("Bài N")."""
    mapped = await session.get(CrawlCategoryMap, (config.site_id, url))
    if mapped is not None:
        return await session.get(Category, mapped.category_id)
    context = context or {}
    if config.auto_toc and context.get("root") and context.get("chapter"):
        target = await ensure_path(
            session, [context["root"], context.get("series", ""), context["chapter"], context.get("lesson", "")]
        )
        session.add(
            CrawlCategoryMap(site_id=config.site_id, source_url=url, category_id=target.id, source_title=title[:500])
        )
        await session.flush()
        return target
    for rule in config.categories:
        if not re.search(rule.pattern, url):
            continue
        root = await _category_by_path(session, rule.root)
        if root is None:
            continue
        target = root
        if rule.lesson and (m := re.search(rule.lesson, title, re.I)):
            number = m.group(1)
            target = await session.scalar(
                select(Category)
                .where(
                    Category.path.like(f"{root.path}%"), Category.name.op("~*")(rf"^\s*bài\s*{number}\y")
                )  # \y: word boundary in PG
                .order_by(Category.depth.desc())
                .limit(1)
            )
            if target is None:
                return None
        session.add(
            CrawlCategoryMap(site_id=config.site_id, source_url=url, category_id=target.id, source_title=title[:500])
        )
        await session.flush()
        return target
    return None


async def set_mapping(session: AsyncSession, site_id: str, url: str, title: str, category_id: int) -> None:
    mapped = await session.get(CrawlCategoryMap, (site_id, url))
    if mapped is None:
        session.add(
            CrawlCategoryMap(
                site_id=site_id, source_url=url, category_id=category_id, source_title=title[:500], by_admin=True
            )
        )
    else:
        mapped.category_id, mapped.by_admin = category_id, True


async def retry_ready(session: AsyncSession, deps: SilverDeps, batch: int = 20) -> int:
    """READY items left waiting (embeddings were unavailable): run dedup + labelling + Gold again."""
    items = list(
        (
            await session.scalars(
                select(CrawledQuestion)
                .where(CrawledQuestion.status == CrawlItemStatus.READY)
                .order_by(CrawledQuestion.updated_at)
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    for item in items:
        item.updated_at = datetime.now(UTC)
        await advance(session, deps, item, await _config(session, item.site_id))
    await session.commit()
    return sum(1 for i in items if i.status != CrawlItemStatus.READY)


async def reprocess(session: AsyncSession, deps: SilverDeps, item: CrawledQuestion) -> None:
    """Run dedup + labelling again (after an admin fixed the category or the text)."""
    item.status = CrawlItemStatus.READY
    await advance(session, deps, item, await _config(session, item.site_id))


async def reparse_site(session: AsyncSession, deps: SilverDeps, site_id: str) -> int:
    """Medallion payoff: run the (improved) parser over every Bronze document again, without crawling."""
    count = 0
    async for doc in deps.store.iter_site(site_id):
        if doc.kind == "html":
            count += len(await ingest_document(session, deps, doc))
    return count
