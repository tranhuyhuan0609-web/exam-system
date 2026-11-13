"""H2Edu SEO structured data (JSON-LD), canonical slugs, and fail-closed sitemap policy."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any


class SeoSchemaError(ValueError):
    """Raised when SEO inputs (slug, base URL, route prefix) are unsafe."""


_ALLOWED_INDEX_RIGHTS: frozenset[str] = frozenset(
    {"OWNED", "LICENSED", "ALLOWED_DERIVATIVE"}
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_BASE_URL_RE = re.compile(r"https?://[A-Za-z0-9.-]+(?::\d{1,5})?", re.ASCII)
_PATH_SEGMENT_RE = re.compile(r"[A-Za-z0-9._~-]+", re.ASCII)


def to_vietnamese_slug(text: str, *, max_length: int = 96) -> str:
    """Convert Unicode Vietnamese titles into URL-safe lowercase ASCII slugs."""
    if not isinstance(text, str):
        raise SeoSchemaError("title must be a string")
    if isinstance(max_length, bool) or not isinstance(max_length, int) or max_length < 8:
        raise SeoSchemaError("max_length must be an integer >= 8")
    normalized = unicodedata.normalize("NFC", text).replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", normalized)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    slug = _NON_ALNUM_RE.sub("-", stripped.lower()).strip("-")
    if not slug:
        # Titles without any Latin letter or digit (CJK, Cyrillic, emoji, blank) must still get
        # distinct, stable slugs instead of colliding on a single fallback.
        digest = hashlib.sha256(normalized.strip().encode("utf-8")).hexdigest()[:12]
        return f"hoc-lieu-{digest}"
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug


def is_indexable_in_sitemap(
    *,
    visibility_scope: str,
    publication_status: str,
    rights_status: str,
) -> bool:
    """Fail-closed SEO rule: index ONLY published SYSTEM_PUBLIC items with cleared rights."""
    return (
        visibility_scope == "SYSTEM_PUBLIC"
        and publication_status == "PUBLISHED"
        and rights_status in _ALLOWED_INDEX_RIGHTS
    )


def build_robots_meta(*, indexable: bool, is_exam_room: bool = False) -> str:
    if is_exam_room or not indexable:
        return "noindex, nofollow"
    return "index, follow"


def build_canonical_url(*, base_url: str, route_prefix: str, slug: str) -> str:
    if not all(isinstance(v, str) for v in (base_url, route_prefix, slug)):
        raise SeoSchemaError("base_url, route_prefix and slug must be strings")
    clean_base = base_url.strip().rstrip("/")
    clean_prefix = route_prefix.strip().strip("/")
    clean_slug = slug.strip().strip("/")
    if _BASE_URL_RE.fullmatch(clean_base) is None:
        raise SeoSchemaError("base_url must be an http(s) origin")
    if not clean_slug or _PATH_SEGMENT_RE.fullmatch(clean_slug) is None or ".." in clean_slug:
        raise SeoSchemaError("slug must be a single safe path segment")
    if clean_prefix and not all(
        _PATH_SEGMENT_RE.fullmatch(p) and p not in {".", ".."} for p in clean_prefix.split("/")
    ):
        raise SeoSchemaError("route_prefix contains unsafe path segments")
    path = f"{clean_prefix}/{clean_slug}" if clean_prefix else clean_slug
    return f"{clean_base}/{path}"


def serialize_jsonld_safe(payload: dict[str, Any]) -> str:
    """Serialize JSON-LD while escaping ``<`` to block ``</script>`` HTML breakout XSS."""
    try:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SeoSchemaError("JSON-LD payload is not serialisable") from exc
    return (
        raw.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


def build_learning_resource_jsonld(
    *,
    title: str,
    description: str,
    canonical_url: str,
    subject_name: str,
    grade_level: str,
    publisher_name: str = "H2Edu",
) -> str:
    data = {
        "@context": "https://schema.org",
        "@type": "LearningResource",
        "name": title,
        "description": description,
        "url": canonical_url,
        "inLanguage": "vi",
        "educationalLevel": grade_level,
        "about": {"@type": "Thing", "name": subject_name},
        "publisher": {"@type": "Organization", "name": publisher_name},
    }
    return serialize_jsonld_safe(data)


def build_breadcrumb_jsonld(items: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> str:
    elements = [
        {
            "@type": "ListItem",
            "position": idx,
            "name": name,
            "item": url,
        }
        for idx, (name, url) in enumerate(items, start=1)
    ]
    return serialize_jsonld_safe(
        {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": elements,
        }
    )
