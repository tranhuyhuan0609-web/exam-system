"""FastAPI /api/v1 routes for F05 access: the caller's permissions and tenant roles.

`tenant_id` and `membership_id` come only from the signed JWT; every query runs in a transaction bound
to that tenant (`app.current_tenant_id`), so another tenant's roles are invisible, not merely refused.
Memberships belong to F04 and are reached only through `app.modules.privacy.public`.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.config import get_settings
from app.modules.access.internal.evaluator import MembershipStatus
from app.modules.access.internal.service import (
    RoleConflictError,
    UnknownPermissionError,
    UnknownRoleError,
    create_role,
    permissions_for_roles,
    require_role_codes,
)
from app.modules.privacy.public import authenticate_login, get_membership, set_membership_roles
from app.platform.auth_context import (
    ActorContext,
    AuthTokenError,
    TenantSpoofingError,
    decode_and_verify_actor_token,
    extract_bearer_token,
    guard_no_tenant_spoofing,
    mint_hs256_token,
)
from app.platform.errors import PlatformErrorCode, build_problem_detail
from app.platform.login_throttle import login_throttle
from app.platform.pagination import resolve_correlation_id
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1", tags=["access"])

ROLE_ADMIN_ROLES = frozenset({"ADMIN", "TENANT_ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN"})
_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class PermissionsMeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    user_id: uuid.UUID
    roles: list[str]
    permissions: list[str]
    entitlement_tier: str
    membership_status: str | None
    source: str  # "membership": roles read from the database now; "token": no membership row, JWT roles


class SetMembershipRolesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roles: list[str] = Field(max_length=20)


class MembershipRolesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    status: str
    roles: list[str]
    entitlement_tier: str


class CreateRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=2, max_length=60)
    name: str = Field(min_length=1, max_length=120)
    permission_codes: list[str] = Field(default_factory=list, max_length=200)


class RoleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    code: str
    name: str
    permission_codes: list[str]
    created_at: datetime


def problem(
    code: PlatformErrorCode,
    detail: str,
    request: Request,
    correlation_id: str,
    status_override: int | None = None,
) -> JSONResponse:
    built = build_problem_detail(
        code=code,
        detail=detail,
        instance=request.url.path,
        correlation_id=correlation_id,
        status_override=status_override,
    )
    return JSONResponse(
        status_code=built.status,
        content=built.to_dict(),
        media_type="application/problem+json",
        headers={"X-Correlation-Id": correlation_id},
    )


def authenticate(
    request: Request, credentials: HTTPAuthorizationCredentials | None, allowed_roles: frozenset[str] | None = None
) -> tuple[ActorContext | None, str, JSONResponse | None]:
    """The verified actor, or a problem response (401 bad token, 403 spoofed tenant or missing role)."""
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    raw_auth = request.headers.get("authorization")
    if not raw_auth and credentials is not None:
        raw_auth = f"{credentials.scheme} {credentials.credentials}"
    try:
        actor = decode_and_verify_actor_token(
            extract_bearer_token(raw_auth), secret=get_settings().secret_key, now_epoch=int(time.time())
        )
        guard_no_tenant_spoofing(
            actor,
            claimed_tenant_header=request.headers.get("x-tenant-id"),
            claimed_membership_header=request.headers.get("x-membership-id"),
        )
    except AuthTokenError as exc:
        return None, correlation_id, problem(PlatformErrorCode.UNAUTHORIZED, str(exc), request, correlation_id)
    except TenantSpoofingError as exc:
        return None, correlation_id, problem(PlatformErrorCode.FORBIDDEN, str(exc), request, correlation_id)
    if allowed_roles is not None and not ({r.upper() for r in actor.roles} & allowed_roles):
        return (
            None,
            correlation_id,
            problem(PlatformErrorCode.FORBIDDEN, "tenant administrator role required", request, correlation_id),
        )
    return actor, correlation_id, None


@router.get("/permissions/me", response_model=PermissionsMeResponse)
async def get_my_permissions(request: Request, credentials: OptionalBearer = None) -> Any:
    """Permissions granted to the caller's roles in the caller's tenant (never another tenant's).

    When the token's membership exists, its current roles and status decide (a role removed or a
    membership suspended takes effect at once, not when the token expires); an inactive membership has
    no permissions. Without a membership row the signed token's roles are used.
    """
    actor, _, err = authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
        membership = await get_membership(session, actor.membership_id)
        if membership is not None and membership.user_id != actor.user_id:
            membership = None  # a token naming somebody else's membership gets nothing from it
            roles: list[str] = []
            source = "membership"
        elif membership is not None:
            roles = sorted({r.upper() for r in membership.roles})
            source = "membership"
        else:
            roles = sorted({r.upper() for r in actor.roles})
            source = "token"
        active = membership is None or membership.status == MembershipStatus.ACTIVE
        permissions = await permissions_for_roles(session, roles) if active and roles else frozenset()
    return PermissionsMeResponse(
        tenant_id=actor.tenant_id,
        membership_id=actor.membership_id,
        user_id=actor.user_id,
        roles=roles,
        permissions=sorted(permissions),
        entitlement_tier=actor.entitlement_tier if membership is None else membership.entitlement_tier,
        membership_status=None if membership is None else membership.status,
        source=source,
    )


@router.post("/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
async def post_role(body: CreateRoleRequest, request: Request, credentials: OptionalBearer = None) -> Any:
    """Create a role of the caller's tenant; the tenant is never taken from the body."""
    actor, correlation_id, err = authenticate(request, credentials, ROLE_ADMIN_ROLES)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            role = await create_role(
                session,
                tenant_id=actor.tenant_id,
                code=body.code,
                name=body.name,
                permission_codes=body.permission_codes,
            )
    except RoleConflictError as exc:
        return problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id, status_override=409)
    except (UnknownPermissionError, ValueError) as exc:
        return problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id)
    return RoleResponse(
        id=role.id,
        tenant_id=role.tenant_id,
        code=role.code,
        name=role.name,
        permission_codes=list(role.permission_codes),
        created_at=role.created_at,
    )


@router.put("/memberships/{membership_id}/roles", response_model=MembershipRolesResponse)
async def put_membership_roles(
    membership_id: uuid.UUID, body: SetMembershipRolesRequest, request: Request, credentials: OptionalBearer = None
) -> Any:
    """Replace the roles of a membership of the caller's tenant with roles defined in that tenant.

    A membership of another tenant answers 404, exactly like one that does not exist.
    """
    actor, correlation_id, err = authenticate(request, credentials, ROLE_ADMIN_ROLES)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            if await get_membership(session, membership_id) is None:
                return problem(PlatformErrorCode.NOT_FOUND, "membership not found", request, correlation_id)
            roles = await require_role_codes(session, body.roles)
            updated = await set_membership_roles(session, membership_id, roles)
    except (UnknownRoleError, ValueError) as exc:
        return problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id)
    if updated is None:
        return problem(PlatformErrorCode.NOT_FOUND, "membership not found", request, correlation_id)
    return MembershipRolesResponse(
        id=updated.id,
        tenant_id=updated.tenant_id,
        user_id=updated.user_id,
        status=updated.status,
        roles=list(updated.roles),
        entitlement_tier=updated.entitlement_tier,
    )


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    tenant_slug: str | None = Field(default=None, max_length=80)


class LoginResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    tenant_id: uuid.UUID
    tenant_slug: str
    membership_id: uuid.UUID
    user_id: uuid.UUID
    roles: list[str]
    entitlement_tier: str


class MeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_slug: str | None
    membership_id: uuid.UUID
    roles: list[str]
    entitlement_tier: str
    membership_status: str | None
    pseudonym_handle: str | None
    token_expires_at: datetime


class ServerTimeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_epoch_ms: int
    server_iso: str


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request) -> Any:
    """Exchange e-mail and password for the JWT of one tenant membership (`tenant_slug` narrows the choice).

    Wrong e-mail, wrong password, a user that is not ACTIVE and a missing membership all answer the same 401.
    """
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    client_ip = request.client.host if request.client else "unknown"
    decision = login_throttle.check(client_ip, body.email)
    if not decision.allowed:
        blocked = problem(
            PlatformErrorCode.RATE_LIMIT_EXCEEDED,
            "too many failed sign-in attempts, try again later",
            request,
            correlation_id,
        )
        blocked.headers["Retry-After"] = str(decision.retry_after_seconds)
        return blocked
    principal = await authenticate_login(body.email, body.password, body.tenant_slug)
    if principal is None:
        login_throttle.record_failure(client_ip, body.email)
        return problem(PlatformErrorCode.UNAUTHORIZED, "invalid e-mail, password or tenant", request, correlation_id)
    login_throttle.clear(client_ip, body.email)
    settings = get_settings()
    token = mint_hs256_token(
        secret=settings.secret_key,
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        membership_id=principal.membership_id,
        roles=principal.roles,
        entitlement_tier=principal.entitlement_tier,
        session_id=f"login-{uuid.uuid4().hex[:12]}",
        issued_at=int(time.time()),
        ttl_seconds=settings.access_token_ttl_seconds,
    )
    return JSONResponse(
        content=LoginResponse(
            access_token=token,
            expires_in=settings.access_token_ttl_seconds,
            tenant_id=principal.tenant_id,
            tenant_slug=principal.tenant_slug,
            membership_id=principal.membership_id,
            user_id=principal.user_id,
            roles=list(principal.roles),
            entitlement_tier=principal.entitlement_tier,
        ).model_dump(mode="json"),
        headers={"X-Correlation-Id": correlation_id, "Cache-Control": "no-store"},
    )


@router.get("/auth/me", response_model=MeResponse)
async def auth_me(request: Request, credentials: OptionalBearer = None) -> Any:
    """Who the token is: the membership as it is now (roles, tier, status), not only what the token claims."""
    actor, _, err = authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
        membership = await get_membership(session, actor.membership_id)
        if membership is not None and membership.user_id != actor.user_id:
            membership = None
        slug = (await session.execute(text("SELECT slug FROM tenants WHERE id = :t"), {"t": actor.tenant_id})).scalar()
        handle = None
        if membership is not None:
            await session.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": str(actor.user_id)})
            handle = (
                await session.execute(text("SELECT pseudonym_handle FROM users WHERE id = :u"), {"u": actor.user_id})
            ).scalar()
    return MeResponse(
        user_id=actor.user_id,
        tenant_id=actor.tenant_id,
        tenant_slug=slug,
        membership_id=actor.membership_id,
        roles=sorted({r.upper() for r in (actor.roles if membership is None else membership.roles)}),
        entitlement_tier=actor.entitlement_tier if membership is None else membership.entitlement_tier,
        membership_status=None if membership is None else membership.status,
        pseudonym_handle=handle,
        token_expires_at=datetime.fromtimestamp(actor.expires_at, tz=UTC),
    )


@router.get("/time", response_model=ServerTimeResponse)
async def server_time() -> ServerTimeResponse:
    """The server clock, so an exam client can show the countdown against server time, not its own."""
    now = datetime.now(UTC)
    return ServerTimeResponse(server_epoch_ms=int(now.timestamp() * 1000), server_iso=now.isoformat())
