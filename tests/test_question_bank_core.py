"""Pure unit tests for C01 Typed Question Validation & Immutable Versioning (P28)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.question_bank.public import (
    DifficultyLevel,
    QuestionAuthoringError,
    QuestionDraft,
    QuestionItemType,
    QuestionOptionDraft,
    QuestionVersionState,
    StimulusType,
    assert_version_mutable,
    edit_draft_version,
    fork_next_version,
    publish_version,
    validate_question_draft,
)


def _abcd_options() -> tuple[QuestionOptionDraft, ...]:
    return (
        QuestionOptionDraft("A", "x = 1"),
        QuestionOptionDraft("B", "x = 2"),
        QuestionOptionDraft("C", "x = 3"),
        QuestionOptionDraft("D", "x = 4"),
    )


def test_enums_match_ddl_check_constraints() -> None:
    ddl = Path("docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql").read_text(encoding="utf-8")
    for enum_cls in (QuestionItemType, DifficultyLevel, StimulusType):
        for member in enum_cls:
            assert f"'{member.value}'" in ddl


def test_validates_all_6_typed_question_formats_and_rejects_broken_items() -> None:
    # 1. SINGLE_CHOICE valid + broken missing answer
    sc = QuestionDraft(
        item_type=QuestionItemType.SINGLE_CHOICE,
        difficulty_level=DifficultyLevel.NB,
        stem_text="Nghiệm của phương trình 2x = 4 là",
        options=_abcd_options(),
        answer_schema={"correct_option": "B"},
    )
    validate_question_draft(sc)
    with pytest.raises(QuestionAuthoringError) as sc_err:
        validate_question_draft(QuestionDraft(
            item_type=QuestionItemType.SINGLE_CHOICE,
            difficulty_level=DifficultyLevel.NB,
            stem_text="Missing option key",
            options=_abcd_options()[:1],
            answer_schema={"correct_option": "A"},
        ))
    assert sc_err.value.code == "INSUFFICIENT_OPTIONS"

    # 2. MULTIPLE_SELECT
    ms = QuestionDraft(
        item_type=QuestionItemType.MULTIPLE_SELECT,
        difficulty_level=DifficultyLevel.TH,
        stem_text="Chọn các phát biểu đúng",
        options=_abcd_options(),
        answer_schema={"correct_options": ["A", "C"]},
    )
    validate_question_draft(ms)

    # 3. CLUSTER_TRUE_FALSE (must be 4 sub-statements a, b, c, d with bools)
    tf_opts = (
        QuestionOptionDraft("a", "Mệnh đề a"),
        QuestionOptionDraft("b", "Mệnh đề b"),
        QuestionOptionDraft("c", "Mệnh đề c"),
        QuestionOptionDraft("d", "Mệnh đề d"),
    )
    tf = QuestionDraft(
        item_type=QuestionItemType.CLUSTER_TRUE_FALSE,
        difficulty_level=DifficultyLevel.VD,
        stem_text="Xét tính đúng/sai:",
        options=tf_opts,
        answer_schema={"statements": {"a": True, "b": False, "c": True, "d": False}},
    )
    validate_question_draft(tf)
    with pytest.raises(QuestionAuthoringError) as tf_err:
        validate_question_draft(QuestionDraft(
            item_type=QuestionItemType.CLUSTER_TRUE_FALSE,
            difficulty_level=DifficultyLevel.VD,
            stem_text="Thiếu ý d",
            options=tf_opts[:3],
            answer_schema={"statements": {"a": True, "b": False, "c": True}},
        ))
    assert tf_err.value.code == "INVALID_CLUSTER_TF_OPTIONS"

    # 4. SHORT_NUMERIC (THPTQG 4-char & separator guard)
    sn = QuestionDraft(
        item_type=QuestionItemType.SHORT_NUMERIC,
        difficulty_level=DifficultyLevel.VDC,
        stem_text="Tính giá trị m:",
        answer_schema={"value": "-1,5"},
    )
    validate_question_draft(sn)
    with pytest.raises(QuestionAuthoringError) as sn_err:
        validate_question_draft(QuestionDraft(
            item_type=QuestionItemType.SHORT_NUMERIC,
            difficulty_level=DifficultyLevel.VDC,
            stem_text="Quá 4 ký tự",
            answer_schema={"value": "12345"},
        ))
    assert sn_err.value.code == "NUMERIC_ANSWER_TOO_LONG"

    # 5. ESSAY
    es = QuestionDraft(
        item_type=QuestionItemType.ESSAY,
        difficulty_level=DifficultyLevel.VD,
        stem_text="Chứng minh bất đẳng thức:",
        answer_schema={"rubric": "Bước 1 (0.5đ), Bước 2 (0.5đ)", "max_points": 1.0},
    )
    validate_question_draft(es)

    # 6. GROUP_STEM
    gs = QuestionDraft(
        item_type=QuestionItemType.GROUP_STEM,
        difficulty_level=DifficultyLevel.TH,
        stem_text="Đoạn ngữ liệu chung về công thức Bayes",
        stimulus_id="stim-001",
        answer_schema={"child_question_ids": ["q-1", "q-2"]},
    )
    validate_question_draft(gs)


def test_published_version_is_immutable_and_forks_to_v_plus_1() -> None:
    v1_draft = QuestionDraft(
        item_type=QuestionItemType.SINGLE_CHOICE,
        difficulty_level=DifficultyLevel.NB,
        stem_text="Câu hỏi v1",
        options=_abcd_options(),
        answer_schema={"correct_option": "A"},
    )
    v1 = QuestionVersionState(
        question_id="q-10",
        tenant_id="tenant-1",
        version_no=1,
        draft=v1_draft,
        content_hash="abc",
        is_immutable=False,
        created_by="author-1",
    )
    frozen_v1 = publish_version(v1, approved_by="reviewer-1")
    assert frozen_v1.is_immutable is True

    # In-place mutation of published version is strictly rejected
    with pytest.raises(QuestionAuthoringError) as imm_err:
        assert_version_mutable(frozen_v1)
    assert imm_err.value.code == "QUESTION_VERSION_IMMUTABLE"

    updated_draft = QuestionDraft(
        item_type=QuestionItemType.SINGLE_CHOICE,
        difficulty_level=DifficultyLevel.TH,
        stem_text="Câu hỏi v2 đã chỉnh ký hiệu",
        options=_abcd_options(),
        answer_schema={"correct_option": "B"},
    )
    with pytest.raises(QuestionAuthoringError):
        edit_draft_version(frozen_v1, new_draft=updated_draft)

    # Forking produces mutable v2 while leaving frozen_v1 intact
    v2 = fork_next_version(
        frozen_v1,
        new_draft=updated_draft,
        change_reason="Fix typo in choice A",
        created_by="author-1",
    )
    assert v2.version_no == 2
    assert v2.is_immutable is False
    assert frozen_v1.version_no == 1
    assert frozen_v1.draft.stem_text == "Câu hỏi v1"
