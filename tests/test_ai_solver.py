"""`app.infrastructure.ai_solver`: the answer, solution, difficulty and topic of a question, and its safe fallbacks."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.infrastructure.ai_solver import (
    SolveRequest,
    TaxonomyHint,
    answer_schema_for,
    keyword_topic,
    normalize_numeric,
    normalize_true_false,
)
from tests.ai_fakes import ai_settings, fake_solver, no_ai_settings

DERIVATIVE = TaxonomyHint(uuid.uuid4(), "DAO_HAM", "Đạo hàm", ("dao ham", "tiếp tuyến"))
LOG = TaxonomyHint(uuid.uuid4(), "MU_LOGARIT", "Mũ và Logarit", ("logarit",))
NODES = (DERIVATIVE, LOG)
OPTIONS = (("A", "3x^2 - 3"), ("B", "3x^2 + 3"), ("C", "x^2 - 3"), ("D", "3x"))


def _solve(solver, **kwargs):
    base = {"stem_text": "Đạo hàm của hàm số y = x^3 - 3x là", "item_type": "SINGLE_CHOICE", "options": OPTIONS,
            "taxonomy_nodes": NODES}  # fmt: skip
    return asyncio.run(solver.solve(SolveRequest(**{**base, **kwargs})))


def test_answers_are_normalised_to_the_c01_schema_of_each_item_type():
    assert answer_schema_for("SINGLE_CHOICE", "b", options=OPTIONS) == {"correct_option": "B"}
    assert answer_schema_for("SINGLE_CHOICE", "Đáp án: C", options=OPTIONS) == {"correct_option": "C"}
    assert answer_schema_for("SINGLE_CHOICE", "E", options=OPTIONS) is None, "not one of the options"
    assert normalize_true_false("ĐSĐS") == {"a": True, "b": False, "c": True, "d": False}
    assert normalize_true_false("a) Đ b) S c) S d) Đ") == {"a": True, "b": False, "c": False, "d": True}
    assert normalize_true_false({"a": "S", "b": True, "c": "T", "d": False}) == {"a": False, "b": True, "c": True,
                                                                                 "d": False}  # fmt: skip
    assert normalize_true_false("ĐSĐ") is None
    assert normalize_numeric("2,5") == "2.5" and normalize_numeric(3) == "3" and normalize_numeric("x+1") is None
    assert answer_schema_for("SHORT_NUMERIC", "-0,75") == {"value": "-0.75"}
    assert answer_schema_for("ESSAY", "Nêu đủ 2 ý", max_points=2) == {"rubric": "Nêu đủ 2 ý", "max_points": 2.0}


def test_without_a_key_the_source_answer_is_kept_and_the_topic_comes_from_keywords():
    solver, fake = fake_solver(settings=no_ai_settings())
    result = _solve(solver, existing_answer="a")
    assert result.answer_schema == {"correct_option": "A"} and result.answer_source == "EXISTING"
    assert result.taxonomy_node_id == DERIVATIVE.id and result.topic_source == "KEYWORD"
    assert result.difficulty_level == "TH" and fake.calls == [], "no provider, no call"


def test_without_a_key_and_without_a_source_answer_the_question_is_marked_unresolved_not_invented():
    solver, _ = fake_solver(settings=no_ai_settings())
    result = _solve(solver)
    assert result.answer_schema is None and result.answer_source == "NONE"
    assert result.unresolved_reason is not None and result.unresolved_reason.startswith("NO_ANSWER")


def test_the_ai_answers_solves_and_tags_and_a_refused_key_rotates_to_the_next_one():
    solver, fake = fake_solver(
        {"Đạo hàm": {"answer": "A", "solution_steps": "y' = 3x^2 - 3", "difficulty_level": "NB",
                     "taxonomy_node_id": str(DERIVATIVE.id)}},
        settings=ai_settings(gemini_api_key="key-1", gemini_api_keys="key-2"),
        refuse_keys=("key-1",),
    )  # fmt: skip
    result = _solve(solver)
    assert result.answer_schema == {"correct_option": "A"} and result.answer_source == "AI"
    assert result.solution_steps == "y' = 3x^2 - 3" and result.difficulty_level == "NB"
    assert result.taxonomy_node_id == DERIVATIVE.id and result.topic_source == "AI"
    assert [k for k, _ in fake.calls] == ["key-1", "key-2"] and result.provider == "gemini:gemini-fake"
    assert "Đạo hàm" in fake.calls[0][1] and str(LOG.id) in fake.calls[0][1], "the prompt lists the topics"


def test_the_source_key_wins_over_a_disagreeing_ai_and_the_conflict_is_flagged():
    solver, _ = fake_solver({"Đạo hàm": {"answer": "B"}})
    result = _solve(solver, existing_answer="A")
    assert result.answer_schema == {"correct_option": "A"} and result.answer_conflict is True
    assert "AI_DISAGREES_WITH_SOURCE_KEY" in result.warnings


def test_a_number_its_own_expression_does_not_reproduce_is_not_trusted():
    def checker(expr: str, value: str):
        return SimpleNamespace(matches=expr == "6/2", error=None if expr == "6/2" else "MISMATCH")

    stem = {"stem_text": "Nghiệm lớn nhất của x^2 - 5x + 6 = 0", "item_type": "SHORT_NUMERIC", "options": ()}
    good, _ = fake_solver({"Nghiệm": {"answer": "3", "numeric_expression": "6/2"}}, numeric_checker=checker)
    accepted = _solve(good, **stem)
    assert accepted.answer_schema == {"value": "3"} and accepted.numeric_check == "MATCH"
    bad, _ = fake_solver({"Nghiệm": {"answer": "4", "numeric_expression": "5-2"}}, numeric_checker=checker)
    refused = _solve(bad, **stem)
    assert refused.answer_schema is None and "NUMERIC_CHECK_MISMATCH" in refused.warnings


@pytest.mark.parametrize("reply", [{"answer": "Z"}, {"answer": "A", "taxonomy_node_id": "not-a-listed-id"}])
def test_an_unusable_ai_reply_falls_back_safely(reply):
    solver, _ = fake_solver({"Đạo hàm": reply})
    result = _solve(solver, existing_answer=None)
    if reply["answer"] == "Z":
        assert result.answer_schema is None and result.unresolved_reason
    else:
        assert result.answer_schema == {"correct_option": "A"}
    assert result.taxonomy_node_id == DERIVATIVE.id and result.topic_source == "KEYWORD", "an unknown id is ignored"


def test_keyword_topics_ignore_accents_and_need_a_shared_word():
    assert keyword_topic("Tính LOGARIT cơ số 2 của 8", NODES) == LOG
    assert keyword_topic("Một câu không liên quan", NODES) is None


def test_a_numeric_check_that_cannot_run_keeps_the_answer_flagged_unverified():
    def broken_sandbox(expr: str, value: str):
        return SimpleNamespace(matches=False, error="SANDBOX_WORKER_UNAVAILABLE")

    solver, _ = fake_solver({"Nghiệm": {"answer": "3", "numeric_expression": "6/2"}}, numeric_checker=broken_sandbox)
    result = _solve(solver, stem_text="Nghiệm lớn nhất của x^2 - 5x + 6 = 0", item_type="SHORT_NUMERIC", options=())
    assert result.answer_schema == {"value": "3"} and result.answer_source == "AI"
    assert result.numeric_check == "UNVERIFIED:SANDBOX_WORKER_UNAVAILABLE"
    assert "NUMERIC_CHECK_UNVERIFIED:SANDBOX_WORKER_UNAVAILABLE" in result.warnings
