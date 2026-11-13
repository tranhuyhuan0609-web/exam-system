"""Pure unit tests for B01 immutable content versioning."""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from app.modules.authoring.public import validate_content_version
from app.modules.content.public import (
    AssetCatalogEntry,
    ContentBlockDraft,
    ContentBlockType,
    ContentStudioError,
    ContentType,
    ContentValidationIssue,
    ContentValidationReport,
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


def _ver(status: ContentVersionStatus = ContentVersionStatus.DRAFT, **kw: object) -> ContentVersionState:
    base: dict[str, object] = {
        "content_item_id": "item-1",
        "tenant_id": "tenant-a",
        "content_type": ContentType.LESSON,
        "version_no": 1,
        "title": "Bài học",
        "summary_md": "",
        "status": status,
        "is_immutable": status != ContentVersionStatus.DRAFT,
        "blocks": (ContentBlockDraft(0, ContentBlockType.THEORY_SUMMARY, {"markdown": "x"}),),
    }
    base.update(kw)
    return ContentVersionState(**base)  # type: ignore[arg-type]


def _ok(_: ContentVersionState) -> ContentValidationReport:
    return ContentValidationReport(valid=True, issues=())


def _code(action) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(ContentStudioError) as exc:
        action()
    return exc.value.code


def test_superseded_and_published_versions_cannot_be_published_again_blocker_like() -> None:
    for status in (ContentVersionStatus.SUPERSEDED, ContentVersionStatus.PUBLISHED):
        assert _code(lambda s=status: publish_content_version(_ver(s), validator=_ok)) == "CONTENT_NOT_DRAFT"
    assert _code(lambda: publish_content_version(_ver(ContentVersionStatus.TAKEN_DOWN), validator=_ok)) == (
        "CONTENT_TAKEN_DOWN"
    )
    # a DRAFT flagged immutable is inconsistent state and is refused as well
    assert _code(lambda: publish_content_version(_ver(is_immutable=True), validator=_ok)) == "CONTENT_NOT_DRAFT"


def test_taken_down_and_draft_versions_cannot_be_forked() -> None:
    assert _code(lambda: fork_next_content_version(_ver(ContentVersionStatus.TAKEN_DOWN), created_by="e")) == (
        "CONTENT_TAKEN_DOWN"
    )
    for status in (ContentVersionStatus.DRAFT, ContentVersionStatus.SUPERSEDED):
        assert _code(lambda s=status: fork_next_content_version(_ver(s), created_by="e")) == "CONTENT_NOT_PUBLISHED"
    published = _ver(ContentVersionStatus.PUBLISHED)
    for author in ("", "  ", None):
        assert _code(lambda a=author: fork_next_content_version(published, created_by=a)) == "MISSING_AUTHOR"  # type: ignore[arg-type]
    kept, draft = fork_next_content_version(published, created_by="editor")
    assert kept is published and draft.version_no == 2 and draft.created_by == "editor"
    assert draft.status == ContentVersionStatus.DRAFT and draft.is_immutable is False
    assert draft.blocks == published.blocks and draft.tenant_id == published.tenant_id


def test_only_drafts_are_editable() -> None:
    for status in (
        ContentVersionStatus.PUBLISHED,
        ContentVersionStatus.SUPERSEDED,
        ContentVersionStatus.TAKEN_DOWN,
    ):
        assert _code(lambda s=status: edit_draft_blocks(_ver(s), title="x")) == "CONTENT_VERSION_IMMUTABLE"
    assert _code(lambda: edit_draft_blocks(_ver(is_immutable=True), title="x")) == "CONTENT_VERSION_IMMUTABLE"
    assert _code(lambda: edit_draft_blocks(_ver(), blocks=[])) == "INVALID_BLOCKS"  # type: ignore[arg-type]
    draft = _ver()
    edited = edit_draft_blocks(draft, title="Mới", summary_md="s", blocks=())
    assert (edited.title, edited.summary_md, edited.blocks) == ("Mới", "s", ())
    assert draft.title == "Bài học" and len(draft.blocks) == 1  # input untouched
    untouched = edit_draft_blocks(draft)
    assert untouched == draft


def test_publish_runs_the_validator_and_surfaces_its_first_issue() -> None:
    issue = ContentValidationIssue(0, "SOME_CODE", "message")
    failing = ContentValidationReport(valid=False, issues=(issue,))
    assert _code(lambda: publish_content_version(_ver(), validator=lambda _v: failing)) == "SOME_CODE"
    inconsistent = ContentValidationReport(valid=False, issues=())
    assert _code(lambda: publish_content_version(_ver(), validator=lambda _v: inconsistent)) == (
        "CONTENT_VALIDATION_FAILED"
    )
    # a "valid" report that still carries issues is not trusted
    sneaky = ContentValidationReport(valid=True, issues=(issue,))
    assert _code(lambda: publish_content_version(_ver(), validator=lambda _v: sneaky)) == "SOME_CODE"
    with pytest.raises(TypeError):
        publish_content_version(_ver())  # type: ignore[call-arg]


def test_publish_freezes_block_payloads_and_leaves_input_untouched() -> None:
    payload = {"markdown": "x", "steps": ["a", "b"], "meta": {"k": 1}}
    draft = _ver(blocks=(ContentBlockDraft(0, ContentBlockType.METHOD_STEPS, payload),))
    published = publish_content_version(draft, validator=_ok)
    frozen = published.blocks[0].payload_json
    with pytest.raises(TypeError):
        frozen["markdown"] = "y"  # type: ignore[index]
    with pytest.raises(TypeError):
        frozen["meta"]["k"] = 2  # type: ignore[index]
    assert frozen["steps"] == ("a", "b")
    payload["markdown"] = "changed after publish"
    assert published.blocks[0].payload_json["markdown"] == "x"
    assert draft.status == ContentVersionStatus.DRAFT and draft.is_immutable is False


def test_publish_then_edit_is_rejected_and_fork_keeps_history() -> None:
    published = publish_content_version(_ver(), validator=_ok)
    assert _code(lambda: edit_draft_blocks(published, title="x")) == "CONTENT_VERSION_IMMUTABLE"
    kept, draft = fork_next_content_version(published, created_by="e")
    second = publish_content_version(edit_draft_blocks(draft, title="v2"), validator=_ok)
    assert (kept.version_no, second.version_no) == (1, 2)
    assert kept.title == "Bài học" and second.title == "v2"
    _, third = fork_next_content_version(second, created_by="e")
    assert third.version_no == 3


def test_vietnamese_titles_keep_their_normalisation_form() -> None:
    for form in ("NFC", "NFD"):
        title = unicodedata.normalize(form, "Đạo hàm của hàm số")
        edited = edit_draft_blocks(_ver(), title=title)
        assert edited.title == title
