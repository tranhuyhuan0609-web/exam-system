"""app.platform.http_auth: the one place the routers read a token and build a problem response."""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest
from starlette.requests import Request

from app.config import get_settings
from app.platform.auth_context import AuthTokenError, TenantSpoofingError, mint_hs256_token
from app.platform.errors import PlatformErrorCode
from app.platform.http_auth import ACCESS_TOKEN_COOKIE, problem_json, verify_actor

ROUTERS = sorted(Path(__file__).resolve().parents[1].glob("app/modules/*/router.py"))


def _token() -> tuple[str, uuid.UUID, uuid.UUID]:
    tenant, member = uuid.uuid4(), uuid.uuid4()
    token = mint_hs256_token(
        secret=get_settings().secret_key,
        user_id=uuid.uuid4(),
        tenant_id=tenant,
        membership_id=member,
        roles=["TEACHER"],
        issued_at=int(time.time()),
    )
    return token, tenant, member


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/x", "headers": raw, "query_string": b""})


def test_a_signed_bearer_header_gives_the_actor():
    token, tenant, member = _token()
    actor = verify_actor(_request({"authorization": f"Bearer {token}"}), None)
    assert (actor.tenant_id, actor.membership_id) == (tenant, member)


def test_the_cookie_counts_only_where_the_router_allows_it():
    token, *_ = _token()
    request = _request({"cookie": f"{ACCESS_TOKEN_COOKIE}={token}"})
    with pytest.raises(AuthTokenError):
        verify_actor(request, None)
    assert verify_actor(request, None, allow_cookie=True).roles == frozenset({"TEACHER"})


def test_a_tenant_header_that_contradicts_the_token_is_refused():
    token, *_ = _token()
    request = _request({"authorization": f"Bearer {token}", "x-tenant-id": str(uuid.uuid4())})
    with pytest.raises(TenantSpoofingError):
        verify_actor(request, None)


def test_problem_json_is_a_problem_with_the_correlation_header_and_optional_error_code():
    response = problem_json(
        code=PlatformErrorCode.NOT_FOUND, detail="gone", instance="/x", correlation_id="c-1", error_code="SPECIFIC"
    )
    assert response.status_code == 404 and response.media_type == "application/problem+json"
    assert response.headers["x-correlation-id"] == "c-1"
    assert b'"error_code":"SPECIFIC"' in response.body
    forced = problem_json(
        code=PlatformErrorCode.VALIDATION_ERROR, detail="x", instance="/x", correlation_id="c", status_override=409
    )
    assert forced.status_code == 409


@pytest.mark.parametrize("router", ROUTERS, ids=lambda p: p.parent.name)
def test_no_router_keeps_its_own_copy_of_the_shared_plumbing(router: Path):
    text = router.read_text(encoding="utf-8")
    assert "decode_and_verify_actor_token" not in text, "the token check is app.platform.http_auth.verify_actor"
    assert "HTTPBearer(" not in text, "OptionalBearer comes from app.platform.http_auth"
    assert "media_type=\"application/problem+json\"" not in text, "problem bodies come from problem_json"
