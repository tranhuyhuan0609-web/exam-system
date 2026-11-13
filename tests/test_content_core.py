"""Pure unit tests for B01 immutable content versioning."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.authoring.public import validate_content_version
from app.modules.content.public import (
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
)


def _validator_for(catalog):
    return lambda version: validate_content_version(version, asset_catalog=catalog)


def test_enums_match_ddl_check_constraints() -> None:
    ddl = Path("docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql").read_text(encoding="utf-8")
    for enum_cls in (ContentType, ContentVersionStatus, ContentBlockType):
        for member in enum_cls:
            assert f"'{member.value}'" in ddl


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
    published_v1 = publish_content_version(v1, validator=_validator_for(catalog))
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

