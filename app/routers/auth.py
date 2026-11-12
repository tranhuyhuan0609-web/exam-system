import time
from collections import defaultdict
from threading import Lock
from typing import Annotated

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.deps import CurrentUser, SessionDep
from app.models import User, UserRole
from app.security import DUMMY_HASH, hash_password, normalize_email, password_problem, safe_next_url, verify_password
from app.templating import templates

router = APIRouter()

# --------------------------------------------------------------------------
# Rate Limiting for /login (Brute-Force & Credential Stuffing Protection)
# --------------------------------------------------------------------------
_LOGIN_ATTEMPTS_IP: dict[str, list[float]] = defaultdict(list)
_LOGIN_ATTEMPTS_EMAIL: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_LOCK = Lock()

MAX_FAILED_ATTEMPTS_PER_IP = 10
MAX_FAILED_ATTEMPTS_PER_EMAIL = 5
RATE_LIMIT_WINDOW_SECONDS = 300  # 5 minutes


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str, email_norm: str) -> str | None:
    now = time.monotonic()
    with _RATE_LIMIT_LOCK:
        _LOGIN_ATTEMPTS_IP[ip] = [t for t in _LOGIN_ATTEMPTS_IP[ip] if now - t < RATE_LIMIT_WINDOW_SECONDS]
        _LOGIN_ATTEMPTS_EMAIL[email_norm] = [
            t for t in _LOGIN_ATTEMPTS_EMAIL[email_norm] if now - t < RATE_LIMIT_WINDOW_SECONDS
        ]

        if len(_LOGIN_ATTEMPTS_IP[ip]) >= MAX_FAILED_ATTEMPTS_PER_IP:
            return "Quá nhiều lần đăng nhập không thành công từ địa chỉ IP này. Vui lòng thử lại sau 5 phút."
        if len(_LOGIN_ATTEMPTS_EMAIL[email_norm]) >= MAX_FAILED_ATTEMPTS_PER_EMAIL:
            return "Tài khoản tạm thời bị giới hạn do nhập sai mật khẩu nhiều lần. Vui lòng thử lại sau 5 phút."
    return None


def _record_failed_attempt(ip: str, email_norm: str) -> None:
    now = time.monotonic()
    with _RATE_LIMIT_LOCK:
        _LOGIN_ATTEMPTS_IP[ip].append(now)
        _LOGIN_ATTEMPTS_EMAIL[email_norm].append(now)


def _clear_failed_attempts(ip: str, email_norm: str) -> None:
    with _RATE_LIMIT_LOCK:
        _LOGIN_ATTEMPTS_IP.pop(ip, None)
        _LOGIN_ATTEMPTS_EMAIL.pop(email_norm, None)


def _home_for(user: User) -> str:
    return {UserRole.ADMIN: "/admin", UserRole.TEACHER: "/teacher"}.get(user.role, "/")


def _log_in(request: Request, user: User) -> None:
    # Start a fresh session on login to avoid session fixation.
    request.session.clear()
    request.session["user_id"] = user.id


@router.get("/login")
async def login_page(request: Request, user: CurrentUser, next: str | None = None):
    if user:
        return RedirectResponse(_home_for(user), status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "login.html", {"next": next or "", "email": ""})


@router.post("/login")
async def login(
    request: Request,
    session: SessionDep,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "",
):
    ip = _get_client_ip(request)
    email_norm = normalize_email(email)

    # 1. Check rate limits
    if limit_error := _check_rate_limit(ip, email_norm):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": next, "email": email, "error": limit_error},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    user = await session.scalar(select(User).where(User.email == email_norm))
    password_ok = verify_password(password, user.password_hash if user else DUMMY_HASH)
    if user is None or not password_ok or not user.is_active:
        _record_failed_attempt(ip, email_norm)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": next, "email": email, "error": "Email hoặc mật khẩu không đúng."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Successful login: reset failed counters
    _clear_failed_attempts(ip, email_norm)
    _log_in(request, user)
    return RedirectResponse(safe_next_url(next, _home_for(user)), status.HTTP_303_SEE_OTHER)


@router.get("/register")
async def register_page(request: Request, user: CurrentUser):
    if user:
        return RedirectResponse(_home_for(user), status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "register.html", {"form": {}})


@router.post("/register")
async def register(
    request: Request,
    session: SessionDep,
    full_name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    password_confirm: Annotated[str, Form()],
    account_type: Annotated[str, Form()] = "STUDENT",
):
    role = UserRole.TEACHER if account_type == "TEACHER" else UserRole.STUDENT
    form = {"full_name": full_name, "email": email, "account_type": role.value}

    def fail(message: str):
        return templates.TemplateResponse(
            request,
            "register.html",
            {"form": form, "error": message},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    full_name = full_name.strip()
    if not full_name or len(full_name) > 200:
        return fail("Vui lòng nhập họ tên (tối đa 200 ký tự).")
    try:
        email = normalize_email(validate_email(email.strip(), check_deliverability=False).normalized)
    except EmailNotValidError:
        return fail("Email không hợp lệ.")
    if problem := password_problem(password):
        return fail(problem)
    if password != password_confirm:
        return fail("Mật khẩu nhập lại không khớp.")

    if await session.scalar(select(User.id).where(User.email == email)):
        return fail("Email này đã được đăng ký.")

    user = User(email=email, full_name=full_name, password_hash=hash_password(password), role=role)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:  # concurrent registration with the same email
        await session.rollback()
        return fail("Email này đã được đăng ký.")

    _log_in(request, user)
    return RedirectResponse(_home_for(user), status.HTTP_303_SEE_OTHER)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status.HTTP_303_SEE_OTHER)
