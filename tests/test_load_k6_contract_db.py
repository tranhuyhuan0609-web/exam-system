"""GATE-05 tooling against the real API: what `tests/load/k6_exam_5000ccu.js` sends is accepted, on real rows.

The k6 run itself (5.000 VUs on the target hardware) cannot happen in CI. What can is the contract between the script
and the API: the tokens `seed_attempts.py` signs verify, the hash k6 computes equals the server's, the autosave /
replay / submit calls the script makes answer as its checks expect. A drift here fails a 12-minute run at minute one.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from pathlib import Path

import pytest

from app.config import get_settings
from app.contracts.autosave import compute_payload_hash
from app.ops.seed_demo import DEFAULT_PASSWORD, seed_demo
from app.platform.auth_context import AuthTokenError, decode_and_verify_actor_token
from tests.load.seed_attempts import build_session_records, sign_loadtest_jwt

SECRET = "load-contract-secret-0123456789abcdef"
K6_SCRIPT = (Path(__file__).resolve().parent / "load" / "k6_exam_5000ccu.js").read_text(encoding="utf-8")


def test_tokens_of_the_session_generator_verify_under_the_apis_own_rules():
    now = int(time.time())
    records = build_session_records(count=50, tenant_count=5, signing_secret=SECRET, issued_at=now)
    jtis = set()
    for record in records:
        actor = decode_and_verify_actor_token(record["jwt"], secret=SECRET, now_epoch=now)
        assert str(actor.tenant_id) == record["tenant_id"] and str(actor.membership_id) == record["membership_id"]
        assert actor.roles == {"LEARNER"}
        jtis.add(actor.jti)
    assert len(jtis) == 50, "one jti per VU"
    with pytest.raises(AuthTokenError, match="signature"):
        decode_and_verify_actor_token(records[0]["jwt"], secret=SECRET + "x", now_epoch=now)
    with pytest.raises(AuthTokenError, match="expired"):
        decode_and_verify_actor_token(records[0]["jwt"], secret=SECRET, now_epoch=now + 7200 + 60)


def test_the_hash_k6_computes_is_the_servers_canonical_hash():
    """k6: `crypto.sha256(JSON.stringify({selected_key: X}), "hex")`; JSON.stringify emits no spaces."""
    assert 'crypto.sha256(JSON.stringify(payload), "hex")' in K6_SCRIPT
    assert re.search(r'const payload = \{ selected_key: \["A", "B", "C", "D"\]\[st\.seq % 4\] \};', K6_SCRIPT)
    for key in "ABCD":
        payload = {"selected_key": key}
        js_stringify = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        assert hashlib.sha256(js_stringify.encode()).hexdigest() == compute_payload_hash(payload)


def test_the_script_sends_the_idempotency_key_of_submit_as_a_header():
    submit = K6_SCRIPT.split("export function runSubmitBurst", 1)[1]
    assert '"Idempotency-Key": `sub:${s.attempt_id}`' in submit and "submit_idempotency_key" not in submit


def test_a_k6_vu_autosaves_replays_and_submits_on_a_real_attempt(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "secret_key", SECRET)  # the API verifies with the secret the VUs signed with
    ids = asyncio.run(seed_demo())["entities"]
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "learner@h2edu.demo", "password": DEFAULT_PASSWORD, "tenant_slug": "h2edu-demo"},
    ).json()
    started = client.post(
        "/api/v1/exam-sessions/start",
        headers={"Authorization": f"Bearer {login['access_token']}"},
        json={"assessment_id": ids["assessment_id"]},
    )
    assert started.status_code == 201, started.text
    attempt_id = started.json()["attempt_id"]
    paper = client.get(
        f"/api/v1/attempts/{attempt_id}/paper", headers={"Authorization": f"Bearer {login['access_token']}"}
    ).json()
    question = next(q["question_version_id"] for q in paper["questions"] if q["item_type"] == "SINGLE_CHOICE")

    # exactly the VU's token, signed the way seed_attempts.py signs it, for the real learner
    vu_token = sign_loadtest_jwt(
        sub=login["user_id"],
        tenant_id=login["tenant_id"],
        membership_id=login["membership_id"],
        signing_secret=SECRET,
        issued_at=int(time.time()),
    )
    headers = {"Authorization": f"Bearer {vu_token}"}

    # runAutosaveLoop: sequence 1 at version 0, then the same key again (the 5% replay branch)
    payload = {"selected_key": "B"}
    key = f"{attempt_id}:{question}:seq_1"
    body = {
        "client_sequence": 1,
        "expected_response_version": 0,
        "idempotency_key": key,
        "payload_hash": hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest(),
        "response_payload": payload,
        "is_flagged": False,
    }
    url = f"/api/v1/attempts/{attempt_id}/responses/{question}"
    first = client.put(url, headers={**headers, "Idempotency-Key": key}, json=body)
    assert first.status_code == 200 and first.json()["status"] == "COMMITTED", first.text
    assert isinstance(first.json()["saved_response_version"], int)
    replay = client.put(url, headers={**headers, "Idempotency-Key": key}, json=body)
    assert replay.status_code == 200 and replay.json()["status"] == "IDEMPOTENT_REPLAY", replay.text

    # runSubmitBurst: no body, the key in the header; a second burst call replays the first answer
    submit_headers = {**headers, "Idempotency-Key": f"sub:{attempt_id}"}
    assert len(submit_headers["Idempotency-Key"]) <= 64
    submitted = client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=submit_headers)
    assert submitted.status_code == 200 and submitted.json()["replayed"] is False, submitted.text
    again = client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=submit_headers)
    assert again.status_code == 200 and again.json()["replayed"] is True, again.text
    assert uuid.UUID(attempt_id)
