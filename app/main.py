from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import SessionLocal
from app.deps import LoginRequired, SessionDep, get_current_user
from app.routers import admin, auth, student
from app.templating import templates

settings = get_settings()

app = FastAPI(title="Exam System", version="0.1.0")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="exam_session",
    max_age=settings.session_max_age_seconds,
    same_site="lax",  # keeps the cookie off cross-site POSTs (basic CSRF protection)
    https_only=settings.session_https_only,
)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
# Local storage only: question figures are public (unguessable names); uploads and pages are never mounted.
if settings.storage_backend == "local":
    _figures = Path(settings.data_dir) / "media" / "figures"
    _figures.mkdir(parents=True, exist_ok=True)
    app.mount("/media/figures", StaticFiles(directory=_figures), name="figures")

app.include_router(student.router)
app.include_router(auth.router)
app.include_router(admin.router)


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
