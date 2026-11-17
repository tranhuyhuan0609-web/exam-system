"""Shared HTTP plumbing of the /api/v1 routers: the bearer scheme, the signed-token check and the RFC 7807 response.

Every module router used to carry its own copy. What they genuinely differ in (which roles a route needs, the shape
a router hands back to its handlers) stays in the router; what must be identical, reading the token, refusing a spoofed
tenant header and building the problem body, lives here once, so a fix to it is one change.

`verify_actor` raises `AuthTokenError` (bad token, answer 401) or `TenantSpoofingError` (header contradicts the
token, answer 403); the router decides how to answer, as before.
"""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings
from app.platform.auth_context import (
    ActorContext,
    decode_and_verify_actor_token,
    extract_bearer_token,
    guard_no_tenant_spoofing,
)
from app.platform.errors import PlatformErrorCode, build_problem_detail

_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]

ACCESS_TOKEN_COOKIE = "ulp_access_token"


def verify_actor(
    request: Request, credentials: HTTPAuthorizationCredentials | None, *, allow_cookie: bool = False
) -> ActorContext:
    """The actor of a signed bearer token (header, FastAPI credentials or, if allowed, the access-token cookie).

    `tenant_id` and `membership_id` come only from the signed claims; an `X-Tenant-Id` / `X-Membership-Id` header that
    contradicts them is refused.
    """
    raw_auth = request.headers.get("authorization")
    if not raw_auth and credentials is not None:
        raw_auth = f"{credentials.scheme} {credentials.credentials}"
    if allow_cookie and not raw_auth and request.cookies.get(ACCESS_TOKEN_COOKIE):
        raw_auth = f"Bearer {request.cookies[ACCESS_TOKEN_COOKIE]}"
    actor = decode_and_verify_actor_token(
        extract_bearer_token(raw_auth), secret=get_settings().secret_key, now_epoch=int(time.time())
    )
    guard_no_tenant_spoofing(
        actor,
        claimed_tenant_header=request.headers.get("x-tenant-id"),
        claimed_membership_header=request.headers.get("x-membership-id"),
    )
    return actor


def problem_json(
    *,
    code: PlatformErrorCode,
    detail: str,
    instance: str,
    correlation_id: str,
    status_override: int | None = None,
    extensions: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> JSONResponse:
    """An `application/problem+json` response; `error_code` (H-04) is added at the top level when given."""
    problem = build_problem_detail(
        code=code,
        detail=detail,
        instance=instance,
        correlation_id=correlation_id,
        status_override=status_override,
        extensions=extensions,
    )
    body = problem.to_dict()
    if error_code is not None:
        body["error_code"] = error_code
    return JSONResponse(
        status_code=problem.status,
        content=body,
        media_type="application/problem+json",
        headers={"X-Correlation-Id": correlation_id},
    )
