"""Tests for B03 media & asset management core rules."""

from __future__ import annotations

import pytest

from app.modules.assets.public import (
    MAX_SIGNED_URL_TTL_SECONDS,
    AssetEntitlementTier,
    AssetNotFoundError,
    AssetVariant,
    AssetVisibility,
    SignedUrlPolicyError,
    UploadSessionStatus,
    UploadValidationError,
    VirusScanStatus,
    finalize_upload_session,
    initiate_upload_session,
    resolve_asset_download_plan,
)

_VALID_SHA = "a" * 64


def test_two_step_upload_and_preview_generation_for_pdf() -> None:
    session, put_desc = initiate_upload_session(
        session_id="sess-1",
        tenant_id="tenant-a",
        asset_id="asset-1",
        declared_mime_type="application/pdf",
        declared_sha256=_VALID_SHA.upper(),
        declared_size_bytes=102400,
        requires_pro=True,
    )
    assert session.status == UploadSessionStatus.INITIATED
    assert put_desc.bucket_scope == "private"
    assert put_desc.object_key.startswith("private/tenant-a/")

    finalized, asset = finalize_upload_session(
        session,
        observed_sha256=_VALID_SHA,
        observed_mime_type="application/pdf",
        observed_size_bytes=102400,
        scan_status=VirusScanStatus.CLEAN,
        page_count=12,
    )
    assert finalized.status == UploadSessionStatus.FINALIZED
    assert asset.preview_pages == 2
    assert asset.preview_object_key is not None


def test_finalize_rejects_checksum_mismatch_mime_mismatch_and_malware() -> None:
    session, _ = initiate_upload_session(
        session_id="sess-2",
        tenant_id="tenant-a",
        asset_id="asset-2",
        declared_mime_type="image/png",
        declared_sha256=_VALID_SHA,
        declared_size_bytes=4096,
    )

    with pytest.raises(UploadValidationError, match="sha256"):
        finalize_upload_session(
            session,
            observed_sha256="b" * 64,
            observed_mime_type="image/png",
            observed_size_bytes=4096,
            scan_status=VirusScanStatus.CLEAN,
        )

    with pytest.raises(UploadValidationError, match="mime"):
        finalize_upload_session(
            session,
            observed_sha256=_VALID_SHA,
            observed_mime_type="image/jpeg",
            observed_size_bytes=4096,
            scan_status=VirusScanStatus.CLEAN,
        )

    with pytest.raises(UploadValidationError, match="INFECTED"):
        finalize_upload_session(
            session,
            observed_sha256=_VALID_SHA,
            observed_mime_type="image/png",
            observed_size_bytes=4096,
            scan_status=VirusScanStatus.INFECTED,
        )


def test_free_user_gets_only_preview_pdf_never_original_url() -> None:
    session, _ = initiate_upload_session(
        session_id="sess-3",
        tenant_id="tenant-a",
        asset_id="asset-pro-pdf",
        declared_mime_type="application/pdf",
        declared_sha256=_VALID_SHA,
        declared_size_bytes=50000,
        requires_pro=True,
    )
    _, asset = finalize_upload_session(
        session,
        observed_sha256=_VALID_SHA,
        observed_mime_type="application/pdf",
        observed_size_bytes=50000,
        scan_status=VirusScanStatus.CLEAN,
        page_count=18,
    )

    free_plan = resolve_asset_download_plan(
        asset,
        actor_tenant_id="tenant-a",
        actor_tier=AssetEntitlementTier.FREE,
    )
    assert free_plan.variant == AssetVariant.PREVIEW_PDF
    assert free_plan.page_limit == 2
    assert free_plan.object_key == asset.preview_object_key
    assert free_plan.object_key != asset.original_object_key

    pro_plan = resolve_asset_download_plan(
        asset,
        actor_tenant_id="tenant-a",
        actor_tier=AssetEntitlementTier.STUDENT_PRO,
    )
    assert pro_plan.variant == AssetVariant.ORIGINAL_PDF
    assert pro_plan.page_limit is None
    assert pro_plan.object_key == asset.original_object_key


def test_cross_tenant_private_asset_returns_not_found_and_enforces_ttl_cap() -> None:
    session, _ = initiate_upload_session(
        session_id="sess-4",
        tenant_id="tenant-a",
        asset_id="secret-asset",
        declared_mime_type="application/pdf",
        declared_sha256=_VALID_SHA,
        declared_size_bytes=2048,
        visibility=AssetVisibility.TENANT_PRIVATE,
    )
    _, asset = finalize_upload_session(
        session,
        observed_sha256=_VALID_SHA,
        observed_mime_type="application/pdf",
        observed_size_bytes=2048,
        scan_status=VirusScanStatus.CLEAN,
        page_count=1,
    )

    with pytest.raises(AssetNotFoundError):
        resolve_asset_download_plan(
            asset,
            actor_tenant_id="tenant-b",
            actor_tier=AssetEntitlementTier.SCHOOL_ENTERPRISE,
        )

    with pytest.raises(SignedUrlPolicyError, match="TTL"):
        resolve_asset_download_plan(
            asset,
            actor_tenant_id="tenant-a",
            actor_tier=AssetEntitlementTier.STUDENT_PRO,
            ttl_seconds=MAX_SIGNED_URL_TTL_SECONDS + 1,
        )
