"""Pure unit tests for B01 Versioning & B02 VietJack Block Studio (P31)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.content_studio.public import (
    AssetCatalogEntry,
    ContentBlockDraft,
    ContentBlockType,
    ContentStudioError,
    ContentType,
    ContentVersionState,
    ContentVersionStatus,
    edit_draft_blocks,
    fork_next_content_version,
    publish_content_version,
    validate_content_version,
    validate_katex_syntax,
)


def test_enums_match_ddl_check_constraints() -> None:
    ddl = Path("docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql").read_text(encoding="utf-8")
    for enum_cls in (ContentType, ContentVersionStatus, ContentBlockType):
        for member in enum_cls:
            assert f"'{member.value}'" in ddl


def test_katex_validator_detects_unclosed_delimiters_braces_and_environments() -> None:
    assert validate_katex_syntax(r"Công thức $P(A|B) = \frac{P(B|A)P(A)}{P(B)}$ và $$x^2 + y^2 = R^2$$.") == []
    assert validate_katex_syntax(r"Môi trường \(\begin{cases} x > 0 \\ y > 0 \end{cases}\) hợp lệ.") == []
    # Escaped literal brace / dollar must not false-positive
    assert validate_katex_syntax(r"Giá là \$100 và tập $\{1, 2\}$") == []

    bad_unclosed = validate_katex_syntax(r"Thiếu dấu đóng $\frac{1}{2}")
    assert bad_unclosed[0].code == "UNCLOSED_KATEX_INLINE"

    bad_brace = validate_katex_syntax(r"Thiếu ngoặc $\frac{1}{2$")
    assert bad_brace[0].code == "UNBALANCED_KATEX_BRACE"

    bad_env = validate_katex_syntax(r"Sai cặp $\begin{aligned} x = 1 \end{cases}$")
    assert bad_env[0].code == "MISMATCHED_KATEX_ENVIRONMENT"


def test_cross_tenant_embedded_asset_blocked_and_own_or_public_assets_allowed() -> None:
    catalog = {
        "asset-own": AssetCatalogEntry("asset-own", "tenant-a", "TENANT_PRIVATE"),
        "asset-public": AssetCatalogEntry("asset-public", "tenant-sys", "SYSTEM_PUBLIC"),
        "asset-other": AssetCatalogEntry("asset-other", "tenant-b", "TENANT_PRIVATE"),
    }
    draft = ContentVersionState(
        content_item_id="item-1",
        tenant_id="tenant-a",
        content_type=ContentType.LESSON,
        version_no=1,
        title="Công thức Bayes",
        summary_md="Tóm tắt $P(A|B)$",
        blocks=(
            ContentBlockDraft(0, ContentBlockType.THEORY_SUMMARY, {"markdown": "Xem $P(A)$", "asset_id": "asset-own"}),
            ContentBlockDraft(
                1,
                ContentBlockType.FORMULA_BOX,
                {"markdown": "$$P(A)=1$$", "image_asset_id": "asset-public"},
            ),
            ContentBlockDraft(
                2,
                ContentBlockType.WORKED_EXAMPLE,
                {"markdown": "Hình minh họa", "asset_ids": ["asset-other"]},
            ),
        ),
    )
    report = validate_content_version(draft, asset_catalog=catalog)
    assert report.valid is False
    assert any(i.code == "CROSS_TENANT_ASSET_FORBIDDEN" and i.block_order == 2 for i in report.issues)

    with pytest.raises(ContentStudioError) as exc:
        publish_content_version(draft, asset_catalog=catalog)
    assert exc.value.code == "CROSS_TENANT_ASSET_FORBIDDEN"


def test_publishing_freezes_version_and_forking_creates_v_plus_1_draft() -> None:
    catalog = {"asset-own": AssetCatalogEntry("asset-own", "tenant-a", "TENANT_PRIVATE")}
    v1 = ContentVersionState(
        content_item_id="item-9",
        tenant_id="tenant-a",
        content_type=ContentType.ARTICLE,
        version_no=1,
        title="Bài 1: Đạo hàm",
        summary_md=r"Định nghĩa $f'(x_0)$",
        blocks=(
            ContentBlockDraft(
                0,
                ContentBlockType.METHOD_STEPS,
                {"steps": ["Bước 1", "Bước 2"], "asset_id": "asset-own"},
            ),
            ContentBlockDraft(1, ContentBlockType.INTERACTIVE_QUESTION_EMBED, {"question_id": "q-101"}),
        ),
    )
    published_v1 = publish_content_version(v1, asset_catalog=catalog)
    assert published_v1.status == ContentVersionStatus.PUBLISHED
    assert published_v1.is_immutable is True

    with pytest.raises(ContentStudioError) as imm_err:
        edit_draft_blocks(published_v1, title="Sửa trực tiếp")
    assert imm_err.value.code == "CONTENT_VERSION_IMMUTABLE"

    kept_v1, draft_v2 = fork_next_content_version(published_v1, created_by="editor-1")
    assert kept_v1.version_no == 1 and kept_v1.is_immutable is True
    assert draft_v2.version_no == 2
    assert draft_v2.status == ContentVersionStatus.DRAFT
    assert draft_v2.is_immutable is False

    edited_v2 = edit_draft_blocks(draft_v2, title="Bài 1: Đạo hàm (Cập nhật)")
    assert edited_v2.title == "Bài 1: Đạo hàm (Cập nhật)"
    assert kept_v1.title == "Bài 1: Đạo hàm"
