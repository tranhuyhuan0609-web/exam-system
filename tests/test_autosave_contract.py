"""H-04 autosave contract and pure decision function (no DB, no conftest)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.contracts.autosave import (
    AutosaveRequestContract,
    canonical_json,
    compute_payload_hash,
)
from app.modules.attempts.public import (
    AttemptState,
    ResponseState,
    StoredAutosave,
    process_autosave_idempotent_first,
)

NOW = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
PAYLOAD = {"selected_key": "B", "ghi_chu": "Đáp án Việt"}
HASH = compute_payload_hash(PAYLOAD)
KEY = "idem-key-0001"


def run(**over):
    args = dict(
        idempotency_key=KEY,
        client_sequence=5,
        expected_response_version=2,
        payload_hash=HASH,
        response_payload=PAYLOAD,
        attempt=AttemptState("IN_PROGRESS", NOW + timedelta(minutes=30)),
        response=ResponseState(current_revision_no=2, current_sequence=4),
        registry_entry=None,
        now_utc=NOW,
    )
    args.update(over)
    return process_autosave_idempotent_first(**args)


def stored(h=HASH):
    return StoredAutosave(payload_hash=h, client_sequence=3, revision_no=2, recorded_at=NOW)


def test_committed():
    r = run()
    assert (r.http_status, r.code) == (200, "COMMITTED")
    assert r.saved_response_version == 3
    assert r.committed_sequence == 5
    assert r.server_timestamp == NOW


def test_first_save_version_zero_commits_to_one():
    r = run(expected_response_version=0, client_sequence=1, response=ResponseState())
    assert (r.http_status, r.code) == (200, "COMMITTED")
    assert r.saved_response_version == 1


def test_idempotent_replay():
    r = run(registry_entry=stored())
    assert (r.http_status, r.code) == (200, "IDEMPOTENT_REPLAY")
    assert r.committed_sequence == 3
    assert r.saved_response_version == 2
    assert r.server_timestamp == NOW


def test_replay_different_hash_422():
    r = run(registry_entry=stored("0" * 64))
    assert (r.http_status, r.code) == (422, "PAYLOAD_HASH_MISMATCH")


def test_replay_wins_over_submitted_and_expired():
    attempt = AttemptState("SUBMITTED", NOW - timedelta(hours=1))
    r = run(registry_entry=stored(), attempt=attempt)
    assert (r.http_status, r.code) == (200, "IDEMPOTENT_REPLAY")


def test_client_hash_must_match_payload():
    r = run(payload_hash="f" * 64)
    assert (r.http_status, r.code) == (422, "PAYLOAD_HASH_MISMATCH")


def test_attempt_not_in_progress():
    r = run(attempt=AttemptState("SUBMITTED", NOW + timedelta(minutes=5)))
    assert (r.http_status, r.code) == (409, "ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED")
    assert r.attempt_status == "SUBMITTED"


def test_deadline_expired_beyond_grace():
    attempt = AttemptState("IN_PROGRESS", NOW - timedelta(seconds=6))
    r = run(attempt=attempt)
    assert (r.http_status, r.code) == (403, "ATTEMPT_DEADLINE_EXPIRED")


def test_deadline_within_grace_still_commits():
    attempt = AttemptState("IN_PROGRESS", NOW - timedelta(seconds=5))
    assert run(attempt=attempt).code == "COMMITTED"


def test_version_conflict():
    r = run(expected_response_version=1)
    assert (r.http_status, r.code) == (409, "OPTIMISTIC_VERSION_CONFLICT")
    assert r.current_response_version == 2
    assert r.current_sequence == 4


@pytest.mark.parametrize("seq", [4, 1])
def test_stale_sequence(seq):
    r = run(client_sequence=seq)
    assert (r.http_status, r.code) == (409, "STALE_CLIENT_SEQUENCE")
    assert r.current_sequence == 4


def test_function_requires_expected_response_version():
    with pytest.raises(TypeError):
        process_autosave_idempotent_first(  # type: ignore[call-arg]
            idempotency_key=KEY,
            client_sequence=1,
            payload_hash=HASH,
            response_payload=PAYLOAD,
            attempt=AttemptState("IN_PROGRESS", NOW),
            response=ResponseState(),
            registry_entry=None,
        )


def _body(**over):
    body = {
        "client_sequence": 1,
        "expected_response_version": 0,
        "idempotency_key": KEY,
        "payload_hash": HASH,
        "response_payload": PAYLOAD,
    }
    body.update(over)
    return body


def test_request_valid():
    req = AutosaveRequestContract(**_body())
    assert req.expected_response_version == 0
    assert req.is_flagged is False


def test_request_missing_expected_version():
    body = _body()
    del body["expected_response_version"]
    with pytest.raises(ValidationError):
        AutosaveRequestContract(**body)


@pytest.mark.parametrize("bad", [None, -1])
def test_request_bad_expected_version(bad):
    with pytest.raises(ValidationError):
        AutosaveRequestContract(**_body(expected_response_version=bad))


def test_request_extra_field_forbidden():
    with pytest.raises(ValidationError):
        AutosaveRequestContract(**_body(surprise=1))


def test_canonical_json_key_order_independent():
    a = {"b": 1, "a": {"y": [1, 2], "x": "z"}}
    b = {"a": {"x": "z", "y": [1, 2]}, "b": 1}
    assert canonical_json(a) == canonical_json(b)
    assert canonical_json(a) == '{"a":{"x":"z","y":[1,2]},"b":1}'


def test_payload_hash_matches_reference():
    payload = {"sub_answers": {"b": True, "a": False}, "text": "Đáp án: -3,5 m/s²", "n": [1, None]}
    ref = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert "Đ" in ref
    assert compute_payload_hash(payload) == hashlib.sha256(ref.encode("utf-8")).hexdigest()
