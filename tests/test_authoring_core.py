"""Pure unit tests for B02 Block Studio validators (module authoring)."""

from __future__ import annotations

import unicodedata

import pytest

from app.modules.authoring.public import (
    MAX_PAYLOAD_DEPTH,
    MAX_PAYLOAD_NODES,
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

    bad_cmd = validate_katex_syntax(r"Macro nguy hiểm $\href{javascript:alert(1)}{x}$")
    assert bad_cmd[0].code == "FORBIDDEN_KATEX_COMMAND"


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


_CATALOG = {
    "own": AssetCatalogEntry("own", "tenant-a", "TENANT_PRIVATE"),
    "public": AssetCatalogEntry("public", "tenant-sys", "SYSTEM_PUBLIC"),
    "foreign": AssetCatalogEntry("foreign", "tenant-b", "TENANT_PRIVATE"),
    "partner": AssetCatalogEntry("partner", "tenant-b", "SHARED_PARTNER", frozenset({"tenant-a"})),
    "partner-other": AssetCatalogEntry("partner-other", "tenant-b", "SHARED_PARTNER", frozenset({"tenant-c"})),
}


def _version(*payloads: object, tenant: str = "tenant-a", **kw: object) -> ContentVersionState:
    blocks = tuple(
        ContentBlockDraft(i, ContentBlockType.THEORY_SUMMARY, payload)  # type: ignore[arg-type]
        for i, payload in enumerate(payloads)
    )
    base: dict[str, object] = {
        "content_item_id": "item-1",
        "tenant_id": tenant,
        "content_type": ContentType.LESSON,
        "version_no": 1,
        "title": "Bài học",
        "summary_md": "",
        "blocks": blocks,
    }
    base.update(kw)
    return ContentVersionState(**base)  # type: ignore[arg-type]


def _codes(version: ContentVersionState, catalog: object = _CATALOG) -> list[str]:
    return [i.code for i in validate_content_version(version, asset_catalog=catalog).issues]  # type: ignore[arg-type]


def test_asset_catalog_is_mandatory_for_validation_and_publication() -> None:
    version = _version({"asset_id": "foreign"})
    with pytest.raises(TypeError):
        validate_content_version(version)  # type: ignore[call-arg]
    for missing in (None, [], "catalog"):
        with pytest.raises(ContentStudioError) as exc:
            validate_content_version(version, asset_catalog=missing)  # type: ignore[arg-type]
        assert exc.value.code == "ASSET_CATALOG_REQUIRED"
        with pytest.raises(ContentStudioError) as exc2:
            publish_validated_content_version(version, asset_catalog=missing)  # type: ignore[arg-type]
        assert exc2.value.code == "ASSET_CATALOG_REQUIRED"


def test_asset_references_under_any_key_shape_are_checked() -> None:
    bypasses = [
        {"asset_id": ["foreign"]},
        {"asset_id": {"nested": "foreign"}},
        {"image_asset_id": "foreign"},
        {"cover": {"hero_asset_id": "foreign"}},
        {"gallery": [{"asset_ids": ["own", "foreign"]}]},
        {"asset_ids": ("foreign",)},
        {"thumbnail_asset": "foreign"},
        {"image_url": "asset://foreign"},
        {"note": "asset://foreign"},
        {"items": [["asset://foreign"]]},
    ]
    for payload in bypasses:
        assert "CROSS_TENANT_ASSET_FORBIDDEN" in _codes(_version(payload)), payload


def test_allowed_assets_pass_and_unknown_or_partner_assets_follow_scope() -> None:
    assert _codes(_version({"asset_id": "own"}, {"asset_ids": ["public", "partner"]})) == []
    assert _codes(_version({"asset_id": "missing"})) == ["MISSING_ASSET"]
    assert _codes(_version({"asset_id": "partner-other"})) == ["CROSS_TENANT_ASSET_FORBIDDEN"]
    assert _codes(_version({"asset_id": "own"}, tenant="tenant-b")) == ["CROSS_TENANT_ASSET_FORBIDDEN"]
    assert _codes(_version({"asset_id": " own "})) == []  # surrounding whitespace is normalised


def test_invalid_asset_reference_values_and_external_media_urls_are_rejected() -> None:
    for bad in ("", "  ", None, 5, {"k": 1}):
        assert "INVALID_ASSET_REFERENCE" in _codes(_version({"asset_id": bad})), bad
    assert _codes(_version({"asset_id": ""})) == ["INVALID_ASSET_REFERENCE"]
    assert _codes(_version({"image_url": "https://evil.example/x.png"})) == ["EXTERNAL_MEDIA_URL_FORBIDDEN"]
    assert _codes(_version({"src": "//cdn.example/a.png"})) == ["EXTERNAL_MEDIA_URL_FORBIDDEN"]
    assert _codes(_version({"image_url": ""})) == []
    assert _codes(_version({"image_url": "asset://own"})) == []
    # the external URL itself is not echoed back in the message
    report = validate_content_version(
        _version({"image_url": "https://evil.example/?token=SECRET"}), asset_catalog=_CATALOG
    )
    assert "SECRET" not in report.issues[0].message


def test_deep_or_huge_payloads_are_rejected_without_recursion_error() -> None:
    deep: dict[str, object] = {}
    cursor = deep
    for _ in range(2000):
        nxt: dict[str, object] = {}
        cursor["n"] = nxt
        cursor = nxt
    cursor["asset_id"] = "foreign"
    assert "PAYLOAD_TOO_DEEP" in _codes(_version(deep))
    deep_list: object = ["x"]
    for _ in range(5000):
        deep_list = [deep_list]
    assert "PAYLOAD_TOO_DEEP" in _codes(_version({"items": deep_list}))
    wide = {"items": ["a"] * (MAX_PAYLOAD_NODES + 5)}
    assert "PAYLOAD_TOO_LARGE" in _codes(_version(wide))
    # a payload at the depth limit still validates
    ok: dict[str, object] = {"text": "x"}
    for _ in range(MAX_PAYLOAD_DEPTH - 2):
        ok = {"n": ok}
    assert _codes(_version(ok)) == []


def test_blank_tenant_cannot_own_an_asset_with_a_blank_tenant() -> None:
    catalog = {"orphan": AssetCatalogEntry("orphan", "", "TENANT_PRIVATE")}
    codes = _codes(_version({"asset_id": "orphan"}, tenant=""), catalog)
    assert "MISSING_TENANT" in codes and "CROSS_TENANT_ASSET_FORBIDDEN" in codes


def test_structure_issues_are_reported() -> None:
    assert "MISSING_TITLE" in _codes(_version(title="  "))
    blocks = (
        ContentBlockDraft(1, ContentBlockType.FORMULA_BOX, {"markdown": "$x$"}),
        ContentBlockDraft(1, ContentBlockType.FORMULA_BOX, {"markdown": "$y$"}),
        ContentBlockDraft(-1, ContentBlockType.FORMULA_BOX, {"markdown": "$z$"}),
        ContentBlockDraft(True, ContentBlockType.FORMULA_BOX, {}),  # type: ignore[arg-type]
        ContentBlockDraft(5, "NEW_TYPE", {}),  # type: ignore[arg-type]
        ContentBlockDraft(6, ContentBlockType.FORMULA_BOX, "not a mapping"),  # type: ignore[arg-type]
        ContentBlockDraft(7, ContentBlockType.INTERACTIVE_QUESTION_EMBED, {"question_id": " "}),
    )
    codes = _codes(_version(blocks=blocks))
    for expected in (
        "DUPLICATE_BLOCK_ORDER",
        "INVALID_BLOCK_ORDER",
        "INVALID_BLOCK_TYPE",
        "INVALID_BLOCK_PAYLOAD",
        "MISSING_EMBEDDED_QUESTION_ID",
    ):
        assert expected in codes, expected


def test_katex_issues_in_summary_and_blocks_are_attributed_to_the_block() -> None:
    version = _version({"markdown": r"Sai $\frac{1}{2"}, summary_md=r"Tóm tắt $x")
    report = validate_content_version(version, asset_catalog=_CATALOG)
    assert any(i.block_order is None and i.code == "UNCLOSED_KATEX_INLINE" for i in report.issues)
    assert any(i.block_order == 0 and i.code == "UNCLOSED_KATEX_INLINE" for i in report.issues)
    assert report.valid is False


def test_katex_edge_cases_and_vietnamese_normalisation_forms() -> None:
    assert validate_katex_syntax("") == []
    assert validate_katex_syntax("Không có công thức nào") == []
    assert validate_katex_syntax(r"\[ x^2 \]") == []
    assert validate_katex_syntax(r"\( x")[0].code == "UNCLOSED_KATEX_INLINE"
    assert validate_katex_syntax(r"\[ x")[0].code == "UNCLOSED_KATEX_DISPLAY"
    assert validate_katex_syntax("$$ x")[0].code == "UNCLOSED_KATEX_DISPLAY"
    assert validate_katex_syntax(r"$\end{a}$")[0].code == "MISMATCHED_KATEX_ENVIRONMENT"
    assert validate_katex_syntax(r"$\begin{a}$")[0].code == "MISMATCHED_KATEX_ENVIRONMENT"
    assert validate_katex_syntax(r"$a}$")[0].code == "UNBALANCED_KATEX_BRACE"
    for form in ("NFC", "NFD"):
        text = unicodedata.normalize(form, "Xác suất có điều kiện $P(A|B)$ và $$\\frac{1}{2}$$")
        assert validate_katex_syntax(text) == []
        broken = unicodedata.normalize(form, "Xác suất có điều kiện $P(A|B")
        assert validate_katex_syntax(broken)[0].code == "UNCLOSED_KATEX_INLINE"


def test_publish_validated_blocks_invalid_content_and_freezes_payloads() -> None:
    bad = _version({"asset_id": "foreign"})
    with pytest.raises(ContentStudioError) as exc:
        publish_validated_content_version(bad, asset_catalog=_CATALOG)
    assert exc.value.code == "CROSS_TENANT_ASSET_FORBIDDEN"
    payload = {"markdown": "$x$", "asset_id": "own", "steps": ["a"]}
    published = publish_validated_content_version(_version(payload), asset_catalog=_CATALOG)
    frozen = published.blocks[0].payload_json
    with pytest.raises(TypeError):
        frozen["markdown"] = "changed"  # type: ignore[index]
    payload["asset_id"] = "foreign"
    assert published.blocks[0].payload_json["asset_id"] == "own"
    assert frozen["steps"] == ("a",)
    # a frozen payload still validates (mapping proxies and tuples are walked)
    assert _codes(published) == []
