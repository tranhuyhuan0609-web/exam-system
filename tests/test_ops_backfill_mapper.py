"""Tests for W0.6 Backfill Value Mapper & Idempotent Reconciliation Kernel (P57)."""

from __future__ import annotations

import pytest

from app.ops.backfill_mapper import (
    BackfillMappingError,
    compute_batch_reconciliation_report,
    format_legacy_stable_code,
    map_legacy_difficulty,
    map_legacy_library_kind,
    map_legacy_question_type,
    resolve_legacy_five_axis_state,
)


def test_question_type_difficulty_and_library_mappings() -> None:
    assert map_legacy_question_type("MULTIPLE_CHOICE") == "SINGLE_CHOICE"
    assert map_legacy_question_type("true_false") == "CLUSTER_TRUE_FALSE"
    assert map_legacy_question_type("SHORT_ANSWER") == "SHORT_NUMERIC"
    assert map_legacy_question_type("ESSAY") == "ESSAY"

    with pytest.raises(BackfillMappingError):
        map_legacy_question_type("UNKNOWN_TYPE")

    hsg = map_legacy_difficulty("HSG")
    assert hsg.difficulty == "VDC"
    assert hsg.extra_framework_tags == ("HSG",)

    assert map_legacy_difficulty("RECOGNITION").difficulty == "NB"
    assert map_legacy_library_kind("BOOK") == "DOCUMENT"
    assert map_legacy_library_kind("knowledge_article") == "ARTICLE"
    assert format_legacy_stable_code(42) == "LEG-42"


def test_five_axis_fail_closed_defaults_for_crawler_vs_authored() -> None:
    # Crawler item with default legacy is_verified=True stays PENDING_REVIEW + DRAFT.
    crawled = resolve_legacy_five_axis_state(
        origin_type="CRAWLED",
        audit_status=None,
        legacy_is_verified=True,
        has_answer_source=True,
    )
    assert crawled.acquisition_status == "NORMALIZED"
    assert crawled.verification_status == "AUTO_VERIFIED"
    assert crawled.rights_status == "PENDING_REVIEW"
    assert crawled.publication_status == "DRAFT"

    # Authored + verified consensus publishes safely.
    authored = resolve_legacy_five_axis_state(
        origin_type="AUTHORED",
        audit_status="VERIFIED_CONSENSUS",
        legacy_is_verified=True,
        has_answer_source=True,
    )
    assert authored.rights_status == "OWNED"
    assert authored.verification_status == "VERIFIED"
    assert authored.publication_status == "PUBLISHED"

    # Unchecked authored stays DRAFT.
    authored_unchecked = resolve_legacy_five_axis_state(
        origin_type="AUTHORED",
        audit_status="UNCHECKED",
        legacy_is_verified=False,
        has_answer_source=False,
    )
    assert authored_unchecked.verification_status == "UNCHECKED"
    assert authored_unchecked.publication_status == "DRAFT"


def test_reconciliation_detects_missing_or_duplicate_rows_and_second_pass_violation() -> None:
    ok = compute_batch_reconciliation_report(
        source_legacy_ids=["1", "2", "3"],
        migrated_legacy_ids=["1", "3"],
        skipped_reasons={"2": "EMPTY_STEM"},
        second_pass_created_count=0,
    )
    assert ok.balanced is True
    assert ok.idempotent_second_pass_ok is True

    bad = compute_batch_reconciliation_report(
        source_legacy_ids=["1", "2", "3"],
        migrated_legacy_ids=["1", "1"],
        skipped_reasons={"2": "EMPTY_STEM"},
        second_pass_created_count=1,
    )
    assert bad.balanced is False
    assert bad.duplicate_legacy_ids == ("1",)
    assert bad.idempotent_second_pass_ok is False


# --- regression + boundary tests --------------------------------------------------------------

from app.contracts import enums as contract_enums  # noqa: E402
from app.contracts.blueprints import CognitiveLevel  # noqa: E402
from typing import get_args  # noqa: E402


def _report(source, migrated, skipped=None, second=0):
    return compute_batch_reconciliation_report(
        source_legacy_ids=source,
        migrated_legacy_ids=migrated,
        skipped_reasons=skipped or {},
        second_pass_created_count=second,
    )


def test_migrated_ids_outside_the_source_are_not_balanced() -> None:
    """Old behaviour: only counts were compared, so ["1","9"] migrated from source ["1","2"] looked fine."""
    report = _report(["1", "2"], ["1", "9"])
    assert report.balanced is False
    assert report.unknown_migrated_ids == ("9",)
    assert report.unaccounted_legacy_ids == ("2",)


def test_id_both_migrated_and_skipped_is_not_balanced() -> None:
    report = _report(["1", "2", "3"], ["1", "2"], {"2": "EMPTY_STEM", "3": "X"})
    assert report.balanced is False
    assert report.migrated_and_skipped_ids == ("2",)


def test_skipped_id_outside_the_source_is_not_balanced() -> None:
    report = _report(["1"], ["1"], {"77": "EMPTY"})
    assert report.balanced is False and report.unknown_skipped_ids == ("77",)


def test_duplicate_source_ids_are_reported() -> None:
    report = _report(["1", "1"], ["1"])
    assert report.balanced is False and report.duplicate_source_ids == ("1",)


def test_unaccounted_source_ids_with_matching_counts_are_detected() -> None:
    # counts line up (3 == 2 + 1) but id "3" vanished while "9" appeared as a skip
    report = _report(["1", "2", "3"], ["1", "2"], {"9": "GHOST"})
    assert report.balanced is False
    assert report.unaccounted_legacy_ids == ("3",)
    assert report.unknown_skipped_ids == ("9",)


def test_empty_batch_is_balanced_and_second_pass_flag_is_independent() -> None:
    ok = _report([], [])
    assert ok.balanced is True and ok.idempotent_second_pass_ok is True
    again = _report(["1"], ["1"], second=3)
    assert again.balanced is True and again.idempotent_second_pass_ok is False


def test_reports_are_sorted_and_deterministic() -> None:
    report = _report(["3", "1", "2"], ["3", "3", "1", "1"], {"2": "R"})
    assert report.duplicate_legacy_ids == ("1", "3")


def test_enums_come_from_the_contract_module() -> None:
    state = resolve_legacy_five_axis_state(
        origin_type="AUTHORED", audit_status="VERIFIED_BY_HUMAN", legacy_is_verified=False, has_answer_source=False
    )
    assert isinstance(state.verification_status, contract_enums.VerificationStatus)
    assert isinstance(state.rights_status, contract_enums.RightsStatus)
    assert isinstance(state.publication_status, contract_enums.PublicationStatus)
    assert isinstance(state.acquisition_status, contract_enums.AcquisitionStatus)
    assert isinstance(map_legacy_question_type("ESSAY"), contract_enums.QuestionItemType)


def test_every_mapped_value_is_a_member_of_the_contract_vocabulary() -> None:
    from app.ops import backfill_mapper as bm

    question_types = set(contract_enums.QuestionItemType)
    assert set(bm._QUESTION_TYPE_MAP.values()) <= question_types
    assert {d for d, _ in bm._DIFFICULTY_MAP.values()} <= set(get_args(CognitiveLevel))
    # the vocabularies the old local literals were missing are reachable from the contract enums
    assert {"TAKEDOWN", "TAKEN_DOWN", "DISPUTED", "REJECTED", "READY", "ARCHIVED", "MULTIPLE_SELECT", "GROUP_STEM"} <= {
        *(m.value for m in contract_enums.RightsStatus),
        *(m.value for m in contract_enums.PublicationStatus),
        *(m.value for m in contract_enums.VerificationStatus),
        *(m.value for m in contract_enums.QuestionItemType),
    }


@pytest.mark.parametrize(
    "mapper,raw",
    [
        (map_legacy_question_type, "NOPE"),
        (map_legacy_difficulty, "NOPE"),
        (map_legacy_library_kind, "NOPE"),
    ],
)
def test_unknown_values_raise_mapping_error_without_echoing_input(mapper, raw) -> None:
    with pytest.raises(BackfillMappingError) as info:
        mapper(raw)
    assert raw not in str(info.value)


@pytest.mark.parametrize("mapper", [map_legacy_question_type, map_legacy_difficulty, map_legacy_library_kind])
@pytest.mark.parametrize("bad", [None, "", "   ", 5, b"ESSAY"])
def test_non_string_or_blank_inputs_raise_mapping_error(mapper, bad) -> None:
    with pytest.raises(BackfillMappingError):
        mapper(bad)


def test_mappers_are_case_and_whitespace_insensitive_and_complete() -> None:
    assert map_legacy_question_type("  multiple_choice ") == "SINGLE_CHOICE"
    assert map_legacy_difficulty(" application ").difficulty == "VD"
    assert map_legacy_difficulty("advanced_application").difficulty == "VDC"
    assert map_legacy_difficulty("understanding").difficulty == "TH"
    assert map_legacy_library_kind("topic") == "DOCUMENT"
    assert map_legacy_library_kind("Paper") == "DOCUMENT"
    assert map_legacy_library_kind("article") == "ARTICLE"


@pytest.mark.parametrize("origin", [None, "", "CRAWLED", "FILE_IMPORT", "authored_by_bot", 5])
def test_only_authored_origin_is_owned(origin) -> None:
    state = resolve_legacy_five_axis_state(
        origin_type=origin, audit_status="VERIFIED_CONSENSUS", legacy_is_verified=True, has_answer_source=True
    )
    assert state.rights_status == "PENDING_REVIEW"
    assert state.publication_status == "DRAFT"  # verified but rights not cleared


def test_origin_is_case_and_whitespace_insensitive() -> None:
    state = resolve_legacy_five_axis_state(
        origin_type=" authored ", audit_status="verified_by_human", legacy_is_verified=False, has_answer_source=False
    )
    assert (state.rights_status, state.verification_status, state.publication_status) == ("OWNED", "VERIFIED", "PUBLISHED")


@pytest.mark.parametrize(
    "verified,has_source,expected",
    [(True, True, "AUTO_VERIFIED"), (True, False, "UNCHECKED"), (False, True, "UNCHECKED"), (False, False, "UNCHECKED")],
)
def test_legacy_is_verified_alone_never_verifies(verified, has_source, expected) -> None:
    state = resolve_legacy_five_axis_state(
        origin_type="AUTHORED", audit_status=None, legacy_is_verified=verified, has_answer_source=has_source
    )
    assert state.verification_status == expected
    assert state.publication_status == "DRAFT"


def test_truthy_non_bool_flags_do_not_count_as_verification_evidence() -> None:
    state = resolve_legacy_five_axis_state(
        origin_type="AUTHORED", audit_status=None, legacy_is_verified=1, has_answer_source="yes"  # type: ignore[arg-type]
    )
    assert state.verification_status == "UNCHECKED"


def test_unknown_audit_status_is_not_verification_and_visibility_is_validated() -> None:
    state = resolve_legacy_five_axis_state(
        origin_type="AUTHORED", audit_status="VERIFIED_MAYBE", legacy_is_verified=False, has_answer_source=False
    )
    assert state.verification_status == "UNCHECKED"
    assert resolve_legacy_five_axis_state(
        origin_type=None, audit_status=None, legacy_is_verified=False, has_answer_source=False,
        visibility_scope="TENANT_PRIVATE",
    ).visibility_scope == "TENANT_PRIVATE"
    for bad in ("PUBLIC", "", None, "system_public"):
        with pytest.raises(BackfillMappingError):
            resolve_legacy_five_axis_state(
                origin_type=None, audit_status=None, legacy_is_verified=False, has_answer_source=False,
                visibility_scope=bad,  # type: ignore[arg-type]
            )


@pytest.mark.parametrize("legacy_id", [0, -5, "abc_1", " 7 ", 10**12])
def test_stable_code_accepts_safe_ids(legacy_id) -> None:
    assert format_legacy_stable_code(legacy_id) == f"LEG-{str(legacy_id).strip()}"


@pytest.mark.parametrize("legacy_id", ["", "   ", "a b", "a/b", "../x", "x\n1", "é", None, True, 1.5, "a" * 65])
def test_stable_code_rejects_unsafe_ids(legacy_id) -> None:
    with pytest.raises(BackfillMappingError):
        format_legacy_stable_code(legacy_id)  # type: ignore[arg-type]
