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


# --- regression + boundary tests --------------------------------------------------------------

from app.platform.storage_objects import (  # noqa: E402
    sanitize_download_filename,
    validate_mime_type,
)

_T = uuid.UUID("abcdef12-1234-5678-9abc-def123456789")


@pytest.mark.parametrize(
    "job_id", [".", "..", "...", " . ", "a..b", "", "   ", "a/b", "a\\b", "a b", "\u00e9", "x" * 129]
)
def test_quarantine_job_id_rejects_navigation_and_unsafe_segments(job_id: str) -> None:
    with pytest.raises(StorageKeyError):
        quarantine_object_key(tenant_id=_T, job_id=job_id, sha256=_SHA)


def test_quarantine_key_accepts_normal_job_ids_and_boundary_length() -> None:
    assert quarantine_object_key(tenant_id=_T, job_id="job-9_a.b", sha256=_SHA) == f"quarantine/{_T}/job-9_a.b/{_SHA}"
    assert quarantine_object_key(tenant_id=_T, job_id="x" * 128, sha256=_SHA).count("/") == 3


@pytest.mark.parametrize(
    "mime",
    [
        "text/html\r\nSet-Cookie: a=b",
        "text/html\nX: y",
        "text/html; charset=utf-8",
        "",
        "   ",
        "texthtml",
        "text/",
        "/html",
        "te xt/html",
        "text/ht\u00e9ml",
        "a/b/c",
        None,
        5,
        "text/html\x00",
    ],
)
def test_mime_type_with_crlf_or_junk_is_rejected(mime) -> None:
    with pytest.raises(StorageKeyError):
        build_download_response_headers(filename="a.pdf", mime_type=mime)
    with pytest.raises(StorageKeyError):
        validate_mime_type(mime)


def test_mime_type_is_normalised_and_valid_types_pass() -> None:
    assert validate_mime_type(" Application/PDF ") == "application/pdf"
    assert validate_mime_type("image/svg+xml") == "image/svg+xml"
    assert validate_mime_type("application/vnd.openxmlformats-officedocument.wordprocessingml.document")


def test_inline_downloads_are_sandboxed_attachments_are_not_forced_csp() -> None:
    inline = build_download_response_headers(filename="a.pdf", mime_type="application/pdf", inline=True)
    assert "sandbox" in inline["Content-Security-Policy"]
    assert "default-src 'none'" in inline["Content-Security-Policy"]
    assert inline["Content-Disposition"].startswith("inline;")
    attach = build_download_response_headers(filename="a.pdf", mime_type="application/pdf")
    assert attach["Content-Disposition"].startswith("attachment;")
    assert "Content-Security-Policy" not in attach


def test_cache_control_for_public_immutable_assets() -> None:
    hdr = build_download_response_headers(filename="a.png", mime_type="image/png", public_immutable=True)
    assert hdr["Cache-Control"] == "public, max-age=31536000, immutable"


@pytest.mark.parametrize(
    "variant",
    [
        lambda t: str(t).upper(),
        lambda t: t.hex,
        lambda t: "{" + str(t) + "}",
        lambda t: "urn:uuid:" + str(t),
    ],
)
def test_non_canonical_tenant_uuid_forms_are_rejected(variant) -> None:
    key = f"bronze/{variant(_T)}/bb/{_SHA}"
    with pytest.raises(StorageKeyError):
        verify_object_key_tenant_ownership(object_key=key, expected_tenant_id=_T)


@pytest.mark.parametrize(
    "key",
    [
        "",
        "bronze",
        "bronze/x",
        f"bronze/{_T}",
        f"/bronze/{_T}/a",
        f"bronze/{_T}/a/",
        f"bronze//{_T}/a",
        f"bronze/{_T}/./a",
        f"bronze/{_T}/../a",
        f"bronze/{_T}/a\\b",
        f"bronze/{_T}/a\x00b",
        f"bronze/{_T}/a b",
        f"evil/{_T}/a",
        f"BRONZE/{_T}/a",
        f"bronze/{_T}/\u00e9",
        "bronze/not-a-uuid/a",
        None,
        5,
        "bronze/" + str(_T) + "/" + "a/" * 600 + "b",
    ],
)
def test_malformed_or_traversal_keys_are_rejected(key) -> None:
    with pytest.raises(StorageKeyError):
        verify_object_key_tenant_ownership(object_key=key, expected_tenant_id=_T)


def test_expected_tenant_must_be_a_uuid() -> None:
    with pytest.raises(StorageKeyError):
        verify_object_key_tenant_ownership(object_key=f"bronze/{_T}/bb/{_SHA}", expected_tenant_id=str(_T))  # type: ignore[arg-type]


def test_every_builder_output_verifies_for_its_tenant() -> None:
    dv, aid = uuid.uuid4(), uuid.uuid4()
    keys = [
        bronze_object_key(tenant_id=_T, sha256=_SHA),
        silver_figure_key(tenant_id=_T, document_version_id=dv, page_no=1, region_id="r1"),
        preview_pdf_key(tenant_id=_T, asset_id=aid, sha256=_SHA),
        quarantine_object_key(tenant_id=_T, job_id="j1", sha256=_SHA),
    ]
    for key in keys:
        verify_object_key_tenant_ownership(object_key=key, expected_tenant_id=_T)
        with pytest.raises(StorageKeyError):
            verify_object_key_tenant_ownership(object_key=key, expected_tenant_id=uuid.uuid4())


@pytest.mark.parametrize(
    "digest", ["", "g" * 64, "a" * 63, "a" * 65, "a" * 32 + "\n" + "a" * 32, "\u00e9" * 64, None, 5]
)
def test_sha256_must_be_64_hex(digest) -> None:
    with pytest.raises(StorageKeyError):
        bronze_object_key(tenant_id=_T, sha256=digest)


def test_figure_key_bounds_and_extension_rules() -> None:
    kw = dict(tenant_id=_T, document_version_id=_T, region_id="r")
    assert silver_figure_key(page_no=1, **kw).endswith("p0001_r.png")
    assert silver_figure_key(page_no=9999, ext=".JPEG", **kw).endswith("p9999_r.jpeg")
    for page in (0, -1, 10000, True, 1.5, "1", None):
        with pytest.raises(StorageKeyError):
            silver_figure_key(page_no=page, **kw)
    for ext in ("svg", "png.exe", "", "p/ng", None):
        with pytest.raises(StorageKeyError):
            silver_figure_key(page_no=1, ext=ext, **kw)


def test_preview_pages_bounds_and_non_uuid_ids() -> None:
    assert preview_pdf_key(tenant_id=_T, asset_id=_T, sha256=_SHA, preview_pages=10).endswith("_p10.pdf")
    for pages in (0, 11, -1, True):
        with pytest.raises(StorageKeyError):
            preview_pdf_key(tenant_id=_T, asset_id=_T, sha256=_SHA, preview_pages=pages)
    with pytest.raises(StorageKeyError):
        bronze_object_key(tenant_id="not-a-uuid", sha256=_SHA)  # type: ignore[arg-type]


def test_error_messages_do_not_echo_untrusted_input() -> None:
    with pytest.raises(StorageKeyError) as info:
        quarantine_object_key(tenant_id=_T, job_id="../SECRET-VALUE", sha256=_SHA)
    assert "SECRET-VALUE" not in str(info.value)
    with pytest.raises(StorageKeyError) as info2:
        verify_object_key_tenant_ownership(object_key=f"bronze/{_T}/../SECRET-VALUE", expected_tenant_id=_T)
    assert "SECRET-VALUE" not in str(info2.value)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\a\\b.docx", "b.docx"),
        ('we"ird\r\nname.pdf', "we_ird__name.pdf"),
        ("..", "document.bin"),
        ("   ", "document.bin"),
        ("....", "document.bin"),
        ("a..b.pdf", "document.bin"),
        ("\u0111\u1ec1 thi.pdf", "__ thi.pdf"),
        (None, "document.bin"),
        ("x" * 500, "x" * 120),
    ],
)
def test_filename_sanitisation(raw, expected) -> None:
    assert sanitize_download_filename(raw) == expected
