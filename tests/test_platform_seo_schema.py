"""Tests for H2Edu SEO structured data, slugifier, and fail-closed sitemap policy (P66)."""

from __future__ import annotations

import json

from app.platform.seo_schema import (
    build_breadcrumb_jsonld,
    build_canonical_url,
    build_learning_resource_jsonld,
    build_robots_meta,
    is_indexable_in_sitemap,
    to_vietnamese_slug,
)


def test_vietnamese_slug_and_canonical_url() -> None:
    slug = to_vietnamese_slug("Đề thi Toán 12 — Công thức Bayes & Xác suất có điều kiện")
    assert slug == "de-thi-toan-12-cong-thuc-bayes-xac-suat-co-dieu-kien"
    assert (
        build_canonical_url(base_url="https://h2edu.vn/", route_prefix="/hoc-lieu/", slug=slug)
        == "https://h2edu.vn/hoc-lieu/de-thi-toan-12-cong-thuc-bayes-xac-suat-co-dieu-kien"
    )


def test_fail_closed_sitemap_and_robots_meta_block_draft_and_exam_room() -> None:
    assert (
        is_indexable_in_sitemap(
            visibility_scope="SYSTEM_PUBLIC",
            publication_status="PUBLISHED",
            rights_status="LICENSED",
        )
        is True
    )
    assert (
        is_indexable_in_sitemap(
            visibility_scope="SYSTEM_PUBLIC",
            publication_status="PUBLISHED",
            rights_status="PENDING_REVIEW",
        )
        is False
    )
    assert (
        is_indexable_in_sitemap(
            visibility_scope="TENANT_PRIVATE",
            publication_status="PUBLISHED",
            rights_status="OWNED",
        )
        is False
    )

    assert build_robots_meta(indexable=True, is_exam_room=False) == "index, follow"
    assert build_robots_meta(indexable=True, is_exam_room=True) == "noindex, nofollow"
    assert build_robots_meta(indexable=False, is_exam_room=False) == "noindex, nofollow"


def test_jsonld_escapes_script_breakout_sequences() -> None:
    raw = build_learning_resource_jsonld(
        title="Toán 12 </script><script>alert(1)</script>",
        description="Hệ sinh thái H2Edu",
        canonical_url="https://h2edu.vn/hoc-lieu/toan-12",
        subject_name="Toán học",
        grade_level="Lớp 12",
    )
    assert "</script>" not in raw
    assert "\\u003c/script\\u003e" in raw
    parsed = json.loads(raw)
    assert parsed["@type"] == "LearningResource"
    assert parsed["publisher"]["name"] == "H2Edu"

    crumb = json.loads(
        build_breadcrumb_jsonld(
            [
                ("Trang chủ", "https://h2edu.vn/"),
                ("Toán 12", "https://h2edu.vn/toan-12"),
            ]
        )
    )
    assert crumb["@type"] == "BreadcrumbList"
    assert len(crumb["itemListElement"]) == 2


# --- regression + boundary tests --------------------------------------------------------------

import pytest  # noqa: E402

from app.platform.seo_schema import SeoSchemaError, serialize_jsonld_safe  # noqa: E402


def test_non_latin_titles_get_distinct_stable_slugs() -> None:
    cjk = to_vietnamese_slug("数学试题")
    cyr = to_vietnamese_slug("Математика")
    emoji = to_vietnamese_slug("\U0001f4da\U0001f4da")
    assert len({cjk, cyr, emoji}) == 3
    assert cjk == to_vietnamese_slug("数学试题")  # stable
    for slug in (cjk, cyr, emoji):
        assert slug.startswith("hoc-lieu-") and slug.isascii()


def test_blank_titles_still_produce_a_valid_slug() -> None:
    for blank in ("", "   ", "---", "!!!"):
        assert to_vietnamese_slug(blank).startswith("hoc-lieu-")


def test_latin_content_never_uses_the_hash_fallback() -> None:
    assert to_vietnamese_slug("Toán 12 数学") == "toan-12"
    assert to_vietnamese_slug("Đ") == "d"


def test_slug_length_boundary_and_trailing_dash_trim() -> None:
    long_title = "a-" * 100
    slug = to_vietnamese_slug(long_title, max_length=10)
    assert len(slug) <= 10 and not slug.endswith("-")
    assert to_vietnamese_slug("abcdefgh", max_length=8) == "abcdefgh"
    with pytest.raises(SeoSchemaError):
        to_vietnamese_slug("x", max_length=0)
    with pytest.raises(SeoSchemaError):
        to_vietnamese_slug(None)  # type: ignore[arg-type]


def test_decomposed_and_composed_vietnamese_give_the_same_slug() -> None:
    import unicodedata

    title = "Hàm số bậc hai"
    assert to_vietnamese_slug(unicodedata.normalize("NFD", title)) == to_vietnamese_slug(title) == "ham-so-bac-hai"


def test_slug_has_only_url_safe_characters() -> None:
    slug = to_vietnamese_slug("A/B\\C?D#E%F\r\nG ../..")
    assert set(slug) <= set("abcdefghijklmnopqrstuvwxyz0123456789-")


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(base_url="javascript:alert(1)", route_prefix="x", slug="a"),
        dict(base_url="//evil.com", route_prefix="x", slug="a"),
        dict(base_url="https://h2edu.vn", route_prefix="x", slug="../admin"),
        dict(base_url="https://h2edu.vn", route_prefix="x", slug="a/b"),
        dict(base_url="https://h2edu.vn", route_prefix="x", slug="a b"),
        dict(base_url="https://h2edu.vn", route_prefix="x", slug=""),
        dict(base_url="https://h2edu.vn", route_prefix="../x", slug="a"),
        dict(base_url="https://h2edu.vn", route_prefix="x", slug="a?b=1"),
        dict(base_url="https://h2edu.vn", route_prefix="x", slug="a\nb"),
        dict(base_url=None, route_prefix="x", slug="a"),
    ],
)
def test_canonical_url_rejects_unsafe_parts(kwargs) -> None:
    with pytest.raises(SeoSchemaError):
        build_canonical_url(**kwargs)


def test_canonical_url_with_empty_prefix_and_nested_prefix() -> None:
    assert build_canonical_url(base_url="https://h2edu.vn/", route_prefix="", slug="a") == "https://h2edu.vn/a"
    assert (
        build_canonical_url(base_url="https://h2edu.vn", route_prefix="/a/b/", slug="/c/") == "https://h2edu.vn/a/b/c"
    )


@pytest.mark.parametrize(
    "visibility,publication,rights",
    [
        ("SYSTEM_PUBLIC", "DRAFT", "LICENSED"),
        ("SYSTEM_PUBLIC", "ARCHIVED", "OWNED"),
        ("SYSTEM_PUBLIC", "PUBLISHED", "TAKEDOWN"),
        ("SYSTEM_PUBLIC", "PUBLISHED", "DISPUTED"),
        ("SYSTEM_PUBLIC", "PUBLISHED", ""),
        ("SYSTEM_PUBLIC", "PUBLISHED", None),
        ("system_public", "PUBLISHED", "OWNED"),
        ("SYSTEM_PUBLIC", "published", "OWNED"),
        ("SYSTEM_PUBLIC", "PUBLISHED", "licensed"),
        (None, None, None),
    ],
)
def test_sitemap_policy_fails_closed(visibility, publication, rights) -> None:
    assert not is_indexable_in_sitemap(
        visibility_scope=visibility, publication_status=publication, rights_status=rights
    )


@pytest.mark.parametrize("rights", ["OWNED", "LICENSED", "ALLOWED_DERIVATIVE"])
def test_sitemap_policy_allows_cleared_rights(rights: str) -> None:
    assert is_indexable_in_sitemap(
        visibility_scope="SYSTEM_PUBLIC", publication_status="PUBLISHED", rights_status=rights
    )


def test_jsonld_escapes_ampersand_angle_brackets_and_line_separators() -> None:
    out = serialize_jsonld_safe({"a": "<b>&  "})
    assert "<" not in out and ">" not in out and "&" not in out
    assert " " not in out and " " not in out
    assert json.loads(out) == {"a": "<b>&  "}


def test_jsonld_rejects_non_finite_and_unserialisable_values() -> None:
    for bad in ({"a": float("nan")}, {"a": object()}):
        with pytest.raises(SeoSchemaError):
            serialize_jsonld_safe(bad)


def test_breadcrumb_positions_start_at_one_and_empty_list_is_valid() -> None:
    crumbs = json.loads(build_breadcrumb_jsonld([("A", "https://h2edu.vn/a"), ("B", "https://h2edu.vn/b")]))
    assert [e["position"] for e in crumbs["itemListElement"]] == [1, 2]
    assert json.loads(build_breadcrumb_jsonld([]))["itemListElement"] == []


def test_breadcrumb_values_cannot_break_out_of_the_script_tag() -> None:
    raw = build_breadcrumb_jsonld([("</script><img src=x onerror=1>", "https://h2edu.vn/")])
    assert "</script>" not in raw and "<img" not in raw
