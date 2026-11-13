"""Tests for app.contracts.core_schemas V11.3 cross-module Pydantic v2 contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import uuid

import pytest
from pydantic import ValidationError

from app.contracts.core_schemas import (
    AssessmentSnapshotPublicContract,
    AssessmentSnapshotPublicItem,
    AutosaveRequestContract,
    AutosaveResponseContract,
    NumericParsePolicy,
    ProvenanceContract,
    QuestionCandidateContract,
    QuestionOptionContract,
)
from app.contracts.enums import AcquisitionMethod, QuestionItemType

_SHA = "a" * 64


def test_question_candidate_enforces_channel_provenance_and_option_sets() -> None:
    tid = uuid.uuid4()
    dv = uuid.uuid4()

    valid_doc = QuestionCandidateContract(
        tenant_id=tid,
        acquisition_method=AcquisitionMethod.DOCUMENT_IMPORT,
        item_type=QuestionItemType.SINGLE_CHOICE,
        stem_md="Tính giá trị biểu thức sau:",
        options=tuple(QuestionOptionContract(option_key=k, text_md=f"Opt {k}") for k in ("A", "B", "C", "D")),
        provenance=ProvenanceContract(document_version_id=dv, page_no=1, bbox=(10.0, 20.0, 200.0, 100.0)),
    )
    assert len(valid_doc.options) == 4

    # WEB_CRAWL does NOT require document_version_id/page_no, only source_url + raw_html_sha256
    valid_crawl = QuestionCandidateContract(
        tenant_id=tid,
        acquisition_method=AcquisitionMethod.WEB_CRAWL,
        item_type=QuestionItemType.CLUSTER_TRUE_FALSE,
        stem_md="Xét tính đúng sai:",
        options=tuple(QuestionOptionContract(option_key=k, text_md=f"Ý {k}") for k in ("a", "b", "c", "d")),
        provenance=ProvenanceContract(source_url="https://example.edu.vn/de-1", raw_html_sha256=_SHA),
    )
    assert valid_crawl.provenance.page_no is None

    # Missing crawl metadata or bad option count rejected
    with pytest.raises(ValidationError):
        QuestionCandidateContract(
            tenant_id=tid,
            acquisition_method=AcquisitionMethod.WEB_CRAWL,
            item_type=QuestionItemType.SINGLE_CHOICE,
            stem_md="Missing opt D",
            options=tuple(QuestionOptionContract(option_key=k, text_md=k) for k in ("A", "B", "C")),
            provenance=ProvenanceContract(source_url="https://example.edu.vn/de-1", raw_html_sha256=_SHA),
        )


def test_assessment_snapshot_rejects_answer_leak_fields_and_autosave_contract_validates() -> None:
    with pytest.raises(ValidationError):
        AssessmentSnapshotPublicItem(
            question_version_id=uuid.uuid4(),
            section_code="PART_I_MCQ",
            sequence_order=1,
            points=Decimal("0.25"),
            content_hash=_SHA,
            correct_answer="A",  # type: ignore[call-arg]
        )

    snap = AssessmentSnapshotPublicContract(
        assessment_version_id=uuid.uuid4(),
        framework_code="THPTQG_2025",
        subject_code="MATH",
        duration_seconds=5400,
        items=(
            AssessmentSnapshotPublicItem(
                question_version_id=uuid.uuid4(),
                section_code="PART_I_MCQ",
                sequence_order=1,
                points=Decimal("0.25"),
                content_hash=_SHA,
            ),
        ),
    )
    assert snap.total_points == Decimal("10.00")

    policy = NumericParsePolicy()
    assert policy.max_chars == 4 and policy.allow_thousand_sep is False

    req = AutosaveRequestContract(
        client_sequence=1,
        expected_response_version=0,
        idempotency_key="attempt1-item1-seq1",
        payload_hash=_SHA,
        response_payload={"selected": "B"},
    )
    res = AutosaveResponseContract(
        status="COMMITTED",
        attempt_id=uuid.uuid4(),
        question_version_id=uuid.uuid4(),
        committed_sequence=req.client_sequence,
        saved_response_version=1,
        server_timestamp=datetime.now(UTC),
    )
    assert res.status == "COMMITTED"
