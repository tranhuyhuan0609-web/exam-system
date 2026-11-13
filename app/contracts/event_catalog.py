"""Canonical V11.3 Event Bus Catalog (spec section 5.1): typed payloads for all 18 domain events."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.contracts.enums import AcquisitionMethod
from app.contracts.events import EventEnvelopeContract


class SourceCrawlRequestedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    job_id: UUID
    tenant_id: UUID
    source_id: UUID
    config_version: int = Field(ge=1)
    requested_by: UUID


class CrawlPageFetchedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    job_id: UUID
    tenant_id: UUID
    source_id: UUID
    url: str = Field(min_length=1)
    raw_asset_id: UUID
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    resolved_ip: str = Field(min_length=1)


class CrawlParserDriftDetectedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    source_id: UUID
    adapter_version: str = Field(min_length=1)
    empty_yield_rate: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(gt=0.0, le=1.0)


class DriveFileDiscoveredPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    connection_id: UUID
    drive_file_id: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    folder_path: str


class DocumentUploadedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    document_id: UUID
    version_id: UUID
    raw_asset_id: UUID
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    mime_type: str = Field(min_length=3)


class DocumentExtractionCompletedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    document_version_id: UUID
    extraction_run_id: UUID
    profile_code: str = Field(min_length=1)


class QuestionCandidateCreatedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    candidate_id: UUID
    acquisition_method: AcquisitionMethod
    content_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    structural_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class QuestionReviewRequiredPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    entity_id: UUID
    reason_codes: tuple[str, ...] = Field(min_length=1)
    severity: str = Field(min_length=1)
    queue_name: str = Field(min_length=1)


class QuestionVersionPublishedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    question_id: UUID
    version_id: UUID
    taxonomy_node_id: UUID
    exam_tags: tuple[str, ...] = ()


class ContentPublishedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    content_item_id: UUID
    version_id: UUID
    curriculum_node_id: UUID


class AssessmentPublishedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    assessment_id: UUID
    assessment_version_id: UUID
    framework_code: str = Field(min_length=1)


class AttemptSubmittedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    attempt_id: UUID
    membership_id: UUID
    assessment_version_id: UUID
    submitted_at: AwareDatetime


class AttemptExpiredPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    attempt_id: UUID
    deadline_at: AwareDatetime
    auto_submitted_at: AwareDatetime


class AttemptGradedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    attempt_id: UUID
    grade_id: UUID
    revision_no: int = Field(ge=1)
    total_score: Decimal = Field(ge=Decimal("0"))
    wrong_question_version_ids: tuple[UUID, ...] = ()


class GradeReleasedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    grade_id: UUID
    attempt_id: UUID
    membership_id: UUID
    released_at: AwareDatetime


class RightsTakedownExecutedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    takedown_case_id: UUID
    target_entity_type: str = Field(min_length=1)
    target_entity_id: UUID
    executed_at: AwareDatetime


class SubscriptionUpdatedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    membership_id: UUID
    plan_code: str = Field(min_length=1)
    status: str = Field(min_length=1)
    current_period_end: AwareDatetime


class AiBudgetThresholdExceededPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    current_spend_usd: Decimal = Field(ge=Decimal("0"))
    daily_budget_usd: Decimal = Field(gt=Decimal("0"))
    threshold_pct: int = Field(ge=1, le=1000)


@dataclass(frozen=True)
class EventCatalogEntry:
    event_type: str
    schema_version: int
    producer: str
    consumers: tuple[str, ...]
    payload_model: type[BaseModel]


EVENT_CATALOG: dict[tuple[str, int], EventCatalogEntry] = {
    ("source.crawl.requested", 1): EventCatalogEntry(
        "source.crawl.requested", 1, "A02", ("crawler_pool",), SourceCrawlRequestedPayload
    ),
    ("crawl.page.fetched", 1): EventCatalogEntry(
        "crawl.page.fetched", 1, "A02", ("A05", "A07"), CrawlPageFetchedPayload
    ),
    ("crawl.parser.drift_detected", 1): EventCatalogEntry(
        "crawl.parser.drift_detected", 1, "A02", ("G01", "A02"), CrawlParserDriftDetectedPayload
    ),
    ("drive.file.discovered", 1): EventCatalogEntry(
        "drive.file.discovered", 1, "A03", ("A01", "A03"), DriveFileDiscoveredPayload
    ),
    ("document.uploaded", 1): EventCatalogEntry(
        "document.uploaded", 1, "A01", ("A05", "A06"), DocumentUploadedPayload
    ),
    ("document.extraction.completed", 1): EventCatalogEntry(
        "document.extraction.completed", 1, "A05", ("A04", "A07"), DocumentExtractionCompletedPayload
    ),
    ("question.candidate.created", 1): EventCatalogEntry(
        "question.candidate.created", 1, "A07", ("A08", "C02"), QuestionCandidateCreatedPayload
    ),
    ("question.review.required", 1): EventCatalogEntry(
        "question.review.required", 1, "C02", ("A09",), QuestionReviewRequiredPayload
    ),
    ("question.version.published", 1): EventCatalogEntry(
        "question.version.published", 1, "C01", ("B07", "C04"), QuestionVersionPublishedPayload
    ),
    ("content.published", 1): EventCatalogEntry(
        "content.published", 1, "B01", ("B07", "D02"), ContentPublishedPayload
    ),
    ("assessment.published", 1): EventCatalogEntry(
        "assessment.published", 1, "C04", ("C05",), AssessmentPublishedPayload
    ),
    ("attempt.submitted", 1): EventCatalogEntry(
        "attempt.submitted", 1, "C05", ("C07",), AttemptSubmittedPayload
    ),
    ("attempt.expired", 1): EventCatalogEntry(
        "attempt.expired", 1, "C05", ("C06", "C07"), AttemptExpiredPayload
    ),
    ("attempt.graded", 1): EventCatalogEntry(
        "attempt.graded", 1, "C07", ("D05", "D06", "F03"), AttemptGradedPayload
    ),
    ("grade.released", 1): EventCatalogEntry(
        "grade.released", 1, "C07", ("F06", "D01"), GradeReleasedPayload
    ),
    ("rights.takedown.executed", 1): EventCatalogEntry(
        "rights.takedown.executed", 1, "G02", ("A09", "B01", "C01", "B07", "C05"), RightsTakedownExecutedPayload
    ),
    ("subscription.updated", 1): EventCatalogEntry(
        "subscription.updated", 1, "G05", ("F05",), SubscriptionUpdatedPayload
    ),
    ("ai.budget.threshold_exceeded", 1): EventCatalogEntry(
        "ai.budget.threshold_exceeded", 1, "E01", ("F04", "G01"), AiBudgetThresholdExceededPayload
    ),
}


def get_event_spec(event_type: str, schema_version: int = 1) -> EventCatalogEntry:
    """Lookup an event contract spec in the canonical V11.3 catalog."""
    key = (event_type, schema_version)
    if key not in EVENT_CATALOG:
        raise KeyError(f"unknown event contract: {event_type!r} v{schema_version}")
    return EVENT_CATALOG[key]


def validate_event_payload(
    event_type: str,
    payload: dict[str, Any],
    *,
    schema_version: int = 1,
) -> BaseModel:
    """Validate an event payload strictly against its canonical schema_version Pydantic model."""
    spec = get_event_spec(event_type, schema_version)
    return spec.payload_model.model_validate(payload)


def build_catalog_envelope(
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    payload: BaseModel,
    schema_version: int = 1,
    correlation_id: str | None = None,
    causation_id: UUID | None = None,
) -> EventEnvelopeContract:
    """Wrap a typed catalog payload into EventEnvelopeContract after type & tenant verification."""
    spec = get_event_spec(event_type, schema_version)
    if not isinstance(payload, spec.payload_model):
        raise TypeError(
            f"{event_type} v{schema_version} expects {spec.payload_model.__name__}, got {type(payload).__name__}"
        )
    tenant_id = getattr(payload, "tenant_id")
    return EventEnvelopeContract(
        event_type=event_type,
        schema_version=schema_version,
        tenant_id=tenant_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload=payload.model_dump(mode="json"),
    )
