"""W0.6 Legacy-to-V11.3 deterministic backfill value mappers & reconciliation verifier.

Implements the mapping specification from ``docs/architecture/BACKFILL_MAP.md``
strictly as pure stdlib helpers in ``app.platform`` so every module's CLI backfill
script produces repeatable, fail-closed, auditable records.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

LegacyQuestionType = Literal["MULTIPLE_CHOICE", "TRUE_FALSE", "SHORT_ANSWER", "ESSAY"]
V113QuestionType = Literal[
    "SINGLE_CHOICE",
    "CLUSTER_TRUE_FALSE",
    "SHORT_NUMERIC",
    "ESSAY",
]

LegacyDifficulty = Literal[
    "RECOGNITION",
    "UNDERSTANDING",
    "APPLICATION",
    "ADVANCED_APPLICATION",
    "HSG",
]
V113Difficulty = Literal["NB", "TH", "VD", "VDC"]

RightsStatus = Literal["OWNED", "LICENSED", "ALLOWED_DERIVATIVE", "PENDING_REVIEW", "RESTRICTED"]
VerificationStatus = Literal["VERIFIED", "AUTO_VERIFIED", "UNCHECKED"]
PublicationStatus = Literal["DRAFT", "PUBLISHED"]

_QUESTION_TYPE_MAP: dict[str, V113QuestionType] = {
    "MULTIPLE_CHOICE": "SINGLE_CHOICE",
    "TRUE_FALSE": "CLUSTER_TRUE_FALSE",
    "SHORT_ANSWER": "SHORT_NUMERIC",
    "ESSAY": "ESSAY",
}

_DIFFICULTY_MAP: dict[str, tuple[V113Difficulty, tuple[str, ...]]] = {
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
_ALLOWED_PUBLISH_RIGHTS: frozenset[str] = frozenset({"OWNED", "LICENSED", "ALLOWED_DERIVATIVE"})


class BackfillMappingError(ValueError):
    """Raised when a legacy record value cannot be safely mapped into V11.3."""


@dataclass(frozen=True)
class DifficultyMapping:
    difficulty: V113Difficulty
    extra_framework_tags: tuple[str, ...]


@dataclass(frozen=True)
class FiveAxisState:
    acquisition_status: str
    verification_status: VerificationStatus
    rights_status: RightsStatus
    publication_status: PublicationStatus
    visibility_scope: Literal["SYSTEM_PUBLIC", "TENANT_PRIVATE"]


@dataclass(frozen=True)
class BatchReconciliationReport:
    source_count: int
    migrated_count: int
    skipped_count: int
    duplicate_legacy_ids: tuple[str, ...]
    balanced: bool
    idempotent_second_pass_ok: bool


def map_legacy_question_type(raw_type: str) -> V113QuestionType:
    key = raw_type.strip().upper()
    mapped = _QUESTION_TYPE_MAP.get(key)
    if mapped is None:
        raise BackfillMappingError(f"unsupported legacy question type: {raw_type!r}")
    return mapped


def map_legacy_difficulty(raw_difficulty: str) -> DifficultyMapping:
    key = raw_difficulty.strip().upper()
    mapped = _DIFFICULTY_MAP.get(key)
    if mapped is None:
        raise BackfillMappingError(f"unsupported legacy difficulty: {raw_difficulty!r}")
    return DifficultyMapping(difficulty=mapped[0], extra_framework_tags=mapped[1])


def map_legacy_library_kind(raw_kind: str) -> str:
    key = raw_kind.strip().upper()
    mapped = _LIBRARY_KIND_MAP.get(key)
    if mapped is None:
        raise BackfillMappingError(f"unsupported legacy library kind: {raw_kind!r}")
    return mapped


def resolve_legacy_five_axis_state(
    *,
    origin_type: str | None,
    audit_status: str | None,
    legacy_is_verified: bool,
    has_answer_source: bool,
    visibility_scope: Literal["SYSTEM_PUBLIC", "TENANT_PRIVATE"] = "SYSTEM_PUBLIC",
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
    norm_origin = (origin_type or "").strip().upper()
    rights: RightsStatus = "OWNED" if norm_origin == "AUTHORED" else "PENDING_REVIEW"

    norm_audit = (audit_status or "").strip().upper()
    if norm_audit in _VERIFIED_AUDIT_STATUSES:
        verification: VerificationStatus = "VERIFIED"
    elif legacy_is_verified and has_answer_source:
        verification = "AUTO_VERIFIED"
    else:
        verification = "UNCHECKED"

    publication: PublicationStatus = (
        "PUBLISHED"
        if verification == "VERIFIED" and rights in _ALLOWED_PUBLISH_RIGHTS
        else "DRAFT"
    )

    return FiveAxisState(
        acquisition_status="NORMALIZED",
        verification_status=verification,
        rights_status=rights,
        publication_status=publication,
        visibility_scope=visibility_scope,
    )


def format_legacy_stable_code(legacy_id: int | str) -> str:
    cleaned = str(legacy_id).strip()
    if not cleaned:
        raise BackfillMappingError("legacy_id must not be empty")
    return f"LEG-{cleaned}"


def compute_batch_reconciliation_report(
    *,
    source_legacy_ids: list[str] | tuple[str, ...],
    migrated_legacy_ids: list[str] | tuple[str, ...],
    skipped_reasons: dict[str, str],
    second_pass_created_count: int = 0,
) -> BatchReconciliationReport:
    """Verify BACKFILL_MAP section 6 invariants:
    1) source_count == migrated_count + skipped_count
    2) No duplicate legacy_ids in migrated output
    3) Re-running idempotent backfill yields 0 newly created rows
    """
    seen: set[str] = set()
    duplicates: list[str] = []
    for lid in migrated_legacy_ids:
        if lid in seen and lid not in duplicates:
            duplicates.append(lid)
        seen.add(lid)

    src_count = len(source_legacy_ids)
    mig_count = len(migrated_legacy_ids)
    skip_count = len(skipped_reasons)

    balanced = (src_count == mig_count + skip_count) and len(duplicates) == 0
    return BatchReconciliationReport(
        source_count=src_count,
        migrated_count=mig_count,
        skipped_count=skip_count,
        duplicate_legacy_ids=tuple(sorted(duplicates)),
        balanced=balanced,
        idempotent_second_pass_ok=second_pass_created_count == 0,
    )
