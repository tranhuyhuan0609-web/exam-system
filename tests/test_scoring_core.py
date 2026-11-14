"""Unit tests for C07 deterministic THPTQG 2025+ scoring core (pure, no conftest)."""

from __future__ import annotations

import itertools
from decimal import Decimal

import pytest

from app.contracts.enums import QuestionItemType
from app.contracts.scoring import NumericParsePolicy
from app.modules.grading.public import (
    TF4_SCORING_MAP,
    ClusterTrueFalseAnswerKey,
    ClusterTrueFalseResponse,
    GradingItemInput,
    ShortNumericAnswerKey,
    ShortNumericResponse,
    SingleChoiceAnswerKey,
    SingleChoiceResponse,
    grade_assessment,
    grade_item,
    parse_short_numeric_vn,
)


@pytest.mark.parametrize(
    "student_tuple",
    list(itertools.product([False, True], repeat=4)),
)
def test_part_ii_all_16_combinations(student_tuple: tuple[bool, bool, bool, bool]) -> None:
    correct = {"a": True, "b": False, "c": True, "d": False}
    keys = ("a", "b", "c", "d")
    student = dict(zip(keys, student_tuple, strict=True))
    expected_matches = sum(1 for k in keys if student[k] == correct[k])
    expected_points = TF4_SCORING_MAP[expected_matches]

    item = GradingItemInput(
        item_id="tf-1",
        item_type=QuestionItemType.CLUSTER_TRUE_FALSE,
        max_points=Decimal("1.00"),
        answer_key=ClusterTrueFalseAnswerKey(sub_items=correct),
        student_response=ClusterTrueFalseResponse(sub_answers=student),
    )
    result = grade_item(item)
    assert isinstance(result.earned_points, Decimal)
    assert result.earned_points == expected_points
    assert result.matched_sub_items == expected_matches
    assert result.is_correct is (expected_matches == 4)


def test_part_ii_partial_sub_answers() -> None:
    item = GradingItemInput(
        item_id="tf-partial",
        item_type=QuestionItemType.CLUSTER_TRUE_FALSE,
        max_points=Decimal("1.00"),
        answer_key=ClusterTrueFalseAnswerKey(sub_items={"a": True, "b": True, "c": False, "d": False}),
        student_response=ClusterTrueFalseResponse(sub_answers={"a": True, "c": False}),
    )
    result = grade_item(item)
    assert result.matched_sub_items == 2
    assert result.earned_points == Decimal("0.25")


def test_part_i_single_choice_with_permutation() -> None:
    permutation = ("C", "A", "D", "B")
    key = SingleChoiceAnswerKey(correct_key="A")

    # Display key "B" is at index 1 -> canonical "A" (correct)
    hit = grade_item(
        GradingItemInput(
            item_id="sc-1",
            item_type=QuestionItemType.SINGLE_CHOICE,
            max_points=Decimal("0.25"),
            answer_key=key,
            student_response=SingleChoiceResponse(selected_key="B"),
            option_permutation=permutation,
        )
    )
    assert hit.is_correct is True
    assert hit.displayed_key == "B"
    assert hit.canonical_key == "A"
    assert hit.earned_points == Decimal("0.25")

    # Display key "A" is at index 0 -> canonical "C" (wrong)
    miss = grade_item(
        GradingItemInput(
            item_id="sc-1",
            item_type=QuestionItemType.SINGLE_CHOICE,
            max_points=Decimal("0.25"),
            answer_key=key,
            student_response=SingleChoiceResponse(selected_key="A"),
            option_permutation=permutation,
        )
    )
    assert miss.is_correct is False
    assert miss.canonical_key == "C"
    assert miss.earned_points == Decimal("0.00")

    # Unanswered
    blank = grade_item(
        GradingItemInput(
            item_id="sc-1",
            item_type=QuestionItemType.SINGLE_CHOICE,
            max_points=Decimal("0.25"),
            answer_key=key,
            student_response=SingleChoiceResponse(selected_key=None),
            option_permutation=permutation,
        )
    )
    assert blank.is_correct is False
    assert blank.earned_points == Decimal("0.00")


@pytest.mark.parametrize(
    ("raw", "expected_val", "expected_err"),
    [
        ("0,5", Decimal("0.5"), None),
        ("0.5", Decimal("0.5"), None),
        ("-1,5", Decimal("-1.5"), None),
        ("-2", Decimal("-2"), None),
        ("12,3", Decimal("12.3"), None),
        (" 0,5 ", Decimal("0.5"), None),
        ("1.000", None, "MAX_CHARS_EXCEEDED_LIMIT_4"),
        ("-1.000", None, "MAX_CHARS_EXCEEDED_LIMIT_4"),
        ("12345", None, "MAX_CHARS_EXCEEDED_LIMIT_4"),
        ("-1234", None, "MAX_CHARS_EXCEEDED_LIMIT_4"),
        ("", None, "EMPTY"),
        ("   ", None, "EMPTY"),
        ("1,2.", None, "AMBIGUOUS_MULTIPLE_SEPARATORS"),
        ("1_0", None, "INVALID_NUMBER_FORMAT"),
        ("nan", None, "INVALID_NUMBER_FORMAT"),
        ("inf", None, "INVALID_NUMBER_FORMAT"),
        ("abc", None, "INVALID_NUMBER_FORMAT"),
    ],
)
def test_parse_short_numeric_vn(raw: str, expected_val: Decimal | None, expected_err: str | None) -> None:
    res = parse_short_numeric_vn(raw)
    assert res.value == expected_val
    assert res.error_code == expected_err


def test_parse_short_numeric_policy_variants() -> None:
    comma_only = NumericParsePolicy(decimal_sep="COMMA_ONLY")
    assert parse_short_numeric_vn("0.5", policy=comma_only).error_code == "DISALLOWED_DECIMAL_SEPARATOR"
    assert parse_short_numeric_vn("0,5", policy=comma_only).value == Decimal("0.5")

    dot_only = NumericParsePolicy(decimal_sep="DOT_ONLY")
    assert parse_short_numeric_vn("0,5", policy=dot_only).error_code == "DISALLOWED_DECIMAL_SEPARATOR"
    assert parse_short_numeric_vn("0.5", policy=dot_only).value == Decimal("0.5")

    wider_policy = NumericParsePolicy(max_chars=6)
    assert (
        parse_short_numeric_vn("1.000", policy=wider_policy).error_code
        == "THOUSAND_SEPARATOR_NOT_ALLOWED_USE_PLAIN_DIGITS"
    )
    assert parse_short_numeric_vn("1,2.3", policy=wider_policy).error_code == "AMBIGUOUS_MULTIPLE_SEPARATORS"


def test_part_iii_grading_with_tolerance() -> None:
    key = ShortNumericAnswerKey(target_value=Decimal("0.5"), policy=NumericParsePolicy())
    ok_res = grade_item(
        GradingItemInput(
            item_id="sn-1",
            item_type=QuestionItemType.SHORT_NUMERIC,
            max_points=Decimal("0.50"),
            answer_key=key,
            student_response=ShortNumericResponse(text_value="0,5"),
        )
    )
    assert ok_res.is_correct is True
    assert ok_res.earned_points == Decimal("0.50")

    bad_res = grade_item(
        GradingItemInput(
            item_id="sn-2",
            item_type=QuestionItemType.SHORT_NUMERIC,
            max_points=Decimal("0.50"),
            answer_key=ShortNumericAnswerKey(
                target_value=Decimal("0.5"),
                policy=NumericParsePolicy(max_chars=6),
            ),
            student_response=ShortNumericResponse(text_value="1.000"),
        )
    )
    assert bad_res.is_correct is False
    assert bad_res.earned_points == Decimal("0.00")
    assert bad_res.parse_error == "THOUSAND_SEPARATOR_NOT_ALLOWED_USE_PLAIN_DIGITS"


def _build_math_exam_items() -> list[GradingItemInput]:
    items: list[GradingItemInput] = []
    for i in range(12):
        items.append(
            GradingItemInput(
                item_id=f"math-p1-{i}",
                item_type=QuestionItemType.SINGLE_CHOICE,
                max_points=Decimal("0.25"),
                answer_key=SingleChoiceAnswerKey(correct_key="A"),
                student_response=SingleChoiceResponse(selected_key="A"),
            )
        )
    for i in range(4):
        items.append(
            GradingItemInput(
                item_id=f"math-p2-{i}",
                item_type=QuestionItemType.CLUSTER_TRUE_FALSE,
                max_points=Decimal("1.00"),
                answer_key=ClusterTrueFalseAnswerKey(sub_items={"a": True, "b": False, "c": True, "d": False}),
                student_response=ClusterTrueFalseResponse(sub_answers={"a": True, "b": False, "c": True, "d": False}),
            )
        )
    for i in range(6):
        items.append(
            GradingItemInput(
                item_id=f"math-p3-{i}",
                item_type=QuestionItemType.SHORT_NUMERIC,
                max_points=Decimal("0.50"),
                answer_key=ShortNumericAnswerKey(target_value=Decimal("2.5")),
                student_response=ShortNumericResponse(text_value="2,5"),
            )
        )
    return items


def _build_physics_exam_items() -> list[GradingItemInput]:
    items: list[GradingItemInput] = []
    for i in range(18):
        items.append(
            GradingItemInput(
                item_id=f"phys-p1-{i}",
                item_type=QuestionItemType.SINGLE_CHOICE,
                max_points=Decimal("0.25"),
                answer_key=SingleChoiceAnswerKey(correct_key="C"),
                student_response=SingleChoiceResponse(selected_key="C"),
            )
        )
    for i in range(4):
        items.append(
            GradingItemInput(
                item_id=f"phys-p2-{i}",
                item_type=QuestionItemType.CLUSTER_TRUE_FALSE,
                max_points=Decimal("1.00"),
                answer_key=ClusterTrueFalseAnswerKey(sub_items={"a": False, "b": True, "c": False, "d": True}),
                student_response=ClusterTrueFalseResponse(sub_answers={"a": False, "b": True, "c": False, "d": True}),
            )
        )
    for i in range(6):
        items.append(
            GradingItemInput(
                item_id=f"phys-p3-{i}",
                item_type=QuestionItemType.SHORT_NUMERIC,
                max_points=Decimal("0.25"),
                answer_key=ShortNumericAnswerKey(target_value=Decimal("-1.5")),
                student_response=ShortNumericResponse(text_value="-1,5"),
            )
        )
    return items


def test_thptqg_2025_math_22_items_total_10() -> None:
    items = _build_math_exam_items()
    assert len(items) == 22
    res1 = grade_assessment(items)
    res2 = grade_assessment(items)
    assert isinstance(res1.total_score, Decimal)
    assert res1.total_score == Decimal("10.00")
    assert res1.max_score == Decimal("10.00")
    assert res1 == res2


def test_thptqg_2025_physics_28_items_total_10() -> None:
    items = _build_physics_exam_items()
    assert len(items) == 28
    res = grade_assessment(items)
    assert isinstance(res.total_score, Decimal)
    assert res.total_score == Decimal("10.00")
    assert res.max_score == Decimal("10.00")
