"""B02 Block Studio validators: KaTeX syntax and cross-tenant asset checks."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.modules.content.public import (
    AssetCatalogEntry,
    ContentBlockType,
    ContentValidationIssue,
    ContentValidationReport,
    ContentVersionState,
    publish_content_version,
)

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


def publish_validated_content_version(
    version: ContentVersionState,
    *,
    asset_catalog: Mapping[str, AssetCatalogEntry],
) -> ContentVersionState:
    """Publish through B01 using the B02 validators; the asset catalog is mandatory."""
    return publish_content_version(
        version,
        validator=lambda candidate: validate_content_version(candidate, asset_catalog=asset_catalog),
    )
