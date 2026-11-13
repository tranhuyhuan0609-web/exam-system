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
