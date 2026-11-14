"""W0.6 Legacy-to-V11.3 deterministic backfill value mappers & reconciliation verifier.

Implements the mapping specification from ``docs/architecture/BACKFILL_MAP.md`` as pure helpers so
every module's CLI backfill script produces repeatable, fail-closed, auditable records. Status and
question-type vocabularies come from ``app.contracts`` (one definition per contract).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.contracts.blueprints import CognitiveLevel
from app.contracts.enums import (
    AcquisitionStatus,
    PublicationStatus,
    QuestionItemType,
    RightsStatus,
    VerificationStatus,
)

V113QuestionType = QuestionItemType
V113Difficulty = CognitiveLevel
VisibilityScope = Literal["SYSTEM_PUBLIC", "TENANT_PRIVATE"]

_QUESTION_TYPE_MAP: dict[str, QuestionItemType] = {
    "MULTIPLE_CHOICE": QuestionItemType.SINGLE_CHOICE,
    "TRUE_FALSE": QuestionItemType.CLUSTER_TRUE_FALSE,
    "SHORT_ANSWER": QuestionItemType.SHORT_NUMERIC,
    "ESSAY": QuestionItemType.ESSAY,
}

_DIFFICULTY_MAP: dict[str, tuple[CognitiveLevel, tuple[str, ...]]] = {
    "RECOGNITION": ("NB", ()),
    "UNDERSTANDING": ("TH", ()),
    "APPLICATION": ("VD", ()),
    "ADVANCED_APPLICATION": ("VDC", ()),
    "HSG": ("VDC", ("HSG",)),
}

_LIBRARY_KIND_MAP: dict[str, str] = {
    "BOOK": "DOCUMENT",
    "TOPIC": "DOCUMENT",
    "PAPER": "DOCUMENT",
    "KNOWLEDGE_ARTICLE": "ARTICLE",
    "ARTICLE": "ARTICLE",
}

_VERIFIED_AUDIT_STATUSES: frozenset[str] = frozenset({"VERIFIED_CONSENSUS", "VERIFIED_BY_HUMAN"})
_ALLOWED_PUBLISH_RIGHTS: frozenset[RightsStatus] = frozenset(
    {RightsStatus.OWNED, RightsStatus.LICENSED, RightsStatus.ALLOWED_DERIVATIVE}
)
_VISIBILITY_SCOPES: frozenset[str] = frozenset({"SYSTEM_PUBLIC", "TENANT_PRIVATE"})
_LEGACY_ID_RE = re.compile(r"-?[A-Za-z0-9_]{1,64}", re.ASCII)


class BackfillMappingError(ValueError):
    """Raised when a legacy record value cannot be safely mapped into V11.3."""


@dataclass(frozen=True)
class DifficultyMapping:
    difficulty: CognitiveLevel
    extra_framework_tags: tuple[str, ...]


@dataclass(frozen=True)
class FiveAxisState:
    acquisition_status: AcquisitionStatus
    verification_status: VerificationStatus
    rights_status: RightsStatus
    publication_status: PublicationStatus
    visibility_scope: VisibilityScope


@dataclass(frozen=True)
class BatchReconciliationReport:
    source_count: int
    migrated_count: int
    skipped_count: int
    duplicate_legacy_ids: tuple[str, ...]
    balanced: bool
    idempotent_second_pass_ok: bool
    unaccounted_legacy_ids: tuple[str, ...] = ()
    unknown_migrated_ids: tuple[str, ...] = ()
    unknown_skipped_ids: tuple[str, ...] = ()
    migrated_and_skipped_ids: tuple[str, ...] = ()
    duplicate_source_ids: tuple[str, ...] = ()


def _normalise_key(raw: object, label: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise BackfillMappingError(f"{label} must be a non-empty string")
    return raw.strip().upper()


def map_legacy_question_type(raw_type: str) -> QuestionItemType:
    mapped = _QUESTION_TYPE_MAP.get(_normalise_key(raw_type, "legacy question type"))
    if mapped is None:
        raise BackfillMappingError("unsupported legacy question type")
    return mapped


def map_legacy_difficulty(raw_difficulty: str) -> DifficultyMapping:
    mapped = _DIFFICULTY_MAP.get(_normalise_key(raw_difficulty, "legacy difficulty"))
    if mapped is None:
        raise BackfillMappingError("unsupported legacy difficulty")
    return DifficultyMapping(difficulty=mapped[0], extra_framework_tags=mapped[1])


def map_legacy_library_kind(raw_kind: str) -> str:
    mapped = _LIBRARY_KIND_MAP.get(_normalise_key(raw_kind, "legacy library kind"))
    if mapped is None:
        raise BackfillMappingError("unsupported legacy library kind")
    return mapped


def resolve_legacy_five_axis_state(
    *,
    origin_type: str | None,
    audit_status: str | None,
    legacy_is_verified: bool,
    has_answer_source: bool,
    visibility_scope: VisibilityScope = "SYSTEM_PUBLIC",
) -> FiveAxisState:
    """Resolve V11.3 5-axis fail-closed state per BACKFILL_MAP.md section 3.

    Fail-closed rules:
    - Only `origin_type == "AUTHORED"` receives `rights_status = "OWNED"`; crawler,
      file-import, and unattributed records default to `PENDING_REVIEW`.
    - `legacy_is_verified` alone is NOT verification evidence (it defaulted to True
      in legacy schema). `VERIFIED` requires explicit consensus/human audit; otherwise
      records with `answer_source` become `AUTO_VERIFIED`, and others `UNCHECKED`.
    - `publication_status` becomes `PUBLISHED` ONLY if `VERIFIED` and rights allow.
    """
    if visibility_scope not in _VISIBILITY_SCOPES:
        raise BackfillMappingError("visibility_scope must be SYSTEM_PUBLIC or TENANT_PRIVATE")
    norm_origin = origin_type.strip().upper() if isinstance(origin_type, str) else ""
    rights = RightsStatus.OWNED if norm_origin == "AUTHORED" else RightsStatus.PENDING_REVIEW

    norm_audit = audit_status.strip().upper() if isinstance(audit_status, str) else ""
    if norm_audit in _VERIFIED_AUDIT_STATUSES:
        verification = VerificationStatus.VERIFIED
    elif legacy_is_verified is True and has_answer_source is True:
        verification = VerificationStatus.AUTO_VERIFIED
    else:
        verification = VerificationStatus.UNCHECKED

    publication = (
        PublicationStatus.PUBLISHED
        if verification == VerificationStatus.VERIFIED and rights in _ALLOWED_PUBLISH_RIGHTS
        else PublicationStatus.DRAFT
    )

    return FiveAxisState(
        acquisition_status=AcquisitionStatus.NORMALIZED,
        verification_status=verification,
        rights_status=rights,
        publication_status=publication,
        visibility_scope=visibility_scope,
    )


def format_legacy_stable_code(legacy_id: int | str) -> str:
    if isinstance(legacy_id, bool) or not isinstance(legacy_id, (int, str)):
        raise BackfillMappingError("legacy_id must be an int or str")
    cleaned = str(legacy_id).strip()
    if not cleaned:
        raise BackfillMappingError("legacy_id must not be empty")
    if _LEGACY_ID_RE.fullmatch(cleaned) is None:
        raise BackfillMappingError("legacy_id contains characters outside [A-Za-z0-9_-]")
    return f"LEG-{cleaned}"


def _duplicates(items: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    dups: list[str] = []
    for item in items:
        if item in seen and item not in dups:
            dups.append(item)
        seen.add(item)
    return dups


def compute_batch_reconciliation_report(
    *,
    source_legacy_ids: list[str] | tuple[str, ...],
    migrated_legacy_ids: list[str] | tuple[str, ...],
    skipped_reasons: dict[str, str],
    second_pass_created_count: int = 0,
) -> BatchReconciliationReport:
    """Verify BACKFILL_MAP section 6 invariants:
    1) every source id is migrated exactly once or skipped (set equality, not just counts)
    2) no migrated id is outside the source, no skipped id is outside the source
    3) no id is both migrated and skipped; no duplicates in migrated output or in the source
    4) re-running idempotent backfill yields 0 newly created rows
    """
    source_set = set(source_legacy_ids)
    migrated_set = set(migrated_legacy_ids)
    skipped_set = set(skipped_reasons)

    duplicates = _duplicates(migrated_legacy_ids)
    source_duplicates = _duplicates(source_legacy_ids)
    unknown_migrated = sorted(migrated_set - source_set)
    unknown_skipped = sorted(skipped_set - source_set)
    overlap = sorted(migrated_set & skipped_set)
    unaccounted = sorted(source_set - migrated_set - skipped_set)

    src_count = len(source_legacy_ids)
    mig_count = len(migrated_legacy_ids)
    skip_count = len(skipped_reasons)

    balanced = (
        src_count == mig_count + skip_count
        and not duplicates
        and not source_duplicates
        and not unknown_migrated
        and not unknown_skipped
        and not overlap
        and not unaccounted
    )
    return BatchReconciliationReport(
        source_count=src_count,
        migrated_count=mig_count,
        skipped_count=skip_count,
        duplicate_legacy_ids=tuple(sorted(duplicates)),
        balanced=balanced,
        idempotent_second_pass_ok=second_pass_created_count == 0,
        unaccounted_legacy_ids=tuple(unaccounted),
        unknown_migrated_ids=tuple(unknown_migrated),
        unknown_skipped_ids=tuple(unknown_skipped),
        migrated_and_skipped_ids=tuple(overlap),
        duplicate_source_ids=tuple(sorted(source_duplicates)),
    )
