"""Tests for tests/load/seed_attempts.py 5,000-VU k6 session generator (P61)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from tests.load.seed_attempts import build_session_records, main, sign_loadtest_jwt

SECRET = "unit-test-secret-0123456789"


def _decode_jwt_payload(token: str) -> dict:
    parts = token.split(".")
    assert len(parts) == 3
    padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))


def test_build_5000_unique_vu_sessions_matching_k6_contract() -> None:
    records = build_session_records(count=5000, tenant_count=20, issued_at=1794570000, signing_secret=SECRET)
    assert len(records) == 5000

    attempt_ids = {r["attempt_id"] for r in records}
    assert len(attempt_ids) == 5000

    sample = records[0]
    assert set(sample.keys()) >= {
        "attempt_id",
        "tenant_id",
        "membership_id",
        "jwt",
        "question_version_ids",
    }
    assert len(sample["question_version_ids"]) == 22

    claims = _decode_jwt_payload(sample["jwt"])
    assert claims["tenant_id"] == sample["tenant_id"]
    assert claims["membership_id"] == sample["membership_id"]
    assert claims["role"] == "STUDENT"
    assert claims["exp"] > claims["iat"]


def test_cli_writes_sessions_json_to_requested_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOAD_TEST_JWT_SECRET", SECRET)
    out = tmp_path / "sessions.json"
    rc = main(["--count", "25", "--tenants", "5", "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data) == 25
    assert len({row["attempt_id"] for row in data}) == 25
    assert len({row["tenant_id"] for row in data}) == 5


def test_missing_secret_env_fails_with_a_clear_message_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Regression: a hard-coded default secret was used silently when the variable was unset.
    monkeypatch.delenv("LOAD_TEST_JWT_SECRET", raising=False)
    out = tmp_path / "sessions.json"
    assert main(["--count", "3", "--out", str(out)]) == 2
    assert "LOAD_TEST_JWT_SECRET" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize("value", ["", "   ", "short", "dev-load-test-secret-do-not-use-in-prod"])
def test_empty_short_or_retired_default_secret_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("LOAD_TEST_JWT_SECRET", value)
    out = tmp_path / "sessions.json"
    assert main(["--count", "3", "--out", str(out)]) == 2
    assert not out.exists()


def test_build_session_records_has_no_default_secret() -> None:
    with pytest.raises(TypeError):
        build_session_records(count=1)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        build_session_records(count=1, signing_secret=None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"count": 0},
        {"count": -1},
        {"count": True},
        {"count": 1.5},
        {"count": 10**9},
        {"tenant_count": 0},
        {"questions_per_attempt": 0},
    ],
)
def test_invalid_sizes_are_rejected(kwargs: dict) -> None:
    base = {"count": 2, "signing_secret": SECRET}
    with pytest.raises(ValueError):
        build_session_records(**{**base, **kwargs})


def test_records_are_deterministic_and_tokens_depend_on_the_secret() -> None:
    a = build_session_records(count=3, signing_secret=SECRET, issued_at=1)
    b = build_session_records(count=3, signing_secret=SECRET, issued_at=1)
    c = build_session_records(count=3, signing_secret=SECRET + "x", issued_at=1)
    assert a == b
    assert a[0]["attempt_id"] == c[0]["attempt_id"] and a[0]["jwt"] != c[0]["jwt"]


def test_jwt_signature_verifies_with_the_secret_only() -> None:
    import hashlib
    import hmac

    token = build_session_records(count=1, signing_secret=SECRET, issued_at=1)[0]["jwt"]
    header, payload, sig = token.split(".")
    expected = hmac.new(SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)) == expected


@pytest.mark.parametrize("ttl", [0, -5, True, 1.5])
def test_jwt_ttl_must_be_a_positive_integer(ttl: object) -> None:
    with pytest.raises(ValueError):
        sign_loadtest_jwt(
            sub="s", tenant_id="t", membership_id="m", signing_secret=SECRET, issued_at=1, ttl_seconds=ttl
        )  # type: ignore[arg-type]


def test_generated_files_and_k6_script_do_not_leak_defaults() -> None:
    load_dir = Path(__file__).resolve().parent / "load"
    ignore = (load_dir / ".gitignore").read_text(encoding="utf-8").split()
    assert "data/" in ignore and "sessions.json" in ignore
    k6 = (load_dir / "k6_exam_5000ccu.js").read_text(encoding="utf-8")
    assert "staging-api" not in k6 and "https://" not in k6.split("API_BASE_URL is required")[0]
    assert (
        "dev-load-test-secret"
        not in (load_dir / "seed_attempts.py")
        .read_text(encoding="utf-8")
        .split("_RETIRED_DEFAULT_SECRET = ", 1)[1]
        .split("\n", 1)[1]
    )
