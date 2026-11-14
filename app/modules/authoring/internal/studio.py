"""B02 Block Studio validators: KaTeX syntax and cross-tenant asset checks."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.modules.content.public import (
    AssetCatalogEntry,
    ContentBlockDraft,
    ContentBlockType,
    ContentStudioError,
    ContentValidationIssue,
    ContentValidationReport,
    ContentVersionState,
    publish_content_version,
)

_ENV_TAG_RE = re.compile(r"\\(begin|end)\{([a-zA-Z0-9*]+)\}")
_FORBIDDEN_KATEX_CMD_RE = re.compile(
    r"\\(?:url|href|input|include|write18|write|openout|catcode|def|newcommand|renewcommand|"
    r"htmlClass|htmlId|htmlStyle|htmlData|includegraphics)\b"
)


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

        if _FORBIDDEN_KATEX_CMD_RE.search(span):
            issues.append(
                ContentValidationIssue(None, "FORBIDDEN_KATEX_COMMAND", "Forbidden LaTeX macro in KaTeX block")
            )

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


MAX_PAYLOAD_DEPTH = 32
MAX_PAYLOAD_NODES = 20_000
_MEDIA_URL_KEYS = frozenset({"image_url", "video_url", "src", "thumbnail_url", "file_url", "download_url", "poster"})
_ASSET_SCHEME = "asset://"


@dataclass(slots=True)
class _Collected:
    texts: list[str] = field(default_factory=list)
    asset_ids: list[str] = field(default_factory=list)
    problems: list[tuple[str, str]] = field(default_factory=list)  # (code, message)


def _is_asset_key(key: str) -> bool:
    return "asset" in key.lower()


def _collect_asset_values(value: Any, out: _Collected, depth: int) -> None:
    """Every scalar below an asset-named key is an asset id; anything that is not a non-empty string is invalid."""
    stack: list[tuple[Any, int]] = [(value, depth)]
    while stack:
        current, level = stack.pop()
        if level > MAX_PAYLOAD_DEPTH:
            out.problems.append(("PAYLOAD_TOO_DEEP", f"Block payload nests deeper than {MAX_PAYLOAD_DEPTH} levels"))
            return
        if isinstance(current, str):
            ref = current.strip()
            if ref.startswith(_ASSET_SCHEME):
                ref = ref[len(_ASSET_SCHEME) :].strip()
            if ref:
                out.asset_ids.append(ref)
            else:
                out.problems.append(("INVALID_ASSET_REFERENCE", "Asset reference must be a non-empty string"))
        elif isinstance(current, Mapping):
            stack.extend((v, level + 1) for v in current.values())
        elif isinstance(current, Sequence) and not isinstance(current, (bytes, bytearray)):
            stack.extend((v, level + 1) for v in current)
        else:
            out.problems.append(("INVALID_ASSET_REFERENCE", "Asset reference must be a non-empty string"))


def _collect_text_and_asset_refs(payload: Any) -> _Collected:
    """Walk a block payload iteratively (no recursion) with depth and size limits."""
    out = _Collected()
    stack: list[tuple[Any, int]] = [(payload, 0)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_PAYLOAD_NODES:
            out.problems.append(("PAYLOAD_TOO_LARGE", f"Block payload has more than {MAX_PAYLOAD_NODES} nodes"))
            break
        if depth > MAX_PAYLOAD_DEPTH:
            out.problems.append(("PAYLOAD_TOO_DEEP", f"Block payload nests deeper than {MAX_PAYLOAD_DEPTH} levels"))
            break
        if isinstance(value, str):
            if value.strip().startswith(_ASSET_SCHEME):
                _collect_asset_values(value, out, depth)
            else:
                out.texts.append(value)
        elif isinstance(value, Mapping):
            for key, item in value.items():
                key_text = str(key)
                if _is_asset_key(key_text):
                    _collect_asset_values(item, out, depth + 1)
                elif key_text.lower() in _MEDIA_URL_KEYS:
                    if isinstance(item, str) and item.strip().startswith(_ASSET_SCHEME):
                        _collect_asset_values(item, out, depth + 1)
                    elif item not in (None, ""):
                        out.problems.append(
                            ("EXTERNAL_MEDIA_URL_FORBIDDEN", f"Media field {key_text!r} must reference an asset://<id>")
                        )
                else:
                    stack.append((item, depth + 1))
        elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            stack.extend((item, depth + 1) for item in value)
    return out


def collect_asset_references(blocks: Iterable[ContentBlockDraft]) -> list[str]:
    """Distinct asset ids referenced by the blocks (`asset://<id>` and asset-named fields), in first-seen order."""
    seen: dict[str, None] = {}
    for block in blocks:
        if isinstance(block.payload_json, Mapping):
            for asset_id in _collect_text_and_asset_refs(block.payload_json).asset_ids:
                seen.setdefault(asset_id, None)
    return list(seen)


def validate_content_version(
    version: ContentVersionState,
    *,
    asset_catalog: Mapping[str, AssetCatalogEntry],
) -> ContentValidationReport:
    """Validate structure, KaTeX syntax, block ordering, and cross-tenant asset permissions (catalog required)."""
    if not isinstance(asset_catalog, Mapping):
        raise ContentStudioError("ASSET_CATALOG_REQUIRED", "An asset catalog is required to validate content")
    issues: list[ContentValidationIssue] = []

    if not isinstance(version.tenant_id, str) or not version.tenant_id.strip():
        issues.append(ContentValidationIssue(None, "MISSING_TENANT", "Content version must carry a tenant id"))

    if not version.title or not version.title.strip():
        issues.append(ContentValidationIssue(None, "MISSING_TITLE", "Content title must be non-empty"))

    if version.summary_md:
        issues.extend(validate_katex_syntax(version.summary_md))

    seen_orders: set[int] = set()
    for block in version.blocks:
        order = block.block_order
        if isinstance(order, bool) or not isinstance(order, int):
            issues.append(ContentValidationIssue(None, "INVALID_BLOCK_ORDER", "block_order must be an integer"))
            continue
        if order < 0:
            issues.append(ContentValidationIssue(order, "INVALID_BLOCK_ORDER", "block_order must be >= 0"))
        if order in seen_orders:
            issues.append(ContentValidationIssue(order, "DUPLICATE_BLOCK_ORDER", f"Duplicate block_order={order}"))
        seen_orders.add(order)

        if block.block_type not in set(ContentBlockType) or not isinstance(block.block_type, ContentBlockType):
            issues.append(ContentValidationIssue(order, "INVALID_BLOCK_TYPE", "Unknown block_type"))
            continue
        if not isinstance(block.payload_json, Mapping):
            issues.append(ContentValidationIssue(order, "INVALID_BLOCK_PAYLOAD", "payload_json must be an object"))
            continue

        if block.block_type == ContentBlockType.INTERACTIVE_QUESTION_EMBED:
            qid = block.payload_json.get("question_id")
            if not isinstance(qid, str) or not qid.strip():
                issues.append(
                    ContentValidationIssue(
                        order,
                        "MISSING_EMBEDDED_QUESTION_ID",
                        "INTERACTIVE_QUESTION_EMBED requires question_id",
                    )
                )

        collected = _collect_text_and_asset_refs(block.payload_json)
        for code, message in collected.problems:
            issues.append(ContentValidationIssue(order, code, message))

        for txt in collected.texts:
            for katex_issue in validate_katex_syntax(txt):
                issues.append(ContentValidationIssue(order, katex_issue.code, katex_issue.message))

        for asset_id in collected.asset_ids:
            entry = asset_catalog.get(asset_id)
            if entry is None:
                issues.append(
                    ContentValidationIssue(order, "MISSING_ASSET", f"Referenced asset {asset_id!r} not found")
                )
                continue
            tenant_known = isinstance(version.tenant_id, str) and bool(version.tenant_id.strip())
            is_owner = tenant_known and entry.tenant_id == version.tenant_id
            is_public = entry.visibility_scope == "SYSTEM_PUBLIC"
            is_partner = (
                tenant_known
                and entry.visibility_scope == "SHARED_PARTNER"
                and version.tenant_id in entry.shared_tenant_ids
            )
            if not (is_owner or is_public or is_partner):
                issues.append(
                    ContentValidationIssue(
                        order,
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
    if not isinstance(asset_catalog, Mapping):
        raise ContentStudioError("ASSET_CATALOG_REQUIRED", "An asset catalog is required to publish content")
    return publish_content_version(
        version,
        validator=lambda candidate: validate_content_version(candidate, asset_catalog=asset_catalog),
    )
