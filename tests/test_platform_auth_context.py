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


# --- regression + boundary tests --------------------------------------------------------------

import hashlib  # noqa: E402
import hmac  # noqa: E402

_UID, _TID, _MID = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _mint(**kw) -> str:
    args = dict(
        secret=_SECRET,
        user_id=_UID,
        tenant_id=_TID,
        membership_id=_MID,
        roles=["STUDENT"],
        jti="jti-1",
        issued_at=_NOW,
        ttl_seconds=900,
    )
    args.update(kw)
    return mint_hs256_token(**args)


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).rstrip(b"=").decode()


def _forge(payload_updates: dict, *, drop: tuple[str, ...] = (), header=None) -> str:
    """A correctly signed token with attacker-chosen claims (simulates a buggy issuer)."""
    base = {
        "sub": str(_UID),
        "tenant_id": str(_TID),
        "membership_id": str(_MID),
        "roles": ["STUDENT"],
        "jti": "j",
        "iss": "ulp-auth",
        "aud": "ulp-api",
        "iat": _NOW,
        "nbf": _NOW,
        "exp": _NOW + 900,
    }
    base.update(payload_updates)
    for key in drop:
        base.pop(key, None)
    h = _b64(header or {"alg": "HS256", "typ": "JWT"})
    p = _b64(base)
    sig = hmac.new(_SECRET.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"


def _decode(token, **kw):
    return decode_and_verify_actor_token(token, secret=_SECRET, now_epoch=kw.pop("now_epoch", _NOW + 1), **kw)


def test_non_integer_iat_raises_auth_error_not_value_error() -> None:
    for bad in ("x", 1.5, None, True, [1]):
        with pytest.raises(AuthTokenError):
            _decode(_forge({"iat": bad}))


def test_iat_in_the_future_is_rejected() -> None:
    with pytest.raises(AuthTokenError, match="iat"):
        _decode(_forge({"iat": _NOW + 10_000, "nbf": _NOW}))


def test_expiry_boundary_with_leeway() -> None:
    token = _mint(ttl_seconds=60)  # exp = NOW + 60
    _decode(token, now_epoch=_NOW + 60 + 14)  # inside leeway 15
    with pytest.raises(AuthTokenError, match="expired"):
        _decode(token, now_epoch=_NOW + 60 + 15)


def test_not_yet_valid_boundary() -> None:
    token = _mint(issued_at=_NOW + 1000)
    with pytest.raises(AuthTokenError):
        _decode(token, now_epoch=_NOW)
    _decode(token, now_epoch=_NOW + 1000 - 15)  # nbf - leeway is accepted


def test_wrong_audience_issuer_and_list_audience() -> None:
    with pytest.raises(AuthTokenError, match="audience"):
        _decode(_mint(audience="other-api"))
    with pytest.raises(AuthTokenError, match="issuer"):
        _decode(_mint(issuer="evil"))
    assert _decode(_forge({"aud": ["x", "ulp-api"]})).tenant_id == _TID
    with pytest.raises(AuthTokenError, match="audience"):
        _decode(_forge({"aud": ["x"]}))
    with pytest.raises(AuthTokenError, match="audience"):
        _decode(_forge({"aud": None}))


@pytest.mark.parametrize("alg", ["none", "HS512", "RS256", "hs256", None, 5])
def test_any_algorithm_other_than_hs256_is_rejected(alg) -> None:
    with pytest.raises(AuthTokenError, match="HS256"):
        _decode(_forge({}, header={"alg": alg}))


def test_tampered_payload_and_wrong_secret_fail_signature() -> None:
    token = _mint()
    h, p, s = token.split(".")
    evil = _b64({**json.loads(base64.urlsafe_b64decode(p + "==")), "tenant_id": str(uuid.uuid4())})
    with pytest.raises(AuthTokenError, match="signature"):
        _decode(f"{h}.{evil}.{s}")
    with pytest.raises(AuthTokenError, match="signature"):
        decode_and_verify_actor_token(token, secret="another-secret-key-another-secret", now_epoch=_NOW)


def test_signature_with_trailing_junk_or_padding_is_rejected() -> None:
    token = _mint()
    for suffix in ("!!!!", "=", "AA", "é", " x"):
        with pytest.raises(AuthTokenError):
            _decode(token + suffix)


def test_malformed_tokens_raise_auth_error() -> None:
    for bad in ("", "   ", "a.b", "a.b.c.d", "....", "é.é.é", "a" * 9000, None, 123, b"x.y.z"):
        with pytest.raises(AuthTokenError):
            _decode(bad)  # type: ignore[arg-type]


def test_missing_or_empty_secret_and_bad_clock_inputs_fail_closed() -> None:
    token = _mint()
    for secret in ("", None):
        with pytest.raises(AuthTokenError):
            decode_and_verify_actor_token(token, secret=secret, now_epoch=_NOW)  # type: ignore[arg-type]
    with pytest.raises(AuthTokenError):
        decode_and_verify_actor_token(token, secret=_SECRET, now_epoch="x")  # type: ignore[arg-type]
    with pytest.raises(AuthTokenError):
        decode_and_verify_actor_token(token, secret=_SECRET, now_epoch=_NOW, leeway_seconds=-1)
    with pytest.raises(AuthTokenError):
        mint_hs256_token(secret="", user_id=_UID, tenant_id=_TID, membership_id=_MID, roles=[], issued_at=_NOW)
    with pytest.raises(AuthTokenError):
        _mint(ttl_seconds=0)


def test_missing_claims_are_rejected() -> None:
    for claim in ("jti", "sub", "tenant_id", "membership_id", "exp", "iss", "aud"):
        with pytest.raises(AuthTokenError):
            _decode(_forge({}, drop=(claim,)))
    with pytest.raises(AuthTokenError):
        _decode(_forge({"exp": "9999999999"}))
    with pytest.raises(AuthTokenError):
        _decode(_forge({"sub": "not-a-uuid"}))
    with pytest.raises(AuthTokenError):
        _decode(_forge({"roles": "ADMIN"}))
    with pytest.raises(AuthTokenError):
        _decode(_forge({"roles": [{"a": 1}]}))


def test_revocation_message_does_not_echo_the_jti() -> None:
    with pytest.raises(AuthTokenError) as info:
        _decode(_mint(jti="secret-jti-value"), revoked_jtis=frozenset({"secret-jti-value"}))
    assert "secret-jti-value" not in str(info.value)


def test_bearer_header_parsing_is_strict() -> None:
    assert extract_bearer_token("bearer abc") == "abc"
    for bad in (None, "", "Bearer", "Bearer a b", "Basic abc", "Token abc", "Bearer " + "x" * 9000, 5):
        with pytest.raises(AuthTokenError):
            extract_bearer_token(bad)  # type: ignore[arg-type]


def test_spoofing_guard_boundaries() -> None:
    actor = _decode(_mint())
    assert guard_no_tenant_spoofing(actor, claimed_tenant_header=None, claimed_membership_header="  ") is actor
    assert guard_no_tenant_spoofing(actor, claimed_tenant_header=str(_TID).upper()) is actor
    with pytest.raises(TenantSpoofingError, match="malformed"):
        guard_no_tenant_spoofing(actor, claimed_tenant_header="not-a-uuid")
    with pytest.raises(TenantSpoofingError, match="X-Membership-Id"):
        guard_no_tenant_spoofing(actor, claimed_membership_header=str(uuid.uuid4()))
    with pytest.raises(TenantSpoofingError, match="malformed"):
        guard_no_tenant_spoofing(actor, claimed_membership_header="zzz")
    with pytest.raises(TenantSpoofingError):
        guard_no_tenant_spoofing(actor, claimed_tenant_header=123)  # type: ignore[arg-type]
