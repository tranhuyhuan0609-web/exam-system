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


def _init(**overrides):
    kwargs = dict(
        session_id="sess-1",
        tenant_id="tenant-a",
        asset_id="asset-1",
        declared_mime_type="image/png",
        declared_sha256=_VALID_SHA,
        declared_size_bytes=1000,
    )
    kwargs.update(overrides)
    return initiate_upload_session(**kwargs)


def _finalize(session, **overrides):
    kwargs = dict(
        observed_sha256=_VALID_SHA,
        observed_mime_type="image/png",
        observed_size_bytes=1000,
        scan_status=VirusScanStatus.CLEAN,
    )
    kwargs.update(overrides)
    return finalize_upload_session(session, **kwargs)


@pytest.mark.parametrize(
    "field,value",
    [
        ("asset_id", "../../t2/assets/x"),
        ("asset_id", "a/b"),
        ("asset_id", "a.b"),
        ("asset_id", ""),
        ("asset_id", " asset-1"),
        ("asset_id", "asset-1\n"),
        ("asset_id", "a" * 65),
        ("tenant_id", "t1/../t2"),
        ("tenant_id", "   "),
        ("session_id", "s/../x"),
    ],
)
def test_ids_that_would_enter_the_object_key_are_validated(field: str, value: str) -> None:
    # Regression: asset_id="../../t2/assets/x" survived into the object key.
    with pytest.raises(UploadValidationError):
        _init(**{field: value})


def test_object_key_is_built_only_from_validated_parts() -> None:
    session, desc = _init(tenant_id="11111111-2222-3333-4444-555555555555", asset_id="asset_9-A")
    assert desc.object_key == f"private/11111111-2222-3333-4444-555555555555/assets/asset_9-A/{_VALID_SHA}"
    assert ".." not in session.object_key


@pytest.mark.parametrize("size", [0, -1, 100 * 1024 * 1024 + 1, True, 1.5, None])
def test_declared_size_must_be_a_bounded_integer(size: object) -> None:
    with pytest.raises(UploadValidationError):
        _init(declared_size_bytes=size)


@pytest.mark.parametrize("ttl", [0, -1, MAX_SIGNED_URL_TTL_SECONDS + 1, True, 1.5])
def test_put_and_get_ttl_are_bounded_integers(ttl: object) -> None:
    with pytest.raises(SignedUrlPolicyError):
        _init(put_ttl_seconds=ttl)
    session, _ = _init()
    _, record = _finalize(session)
    with pytest.raises(SignedUrlPolicyError):
        resolve_asset_download_plan(
            record,
            actor_tenant_id="tenant-a",
            actor_tier=AssetEntitlementTier.FREE,
            ttl_seconds=ttl,
        )


def test_finalize_rejects_hash_mime_size_and_scan_mismatches() -> None:
    session, _ = _init()
    for override in (
        {"observed_sha256": "b" * 64},
        {"observed_sha256": "zz"},
        {"observed_mime_type": "image/jpeg"},
        {"observed_mime_type": "text/html"},
        {"observed_size_bytes": 999},
        {"observed_size_bytes": 0},
        {"scan_status": VirusScanStatus.INFECTED},
        {"scan_status": VirusScanStatus.PENDING},
        {"scan_status": "WEIRD"},
    ):
        with pytest.raises(UploadValidationError):
            _finalize(session, **override)


def test_finalized_session_cannot_be_finalized_twice_and_pdf_needs_page_count() -> None:
    session, _ = _init()
    finalized, _ = _finalize(session)
    with pytest.raises(UploadValidationError):
        _finalize(finalized)
    pdf, _ = _init(declared_mime_type="application/pdf")
    for pages in (None, 0, -2, True):
        with pytest.raises(UploadValidationError):
            _finalize(pdf, observed_mime_type="application/pdf", page_count=pages)


def _record(visibility: AssetVisibility = AssetVisibility.TENANT_PRIVATE):
    session, _ = _init(visibility=visibility)
    _, record = _finalize(session)
    return record


@pytest.mark.parametrize("actor", ["", "   ", "tenant-b", None])
def test_wrong_or_empty_actor_tenant_gets_404(actor: object) -> None:
    with pytest.raises(AssetNotFoundError):
        resolve_asset_download_plan(_record(), actor_tenant_id=actor, actor_tier=AssetEntitlementTier.STUDENT_PRO)


def test_system_public_asset_is_readable_by_any_non_empty_tenant_but_not_by_empty() -> None:
    record = _record(AssetVisibility.SYSTEM_PUBLIC)
    plan = resolve_asset_download_plan(record, actor_tenant_id="tenant-z", actor_tier=AssetEntitlementTier.FREE)
    assert plan.variant == AssetVariant.IMAGE
    with pytest.raises(AssetNotFoundError):
        resolve_asset_download_plan(record, actor_tenant_id="", actor_tier=AssetEntitlementTier.FREE)


def test_unknown_tier_or_visibility_fails_closed() -> None:
    with pytest.raises(SignedUrlPolicyError):
        resolve_asset_download_plan(_record(), actor_tenant_id="tenant-a", actor_tier="GOD")
    with pytest.raises(UploadValidationError):
        _init(visibility="PUBLIC_ISH")
