"""H2Edu SEO structured data (JSON-LD), canonical slugs, and fail-closed sitemap policy."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

_ALLOWED_INDEX_RIGHTS: frozenset[str] = frozenset(
    {"OWNED", "LICENSED", "ALLOWED_DERIVATIVE"}
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def to_vietnamese_slug(text: str, *, max_length: int = 96) -> str:
    """Convert Unicode Vietnamese titles into URL-safe lowercase ASCII slugs."""
    normalized = unicodedata.normalize("NFC", text).replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", normalized)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    slug = _NON_ALNUM_RE.sub("-", stripped.lower()).strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug or "hoc-lieu-h2edu"


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
    clean_base = base_url.strip().rstrip("/")
    clean_prefix = route_prefix.strip().strip("/")
    clean_slug = slug.strip().strip("/")
    return f"{clean_base}/{clean_prefix}/{clean_slug}"


def serialize_jsonld_safe(payload: dict[str, Any]) -> str:
    """Serialize JSON-LD while escaping ``<`` to block ``</script>`` HTML breakout XSS."""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


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
