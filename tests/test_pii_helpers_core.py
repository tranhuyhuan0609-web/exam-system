"""Unit tests for F04/G02 PII blind index, audit JSON sanitizer & pure erasure (P20)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.modules.privacy.public import (
    REDACTED_TEXT_MARKER,
    compute_email_blind_index,
    sanitize_audit_json,
    scrub_free_text_pii_records,
    verify_email_blind_index,
)

PEPPER = b"super-secret-cluster-pepper-32b!"


def test_email_blind_index_normalizes_nfkc_whitespace_case_and_rejects_short_pepper():
    # Fullwidth Latin NFKC letters + internal/outer whitespace + mixed case
    raw = " \uFF21lice.\uFF2E\uFF47uyen @Example.EDU.vn \n"
    canonical = "alice.nguyen@example.edu.vn"

    d1 = compute_email_blind_index(raw, PEPPER)
    d2 = compute_email_blind_index(canonical, PEPPER)
    assert d1 == d2
    assert len(d1) == 64
    assert verify_email_blind_index(raw, d1, PEPPER) is True
    assert verify_email_blind_index("other@example.edu.vn", d1, PEPPER) is False

    for bad_pepper in (b"", b"short", b"x" * 31):
        with pytest.raises(ValueError, match="32 bytes"):
            compute_email_blind_index(canonical, bad_pepper)
        with pytest.raises(ValueError, match="32 bytes"):
            verify_email_blind_index(canonical, d1, bad_pepper)


def test_audit_sanitizer_handles_nested_lists_key_variants_free_text_and_immutability():
    original: dict[str, Any] = {
        "event": "UPDATE_PROFILE",
        "score": "0.25 in 2026",
        "actors": [
            {"phoneNumber": "0912345678", "role": "STUDENT"},
            {"phone_number": "0987654321", "Phone-Number": "+84901234567"},
            ["nested list note: contact user@ulp.edu.vn or +84 912 345 678 asap"],
        ],
        "nested": {
            "guardianEmail": "parent@example.org",
            "free_text": "Goi so 035-123-4567 hoac 02438251234, email backup_1@test.vn",
        },
    }
    snapshot = copy.deepcopy(original)

    sanitized = sanitize_audit_json(original, PEPPER)

    # Original input must NEVER be mutated.
    assert original == snapshot

    # Key variants matched across camelCase, snake_case, and kebab-case inside lists/dicts.
    actor0, actor1, nested_list = sanitized["actors"]
    assert "phoneNumber" not in actor0
    assert actor0["phoneNumber_redacted_hash"].startswith("hmac:")
    assert actor0["role"] == "STUDENT"

    assert actor1["phone_number_redacted_hash"].startswith("hmac:")
    assert actor1["Phone-Number_redacted_hash"].startswith("hmac:")

    assert "user@ulp.edu.vn" not in nested_list[0]
    assert "912 345 678" not in nested_list[0]
    assert "[REDACTED_EMAIL:" in nested_list[0]
    assert "[REDACTED_PHONE:" in nested_list[0]

    assert "parent@example.org" not in str(sanitized)
    assert sanitized["nested"]["guardianEmail_redacted_hash"].startswith("hmac:")
    note = sanitized["nested"]["free_text"]
    assert "035-123-4567" not in note
    assert "02438251234" not in note
    assert "backup_1@test.vn" not in note
    assert "[REDACTED_PHONE:" in note
    assert "[REDACTED_EMAIL:" in note

    # Innocent numeric text not masked falsely.
    assert sanitized["score"] == "0.25 in 2026"


def test_audit_sanitizer_blocks_cyclic_or_overly_deep_nesting():
    cyclic: dict[str, Any] = {}
    cyclic["self"] = cyclic
    with pytest.raises(ValueError, match="maximum nesting depth"):
        sanitize_audit_json(cyclic, PEPPER, max_depth=8)

    # Deep nested list chain exceeding max_depth
    deep: Any = "ok"
    for _ in range(10):
        deep = [deep]
    with pytest.raises(ValueError, match="maximum nesting depth"):
        sanitize_audit_json({"wrap": deep}, PEPPER, max_depth=5)


def test_scrub_free_text_pii_records_is_pure_and_returns_counts():
    msgs = [
        {"id": "m1", "message_md": "My phone is 0912345678", "citations_json": [{"doc": 1}]},
        {"id": "m2", "message_md": "Email me", "citations_json": [{"doc": 2}]},
    ]
    appeals = [{"id": "a1", "reason_text": "My ID card", "resolution_note": "Called parent"}]
    comments = [
        {"id": "c1", "body_text": "secret 1"},
        {"id": "c2", "body_text": "secret 2"},
        {"id": "c3", "body_text": "secret 3"},
    ]
    msgs_snap = copy.deepcopy(msgs)
    appeals_snap = copy.deepcopy(appeals)
    comments_snap = copy.deepcopy(comments)

    res = scrub_free_text_pii_records(msgs, appeals, comments)

    # Inputs untouched.
    assert msgs == msgs_snap
    assert appeals == appeals_snap
    assert comments == comments_snap

    assert res.counts.tutor_messages == 2
    assert res.counts.appeals == 1
    assert res.counts.comment_threads == 3
    assert res.counts.total == 6
    assert all(
        m["message_md"] == REDACTED_TEXT_MARKER and m["citations_json"] == []
        for m in res.tutor_messages
    )
    assert res.appeals[0]["reason_text"] == REDACTED_TEXT_MARKER
    assert res.appeals[0]["resolution_note"] is None
    assert all(c["body_text"] == REDACTED_TEXT_MARKER for c in res.comment_threads)
