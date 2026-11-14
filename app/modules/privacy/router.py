"""FastAPI /api/v1 routes for F04: tenants, memberships, parental consent and data erasure.

The tenant comes only from the signed JWT; each request runs in one transaction bound to that tenant
and to the signed-in user. Personal data never appears in clear in the database or in these responses.
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.config import get_settings
from app.modules.access.public import UnknownRoleError, require_role_codes
from app.modules.privacy.internal.crypto_shredding import KeyShreddedError, blind_index_pepper, key_service
from app.modules.privacy.internal.service import (
    ErasureForbiddenError,
    MembershipConflictError,
    PrivacyNotFoundError,
    TenantConflictError,
    bind_user,
    create_tenant,
    enroll_member,
    erase_user,
    record_parental_consent,
)
from app.platform.auth_context import (
    ActorContext,
    AuthTokenError,
    TenantSpoofingError,
    decode_and_verify_actor_token,
    extract_bearer_token,
    guard_no_tenant_spoofing,
)
from app.platform.errors import PlatformErrorCode, build_problem_detail
from app.platform.pagination import resolve_correlation_id
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1", tags=["privacy"])

PLATFORM_ADMIN_ROLES = frozenset({"PLATFORM_ADMIN", "SUPER_ADMIN"})
TENANT_ADMIN_ROLES = PLATFORM_ADMIN_ROLES | {"ADMIN", "TENANT_ADMIN"}
SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{1,78}[a-z0-9]$"
_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


# --- request / response models ----------------------------------------------------------------


class CreateTenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=3, max_length=80, pattern=SLUG_PATTERN)
    name: str = Field(min_length=1, max_length=255)
    daily_ai_budget_usd: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)


class TenantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    slug: str
    name: str
    status: str
    daily_ai_budget_usd: Decimal
    created_at: datetime


class CreateMembershipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    birth_year: int | None = Field(default=None, ge=1920, le=2100)
    display_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=30)
    pseudonym_handle: str | None = Field(default=None, min_length=1, max_length=80)
    roles: list[str] | None = Field(default=None, max_length=20)
    entitlement_tier: Literal["FREE", "STUDENT_PRO", "TEACHER_PRO", "SCHOOL_ENTERPRISE"] = "FREE"


class MembershipResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    status: str
    roles: list[str]
    entitlement_tier: str
    created_at: datetime


class CreateMembershipResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    membership: MembershipResponse
    user_created: bool
    user_status: str
    is_minor: bool
    pii_stored: bool
    parental_consent_required: bool


class ParentalConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    learner_user_id: uuid.UUID
    guardian_email: EmailStr
    guardian_relationship: Literal["PARENT", "LEGAL_GUARDIAN", "SCHOOL_CONTRACT"]
    verification_method: Literal["OTP_EMAIL", "SIGNED_SCHOOL_AGREEMENT"]
    consent_scope_json: dict[str, bool] | None = None


class ParentalConsentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    learner_user_id: uuid.UUID
    guardian_relationship: str
    verification_method: str
    consent_scope_json: dict[str, Any]
    consented_at: datetime
    learner_status: str


class ErasureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_user_id: uuid.UUID | None = None  # default: the signed-in user
    reason: str | None = Field(default=None, max_length=500)


class ErasureCertificateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    subject_user_id: uuid.UUID
    kms_kek_key_id: str
    primary_db_shredded_at: datetime
    kms_kek_revoked_at: datetime
    backup_expiry_guaranteed_at: datetime
    verification_hash: str


# --- helpers -----------------------------------------------------------------------------------


def _problem(
    code: PlatformErrorCode, detail: str, request: Request, correlation_id: str, status_override: int | None = None
) -> JSONResponse:
    built = build_problem_detail(
        code=code, detail=detail, instance=request.url.path, correlation_id=correlation_id,
        status_override=status_override,
    )  # fmt: skip
    return JSONResponse(
        status_code=built.status,
        content=built.to_dict(),
        media_type="application/problem+json",
        headers={"X-Correlation-Id": correlation_id},
    )


def _authenticate(
    request: Request, credentials: HTTPAuthorizationCredentials | None, allowed_roles: frozenset[str] | None
) -> tuple[ActorContext | None, str, JSONResponse | None]:
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
        return None, correlation_id, _problem(PlatformErrorCode.UNAUTHORIZED, str(exc), request, correlation_id)
    except TenantSpoofingError as exc:
        return None, correlation_id, _problem(PlatformErrorCode.FORBIDDEN, str(exc), request, correlation_id)
    if allowed_roles is not None and not _has_role(actor, allowed_roles):
        return (
            None,
            correlation_id,
            _problem(PlatformErrorCode.FORBIDDEN, "administrator role required", request, correlation_id),
        )
    return actor, correlation_id, None


def _has_role(actor: ActorContext, roles: frozenset[str]) -> bool:
    return bool({r.upper() for r in actor.roles} & roles)


def _pepper() -> bytes:
    return blind_index_pepper(get_settings().secret_key)


# --- routes -------------------------------------------------------------------------------------


@router.post("/tenants", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def post_tenant(body: CreateTenantRequest, request: Request, credentials: OptionalBearer = None) -> Any:
    """Create a school or centre (platform administrators only)."""
    actor, correlation_id, err = _authenticate(request, credentials, PLATFORM_ADMIN_ROLES)
    if err is not None:
        return err
    assert actor is not None
    if not re.fullmatch(SLUG_PATTERN, body.slug):
        return _problem(PlatformErrorCode.VALIDATION_ERROR, "invalid slug", request, correlation_id)
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            tenant = await create_tenant(
                session, slug=body.slug, name=body.name, daily_ai_budget_usd=body.daily_ai_budget_usd
            )
    except TenantConflictError as exc:
        return _problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id, status_override=409)
    return TenantResponse(**{f: getattr(tenant, f) for f in TenantResponse.model_fields})


@router.post("/memberships", response_model=CreateMembershipResponse, status_code=status.HTTP_201_CREATED)
async def post_membership(body: CreateMembershipRequest, request: Request, credentials: OptionalBearer = None) -> Any:
    """Add a person to the caller's tenant. A learner under 16 gets no stored PII until consent is recorded."""
    actor, correlation_id, err = _authenticate(request, credentials, TENANT_ADMIN_ROLES)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            await bind_user(session, actor.user_id)
            roles = None if body.roles is None else await require_role_codes(session, body.roles)
            result = await enroll_member(
                session,
                tenant_id=actor.tenant_id,
                email=str(body.email),
                pepper=_pepper(),
                keys=key_service(),
                today=datetime.now(UTC).date(),
                birth_year=body.birth_year,
                display_name=body.display_name,
                phone=body.phone,
                pseudonym_handle=body.pseudonym_handle,
                roles=roles,
                entitlement_tier=body.entitlement_tier,
            )
    except MembershipConflictError as exc:  # a ValueError too: must come first
        return _problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id, status_override=409)
    except (UnknownRoleError, ValueError) as exc:
        return _problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id)
    m = result.membership
    return CreateMembershipResponse(
        membership=MembershipResponse(
            id=m.id,
            tenant_id=m.tenant_id,
            user_id=m.user_id,
            status=m.status,
            roles=list(m.roles),
            entitlement_tier=m.entitlement_tier,
            created_at=m.created_at,
        ),  # fmt: skip
        user_created=result.user_created,
        user_status=result.user_status,
        is_minor=result.is_minor,
        pii_stored=result.pii_stored,
        parental_consent_required=result.user_status == "PENDING_PARENTAL_CONSENT",
    )


@router.post("/privacy/parental-consents", response_model=ParentalConsentResponse, status_code=status.HTTP_201_CREATED)
async def post_parental_consent(
    body: ParentalConsentRequest, request: Request, credentials: OptionalBearer = None
) -> Any:
    """Record a parent's or school's consent for a learner of the caller's tenant."""
    actor, correlation_id, err = _authenticate(request, credentials, TENANT_ADMIN_ROLES)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            await bind_user(session, actor.user_id)
            consent = await record_parental_consent(
                session,
                tenant_id=actor.tenant_id,
                learner_user_id=body.learner_user_id,
                guardian_email=str(body.guardian_email),
                guardian_relationship=body.guardian_relationship,
                verification_method=body.verification_method,
                consent_scope_json=body.consent_scope_json,
                pepper=_pepper(),
                keys=key_service(),
            )
    except PrivacyNotFoundError:
        return _problem(PlatformErrorCode.NOT_FOUND, "learner not found", request, correlation_id)
    except KeyShreddedError as exc:
        return _problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id, status_override=409)
    except ValueError as exc:
        return _problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id)
    return ParentalConsentResponse(**{f: getattr(consent, f) for f in ParentalConsentResponse.model_fields})


@router.post(
    "/privacy/data-erasure-requests", response_model=ErasureCertificateResponse, status_code=status.HTTP_201_CREATED
)
async def post_data_erasure_request(body: ErasureRequest, request: Request, credentials: OptionalBearer = None) -> Any:
    """Crypto-shred a user and return the erasure certificate.

    Anyone may erase themself; a tenant administrator may erase a member who belongs to no other tenant.
    """
    actor, correlation_id, err = _authenticate(request, credentials, None)
    if err is not None:
        return err
    assert actor is not None
    subject = body.subject_user_id or actor.user_id
    if subject != actor.user_id and not _has_role(actor, TENANT_ADMIN_ROLES):
        return _problem(
            PlatformErrorCode.FORBIDDEN, "only administrators may erase another user", request, correlation_id
        )
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            await bind_user(session, actor.user_id)
            certificate = await erase_user(
                session, tenant_id=actor.tenant_id, subject_user_id=subject, keys=key_service()
            )
    except ErasureForbiddenError:
        return _problem(PlatformErrorCode.NOT_FOUND, "user not found", request, correlation_id)
    except PrivacyNotFoundError:
        return _problem(PlatformErrorCode.NOT_FOUND, "user not found", request, correlation_id)
    return ErasureCertificateResponse(**{f: getattr(certificate, f) for f in ErasureCertificateResponse.model_fields})
