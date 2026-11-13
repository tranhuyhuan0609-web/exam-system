"""Tests for app.platform.auth_context signed JWT & anti-spoofing kernel."""

from __future__ import annotations

import base64
import json
import uuid

import pytest

from app.platform.auth_context import (
    AuthTokenError,
    TenantSpoofingError,
    decode_and_verify_actor_token,
    extract_bearer_token,
    guard_no_tenant_spoofing,
    mint_hs256_token,
)

_SECRET = "super-secret-signing-key-32-bytes"
_NOW = 1_794_000_000


def test_mint_decode_and_anti_spoofing_success() -> None:
    uid = uuid.uuid4()
    tid = uuid.uuid4()
    mid = uuid.uuid4()

    token = mint_hs256_token(
        secret=_SECRET,
        user_id=uid,
        tenant_id=tid,
        membership_id=mid,
        roles=["TEACHER", "REVIEWER"],
        entitlement_tier="TEACHER_PRO",
        jti="jti-1",
        issued_at=_NOW,
        ttl_seconds=900,
    )
    extracted = extract_bearer_token(f"Bearer {token}")
    actor = decode_and_verify_actor_token(extracted, secret=_SECRET, now_epoch=_NOW + 10)
    assert actor.user_id == uid
    assert actor.tenant_id == tid
    assert actor.membership_id == mid
    assert actor.roles == frozenset({"TEACHER", "REVIEWER"})
    assert actor.entitlement_tier == "TEACHER_PRO"

    # Matching optional header passes; mismatched header fails closed
    assert guard_no_tenant_spoofing(actor, claimed_tenant_header=str(tid)) == actor
    with pytest.raises(TenantSpoofingError, match="X-Tenant-Id"):
        guard_no_tenant_spoofing(actor, claimed_tenant_header=str(uuid.uuid4()))


def test_rejects_alg_none_tampered_payload_expired_and_revoked_jti() -> None:
    uid = uuid.uuid4()
    tid = uuid.uuid4()
    mid = uuid.uuid4()
    token = mint_hs256_token(
        secret=_SECRET,
        user_id=uid,
        tenant_id=tid,
        membership_id=mid,
        roles=["STUDENT"],
        jti="jti-revoked",
        issued_at=_NOW,
        ttl_seconds=60,
    )

    # alg=none forgery
    _, payload_seg, _ = token.split(".")
    none_hdr = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
    with pytest.raises(AuthTokenError, match="HS256"):
        decode_and_verify_actor_token(f"{none_hdr}.{payload_seg}.", secret=_SECRET, now_epoch=_NOW)

    # Expired after leeway
    with pytest.raises(AuthTokenError, match="expired"):
        decode_and_verify_actor_token(token, secret=_SECRET, now_epoch=_NOW + 100)

    # Revoked jti
    with pytest.raises(AuthTokenError, match="revoked"):
        decode_and_verify_actor_token(
            token,
            secret=_SECRET,
            now_epoch=_NOW + 5,
            revoked_jtis=frozenset({"jti-revoked"}),
        )
