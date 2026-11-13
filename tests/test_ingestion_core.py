"""Pure tests for A01 Unified Ingestion Backbone core (P23)."""

from __future__ import annotations

import hashlib
import io
import random
from pathlib import Path

import pytest

from app.modules.ingestion.public import (
    TERMINAL_JOB_STATUSES,
    AcquisitionJobState,
    AcquisitionJobStatus,
    CancelJobEvent,
    CompleteJobEvent,
    FailJobEvent,
    IngestionTransitionError,
    PipelineStageStatus,
    RetryJobEvent,
    StageCompletedEvent,
    StartJobEvent,
    StreamFingerprint,
    apply_job_event,
    compute_stream_fingerprint,
    decide_duplicate_ingest,
    validate_job_transition,
    validate_stage_transition,
)


def test_statuses_match_postgresql_ddl_check_constraints() -> None:
    assert {s.value for s in AcquisitionJobStatus} == {
        "QUEUED",
        "RUNNING",
        "COMPLETED",
        "FAILED",
        "DEAD_LETTER",
        "CANCELLED",
    }
    assert {s.value for s in PipelineStageStatus} == {
        "RUNNING",
        "COMPLETED",
        "FAILED",
        "SKIPPED",
    }
    # Sanity cross-check against architecture DDL chapter when present.
    ddl_path = Path("docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql")
    if ddl_path.exists():
        ddl_text = ddl_path.read_text(encoding="utf-8")
        for s in AcquisitionJobStatus:
            assert f"'{s.value}'" in ddl_text
        for s in PipelineStageStatus:
            assert f"'{s.value}'" in ddl_text


def test_invalid_transitions_raise_typed_errors() -> None:
    with pytest.raises(IngestionTransitionError) as exc:
        validate_job_transition(AcquisitionJobStatus.QUEUED, AcquisitionJobStatus.COMPLETED)
    assert exc.value.code == "INVALID_JOB_TRANSITION"

    with pytest.raises(IngestionTransitionError) as stage_exc:
        validate_stage_transition(PipelineStageStatus.COMPLETED, PipelineStageStatus.FAILED)
    assert stage_exc.value.code == "INVALID_STAGE_TRANSITION"

    # Retry only allowed from FAILED state
    queued = AcquisitionJobState(job_id="job-1")
    with pytest.raises(IngestionTransitionError) as retry_exc:
        apply_job_event(queued, RetryJobEvent(event_id="ev-retry-1"))
    assert retry_exc.value.code == "RETRY_NOT_ALLOWED"

    running = apply_job_event(queued, StartJobEvent(event_id="ev-start", stage_name="UPLOAD_SCAN"))
    with pytest.raises(IngestionTransitionError) as retry_running_exc:
        apply_job_event(running, RetryJobEvent(event_id="ev-retry-2"))
    assert retry_running_exc.value.code == "RETRY_NOT_ALLOWED"


def test_cancel_forbidden_from_all_terminal_states() -> None:
    # 1. COMPLETED
    s = AcquisitionJobState(job_id="j-completed")
    s = apply_job_event(s, StartJobEvent(event_id="e1", stage_name="SCAN"))
    s = apply_job_event(s, StageCompletedEvent(event_id="e2", stage_name="SCAN"))
    s = apply_job_event(s, CompleteJobEvent(event_id="e3"))
    assert s.status == AcquisitionJobStatus.COMPLETED

    # 2. CANCELLED
    c = apply_job_event(AcquisitionJobState(job_id="j-cancel"), CancelJobEvent(event_id="c1"))
    assert c.status == AcquisitionJobStatus.CANCELLED

    # 3. DEAD_LETTER
    d = AcquisitionJobState(job_id="j-dlq", max_attempts=1)
    d = apply_job_event(d, StartJobEvent(event_id="d1", stage_name="OCR"))
    d = apply_job_event(d, FailJobEvent(event_id="d2", stage_name="OCR", error_code="OCR_TIMEOUT"))
    assert d.status == AcquisitionJobStatus.DEAD_LETTER

    for idx, terminal in enumerate((s, c, d)):
        assert terminal.status in TERMINAL_JOB_STATUSES
        with pytest.raises(IngestionTransitionError) as exc:
            apply_job_event(terminal, CancelJobEvent(event_id=f"cancel-{idx}"))
        assert exc.value.code == "CANCEL_FROM_TERMINAL_STATE"


def test_exceeding_max_attempts_routes_to_dead_letter_and_preserves_completed_stages() -> None:
    state = AcquisitionJobState(job_id="job-dlq", max_attempts=2)
    state = apply_job_event(state, StartJobEvent(event_id="e1", stage_name="CHECKSUM"))
    assert state.attempt_count == 1

    state = apply_job_event(
        state,
        StageCompletedEvent(event_id="e2", stage_name="CHECKSUM", next_stage="OCR"),
    )
    state = apply_job_event(
        state,
        FailJobEvent(event_id="e3", stage_name="OCR", error_code="E_OCR", error_detail="page 2 corrupt"),
    )
    assert state.status == AcquisitionJobStatus.FAILED
    assert state.stage_map()["CHECKSUM"] == PipelineStageStatus.COMPLETED
    assert state.stage_map()["OCR"] == PipelineStageStatus.FAILED

    # Retry once (attempt 2 == max_attempts): completed CHECKSUM stays completed!
    state = apply_job_event(state, RetryJobEvent(event_id="e4"))
    assert state.status == AcquisitionJobStatus.RUNNING
    assert state.attempt_count == 2
    assert state.stage_map()["CHECKSUM"] == PipelineStageStatus.COMPLETED
    assert state.stage_map()["OCR"] == PipelineStageStatus.RUNNING

    # Failing again at max_attempts moves directly to DEAD_LETTER
    state = apply_job_event(
        state,
        FailJobEvent(event_id="e5", stage_name="OCR", error_code="E_OCR_FATAL", error_detail="oom"),
    )
    assert state.status == AcquisitionJobStatus.DEAD_LETTER
    assert state.dead_letter is not None
    assert state.dead_letter.error_code == "E_OCR_FATAL"
    assert state.dead_letter.failed_stage == "OCR"
    assert state.dead_letter.attempt_count == 2


def test_event_application_idempotent_across_50_seeded_random_scenarios() -> None:
    stage_names = ("CHECKSUM", "CLAMAV", "OCR", "EXTRACT")
    for seed in range(50):
        rng = random.Random(seed)
        max_attempts = rng.randint(1, 5)
        state = AcquisitionJobState(job_id=f"rand-job-{seed}", max_attempts=max_attempts)

        seq = 0
        start_ev = StartJobEvent(event_id=f"s-{seed}-{seq}", stage_name=stage_names[0])
        state = apply_job_event(state, start_ev)
        # Re-applying the exact start event changes nothing
        assert apply_job_event(state, start_ev) == state

        stage_idx = 0
        for step in range(rng.randint(3, 18)):
            if state.status in TERMINAL_JOB_STATUSES:
                break
            seq += 1
            ev_id = f"ev-{seed}-{step}-{seq}"
            if state.status == AcquisitionJobStatus.RUNNING:
                choice = rng.choice(["complete_stage", "complete_stage", "fail", "finish"])
                if choice == "complete_stage" and stage_idx + 1 < len(stage_names):
                    ev = StageCompletedEvent(
                        event_id=ev_id,
                        stage_name=stage_names[stage_idx],
                        next_stage=stage_names[stage_idx + 1],
                    )
                    stage_idx += 1
                elif choice == "fail":
                    ev = FailJobEvent(
                        event_id=ev_id,
                        stage_name=stage_names[stage_idx],
                        error_code=f"ERR_{step}",
                        error_detail=f"detail-{step}",
                    )
                else:
                    ev = CompleteJobEvent(event_id=ev_id)
            elif state.status == AcquisitionJobStatus.FAILED:
                ev = (
                    CancelJobEvent(event_id=ev_id, reason="aborted")
                    if rng.random() < 0.25
                    else RetryJobEvent(event_id=ev_id)
                )
            else:
                ev = CancelJobEvent(event_id=ev_id)

            next_state = apply_job_event(state, ev)
            # Idempotency guarantee: applying same event twice/thrice yields identical state
            assert apply_job_event(next_state, ev) == next_state
            assert apply_job_event(apply_job_event(next_state, ev), ev) == next_state
            state = next_state


def test_streaming_chunked_sha256_never_reads_full_stream_into_ram() -> None:
    payload = b"PDF-HEADER-" + (b"0123456789abcdef" * 500)
    expected_sha = hashlib.sha256(payload).hexdigest()

    class GuardedChunkStream:
        def __init__(self, data: bytes) -> None:
            self._buf = io.BytesIO(data)
            self.read_calls: list[int] = []

        def read(self, n: int = -1) -> bytes:
            if n == -1:
                raise AssertionError("Full unchunked read() must never be called")
            self.read_calls.append(n)
            return self._buf.read(n)

    guarded = GuardedChunkStream(payload)
    fp = compute_stream_fingerprint(guarded, chunk_size=128)
    assert fp == StreamFingerprint(sha256=expected_sha, size_bytes=len(payload))
    assert all(size == 128 for size in guarded.read_calls)
    assert len(guarded.read_calls) > 10

    # Also supports generator / chunk iterator without .read()
    def chunk_gen():
        for i in range(0, len(payload), 257):
            yield payload[i : i + 257]

    assert compute_stream_fingerprint(chunk_gen()) == fp


def test_decide_duplicate_ingest_returns_existing_record_for_same_tenant() -> None:
    fp = StreamFingerprint(sha256="A" * 64, size_bytes=2048)
    existing = {"document_version_id": "ver-001", "storage_key": "bronze/t1/" + "a" * 64}
    catalog = {"a" * 64: existing}

    hit = decide_duplicate_ingest(fp, catalog)
    assert hit.is_duplicate is True
    assert hit.existing_record == existing

    miss = decide_duplicate_ingest(StreamFingerprint(sha256="b" * 64, size_bytes=128), catalog)
    assert miss.is_duplicate is False
    assert miss.existing_record is None
