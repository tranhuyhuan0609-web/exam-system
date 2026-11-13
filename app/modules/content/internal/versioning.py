"""B01 immutable content versioning: states, edit guard, publish freeze and fork."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class ContentType(StrEnum):
    """Exact DDL CHECK values for `content_items.content_type`."""

    ARTICLE = "ARTICLE"
    LESSON = "LESSON"
    VIDEO = "VIDEO"
    DOCUMENT = "DOCUMENT"
    FLASHCARD_SET = "FLASHCARD_SET"
    VOCABULARY_SET = "VOCABULARY_SET"


class ContentVersionStatus(StrEnum):
    """Exact DDL CHECK values for `content_versions.status`."""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    SUPERSEDED = "SUPERSEDED"
    TAKEN_DOWN = "TAKEN_DOWN"


class ContentBlockType(StrEnum):
    """Exact DDL CHECK values for `content_blocks.block_type`."""

    THEORY_SUMMARY = "THEORY_SUMMARY"
    FORMULA_BOX = "FORMULA_BOX"
    METHOD_STEPS = "METHOD_STEPS"
    TIP_WARNING = "TIP_WARNING"
    WORKED_EXAMPLE = "WORKED_EXAMPLE"
    INTERACTIVE_QUESTION_EMBED = "INTERACTIVE_QUESTION_EMBED"
    HTML_TABLE = "HTML_TABLE"


class ContentStudioError(ValueError):
    """Raised when B01 immutability or B02 Block Studio validation fails."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AssetCatalogEntry:
    asset_id: str
    tenant_id: str
    visibility_scope: str  # SYSTEM_PUBLIC | TENANT_PRIVATE | SHARED_PARTNER
    shared_tenant_ids: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class ContentBlockDraft:
    block_order: int
    block_type: ContentBlockType
    payload_json: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class ContentVersionState:
    content_item_id: str
    tenant_id: str
    content_type: ContentType
    version_no: int
    title: str
    summary_md: str
    blocks: tuple[ContentBlockDraft, ...] = ()
    status: ContentVersionStatus = ContentVersionStatus.DRAFT
    is_immutable: bool = False
    created_by: str | None = None


@dataclass(frozen=True, slots=True)
class ContentValidationIssue:
    block_order: int | None
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ContentValidationReport:
    valid: bool
    issues: tuple[ContentValidationIssue, ...]


def publish_content_version(
    version: ContentVersionState,
    *,
    validator: Callable[[ContentVersionState], ContentValidationReport],
) -> ContentVersionState:
    """Validate (through the injected B02 validator) and freeze a content version as immutable `PUBLISHED`."""
    if version.status == ContentVersionStatus.TAKEN_DOWN:
        raise ContentStudioError("CONTENT_TAKEN_DOWN", "Cannot publish a TAKEN_DOWN content version")
    report = validator(version)
    if not report.valid:
        raise ContentStudioError(report.issues[0].code, report.issues[0].message)
    return replace(version, status=ContentVersionStatus.PUBLISHED, is_immutable=True)


def edit_draft_blocks(
    version: ContentVersionState,
    *,
    title: str | None = None,
    summary_md: str | None = None,
    blocks: tuple[ContentBlockDraft, ...] | None = None,
) -> ContentVersionState:
    """Mutate an unpublished `DRAFT` version; rejects any edit to an immutable/published version."""
    if version.is_immutable or version.status == ContentVersionStatus.PUBLISHED:
        raise ContentStudioError(
            "CONTENT_VERSION_IMMUTABLE",
            f"Content version v{version.version_no} is PUBLISHED/immutable; fork v{version.version_no + 1} instead",
        )
    return replace(
        version,
        title=version.title if title is None else title,
        summary_md=version.summary_md if summary_md is None else summary_md,
        blocks=version.blocks if blocks is None else blocks,
    )


def fork_next_content_version(
    published: ContentVersionState,
    *,
    created_by: str,
) -> tuple[ContentVersionState, ContentVersionState]:
    """
    Preserve historical published version and emit a new mutable `DRAFT` at `version_no + 1`.

    Returns `(historical_published_version, new_draft_version)`.
    """
    if not created_by:
        raise ContentStudioError("MISSING_AUTHOR", "created_by is required")
    new_draft = ContentVersionState(
        content_item_id=published.content_item_id,
        tenant_id=published.tenant_id,
        content_type=published.content_type,
        version_no=published.version_no + 1,
        title=published.title,
        summary_md=published.summary_md,
        blocks=published.blocks,
        status=ContentVersionStatus.DRAFT,
        is_immutable=False,
        created_by=created_by,
    )
    return published, new_draft
