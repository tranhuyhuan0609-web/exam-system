"""Admin: crawler sites and runs, the Silver review queue, and source page -> category assignments."""

from collections import defaultdict
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.config import get_settings
from app.deps import AdminUser, SessionDep
from app.models import (
    Category,
    CrawledQuestion,
    CrawlerSite,
    CrawlItemStatus,
    CrawlRun,
    CrawlRunStatus,
    Question,
    SourceType,
)
from app.pipeline.use_cases.silver import NO_CATEGORY, SilverDeps, publish, reprocess, set_mapping
from app.pipeline.use_cases.sync_configs import sync_configs
from app.routers.admin.common import get_or_404, redirect
from app.scope import load_categories, scope_options, scope_paths
from app.templating import flash, templates

router = APIRouter()

STATUS_LABELS = {
    "PENDING_AI": "Chờ AI",
    "READY": "Sẵn sàng",
    "PUBLISHED": "Đã vào ngân hàng",
    "DUPLICATE": "Trùng (SHA-256)",
    "SIMILAR": "Trùng ngữ nghĩa",
    "NEEDS_REVIEW": "Cần duyệt",
    "REJECTED": "Đã bỏ",
}
PAGE_SIZE = 30


def _deps() -> SilverDeps | None:
    """Embeddings for admin-published items (the semantic index stays complete); None without a Gemini key."""
    if not get_settings().gemini_api_key:
        return None
    from app.pipeline.workers import silver_deps

    return silver_deps()


async def _dead_letters() -> int | None:
    try:
        from app.pipeline.adapters.mongo import MongoRawStore

        store = MongoRawStore()
        try:
            return await store.count_dead_letters()
        finally:
            await store.close()
    except Exception:  # noqa: BLE001 - MongoDB not running: the page still works
        return None


@router.get("/crawler")
async def crawler_home(request: Request, user: AdminUser, session: SessionDep):
    sites = (await session.scalars(select(CrawlerSite).order_by(CrawlerSite.site_id))).all()
    counts: dict[str, dict[str, int]] = defaultdict(dict)
    for site_id, status, n in (
        await session.execute(
            select(CrawledQuestion.site_id, CrawledQuestion.status, func.count()).group_by(
                CrawledQuestion.site_id, CrawledQuestion.status
            )
        )
    ).all():
        counts[site_id][status.value] = n
    runs = (await session.scalars(select(CrawlRun).order_by(CrawlRun.id.desc()).limit(15))).all()
    return templates.TemplateResponse(
        request,
        "admin/crawler.html",
        {
            "user": user,
            "sites": sites,
            "counts": counts,
            "runs": runs,
            "dead_letters": await _dead_letters(),
            "STATUS_LABELS": STATUS_LABELS,
        },
    )


@router.post("/crawler/sync")
async def sync(request: Request, user: AdminUser, session: SessionDep):
    try:
        changed = await sync_configs(session, get_settings().crawler_configs_dir)
    except Exception as exc:  # noqa: BLE001 - invalid YAML: show it
        flash(request, f"Cấu hình không hợp lệ: {exc}", "error")
        return redirect("/admin/crawler")
    flash(request, "Đã nạp cấu hình: " + (", ".join(changed) if changed else "không có thay đổi."))
    return redirect("/admin/crawler")


@router.post("/crawler/{site_id}/run")
async def queue_run(request: Request, user: AdminUser, session: SessionDep, site_id: str):
    site = await get_or_404(session, CrawlerSite, site_id)
    form = await request.form()
    max_pages = int(str(form.get("max_pages") or 0) or 0) or None
    busy = await session.scalar(
        select(CrawlRun.id).where(
            CrawlRun.site_id == site_id, CrawlRun.status.in_([CrawlRunStatus.QUEUED, CrawlRunStatus.RUNNING])
        )
    )
    if busy:
        flash(request, f"{site.name} đang có lượt chạy #{busy}.", "error")
        return redirect("/admin/crawler")
    session.add(
        CrawlRun(
            site_id=site_id,
            trigger="manual",
            max_pages=max_pages,
            force=form.get("force") == "1",
            seed_filter=str(form.get("seed") or "").strip() or None,
            created_by=user.id,
        )
    )
    await session.commit()
    flash(request, f"Đã xếp lượt thu thập {site.name}. Scheduler sẽ bắt đầu trong vài giây.")
    return redirect("/admin/crawler")


@router.post("/crawler/{site_id}/toggle")
async def toggle_site(request: Request, user: AdminUser, session: SessionDep, site_id: str):
    site = await get_or_404(session, CrawlerSite, site_id)
    site.enabled = not site.enabled
    await session.commit()
    flash(request, f"{site.name}: {'đã bật lại lịch thu thập' if site.enabled else 'đã tạm dừng lịch thu thập'}.")
    return redirect("/admin/crawler")


@router.get("/crawler/stats")
async def crawler_stats(request: Request, user: AdminUser, session: SessionDep):
    """Data quality per source and subject: outcomes, AI-written solutions, parser issues, daily intake."""
    items = (
        await session.execute(
            select(
                CrawledQuestion.site_id,
                CrawledQuestion.status,
                CrawledQuestion.category_id,
                CrawledQuestion.source_context,
                CrawledQuestion.explanation_by_ai,
                CrawledQuestion.issues,
                CrawledQuestion.question_type,
            )
        )
    ).all()
    paths = dict((await session.execute(select(Category.id, Category.path))).all())
    names = dict((await session.execute(select(Category.id, Category.name))).all())

    def subject(category_id, context) -> str:
        if category_id and category_id in paths:
            return names.get(int(paths[category_id].strip("/").split("/")[0]), "?")
        return (context or {}).get("root") or "Chưa gắn mục"

    rows: dict[tuple[str, str], dict] = {}
    issues: dict[str, int] = defaultdict(int)
    types: dict[str, int] = defaultdict(int)
    for site_id, status, category_id, context, by_ai, item_issues, qtype in items:
        row = rows.setdefault(
            (site_id, subject(category_id, context)),
            {"total": 0, "ai": 0, "issues": 0, **{k: 0 for k in STATUS_LABELS}},
        )
        row["total"] += 1
        row[status.value] += 1
        row["ai"] += bool(by_ai) and status == CrawlItemStatus.PUBLISHED
        row["issues"] += bool(item_issues)
        types[qtype.value] += status == CrawlItemStatus.PUBLISHED
        for issue in item_issues or []:
            issues[issue.split(":")[0][:80]] += 1
    daily = (
        await session.execute(
            select(func.date(Question.created_at).label("day"), func.count())
            .where(Question.source_type == SourceType.CRAWLER)
            .group_by("day")
            .order_by(func.date(Question.created_at).desc())
            .limit(14)
        )
    ).all()
    return templates.TemplateResponse(
        request,
        "admin/crawler_stats.html",
        {
            "user": user,
            "rows": sorted(rows.items()),
            "issues": sorted(issues.items(), key=lambda kv: -kv[1])[:10],
            "types": types,
            "daily": list(reversed(daily)),
            "STATUS_LABELS": STATUS_LABELS,
            "total": len(items),
        },
    )


@router.get("/crawler/items")
async def review_queue(
    request: Request, user: AdminUser, session: SessionDep, status: str = "NEEDS_REVIEW", site: str = "", page: int = 1
):
    stmt = select(CrawledQuestion)
    if status in STATUS_LABELS:
        stmt = stmt.where(CrawledQuestion.status == CrawlItemStatus(status))
    if site:
        stmt = stmt.where(CrawledQuestion.site_id == site)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    items = (
        await session.scalars(
            stmt.order_by(CrawledQuestion.source_url, CrawledQuestion.id)
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
    ).all()
    groups: dict[tuple[str, str], list[CrawledQuestion]] = defaultdict(list)
    for item in items:
        groups[(item.site_id, item.source_url)].append(item)
    roots = await load_categories(session)
    status_counts = dict(
        (await session.execute(select(CrawledQuestion.status, func.count()).group_by(CrawledQuestion.status))).all()
    )
    return templates.TemplateResponse(
        request,
        "admin/crawler_items.html",
        {
            "user": user,
            "groups": groups,
            "status": status,
            "site": site,
            "page": page,
            "pages": max(1, -(-(total or 0) // PAGE_SIZE)),
            "total": total,
            "status_counts": {s.value: n for s, n in status_counts.items()},
            "STATUS_LABELS": STATUS_LABELS,
            "scope_options": scope_options(roots),
            "scope_paths": scope_paths(roots),
            "sites": (await session.scalars(select(CrawlerSite.site_id))).all(),
            "back": "/admin/crawler/items?" + urlencode({"status": status, "site": site, "page": page}),
        },
    )


def _back(form) -> str:
    back = str(form.get("back") or "")
    return back if back.startswith("/admin/crawler") else "/admin/crawler/items"


@router.post("/crawler/map")
async def map_source(request: Request, user: AdminUser, session: SessionDep):
    """Assign a source page to a category; its waiting questions go through dedup + publishing again."""
    form = await request.form()
    category = await get_or_404(session, Category, int(str(form.get("category_id") or 0) or 0))
    site_id, url = str(form.get("site_id")), str(form.get("source_url"))
    items = (
        await session.scalars(
            select(CrawledQuestion).where(
                CrawledQuestion.site_id == site_id,
                CrawledQuestion.source_url == url,
                CrawledQuestion.status.in_([CrawlItemStatus.NEEDS_REVIEW, CrawlItemStatus.READY]),
            )
        )
    ).all()
    await set_mapping(session, site_id, url, items[0].source_title if items else "", category.id)
    deps = _deps() or SilverDeps(store=None, storage=None, bronze_storage=None, downloader=None)
    published = 0
    for item in items:
        item.category_id = category.id
        if item.status == CrawlItemStatus.READY or item.issues == [NO_CATEGORY]:  # nothing else was wrong
            item.issues = []
            await reprocess(session, deps, item)
        published += item.status == CrawlItemStatus.PUBLISHED
    await session.commit()
    flash(request, f"Đã gán vào “{category.name}”; {published} câu được đưa vào ngân hàng.")
    return redirect(_back(form))


@router.post("/crawler/items/action")
async def bulk_action(request: Request, user: AdminUser, session: SessionDep):
    form = await request.form()
    ids = [int(i) for i in form.getlist("ids") if str(i).isdigit()]
    action = str(form.get("action"))
    items = (await session.scalars(select(CrawledQuestion).where(CrawledQuestion.id.in_(ids)))).all()
    done = 0
    deps = _deps()
    for item in items:
        if item.status == CrawlItemStatus.PUBLISHED:
            continue
        if action == "reject":
            item.status = CrawlItemStatus.REJECTED
            done += 1
        elif action == "publish":
            category = await session.get(Category, item.category_id) if item.category_id else None
            if category is None:
                continue
            vector = None
            if deps and deps.embedder:
                text = " ".join([item.content, *(c["content"] for c in item.choices or [])])
                vector = (await deps.embedder.embed([text]))[0]
            await publish(session, item, category, vector)  # admin decision: skips dedup on purpose
            done += 1
    await session.commit()
    verb = "đưa vào ngân hàng" if action == "publish" else "bỏ"
    flash(
        request,
        f"Đã {verb} {done} câu."
        + (
            ""
            if done == len(items) or action != "publish"
            else " Câu chưa gắn chương/bài thì gán mục cho trang nguồn trước."
        ),
    )
    return redirect(_back(form))
