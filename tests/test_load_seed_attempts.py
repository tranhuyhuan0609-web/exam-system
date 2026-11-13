"""Tests for tests/load/seed_attempts.py 5,000-VU k6 session generator (P61)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from tests.load.seed_attempts import build_session_records, main


def _decode_jwt_payload(token: str) -> dict:
    parts = token.split(".")
    assert len(parts) == 3
    padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))


def test_build_5000_unique_vu_sessions_matching_k6_contract() -> None:
    records = build_session_records(count=5000, tenant_count=20, issued_at=1794570000)
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


def test_cli_writes_sessions_json_to_requested_path(tmp_path: Path) -> None:
    out = tmp_path / "sessions.json"
    rc = main(["--count", "25", "--tenants", "5", "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data) == 25
    assert len({row["attempt_id"] for row in data}) == 25
    assert len({row["tenant_id"] for row in data}) == 5
