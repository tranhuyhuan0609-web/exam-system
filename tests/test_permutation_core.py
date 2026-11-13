"""Unit tests for C04 deterministic attempt permutation (P8)."""

from __future__ import annotations

import random
from decimal import Decimal
from uuid import UUID

import pytest

from app.contracts.enums import QuestionItemType
from app.modules.assembly.public import (
    PermutationItemInput,
    generate_attempt_permutation,
)
from app.modules.grading.public import (
    GradingItemInput,
    SingleChoiceAnswerKey,
    SingleChoiceResponse,
    grade_item,
)

Q1 = UUID("00000000-0000-0000-0000-000000000001")
Q2 = UUID("00000000-0000-0000-0000-000000000002")
Q3 = UUID("00000000-0000-0000-0000-000000000003")
Q4 = UUID("00000000-0000-0000-0000-000000000004")
Q5 = UUID("00000000-0000-0000-0000-000000000005")
Q6 = UUID("00000000-0000-0000-0000-000000000006")
Q7 = UUID("00000000-0000-0000-0000-000000000007")
STIM = UUID("11111111-1111-1111-1111-111111111111")


def test_deterministic_and_input_order_independent():
    items = [
        PermutationItemInput(Q5, "PART_III", None, "SHORT_NUMERIC"),
        PermutationItemInput(Q2, "PART_I", STIM, "SINGLE_CHOICE", position=2),
        PermutationItemInput(Q7, "PART_IV", None, "ESSAY"),
        PermutationItemInput(Q3, "PART_I", None, "SINGLE_CHOICE"),
        PermutationItemInput(Q4, "PART_II", None, "CLUSTER_TRUE_FALSE"),
        PermutationItemInput(Q1, "PART_I", STIM, "SINGLE_CHOICE", position=1),
        PermutationItemInput(Q6, "PART_II", None, "SINGLE_CHOICE"),
    ]

    baseline = generate_attempt_permutation(items, attempt_seed=202505)
    rev = generate_attempt_permutation(list(reversed(items)), attempt_seed=202505)
    rotated = generate_attempt_permutation(items[3:] + items[:3], attempt_seed=202505)

    assert baseline == rev
    assert baseline == rotated


def test_section_order_stimulus_contiguity_and_single_choice_only():
    items = [
        PermutationItemInput(Q7, "PART_IV", None, "ESSAY"),
        PermutationItemInput(Q6, "PART_III", None, "SHORT_NUMERIC"),
        PermutationItemInput(Q4, "PART_II", None, "CLUSTER_TRUE_FALSE"),
        PermutationItemInput(Q2, "PART_I", STIM, "SINGLE_CHOICE", position=2),
        PermutationItemInput(Q3, "PART_I", None, "SINGLE_CHOICE"),
        PermutationItemInput(Q1, "PART_I", STIM, "SINGLE_CHOICE", position=1),
        PermutationItemInput(Q5, "PART_II", None, "SINGLE_CHOICE"),
    ]

    result = generate_attempt_permutation(items, attempt_seed=12345)
    ids = list(result.ordered_question_ids)

    # Section order strictly follows PART_I -> PART_II -> PART_III -> PART_IV.
    assert set(ids[:3]) == {Q1, Q2, Q3}
    assert set(ids[3:5]) == {Q4, Q5}
    assert ids[5] == Q6
    assert ids[6] == Q7

    # Stimulus group (Q1, Q2) stays adjacent and in original intra-group order (Q1 before Q2).
    idx_q1 = ids.index(Q1)
    assert ids[idx_q1 + 1] == Q2

    # Only SINGLE_CHOICE items receive an option permutation.
    assert set(result.option_permutations.keys()) == {str(Q1), str(Q2), str(Q3), str(Q5)}
    for perm in result.option_permutations.values():
        assert sorted(perm) == ["A", "B", "C", "D"]


def test_does_not_touch_global_random_state():
    random.seed(999)
    state_before = random.getstate()

    items = [
        PermutationItemInput(Q1, "PART_I", None, "SINGLE_CHOICE"),
        PermutationItemInput(Q3, "PART_I", None, "SINGLE_CHOICE"),
    ]
    r1 = generate_attempt_permutation(items, attempt_seed=42)
    assert random.getstate() == state_before

    random.seed(1)
    r2 = generate_attempt_permutation(items, attempt_seed=42)
    assert r1 == r2


def test_closed_loop_with_grading_module():
    items = [
        PermutationItemInput(Q1, "PART_I", None, "SINGLE_CHOICE"),
        PermutationItemInput(Q2, "PART_I", None, "SINGLE_CHOICE"),
        PermutationItemInput(Q3, "PART_I", None, "SINGLE_CHOICE"),
    ]
    perm = generate_attempt_permutation(items, attempt_seed=42)
    correct_canonical = "C"

    for qid in perm.ordered_question_ids:
        opt_perm = perm.option_permutations[str(qid)]
        correct_display_idx = opt_perm.index(correct_canonical)
        displayed_choice = ("A", "B", "C", "D")[correct_display_idx]

        graded_ok = grade_item(
            GradingItemInput(
                item_id=str(qid),
                item_type=QuestionItemType.SINGLE_CHOICE,
                max_points=Decimal("0.25"),
                answer_key=SingleChoiceAnswerKey(correct_key=correct_canonical),
                student_response=SingleChoiceResponse(selected_key=displayed_choice),
                option_permutation=opt_perm,
            )
        )
        assert graded_ok.is_correct is True
        assert graded_ok.earned_points == Decimal("0.25")
        assert graded_ok.canonical_key == correct_canonical

        wrong_display = next(k for k in ("A", "B", "C", "D") if k != displayed_choice)
        graded_wrong = grade_item(
            GradingItemInput(
                item_id=str(qid),
                item_type=QuestionItemType.SINGLE_CHOICE,
                max_points=Decimal("0.25"),
                answer_key=SingleChoiceAnswerKey(correct_key=correct_canonical),
                student_response=SingleChoiceResponse(selected_key=wrong_display),
                option_permutation=opt_perm,
            )
        )
        assert graded_wrong.is_correct is False
        assert graded_wrong.earned_points == Decimal("0.00")


@pytest.mark.parametrize(
    ("seed", "expected_order", "expected_opts"),
    [
        (
            7,
            (Q1, Q2, Q3, Q4, Q5),
            {
                str(Q1): ("B", "A", "C", "D"),
                str(Q2): ("B", "D", "C", "A"),
                str(Q3): ("B", "A", "D", "C"),
                str(Q4): ("D", "C", "A", "B"),
            },
        ),
        (
            42,
            (Q4, Q3, Q1, Q2, Q5),
            {
                str(Q4): ("D", "B", "C", "A"),
                str(Q3): ("D", "C", "A", "B"),
                str(Q1): ("B", "D", "C", "A"),
                str(Q2): ("B", "C", "A", "D"),
            },
        ),
        (
            1337,
            (Q3, Q4, Q1, Q2, Q5),
            {
                str(Q3): ("A", "C", "D", "B"),
                str(Q4): ("A", "D", "B", "C"),
                str(Q1): ("A", "D", "C", "B"),
                str(Q2): ("D", "C", "B", "A"),
            },
        ),
    ],
)
def test_golden_vectors(seed, expected_order, expected_opts):
    items = [
        PermutationItemInput(Q1, "PART_I", STIM, "SINGLE_CHOICE", position=1),
        PermutationItemInput(Q2, "PART_I", STIM, "SINGLE_CHOICE", position=2),
        PermutationItemInput(Q3, "PART_I", None, "SINGLE_CHOICE"),
        PermutationItemInput(Q4, "PART_I", None, "SINGLE_CHOICE"),
        PermutationItemInput(Q5, "PART_II", None, "CLUSTER_TRUE_FALSE"),
    ]
    res = generate_attempt_permutation(items, attempt_seed=seed)
    assert res.ordered_question_ids == expected_order
    assert dict(res.option_permutations) == expected_opts
