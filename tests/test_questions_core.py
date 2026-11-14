"""Pure unit tests for C01 Typed Question Validation & Immutable Versioning (P28)."""

from __future__ import annotations

import unicodedata
from decimal import Decimal
from pathlib import Path

import pytest

from app.modules.questions.public import (
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
        validate_question_draft(
            QuestionDraft(
                item_type=QuestionItemType.SINGLE_CHOICE,
                difficulty_level=DifficultyLevel.NB,
                stem_text="Missing option key",
                options=_abcd_options()[:1],
                answer_schema={"correct_option": "A"},
            )
        )
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
        validate_question_draft(
            QuestionDraft(
                item_type=QuestionItemType.CLUSTER_TRUE_FALSE,
                difficulty_level=DifficultyLevel.VD,
                stem_text="Thiếu ý d",
                options=tf_opts[:3],
                answer_schema={"statements": {"a": True, "b": False, "c": True}},
            )
        )
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
        validate_question_draft(
            QuestionDraft(
                item_type=QuestionItemType.SHORT_NUMERIC,
                difficulty_level=DifficultyLevel.VDC,
                stem_text="Quá 4 ký tự",
                answer_schema={"value": "12345"},
            )
        )
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


def _sn(value: object, **kw: object) -> QuestionDraft:
    return QuestionDraft(
        item_type=QuestionItemType.SHORT_NUMERIC,
        difficulty_level=DifficultyLevel.TH,
        stem_text="Tính m",
        answer_schema={"value": value},
        **kw,  # type: ignore[arg-type]
    )


def _code(draft: QuestionDraft) -> str:
    with pytest.raises(QuestionAuthoringError) as exc:
        validate_question_draft(draft)
    return exc.value.code


def _sc(**kw: object) -> QuestionDraft:
    base: dict[str, object] = {
        "item_type": QuestionItemType.SINGLE_CHOICE,
        "difficulty_level": DifficultyLevel.NB,
        "stem_text": "Câu hỏi",
        "options": _abcd_options(),
        "answer_schema": {"correct_option": "A"},
    }
    base.update(kw)
    return QuestionDraft(**base)  # type: ignore[arg-type]


def test_short_numeric_accepts_plain_decimals_and_rejects_text_and_ambiguous_grouping() -> None:
    for ok in ("5", "-1,5", "0.25", "12", "-12", "0,75", "100", 7, 3.5):
        validate_question_draft(_sn(ok))
    for bad in ("abc", "1e3", "+5", "1,", ",5", "--1", "1 2", "NaN", "inf", "٣", "x", True):
        assert _code(_sn(bad)) in {"INVALID_NUMERIC_ANSWER", "MISSING_NUMERIC_ANSWER"}, bad
    # four-digit limit raised: "1.000" and "12,345" are ambiguous, "0.125" is clearly a decimal
    assert _code(_sn("1.000", max_short_numeric_chars=8)) == "AMBIGUOUS_DECIMAL_SEPARATOR"
    assert _code(_sn("12,345", max_short_numeric_chars=8)) == "AMBIGUOUS_DECIMAL_SEPARATOR"
    validate_question_draft(_sn("0.125", max_short_numeric_chars=8))
    assert _code(_sn("1.000")) == "NUMERIC_ANSWER_TOO_LONG"
    assert _code(_sn("1.2.3", max_short_numeric_chars=8)) == "INVALID_NUMERIC_ANSWER"
    for missing in (None, "", "   "):
        assert _code(_sn(missing)) == "MISSING_NUMERIC_ANSWER"
    for bad_limit in (0, -1, True):
        assert _code(_sn("1", max_short_numeric_chars=bad_limit)) == "INVALID_NUMERIC_LIMIT"


def _essay(points: object) -> QuestionDraft:
    return QuestionDraft(
        item_type=QuestionItemType.ESSAY,
        difficulty_level=DifficultyLevel.VD,
        stem_text="Chứng minh",
        answer_schema={"rubric": "Bước 1", "max_points": points},
    )


def test_essay_max_points_must_be_finite_and_positive() -> None:
    for ok in (1, 0.5, Decimal("2.5")):
        validate_question_draft(_essay(ok))
    for bad in (float("nan"), float("inf"), float("-inf"), Decimal("NaN"), Decimal("Infinity"), 0, -1, True, "3", None):
        assert _code(_essay(bad)) == "INVALID_ESSAY_POINTS", bad
    for rubric in ("", "   ", None):
        draft = QuestionDraft(
            item_type=QuestionItemType.ESSAY,
            difficulty_level=DifficultyLevel.VD,
            stem_text="x",
            answer_schema={"rubric": rubric, "max_points": 1},
        )
        assert _code(draft) == "MISSING_ESSAY_RUBRIC"


def test_multiple_select_rejects_duplicate_empty_and_unknown_correct_options() -> None:
    def ms(correct: object) -> QuestionDraft:
        return QuestionDraft(
            item_type=QuestionItemType.MULTIPLE_SELECT,
            difficulty_level=DifficultyLevel.TH,
            stem_text="Chọn",
            options=_abcd_options(),
            answer_schema={"correct_options": correct},
        )

    assert _code(ms(["A", "A"])) == "DUPLICATE_CORRECT_OPTION"
    assert _code(ms(["A", "C", "A"])) == "DUPLICATE_CORRECT_OPTION"
    assert _code(ms([])) == "INVALID_ANSWER_KEY"
    assert _code(ms(["E"])) == "INVALID_ANSWER_KEY"
    assert _code(ms(["a"])) == "INVALID_ANSWER_KEY"
    assert _code(ms("AC")) == "INVALID_ANSWER_KEY"
    assert _code(ms(None)) == "INVALID_ANSWER_KEY"
    validate_question_draft(ms(["A", "B", "C", "D"]))


def test_single_choice_boundaries() -> None:
    assert _code(_sc(stem_text="")) == "MISSING_STEM"
    assert _code(_sc(stem_text="   ")) == "MISSING_STEM"
    assert _code(_sc(answer_schema={"correct_option": "E"})) == "INVALID_ANSWER_KEY"
    assert _code(_sc(answer_schema={"correct_option": None})) == "INVALID_ANSWER_KEY"
    assert _code(_sc(answer_schema={})) == "INVALID_ANSWER_KEY"
    dup = (QuestionOptionDraft("A", "x"), QuestionOptionDraft(" A ", "y"))
    assert _code(_sc(options=dup)) == "DUPLICATE_OPTION_LABEL"
    assert _code(_sc(options=(QuestionOptionDraft("", "x"), QuestionOptionDraft("B", "y")))) == "EMPTY_OPTION_LABEL"
    assert _code(_sc(options=(QuestionOptionDraft("A", "  "), QuestionOptionDraft("B", "y")))) == "EMPTY_OPTION_CONTENT"
    assert _code(_sc(difficulty_level="NB ")) == "INVALID_DIFFICULTY"
    assert _code(_sc(difficulty_level="nb")) == "INVALID_DIFFICULTY"
    assert _code(_sc(item_type="TRUE_FALSE")) == "UNSUPPORTED_ITEM_TYPE"
    # Vietnamese stems validate identically in NFC and NFD
    for form in ("NFC", "NFD"):
        validate_question_draft(_sc(stem_text=unicodedata.normalize(form, "Nghiệm của phương trình là")))


def test_cluster_requires_exactly_a_b_c_d_labels_and_boolean_values() -> None:
    def tf(labels: str, statements: object) -> QuestionDraft:
        return QuestionDraft(
            item_type=QuestionItemType.CLUSTER_TRUE_FALSE,
            difficulty_level=DifficultyLevel.VD,
            stem_text="Xét",
            options=tuple(QuestionOptionDraft(c, f"ý {c}") for c in labels),
            answer_schema={"statements": statements},
        )

    good = {"a": True, "b": False, "c": True, "d": False}
    validate_question_draft(tf("abcd", good))
    assert _code(tf("wxyz", dict.fromkeys("wxyz", True))) == "INVALID_CLUSTER_TF_OPTIONS"
    assert _code(tf("abcd", {**good, "d": "false"})) == "INVALID_ANSWER_KEY"
    assert _code(tf("abcd", {**good, "d": 0})) == "INVALID_ANSWER_KEY"
    assert _code(tf("abcd", {"a": True, "b": True, "c": True})) == "INVALID_ANSWER_KEY"
    assert _code(tf("abcd", None)) == "INVALID_ANSWER_KEY"


def test_group_stem_children_must_be_unique_non_empty_strings() -> None:
    def gs(children: object, stimulus: str | None = "stim-1") -> QuestionDraft:
        return QuestionDraft(
            item_type=QuestionItemType.GROUP_STEM,
            difficulty_level=DifficultyLevel.TH,
            stem_text="Ngữ liệu",
            stimulus_id=stimulus,
            answer_schema={"child_question_ids": children},
        )

    validate_question_draft(gs(["q1", "q2"]))
    assert _code(gs(["q1"])) == "INSUFFICIENT_GROUP_CHILDREN"
    assert _code(gs(["q1", "q1"])) == "INVALID_GROUP_CHILDREN"
    assert _code(gs(["q1", ""])) == "INVALID_GROUP_CHILDREN"
    assert _code(gs(["q1", 2])) == "INVALID_GROUP_CHILDREN"
    assert _code(gs(["q1", "q2"], stimulus=None)) == "MISSING_STIMULUS_ID"
    assert _code(gs(["q1", "q2"], stimulus="  ")) == "MISSING_STIMULUS_ID"


def _version(draft: QuestionDraft | None = None, **kw: object) -> QuestionVersionState:
    base: dict[str, object] = {
        "question_id": "q-1",
        "tenant_id": "tenant-1",
        "version_no": 1,
        "draft": draft or _sc(),
        "content_hash": "stale",
    }
    base.update(kw)
    return QuestionVersionState(**base)  # type: ignore[arg-type]


def test_publish_recomputes_hash_and_freezes_answer_schema() -> None:
    mutable_schema = {"correct_option": "A", "meta": {"tags": ["x"]}}
    version = _version(_sc(answer_schema=mutable_schema))
    published = publish_version(version, approved_by="rev")
    assert published.content_hash != "stale"
    assert len(published.content_hash) == 64
    schema = published.draft.answer_schema
    with pytest.raises(TypeError):
        schema["correct_option"] = "B"  # type: ignore[index]
    with pytest.raises(TypeError):
        schema["meta"]["extra"] = 1  # type: ignore[index]
    assert schema["meta"]["tags"] == ("x",)
    # mutating the caller's original dict no longer changes the published version
    mutable_schema["correct_option"] = "D"
    assert published.draft.answer_schema["correct_option"] == "A"


def test_content_hash_is_stable_for_equal_content_and_changes_with_content() -> None:
    first = publish_version(_version(_sc()), approved_by="a")
    second = publish_version(_version(_sc()), approved_by="b")
    assert first.content_hash == second.content_hash
    changed = publish_version(_version(_sc(answer_schema={"correct_option": "B"})), approved_by="a")
    assert changed.content_hash != first.content_hash
    padded = publish_version(_version(_sc(stem_text="  Câu hỏi  ")), approved_by="a")
    assert padded.content_hash == first.content_hash


def test_publish_rejects_blank_approver_invalid_draft_and_double_publish() -> None:
    for approver in ("", "   ", None):
        with pytest.raises(QuestionAuthoringError) as exc:
            publish_version(_version(), approved_by=approver)  # type: ignore[arg-type]
        assert exc.value.code == "MISSING_APPROVER"
    with pytest.raises(QuestionAuthoringError) as invalid:
        publish_version(_version(_sc(stem_text="")), approved_by="rev")
    assert invalid.value.code == "MISSING_STEM"
    published = publish_version(_version(), approved_by="rev")
    with pytest.raises(QuestionAuthoringError) as twice:
        publish_version(published, approved_by="someone-else")
    assert twice.value.code == "QUESTION_VERSION_IMMUTABLE"
    assert published.approved_by == "rev"


def test_fork_requires_published_version_and_valid_inputs() -> None:
    draft_version = _version()
    with pytest.raises(QuestionAuthoringError) as exc:
        fork_next_version(draft_version, new_draft=_sc(), change_reason="r", created_by="a")
    assert exc.value.code == "VERSION_NOT_PUBLISHED"
    published = publish_version(draft_version, approved_by="rev")
    for reason in ("", "  ", None):
        with pytest.raises(QuestionAuthoringError) as bad_reason:
            fork_next_version(published, new_draft=_sc(), change_reason=reason, created_by="a")  # type: ignore[arg-type]
        assert bad_reason.value.code == "MISSING_CHANGE_REASON"
    for author in ("", "  ", None):
        with pytest.raises(QuestionAuthoringError) as bad_author:
            fork_next_version(published, new_draft=_sc(), change_reason="r", created_by=author)  # type: ignore[arg-type]
        assert bad_author.value.code == "MISSING_AUTHOR"
    with pytest.raises(QuestionAuthoringError) as invalid:
        fork_next_version(published, new_draft=_sc(stem_text=""), change_reason="r", created_by="a")
    assert invalid.value.code == "MISSING_STEM"
    forked = fork_next_version(published, new_draft=_sc(), change_reason="  typo  ", created_by="a")
    assert forked.version_no == 2 and forked.change_reason == "typo" and forked.approved_by is None
    assert forked.question_id == published.question_id and forked.tenant_id == published.tenant_id


def test_edit_draft_version_updates_hash_and_detaches_schema() -> None:
    version = _version()
    schema = {"correct_option": "B"}
    edited = edit_draft_version(version, new_draft=_sc(answer_schema=schema))
    assert edited.content_hash != version.content_hash
    schema["correct_option"] = "C"
    assert edited.draft.answer_schema["correct_option"] == "B"
    assert version.draft.answer_schema["correct_option"] == "A"
    with pytest.raises(QuestionAuthoringError):
        edit_draft_version(version, new_draft=_sc(stem_text=""))
