"""Unit tests and 200-paper randomized leak-scan for C05 paper sanitizer (P19)."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.contracts.delivery import (
    NumericParsePolicyPublicContract,
    SanitizedExamPaperContract,
    SanitizedOptionContract,
    SanitizedQuestionContract,
)
from app.contracts.enums import QuestionItemType
from app.modules.assembly.public import PermutationItemInput, generate_attempt_permutation
from app.modules.delivery.public import sanitize_exam_paper

Q1 = UUID("00000000-0000-0000-0000-000000000001")
Q2 = UUID("00000000-0000-0000-0000-000000000002")
Q3 = UUID("00000000-0000-0000-0000-000000000003")
Q4 = UUID("00000000-0000-0000-0000-000000000004")
ATTEMPT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
ASSESSMENT_VER_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
DEADLINE = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def test_output_models_forbid_extra_fields_via_strict_allowlist():
    with pytest.raises(ValidationError):
        SanitizedOptionContract(display_label="A", content_json={"text": "1"}, is_correct=True)  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        SanitizedQuestionContract(
            question_version_id=Q1,
            section_code="PART_I",
            item_type=QuestionItemType.SINGLE_CHOICE,
            display_position=1,
            answer_schema={"correct_key": "A"},  # type: ignore[call-arg]
        )

    with pytest.raises(ValidationError):
        NumericParsePolicyPublicContract(tolerance=0.01)  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        SanitizedExamPaperContract(
            attempt_id=ATTEMPT_ID,
            assessment_version_id=ASSESSMENT_VER_ID,
            deadline_at=DEADLINE,
            questions=(),
            assessment_answer_keys={"q1": "A"},  # type: ignore[call-arg]
        )


def test_reorders_options_by_p8_permutation_relabels_ad_and_strips_identity_and_answers():
    raw_questions = [
        {
            "id": Q1,
            "question_version_id": Q1,
            "section_code": "PART_I",
            "item_type": "SINGLE_CHOICE",
            "stem_json": {
                "text": "2 + 2 = ?",
                "blocks": [{"type": "text", "text": "2 + 2 = ?", "answer_key": "CANARY_NESTED"}],
                "rationale_json": {"explanation": "CANARY_RATIONALE"},
            },
            "answer_schema": {"correct_key": "D"},
            "rationale_json": {"solution": "CANARY_SOLUTION"},
            "options": [
                {
                    "id": str(uuid4()),
                    "option_key": "A",
                    "sequence_order": 1,
                    "is_correct": False,
                    "content_json": {"text": "3"},
                },
                {
                    "id": str(uuid4()),
                    "option_key": "B",
                    "sequence_order": 2,
                    "is_correct": False,
                    "content_json": {"text": "5"},
                },
                {
                    "id": str(uuid4()),
                    "option_key": "C",
                    "sequence_order": 3,
                    "is_correct": False,
                    "content_json": {"text": "6"},
                },
                {
                    "id": str(uuid4()),
                    "option_key": "D",
                    "sequence_order": 4,
                    "is_correct": True,
                    "points": 0.25,
                    "content_json": {"text": "4", "is_correct": True},
                },
            ],
        },
        {
            "id": Q2,
            "question_version_id": Q2,
            "section_code": "PART_II",
            "item_type": "CLUSTER_TRUE_FALSE",
            "stem_json": {"text": "Determine truth values"},
            "options": [
                {
                    "id": "orig-opt-b",
                    "option_key": "b",
                    "sequence_order": 2,
                    "is_correct": False,
                    "sub_points": 0.25,
                    "content_json": {"text": "Sub B"},
                },
                {
                    "id": "orig-opt-a",
                    "option_key": "a",
                    "sequence_order": 1,
                    "is_correct": True,
                    "sub_points": 0.25,
                    "content_json": {"text": "Sub A"},
                },
            ],
        },
        {
            "id": Q3,
            "question_version_id": Q3,
            "section_code": "PART_III",
            "item_type": "SHORT_NUMERIC",
            "stem_json": {"text": "x = ?"},
            "numeric_parse_policy_json": {
                "decimal_sep": "COMMA_OR_DOT",
                "allow_thousand_sep": False,
                "max_chars": 4,
                "tolerance": 0.01,
                "expected_answer": "CANARY_NUM",
            },
            "answer_schema": {"expected_value": "42"},
        },
    ]
    perm = generate_attempt_permutation(
        [
            PermutationItemInput(Q1, "PART_I", None, "SINGLE_CHOICE"),
            PermutationItemInput(Q2, "PART_II", None, "CLUSTER_TRUE_FALSE"),
            PermutationItemInput(Q3, "PART_III", None, "SHORT_NUMERIC"),
        ],
        attempt_seed=42,
    )

    paper = sanitize_exam_paper(
        attempt_id=ATTEMPT_ID,
        assessment_version_id=ASSESSMENT_VER_ID,
        deadline_at=DEADLINE,
        raw_questions=raw_questions,
        permutation=perm,
    )
    payload_json = paper.model_dump_json()
    for canary in (
        "CANARY_NESTED",
        "CANARY_RATIONALE",
        "CANARY_SOLUTION",
        "CANARY_NUM",
        "orig-opt-a",
        "orig-opt-b",
        "is_correct",
        "sequence_order",
        "option_key",
    ):
        assert canary not in payload_json

    q1_out = paper.questions[0]
    expected_perm = perm.option_permutations[str(Q1)]
    text_by_canon = {"A": "3", "B": "5", "C": "6", "D": "4"}
    assert [opt.display_label for opt in q1_out.options] == ["A", "B", "C", "D"]
    assert [opt.content_json["text"] for opt in q1_out.options] == [
        text_by_canon[k] for k in expected_perm
    ]

    q2_out = paper.questions[1]
    assert [opt.display_label for opt in q2_out.options] == ["a", "b"]
    assert [opt.content_json["text"] for opt in q2_out.options] == ["Sub A", "Sub B"]

    q3_out = paper.questions[2]
    assert q3_out.numeric_parse_policy == NumericParsePolicyPublicContract(
        decimal_sep="COMMA_OR_DOT", allow_thousand_sep=False, max_chars=4
    )


def test_leak_scan_across_200_randomized_exams_never_emits_any_canary_marker():
    rng = random.Random(20261113)
    item_types = (
        (QuestionItemType.SINGLE_CHOICE, "PART_I"),
        (QuestionItemType.CLUSTER_TRUE_FALSE, "PART_II"),
        (QuestionItemType.SHORT_NUMERIC, "PART_III"),
        (QuestionItemType.ESSAY, "PART_IV"),
    )

    for paper_idx in range(200):
        raw_questions: list[dict] = []
        perm_inputs: list[PermutationItemInput] = []
        canaries: list[str] = []

        for q_idx in range(6):
            qv_id = UUID(int=paper_idx * 100 + q_idx + 1)
            itype, sec = rng.choice(item_types)
            canary_base = f"SECRET_CANARY_{paper_idx}_{q_idx}"
            c_ans = f"{canary_base}_ANS"
            c_rat = f"{canary_base}_RAT"
            c_sub = f"{canary_base}_SUBPTS"
            c_optid = f"{canary_base}_OPTID"
            c_deep = f"{canary_base}_DEEPJSON"
            c_extra = f"{canary_base}_EXTRA"
            canaries.extend([c_ans, c_rat, c_sub, c_optid, c_deep, c_extra])

            perm_inputs.append(PermutationItemInput(qv_id, sec, None, itype, position=q_idx))
            opts = []
            if itype == QuestionItemType.SINGLE_CHOICE:
                for seq, key in enumerate(("A", "B", "C", "D"), start=1):
                    opts.append(
                        {
                            "id": f"{c_optid}_{key}",
                            "option_key": key,
                            "sequence_order": seq,
                            "is_correct": key == "C",
                            "correct_answer_marker": c_ans,
                            "content_json": {
                                "text": f"Choice {key}",
                                "blocks": [{"type": "text", "text": f"B {key}", "rationale": c_deep}],
                            },
                        }
                    )
            elif itype == QuestionItemType.CLUSTER_TRUE_FALSE:
                for seq, key in enumerate(("a", "b", "c", "d"), start=1):
                    opts.append(
                        {
                            "id": f"{c_optid}_{key}",
                            "option_key": key,
                            "sequence_order": seq,
                            "is_correct": True,
                            "sub_points": c_sub,
                            "content_json": {"text": f"Stmt {key}", "rubric": c_deep},
                        }
                    )

            raw_questions.append(
                {
                    "id": qv_id,
                    "question_version_id": qv_id,
                    "section_code": sec,
                    "item_type": itype,
                    "stem_json": {
                        "text": f"Prompt {paper_idx}-{q_idx}",
                        "nested": {"answer_schema": c_deep, "explanation": c_rat},
                        "blocks": [{"type": "math", "latex": "x^2", "solution": c_deep}],
                    },
                    "answer_schema": {"correct_key": c_ans},
                    "rationale_json": {"steps": [c_rat]},
                    "answer_key_payload_json": {str(qv_id): c_ans},
                    "numeric_parse_policy_json": {
                        "decimal_sep": "COMMA_OR_DOT",
                        "allow_thousand_sep": False,
                        "max_chars": 4,
                        "tolerance": 0.01,
                        "expected_value": c_ans,
                    },
                    "unknown_extra_field": c_extra,
                    "options": opts,
                }
            )

        perm = generate_attempt_permutation(perm_inputs, attempt_seed=paper_idx + 1)
        paper = sanitize_exam_paper(
            attempt_id=ATTEMPT_ID,
            assessment_version_id=ASSESSMENT_VER_ID,
            deadline_at=DEADLINE,
            raw_questions=raw_questions,
            permutation=perm,
        )
        dumped = paper.model_dump_json()
        for canary in canaries:
            assert canary not in dumped, f"Leak detected in paper {paper_idx}: {canary}"
