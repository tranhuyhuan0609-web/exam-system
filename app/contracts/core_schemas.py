"""Canonical Pydantic v2 cross-module contracts (spec section 5.2).

Leaf package: depends only on stdlib, pydantic, and app.contracts.enums.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.contracts.enums import (
    AcquisitionMethod,
    AcquisitionStatus,
    QuestionItemType,
    RightsStatus,
    VerificationStatus,
)


class NumericParsePolicy(BaseModel):
    """Short-numeric ballot answer parse rules (Part III THPTQG 2025+), guarding thousands dots."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decimal_sep: Literal["COMMA_OR_DOT", "COMMA_ONLY", "DOT_ONLY"] = "COMMA_OR_DOT"
    allow_thousand_sep: bool = False
    max_chars: int = Field(default=4, ge=1, le=12)
    tolerance: Decimal = Field(default=Decimal("0.01"), ge=Decimal("0"))


class ProvenanceContract(BaseModel):
    """Channel-aware provenance contract (PDF/Drive vs Web Crawl)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_version_id: UUID | None = None
    extraction_run_id: UUID | None = None
    page_no: int | None = Field(default=None, ge=1)
    bbox: tuple[float, float, float, float] | None = None
    source_url: str | None = None
    raw_html_sha256: str | None = Field(default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    original_question_label: str | None = None

    @model_validator(mode="after")
    def validate_bbox_geometry(self) -> ProvenanceContract:
        if self.bbox is not None:
            x0, y0, x1, y1 = self.bbox
            if x1 <= x0 or y1 <= y0:
                raise ValueError("provenance bbox must satisfy x1 > x0 and y1 > y0")
        return self


class QuestionOptionContract(BaseModel):
    """Single option (A/B/C/D) or True/False proposition (a/b/c/d)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    option_key: Literal["A", "B", "C", "D", "a", "b", "c", "d"]
    text_md: str = Field(min_length=1)
    figure_asset_ids: tuple[UUID, ...] = ()


class QuestionCandidateContract(BaseModel):
    """Unified output contract for all extractors (A02 Web Crawler, A03 Drive, A07 Extractor)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: UUID
    acquisition_method: AcquisitionMethod
    item_type: QuestionItemType
    stem_md: str = Field(min_length=1)
    options: tuple[QuestionOptionContract, ...] = ()
    source_answer_raw: str | None = None
    source_rationale_md: str | None = None
    figure_asset_ids: tuple[UUID, ...] = ()
    provenance: ProvenanceContract
    acquisition_status: AcquisitionStatus = AcquisitionStatus.EXTRACTED
    verification_status: VerificationStatus = VerificationStatus.UNCHECKED
    rights_status: RightsStatus = RightsStatus.PENDING_REVIEW

    @model_validator(mode="after")
    def validate_options_and_provenance(self) -> QuestionCandidateContract:
        keys = [opt.option_key for opt in self.options]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate option_key entries in candidate options")

        if self.item_type == QuestionItemType.SINGLE_CHOICE:
            if set(keys) != {"A", "B", "C", "D"}:
                raise ValueError("SINGLE_CHOICE must contain exactly keys A, B, C, D")
        elif self.item_type == QuestionItemType.CLUSTER_TRUE_FALSE:
            if set(keys) != {"a", "b", "c", "d"}:
                raise ValueError("CLUSTER_TRUE_FALSE must contain exactly keys a, b, c, d")

        if self.acquisition_method in (AcquisitionMethod.DOCUMENT_IMPORT, AcquisitionMethod.DRIVE_SYNC):
            if self.provenance.document_version_id is None or self.provenance.page_no is None:
                raise ValueError(
                    f"{self.acquisition_method.value} requires provenance.document_version_id and page_no"
                )
        elif self.acquisition_method == AcquisitionMethod.WEB_CRAWL:
            if not self.provenance.source_url or not self.provenance.raw_html_sha256:
                raise ValueError("WEB_CRAWL requires provenance.source_url and raw_html_sha256")
        return self


class AssessmentSnapshotPublicItem(BaseModel):
    """Public exam paper snapshot item: strictly answer-free and key-free."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question_version_id: UUID
    stimulus_id: UUID | None = None
    section_code: Literal["PART_I_MCQ", "PART_II_TF4", "PART_III_SHORT", "PART_IV_ESSAY"]
    sequence_order: int = Field(ge=1)
    points: Decimal = Field(gt=Decimal("0"))
    content_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class AssessmentSnapshotPublicContract(BaseModel):
    """Frozen assessment_versions.structure_snapshot_json contract (ADR-005)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assessment_version_id: UUID
    framework_code: str = Field(min_length=1, max_length=64)
    subject_code: str = Field(min_length=1, max_length=32)
    duration_seconds: int = Field(gt=0)
    total_points: Decimal = Field(default=Decimal("10.00"), gt=Decimal("0"))
    items: tuple[AssessmentSnapshotPublicItem, ...] = Field(min_length=1)


class TelemetryCorrelationContract(BaseModel):
    """Cross-module observability correlation context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    correlation_id: str = Field(min_length=8, max_length=100)
    causation_id: str | None = Field(default=None, min_length=8, max_length=100)
    tenant_id: UUID
    actor_id: UUID | None = None
    document_version_id: UUID | None = None
    acquisition_job_id: UUID | None = None
    pipeline_stage_run_id: UUID | None = None
    parser_or_model_version: str | None = None


class AutosaveRequestContract(BaseModel):
    """Canonical autosave request contract with optimistic concurrency & SHA-256 payload hash."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    client_sequence: int = Field(ge=1)
    expected_response_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=128)
    payload_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    response_payload: dict[str, Any]
    is_flagged: bool = False


class AutosaveResponseContract(BaseModel):
    """Canonical autosave ACK response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["COMMITTED", "IDEMPOTENT_REPLAY"]
    attempt_id: UUID
    question_version_id: UUID
    committed_sequence: int = Field(ge=1)
    saved_response_version: int = Field(ge=1)
    server_timestamp: AwareDatetime
