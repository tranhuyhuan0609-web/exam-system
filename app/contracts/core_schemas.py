"""Canonical Pydantic v2 cross-module contracts (spec section 5.2).

Leaf package: depends only on stdlib, pydantic, and sibling ``app.contracts`` modules.
Re-exports ``NumericParsePolicy``, ``AutosaveRequestContract`` and ``AutosaveResponseContract``
from their dedicated ``app.contracts`` modules so there is a single canonical definition.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.autosave import AutosaveRequestContract, AutosaveResponseContract
from app.contracts.blueprints import SectionCode
from app.contracts.enums import (
    AcquisitionMethod,
    AcquisitionStatus,
    QuestionItemType,
    RightsStatus,
    VerificationStatus,
)
from app.contracts.scoring import NumericParsePolicy

_HTTP_URL_RE = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)


def _require_non_blank(value: str | None, field_name: str) -> None:
    if value is not None and not value.strip():
        raise ValueError(f"{field_name} must not be blank")


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
    def validate_bbox_and_fields(self) -> ProvenanceContract:
        if self.bbox is not None:
            x0, y0, x1, y1 = self.bbox
            if any(isinstance(c, bool) or not math.isfinite(c) or c < 0.0 for c in (x0, y0, x1, y1)):
                raise ValueError("provenance bbox coordinates must be finite non-negative numbers")
            if x1 <= x0 or y1 <= y0:
                raise ValueError("provenance bbox must satisfy x1 > x0 and y1 > y0")
        if self.source_url is not None and not _HTTP_URL_RE.match(self.source_url.strip()):
            raise ValueError("provenance source_url must be a valid http(s) URL")
        _require_non_blank(self.original_question_label, "original_question_label")
        return self


class QuestionOptionContract(BaseModel):
    """Single option (A/B/C/D) or True/False proposition (a/b/c/d)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    option_key: Literal["A", "B", "C", "D", "a", "b", "c", "d"]
    text_md: str = Field(min_length=1)
    figure_asset_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def validate_non_blank_text(self) -> QuestionOptionContract:
        if not self.text_md.strip():
            raise ValueError("option text_md must not be blank")
        return self


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
        if not self.stem_md.strip():
            raise ValueError("stem_md must not be blank")
        keys = [opt.option_key for opt in self.options]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate option_key entries in candidate options")

        if self.item_type == QuestionItemType.SINGLE_CHOICE:
            if set(keys) != {"A", "B", "C", "D"}:
                raise ValueError("SINGLE_CHOICE must contain exactly keys A, B, C, D")
        elif self.item_type == QuestionItemType.CLUSTER_TRUE_FALSE:
            if set(keys) != {"a", "b", "c", "d"}:
                raise ValueError("CLUSTER_TRUE_FALSE must contain exactly keys a, b, c, d")
        elif self.item_type == QuestionItemType.MULTIPLE_SELECT:
            if len(keys) < 2 or not set(keys).issubset({"A", "B", "C", "D"}):
                raise ValueError("MULTIPLE_SELECT requires at least 2 options drawn from A, B, C, D")
        elif self.item_type in (QuestionItemType.SHORT_NUMERIC, QuestionItemType.ESSAY) and keys:
            raise ValueError(f"{self.item_type.value} must not carry choice options")

        if self.acquisition_method in (AcquisitionMethod.DOCUMENT_IMPORT, AcquisitionMethod.DRIVE_SYNC):
            if (
                self.provenance.document_version_id is None
                or self.provenance.page_no is None
                or self.provenance.bbox is None
            ):
                raise ValueError(
                    f"{self.acquisition_method.value} requires provenance.document_version_id, page_no, and bbox"
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
    section_code: SectionCode
    sequence_order: int = Field(ge=1)
    points: Decimal = Field(gt=Decimal("0"))
    content_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_finite_points(self) -> AssessmentSnapshotPublicItem:
        if not self.points.is_finite():
            raise ValueError("points must be a finite Decimal")
        return self


class AssessmentSnapshotPublicContract(BaseModel):
    """Frozen assessment_versions.structure_snapshot_json contract (ADR-005)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assessment_version_id: UUID
    framework_code: str = Field(min_length=1, max_length=64)
    subject_code: str = Field(min_length=1, max_length=32)
    duration_seconds: int = Field(gt=0)
    total_points: Decimal = Field(default=Decimal("10.00"), gt=Decimal("0"))
    items: tuple[AssessmentSnapshotPublicItem, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_snapshot_invariants(self) -> AssessmentSnapshotPublicContract:
        if not self.framework_code.strip() or not self.subject_code.strip():
            raise ValueError("framework_code and subject_code must not be blank")
        if not self.total_points.is_finite():
            raise ValueError("total_points must be a finite Decimal")
        orders = [item.sequence_order for item in self.items]
        if len(orders) != len(set(orders)):
            raise ValueError("duplicate sequence_order in assessment snapshot items")
        qids = [item.question_version_id for item in self.items]
        if len(qids) != len(set(qids)):
            raise ValueError("duplicate question_version_id in assessment snapshot items")
        return self


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

    @model_validator(mode="after")
    def validate_correlation_fields(self) -> TelemetryCorrelationContract:
        if not self.correlation_id.strip():
            raise ValueError("correlation_id must not be blank")
        _require_non_blank(self.causation_id, "causation_id")
        _require_non_blank(self.parser_or_model_version, "parser_or_model_version")
        return self


__all__ = [
    "AssessmentSnapshotPublicContract",
    "AssessmentSnapshotPublicItem",
    "AutosaveRequestContract",
    "AutosaveResponseContract",
    "NumericParsePolicy",
    "ProvenanceContract",
    "QuestionCandidateContract",
    "QuestionOptionContract",
    "TelemetryCorrelationContract",
]
