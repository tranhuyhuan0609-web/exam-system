"""Unit tests for D05 error_notes remediation state machine (P11)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.modules.error_notebook.public import (
    REQUIRED_CONSECUTIVE_CORRECT,
    ErrorNoteState,
    apply_attempt,
)


def _ts(hour_offset: int) -> datetime:
    return datetime(2026, 11, 13, 8, 0, tzinfo=UTC) + timedelta(hours=hour_offset)


def test_requires_two_consecutive_correct_attempts_to_resolve():
    assert REQUIRED_CONSECUTIVE_CORRECT == 2
    t0 = _ts(0)
    state = ErrorNoteState(last_wrong_at=t0, status="OPEN", wrong_count=1, consecutive_correct_count=0)

    # First correct answer moves OPEN -> IN_REMEDIATION (not RESOLVED yet).
    s1 = apply_attempt(state, is_correct=True, at=_ts(1))
    assert s1.status == "IN_REMEDIATION"
    assert s1.consecutive_correct_count == 1
    assert s1.wrong_count == 1
    assert s1.resolved_at is None

    # Second consecutive correct answer resolves the error note.
    s2 = apply_attempt(s1, is_correct=True, at=_ts(2))
    assert s2.status == "RESOLVED"
    assert s2.consecutive_correct_count == 2
    assert s2.wrong_count == 1
    assert s2.resolved_at == _ts(2)


def test_wrong_attempt_resets_streak_and_reopens_resolved_note():
    state = ErrorNoteState(last_wrong_at=_ts(0), status="OPEN", wrong_count=1, consecutive_correct_count=0)

    # 1 correct -> wrong resets streak to 0.
    s1 = apply_attempt(state, is_correct=True, at=_ts(1))
    assert s1.status == "IN_REMEDIATION"
    s2 = apply_attempt(s1, is_correct=False, at=_ts(2))
    assert s2.status == "IN_REMEDIATION"
    assert s2.consecutive_correct_count == 0
    assert s2.wrong_count == 2
    assert s2.last_wrong_at == _ts(2)
    assert s2.resolved_at is None

    # Must get 2 fresh consecutive correct attempts to resolve.
    s3 = apply_attempt(s2, is_correct=True, at=_ts(3))
    assert s3.status == "IN_REMEDIATION"
    s4 = apply_attempt(s3, is_correct=True, at=_ts(4))
    assert s4.status == "RESOLVED"
    assert s4.resolved_at == _ts(4)

    # Subsequent correct attempt stays RESOLVED and keeps original resolved_at.
    s5 = apply_attempt(s4, is_correct=True, at=_ts(5))
    assert s5.status == "RESOLVED"
    assert s5.consecutive_correct_count == 3
    assert s5.resolved_at == _ts(4)

    # A single wrong answer while RESOLVED reopens to OPEN cleanly.
    s6 = apply_attempt(s5, is_correct=False, at=_ts(6))
    assert s6.status == "OPEN"
    assert s6.wrong_count == 3
    assert s6.consecutive_correct_count == 0
    assert s6.last_wrong_at == _ts(6)
    assert s6.resolved_at is None


def test_rejects_negative_counts():
    with pytest.raises(ValueError):
        apply_attempt(ErrorNoteState(last_wrong_at=_ts(0), wrong_count=-1), is_correct=True, at=_ts(1))


def test_rejects_naive_timestamp_and_out_of_order_attempt():
    state = ErrorNoteState(last_wrong_at=_ts(2), status="OPEN", wrong_count=1, consecutive_correct_count=0)

    naive_at = datetime(2026, 11, 13, 12, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        apply_attempt(state, is_correct=True, at=naive_at)

    earlier_at = _ts(1)
    with pytest.raises(ValueError, match="earlier"):
        apply_attempt(state, is_correct=True, at=earlier_at)
    with pytest.raises(ValueError, match="earlier"):
        apply_attempt(state, is_correct=False, at=earlier_at)

    # Exact timestamp equal to last_wrong_at is accepted.
    same_ts = apply_attempt(state, is_correct=True, at=_ts(2))
    assert same_ts.status == "IN_REMEDIATION"
