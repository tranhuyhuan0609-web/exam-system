"""Platform JWT verification and anti-spoofing ActorContext extraction (V11.3 kernel).

Stdlib only; enforces that `tenant_id` and `membership_id` always originate from signed JWT claims
and never from untrusted client headers or body overrides.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass


class AuthTokenError(ValueError):
    """Raised when a bearer token or JWT signature/claim fails validation."""


class TenantSpoofingError(PermissionError):
    """Raised when untrusted client headers conflict with signed JWT tenant/membership claims."""


@dataclass(frozen=True)
class ActorContext:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    roles: frozenset[str]
    entitlement_tier: str
    session_id: str
    jti: str
    issued_at: int
    expires_at: int


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    if not segment:
        raise AuthTokenError("empty base64url segment")
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except Exception as exc:
        raise AuthTokenError("malformed base64url segment") from exc


def extract_bearer_token(authorization_header: str | None) -> str:
    """Strictly parse `Authorization: Bearer <token>` header."""
    if not authorization_header:
        raise AuthTokenError("missing Authorization header")
    parts = authorization_header.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise AuthTokenError("Authorization header must use Bearer scheme")
    return parts[1]


def mint_hs256_token(
    *,
    secret: str,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    roles: Iterable[str],
    entitlement_tier: str = "FREE",
    session_id: str = "sess-default",
    jti: str | None = None,
    issuer: str = "ulp-auth",
    audience: str = "ulp-api",
    issued_at: int,
    ttl_seconds: int = 900,
) -> str:
    """Mint a deterministic HS256 JWT carrying V11.3 principal + tenant claims."""
    if not secret:
        raise ValueError("JWT secret cannot be empty")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")

    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "membership_id": str(membership_id),
        "roles": sorted({r.strip() for r in roles if r.strip()}),
        "entitlement_tier": entitlement_tier.strip(),
        "sid": session_id.strip(),
        "jti": (jti or str(uuid.uuid4())).strip(),
        "iss": issuer,
        "aud": audience,
        "iat": int(issued_at),
        "nbf": int(issued_at),
        "exp": int(issued_at) + int(ttl_seconds),
    }
    encoded_header = _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    encoded_payload = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_payload}.{_b64url_encode(sig)}"


def decode_and_verify_actor_token(
    token: str,
    *,
    secret: str,
    now_epoch: int,
    expected_issuer: str = "ulp-auth",
    expected_audience: str = "ulp-api",
    leeway_seconds: int = 15,
    revoked_jtis: frozenset[str] = frozenset(),
) -> ActorContext:
    """Verify HS256 JWT signature, algorithm, timestamps, revocation, and required V11.3 claims."""
    if not secret:
        raise ValueError("JWT secret cannot be empty")
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise AuthTokenError("JWT must contain exactly 3 dot-separated segments")

    encoded_header, encoded_payload, encoded_sig = parts
    try:
        header = json.loads(_b64url_decode(encoded_header).decode("utf-8"))
        payload = json.loads(_b64url_decode(encoded_payload).decode("utf-8"))
    except AuthTokenError:
        raise
    except Exception as exc:
        raise AuthTokenError("invalid JSON in JWT header or payload") from exc

    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise AuthTokenError("unsupported or forbidden JWT algorithm (HS256 required)")

    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    expected_sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    actual_sig = _b64url_decode(encoded_sig)
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise AuthTokenError("invalid JWT signature")

    if not isinstance(payload, dict):
        raise AuthTokenError("JWT payload must be a JSON object")
    if payload.get("iss") != expected_issuer:
        raise AuthTokenError("JWT issuer mismatch")
    aud = payload.get("aud")
    if isinstance(aud, list):
        if expected_audience not in aud:
            raise AuthTokenError("JWT audience mismatch")
    elif aud != expected_audience:
        raise AuthTokenError("JWT audience mismatch")

    exp = payload.get("exp")
    nbf = payload.get("nbf")
    iat = payload.get("iat", nbf or 0)
    if not isinstance(exp, int):
        raise AuthTokenError("JWT missing integer exp claim")
    if now_epoch - leeway_seconds >= exp:
        raise AuthTokenError("JWT has expired")
    if nbf is not None:
        if not isinstance(nbf, int):
            raise AuthTokenError("JWT nbf claim must be an integer")
        if now_epoch + leeway_seconds < nbf:
            raise AuthTokenError("JWT is not yet valid (nbf)")

    jti = str(payload.get("jti", "")).strip()
    if not jti:
        raise AuthTokenError("JWT missing jti claim")
    if jti in revoked_jtis:
        raise AuthTokenError(f"JWT jti {jti!r} has been revoked")

    try:
        user_id = uuid.UUID(str(payload["sub"]))
        tenant_id = uuid.UUID(str(payload["tenant_id"]))
        membership_id = uuid.UUID(str(payload["membership_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise AuthTokenError("JWT missing valid UUID sub/tenant_id/membership_id") from exc

    raw_roles = payload.get("roles", [])
    if not isinstance(raw_roles, list):
        raise AuthTokenError("JWT roles claim must be a list")
    roles = frozenset(str(r).strip() for r in raw_roles if str(r).strip())

    tier = str(payload.get("entitlement_tier", "FREE")).strip() or "FREE"
    sid = str(payload.get("sid", "")).strip()

    return ActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        membership_id=membership_id,
        roles=roles,
        entitlement_tier=tier,
        session_id=sid,
        jti=jti,
        issued_at=int(iat),
        expires_at=exp,
    )


def guard_no_tenant_spoofing(
    actor: ActorContext,
    *,
    claimed_tenant_header: str | None = None,
    claimed_membership_header: str | None = None,
) -> ActorContext:
    """Reject any request whose client-supplied tenant/membership header conflicts with signed JWT claims."""
    if claimed_tenant_header is not None and claimed_tenant_header.strip():
        try:
            hdr_tenant = uuid.UUID(claimed_tenant_header.strip())
        except ValueError as exc:
            raise TenantSpoofingError("malformed X-Tenant-Id override header") from exc
        if hdr_tenant != actor.tenant_id:
            raise TenantSpoofingError("client X-Tenant-Id conflicts with signed JWT tenant_id")

    if claimed_membership_header is not None and claimed_membership_header.strip():
        try:
            hdr_member = uuid.UUID(claimed_membership_header.strip())
        except ValueError as exc:
            raise TenantSpoofingError("malformed X-Membership-Id override header") from exc
        if hdr_member != actor.membership_id:
            raise TenantSpoofingError("client X-Membership-Id conflicts with signed JWT membership_id")

    return actor
