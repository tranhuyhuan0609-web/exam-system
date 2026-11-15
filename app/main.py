from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Request, Response, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import SessionLocal
from app.deps import LoginRequired, SessionDep, get_current_user
from app.modules.access.router import router as access_router
from app.modules.ai_gateway.router import router as ai_gateway_router
from app.modules.analytics.router import router as analytics_router
from app.modules.assembly.router import router as assembly_router
from app.modules.assets.router import router as assets_router
from app.modules.assignments.router import router as assignments_router
from app.modules.attempts.router import router as attempts_router
from app.modules.audit.router import router as audit_router
from app.modules.authoring.router import router as authoring_router
from app.modules.billing.router import router as billing_router
from app.modules.classes.router import router as classes_router
from app.modules.config.router import router as config_router
from app.modules.content.router import router as content_router
from app.modules.crawler.router import router as crawler_router
from app.modules.curriculum.router import router as curriculum_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.dedup.router import router as dedup_router
from app.modules.delivery.router import router as delivery_router
from app.modules.drive_sync.router import router as drive_sync_router
from app.modules.error_notebook.router import router as error_notebook_router
from app.modules.exam_import.router import router as exam_import_router
from app.modules.explanations.router import router as explanations_router
from app.modules.extraction.router import router as extraction_router
from app.modules.flashcards.router import router as flashcards_router
from app.modules.frameworks.router import router as frameworks_router
from app.modules.generation.router import router as generation_router
from app.modules.gradebook.router import router as gradebook_router
from app.modules.grading.router import router as grading_router
from app.modules.ingestion.router import router as ingestion_router
from app.modules.knowledge_graph.router import router as knowledge_graph_router
from app.modules.layout.router import router as layout_router
from app.modules.learning_path.router import router as learning_path_router
from app.modules.mastery.router import router as mastery_router
from app.modules.notifications.router import router as notifications_router
from app.modules.outbox.router import router as outbox_router
from app.modules.parsers.router import router as parsers_router
from app.modules.practice.router import router as practice_router
from app.modules.privacy.router import router as privacy_router
from app.modules.quality.router import router as quality_router
from app.modules.questions.router import router as questions_router
from app.modules.recommendations.router import router as recommendations_router
from app.modules.review.router import router as review_router
from app.modules.search.router import router as search_router
from app.modules.taxonomy.router import router as taxonomy_router
from app.modules.teacher_copilot.router import router as teacher_copilot_router
from app.modules.tutor.router import router as tutor_router
from app.modules.vertical_packs.router import router as vertical_packs_router
from app.platform.branding import BRAND_NAME, BRAND_SEO_DESCRIPTION
from app.routers import admin, auth, library, maker, plans, student, teacher
from app.templating import templates

settings = get_settings()
is_dev = settings.app_env.lower() in ("dev", "development", "test", "testing")


class CSRFProtectMiddleware(BaseHTTPMiddleware):
    """Enforces Origin / Referer validation on all state-modifying requests (POST, PUT, DELETE, PATCH).

    Protects all HTML form posts and AJAX endpoints against Cross-Site Request Forgery (CSRF).
    """

    SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}

    @staticmethod
    def _is_stateless_bearer_call(request: Request) -> bool:
        """A /api/v1 call that authenticates with an explicit Bearer token and carries no session cookie.

        A bearer token is not an ambient credential (a browser never attaches it on its own), so a forged
        cross-site request cannot use it; the Next.js proxy forwards such calls with a foreign Origin.
        """
        if not request.url.path.startswith("/api/v1/"):
            return False
        if "exam_session" in request.cookies:
            return False
        if request.url.path == "/api/v1/auth/login":
            return True  # carries the credentials in the body, sets no cookie: nothing ambient to forge
        return request.headers.get("authorization", "").lower().startswith("bearer ")

    async def dispatch(self, request: Request, call_next):
        if request.method not in self.SAFE_METHODS and not self._is_stateless_bearer_call(request):
            origin = request.headers.get("origin")
            referer = request.headers.get("referer")
            host = request.headers.get("host") or request.url.netloc

            if origin:
                origin_netloc = urlparse(origin).netloc
                if origin_netloc != host:
                    return Response(
                        "403 Forbidden: CSRF Origin verification failed.",
                        status_code=403,
                        media_type="text/plain",
                    )
            elif referer:
                referer_netloc = urlparse(referer).netloc
                if referer_netloc != host:
                    return Response(
                        "403 Forbidden: CSRF Referer verification failed.",
                        status_code=403,
                        media_type="text/plain",
                    )
            else:
                # Neither Origin nor Referer provided
                if not is_dev and not request.headers.get("authorization"):
                    return Response(
                        "403 Forbidden: Missing CSRF Origin or Referer header.",
                        status_code=403,
                        media_type="text/plain",
                    )
        return await call_next(request)


app = FastAPI(
    title=BRAND_NAME,
    description=BRAND_SEO_DESCRIPTION,
    version="0.1.0",
    docs_url="/docs" if is_dev else None,
    redoc_url="/redoc" if is_dev else None,
    openapi_url="/openapi.json" if is_dev else None,
)
app.add_middleware(CSRFProtectMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="exam_session",
    max_age=settings.session_max_age_seconds,
    same_site="lax",  # keeps the cookie off cross-site POSTs (basic CSRF protection)
    https_only=settings.session_https_only,
)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
# Local storage only: question figures and 2-page PDF previews are public; uploads and pages are never mounted.
if settings.storage_backend == "local":
    _figures = Path(settings.data_dir) / "media" / "figures"
    _figures.mkdir(parents=True, exist_ok=True)
    app.mount("/media/figures", StaticFiles(directory=_figures), name="figures")
    _previews = Path(settings.data_dir) / "media" / "previews"
    _previews.mkdir(parents=True, exist_ok=True)
    app.mount("/media/previews", StaticFiles(directory=_previews), name="previews")

app.include_router(student.router)
app.include_router(teacher.router)
app.include_router(maker.router)
app.include_router(library.router)
app.include_router(plans.router)
app.include_router(auth.router)
app.include_router(admin.router)

# V11.3 /api/v1 modules (JWT bearer auth; tenant context bound per transaction for RLS).
for _module_router in (
    outbox_router,
    taxonomy_router,
    curriculum_router,
    assets_router,
    ingestion_router,
    config_router,
    access_router,
    privacy_router,
    content_router,
    authoring_router,
    ai_gateway_router,
    audit_router,
    parsers_router,
    layout_router,
    extraction_router,
    review_router,
    quality_router,
    questions_router,
    dedup_router,
    exam_import_router,
    search_router,
    frameworks_router,
    assembly_router,
    delivery_router,
    attempts_router,
    grading_router,
    knowledge_graph_router,
    dashboard_router,
    learning_path_router,
    practice_router,
    error_notebook_router,
    classes_router,
    assignments_router,
    gradebook_router,
    notifications_router,
    flashcards_router,
    tutor_router,
    explanations_router,
    crawler_router,
    drive_sync_router,
    generation_router,
    mastery_router,
    recommendations_router,
    teacher_copilot_router,
    analytics_router,
    billing_router,
    vertical_packs_router,
):
    app.include_router(_module_router)


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, exc: LoginRequired):
    return RedirectResponse(f"/login?next={quote(exc.next_url)}", status.HTTP_303_SEE_OTHER)


@app.exception_handler(StarletteHTTPException)
async def html_error_handler(request: Request, exc: StarletteHTTPException):
    # JSON errors for API/health endpoints, a friendly HTML page for everything else.
    if request.url.path.startswith(("/api/", "/health")):
        return await http_exception_handler(request, exc)
    async with SessionLocal() as session:
        user = await get_current_user(request, session)
    return templates.TemplateResponse(
        request,
        "error.html",
        {"user": user, "status_code": exc.status_code, "detail": exc.detail},
        status_code=exc.status_code,
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/health/db")
async def health_db(session: SessionDep) -> dict:
    await session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "up"}


@app.get("/api/time")
async def server_time() -> dict:
    """Authoritative server clock. Clients use it to compute their offset; client time is never trusted."""
    return {"server_time": datetime.now(UTC).isoformat()}
