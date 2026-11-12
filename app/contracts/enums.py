"""Shared status enums (spec section 5.2). Stdlib only."""

from __future__ import annotations

from enum import StrEnum


class AcquisitionStatus(StrEnum):
    INGESTED = "INGESTED"
    EXTRACTED = "EXTRACTED"
    NORMALIZED = "NORMALIZED"
    FAILED = "FAILED"


class VerificationStatus(StrEnum):
    UNCHECKED = "UNCHECKED"
    AUTO_VERIFIED = "AUTO_VERIFIED"
    VERIFIED = "VERIFIED"
    DISPUTED = "DISPUTED"
    REJECTED = "REJECTED"


class RightsStatus(StrEnum):
    PENDING_REVIEW = "PENDING_REVIEW"
    OWNED = "OWNED"
    LICENSED = "LICENSED"
    ALLOWED_DERIVATIVE = "ALLOWED_DERIVATIVE"
    RESTRICTED = "RESTRICTED"
    TAKEDOWN = "TAKEDOWN"


class PublicationStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"
    TAKEN_DOWN = "TAKEN_DOWN"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"
    CANCELLED = "CANCELLED"


class QuestionItemType(StrEnum):
    SINGLE_CHOICE = "SINGLE_CHOICE"
    MULTIPLE_SELECT = "MULTIPLE_SELECT"
    CLUSTER_TRUE_FALSE = "CLUSTER_TRUE_FALSE"
    SHORT_NUMERIC = "SHORT_NUMERIC"
    ESSAY = "ESSAY"
    GROUP_STEM = "GROUP_STEM"


class AcquisitionMethod(StrEnum):
    DOCUMENT_IMPORT = "DOCUMENT_IMPORT"
    WEB_CRAWL = "WEB_CRAWL"
    DRIVE_SYNC = "DRIVE_SYNC"
    AI_GENERATED = "AI_GENERATED"
    MANUAL_AUTHOR = "MANUAL_AUTHOR"
