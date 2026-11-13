"""Tests for app.platform.storage_objects key builder, tenant check, and download headers."""

from __future__ import annotations

import uuid

import pytest

from app.platform.storage_objects import (
    StorageKeyError,
    bronze_object_key,
    build_download_response_headers,
    preview_pdf_key,
    quarantine_object_key,
    silver_figure_key,
    verify_object_key_tenant_ownership,
)

_SHA = "b" * 64


def test_canonical_keys_and_tenant_ownership_verification() -> None:
    t1 = uuid.uuid4()
    t2 = uuid.uuid4()
    dv = uuid.uuid4()
    aid = uuid.uuid4()

    bkey = bronze_object_key(tenant_id=t1, sha256=_SHA.upper())
    assert bkey == f"bronze/{t1}/bb/{_SHA}"
    verify_object_key_tenant_ownership(object_key=bkey, expected_tenant_id=t1)

    with pytest.raises(StorageKeyError, match="tenant"):
        verify_object_key_tenant_ownership(object_key=bkey, expected_tenant_id=t2)

    skey = silver_figure_key(tenant_id=t1, document_version_id=dv, page_no=3, region_id="fig_5", ext="PNG")
    assert skey == f"silver/{t1}/{dv}/p0003_fig_5.png"

    pkey = preview_pdf_key(tenant_id=t1, asset_id=aid, sha256=_SHA, preview_pages=2)
    assert pkey == f"previews/{t1}/{aid}/{_SHA}_p2.pdf"

    qkey = quarantine_object_key(tenant_id=t1, job_id="job-9", sha256=_SHA)
    assert qkey.startswith(f"quarantine/{t1}/")


def test_blocks_path_traversal_and_sanitizes_content_disposition() -> None:
    t1 = uuid.uuid4()
    with pytest.raises(StorageKeyError):
        silver_figure_key(tenant_id=t1, document_version_id=uuid.uuid4(), page_no=1, region_id="../etc")

    with pytest.raises(StorageKeyError):
        verify_object_key_tenant_ownership(object_key=f"bronze/{t1}/../secret", expected_tenant_id=t1)

    hdrs = build_download_response_headers(
        filename='../../evil"\r\nX-Inject: 1.pdf',
        mime_type="application/pdf",
        inline=False,
        public_immutable=False,
    )
    assert hdrs["X-Content-Type-Options"] == "nosniff"
    assert hdrs["Cache-Control"] == "private, no-store"
    assert "\r" not in hdrs["Content-Disposition"]
    assert "\n" not in hdrs["Content-Disposition"]
    assert ".." not in hdrs["Content-Disposition"]
