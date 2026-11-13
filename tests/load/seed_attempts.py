"""Synthetic 5,000-VU exam session generator for tests/load/k6_exam_5000ccu.js (GATE-05).

Generates deterministic per-VU attempt payloads matching the fields consumed
by ``k6_exam_5000ccu.js`` (``attempt_id``, ``tenant_id``, ``membership_id``,
``jwt``, ``question_version_ids``) without hitting a live database or embedding
production secrets.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

DEFAULT_QUESTIONS_PER_ATTEMPT = 22  # THPTQG 2025+ Math exam size
SECRET_ENV_VAR = "LOAD_TEST_JWT_SECRET"
MIN_SECRET_LENGTH = 16
MAX_COUNT = 100_000
# The secret that used to be the silent default. Refuse it so it can never come back by accident.
_RETIRED_DEFAULT_SECRET = "dev-load-test-secret-do-not-use-in-prod"
_NAMESPACE = uuid.UUID("c05c0600-0000-4000-8000-000000000001")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def require_signing_secret(secret: object) -> str:
    """Fail closed on a missing, too short or retired-default signing secret."""
    if not isinstance(secret, str) or not secret.strip():
        raise ValueError(f"{SECRET_ENV_VAR} is required: export a signing secret for the load-test environment")
    if len(secret) < MIN_SECRET_LENGTH:
        raise ValueError(f"signing_secret must be at least {MIN_SECRET_LENGTH} characters")
    if secret == _RETIRED_DEFAULT_SECRET:
        raise ValueError("the retired built-in default secret must not be used")
    return secret


def sign_loadtest_jwt(
    *,
    sub: str,
    tenant_id: str,
    membership_id: str,
    signing_secret: str,
    issued_at: int,
    ttl_seconds: int = 7200,
) -> str:
    """Sign a compact HS256 JWT token for staging/load-test VUs using stdlib only."""
    require_signing_secret(signing_secret)
    if not all(isinstance(v, str) and v for v in (sub, tenant_id, membership_id)):
        raise ValueError("sub, tenant_id and membership_id must be non-empty strings")
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be a positive integer")
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    payload = _b64url(
        json.dumps(
            {
                "sub": sub,
                "tenant_id": tenant_id,
                "membership_id": membership_id,
                "role": "STUDENT",
                "iat": issued_at,
                "exp": issued_at + ttl_seconds,
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    sig = hmac.new(signing_secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(sig)}"


def build_session_records(
    *,
    count: int = 5000,
    tenant_count: int = 10,
    questions_per_attempt: int = DEFAULT_QUESTIONS_PER_ATTEMPT,
    seed: int = 20261113,
    signing_secret: str,
    issued_at: int | None = None,
) -> list[dict[str, Any]]:
    """Build ``count`` independent per-VU attempt records for k6 SharedArray."""
    require_signing_secret(signing_secret)
    for name, value in (
        ("count", count),
        ("tenant_count", tenant_count),
        ("questions_per_attempt", questions_per_attempt),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if count > MAX_COUNT:
        raise ValueError(f"count must not exceed {MAX_COUNT}")

    now = int(time.time()) if issued_at is None else issued_at
    tenant_ids = [str(uuid.uuid5(_NAMESPACE, f"{seed}:tenant:{t}")) for t in range(tenant_count)]
    question_pool = [
        str(uuid.uuid5(_NAMESPACE, f"{seed}:qver:{q}")) for q in range(questions_per_attempt)
    ]

    records: list[dict[str, Any]] = []
    for idx in range(count):
        tenant_id = tenant_ids[idx % tenant_count]
        attempt_id = str(uuid.uuid5(_NAMESPACE, f"{seed}:attempt:{idx}"))
        actor_id = str(uuid.uuid5(_NAMESPACE, f"{seed}:user:{idx}"))
        membership_id = str(uuid.uuid5(_NAMESPACE, f"{seed}:membership:{idx}"))
        jwt_token = sign_loadtest_jwt(
            sub=actor_id,
            tenant_id=tenant_id,
            membership_id=membership_id,
            signing_secret=signing_secret,
            issued_at=now,
        )
        records.append(
            {
                "vu_index": idx + 1,
                "attempt_id": attempt_id,
                "tenant_id": tenant_id,
                "membership_id": membership_id,
                "jwt": jwt_token,
                "question_version_ids": question_pool,
            }
        )
    return records


def write_sessions_file(out_path: Path, records: list[dict[str, Any]]) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    try:
        out_path.chmod(0o600)  # the file holds live signed tokens
    except OSError:
        pass
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed per-VU sessions JSON for k6 5000 CCU load tests.")
    parser.add_argument("--count", type=int, default=5000)
    parser.add_argument("--tenants", type=int, default=10)
    parser.add_argument("--questions-per-attempt", type=int, default=DEFAULT_QUESTIONS_PER_ATTEMPT)
    parser.add_argument("--seed", type=int, default=20261113)
    parser.add_argument("--out", type=Path, default=Path("tests/load/data/sessions.json"))
    args = parser.parse_args(argv)

    try:
        secret = require_signing_secret(os.environ.get(SECRET_ENV_VAR))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    records = build_session_records(
        count=args.count,
        tenant_count=args.tenants,
        questions_per_attempt=args.questions_per_attempt,
        seed=args.seed,
        signing_secret=secret,
    )
    target = write_sessions_file(args.out, records)
    print(f"Wrote {len(records)} unique per-VU sessions to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
