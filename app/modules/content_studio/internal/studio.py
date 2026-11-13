"""B01 immutable content versioning & B02 Block Studio KaTeX + cross-tenant asset validators."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
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


_ENV_TAG_RE = re.compile(r"\\(begin|end)\{([a-zA-Z0-9*]+)\}")


def _extract_math_spans(markdown: str) -> tuple[list[str], list[ContentValidationIssue]]:
    """Scan markdown text for `$$...$$`, `$...$`, `\\(...\\)`, and `\\[...\\]` delimiters."""
    spans: list[str] = []
    issues: list[ContentValidationIssue] = []
    n = len(markdown)
    i = 0

    while i < n:
        ch = markdown[i]
        # Skip escaped character
        if ch == "\\":
            nxt = markdown[i + 1] if i + 1 < n else ""
            if nxt == "(":
                end = markdown.find("\\)", i + 2)
                if end == -1:
                    issues.append(ContentValidationIssue(None, "UNCLOSED_KATEX_INLINE", "Unclosed '\\(' delimiter"))
                    break
                spans.append(markdown[i + 2 : end])
                i = end + 2
                continue
            if nxt == "[":
                end = markdown.find("\\]", i + 2)
                if end == -1:
                    issues.append(ContentValidationIssue(None, "UNCLOSED_KATEX_DISPLAY", "Unclosed '\\[' delimiter"))
                    break
                spans.append(markdown[i + 2 : end])
                i = end + 2
                continue
            i += 2
            continue

        if ch == "$":
            is_display = i + 1 < n and markdown[i + 1] == "$"
            delim = "$$" if is_display else "$"
            start = i + len(delim)
            # Find matching unescaped delimiter
            j = start
            found = -1
            while j < n:
                if markdown[j] == "\\":
                    j += 2
                    continue
                if is_display:
                    if markdown[j : j + 2] == "$$":
                        found = j
                        break
                elif markdown[j] == "$" and (j + 1 >= n or markdown[j + 1] != "$"):
                    found = j
                    break
                j += 1

            if found == -1:
                code = "UNCLOSED_KATEX_DISPLAY" if is_display else "UNCLOSED_KATEX_INLINE"
                issues.append(ContentValidationIssue(None, code, f"Unclosed '{delim}' math delimiter"))
                break
            spans.append(markdown[start:found])
            i = found + len(delim)
            continue

        i += 1

    return spans, issues


def validate_katex_syntax(text: str) -> list[ContentValidationIssue]:
    """Validate KaTeX delimiters, `{}` brace balance, and `\\begin{...}/\\end{...}` matching."""
    spans, issues = _extract_math_spans(text)
    if issues:
        return issues

    for span in spans:
        depth = 0
        k = 0
        while k < len(span):
            if span[k] == "\\":
                k += 2
                continue
            if span[k] == "{":
                depth += 1
            elif span[k] == "}":
                depth -= 1
                if depth < 0:
                    issues.append(
                        ContentValidationIssue(None, "UNBALANCED_KATEX_BRACE", "Unexpected closing '}' in KaTeX")
                    )
                    break
            k += 1
        if depth > 0:
            issues.append(ContentValidationIssue(None, "UNBALANCED_KATEX_BRACE", "Unclosed '{' brace in KaTeX"))

        env_stack: list[str] = []
        for match in _ENV_TAG_RE.finditer(span):
            kind, env_name = match.group(1), match.group(2)
            if kind == "begin":
                env_stack.append(env_name)
            elif not env_stack or env_stack[-1] != env_name:
                issues.append(
                    ContentValidationIssue(
                        None,
                        "MISMATCHED_KATEX_ENVIRONMENT",
                        f"Unexpected \\end{{{env_name}}} in KaTeX block",
                    )
                )
                break
            else:
                env_stack.pop()
        if env_stack:
            issues.append(
                ContentValidationIssue(
                    None,
                    "MISMATCHED_KATEX_ENVIRONMENT",
                    f"Unclosed \\begin{{{env_stack[-1]}}} environment in KaTeX block",
                )
            )

    return issues


def _collect_text_and_asset_refs(value: Any, *, texts: list[str], asset_ids: list[str]) -> None:
    if isinstance(value, str):
        texts.append(value)
        return
    if isinstance(value, Mapping):
        for k, v in value.items():
            if k in {"asset_id", "image_asset_id", "video_asset_id"} and isinstance(v, str) and v.strip():
                asset_ids.append(v.strip())
            elif k == "asset_ids" and isinstance(v, (list, tuple)):
                for item in v:
                    if isinstance(item, str) and item.strip():
                        asset_ids.append(item.strip())
            else:
                _collect_text_and_asset_refs(v, texts=texts, asset_ids=asset_ids)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            _collect_text_and_asset_refs(item, texts=texts, asset_ids=asset_ids)


def validate_content_version(
    version: ContentVersionState,
    *,
    asset_catalog: Mapping[str, AssetCatalogEntry] | None = None,
) -> ContentValidationReport:
    """Validate structure, KaTeX syntax, block ordering, and cross-tenant asset permissions."""
    issues: list[ContentValidationIssue] = []

    if not version.title or not version.title.strip():
        issues.append(ContentValidationIssue(None, "MISSING_TITLE", "Content title must be non-empty"))

    if version.summary_md:
        for issue in validate_katex_syntax(version.summary_md):
            issues.append(issue)

    seen_orders: set[int] = set()
    for block in version.blocks:
        if block.block_order < 0:
            issues.append(
                ContentValidationIssue(block.block_order, "INVALID_BLOCK_ORDER", "block_order must be >= 0")
            )
        if block.block_order in seen_orders:
            issues.append(
                ContentValidationIssue(
                    block.block_order, "DUPLICATE_BLOCK_ORDER", f"Duplicate block_order={block.block_order}"
                )
            )
        seen_orders.add(block.block_order)

        if block.block_type == ContentBlockType.INTERACTIVE_QUESTION_EMBED:
            qid = block.payload_json.get("question_id")
            if not isinstance(qid, str) or not qid.strip():
                issues.append(
                    ContentValidationIssue(
                        block.block_order,
                        "MISSING_EMBEDDED_QUESTION_ID",
                        "INTERACTIVE_QUESTION_EMBED requires question_id",
                    )
                )

        texts: list[str] = []
        asset_refs: list[str] = []
        _collect_text_and_asset_refs(block.payload_json, texts=texts, asset_ids=asset_refs)

        for txt in texts:
            for katex_issue in validate_katex_syntax(txt):
                issues.append(
                    ContentValidationIssue(block.block_order, katex_issue.code, katex_issue.message)
                )

        if asset_catalog is not None:
            for asset_id in asset_refs:
                entry = asset_catalog.get(asset_id)
                if entry is None:
                    issues.append(
                        ContentValidationIssue(
                            block.block_order, "MISSING_ASSET", f"Referenced asset {asset_id!r} not found"
                        )
                    )
                    continue
                is_owner = entry.tenant_id == version.tenant_id
                is_public = entry.visibility_scope == "SYSTEM_PUBLIC"
                is_partner = (
                    entry.visibility_scope == "SHARED_PARTNER"
                    and version.tenant_id in entry.shared_tenant_ids
                )
                if not (is_owner or is_public or is_partner):
                    issues.append(
                        ContentValidationIssue(
                            block.block_order,
                            "CROSS_TENANT_ASSET_FORBIDDEN",
                            f"Asset {asset_id!r} belongs to another tenant and is not public",
                        )
                    )

    return ContentValidationReport(valid=len(issues) == 0, issues=tuple(issues))


def publish_content_version(
    version: ContentVersionState,
    *,
    asset_catalog: Mapping[str, AssetCatalogEntry] | None = None,
) -> ContentVersionState:
    """Validate and freeze a content version as immutable `PUBLISHED`."""
    if version.status == ContentVersionStatus.TAKEN_DOWN:
        raise ContentStudioError("CONTENT_TAKEN_DOWN", "Cannot publish a TAKEN_DOWN content version")
    report = validate_content_version(version, asset_catalog=asset_catalog)
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
