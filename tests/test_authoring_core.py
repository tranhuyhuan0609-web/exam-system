"""Pure unit tests for B02 Block Studio validators (module authoring)."""

from __future__ import annotations

import pytest

from app.modules.authoring.public import (
    publish_validated_content_version,
    validate_content_version,
    validate_katex_syntax,
)
from app.modules.content.public import (
    AssetCatalogEntry,
    ContentBlockDraft,
    ContentBlockType,
    ContentStudioError,
    ContentType,
    ContentVersionState,
)


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
        publish_validated_content_version(draft, asset_catalog=catalog)
    assert exc.value.code == "CROSS_TENANT_ASSET_FORBIDDEN"
