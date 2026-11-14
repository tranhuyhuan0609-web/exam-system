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
from app.modules.assets.router import router as assets_router
from app.modules.config.router import router as config_router
from app.modules.curriculum.router import router as curriculum_router
from app.modules.ingestion.router import router as ingestion_router
from app.modules.outbox.router import router as outbox_router
from app.modules.privacy.router import router as privacy_router
from app.modules.taxonomy.router import router as taxonomy_router
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

    async def dispatch(self, request: Request, call_next):
        if request.method not in self.SAFE_METHODS:
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
