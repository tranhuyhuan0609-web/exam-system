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
