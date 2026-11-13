"""Tests for G06 vertical pack isolation + localization."""

from __future__ import annotations

import pytest

from app.modules.vertical_packs.public import (
    VerticalPackConfig,
    VerticalPackError,
    VerticalPackRegistry,
    resolve_localized_label,
)


def test_g06_vertical_pack_isolation_and_locale_fallback() -> None:
    k12 = VerticalPackConfig(
        code="K12_VN",
        display_name="Phổ thông Việt Nam",
        taxonomy_root_prefix="K12",
        assessment_framework_code="THPTQG_2025",
        scoring_policy_code="VN_10PT",
        is_core_k12=True,
        active=True,
    )
    ielts = VerticalPackConfig(
        code="IELTS",
        display_name="IELTS Academic",
        taxonomy_root_prefix="IELTS",
        assessment_framework_code="IELTS_BAND_9",
        scoring_policy_code="BAND_ROUND_0_5",
        default_locale="en-GB",
        active=False,
    )
    reg = VerticalPackRegistry((k12, ielts))

    activated = reg.activate("IELTS")
    assert activated.active is True
    assert reg.get("K12_VN").scoring_policy_code == "VN_10PT"

    with pytest.raises(VerticalPackError, match="core K-12"):
        reg.deactivate("K12_VN")

    with pytest.raises(VerticalPackError, match="taxonomy_root_prefix"):
        reg.register(
            VerticalPackConfig(
                code="IELTS_GT",
                display_name="General",
                taxonomy_root_prefix="IELTS",
                assessment_framework_code="IELTS_BAND_9",
                scoring_policy_code="BAND_ROUND_0_5",
            )
        )

    bundles = {
        "vi-VN": {"submit_exam": "Nộp bài", "band_score": "Điểm chuẩn"},
        "en": {"submit_exam": "Submit Exam"},
    }
    assert resolve_localized_label(bundles=bundles, key="submit_exam", requested_locale="en-GB") == "Submit Exam"
    assert resolve_localized_label(bundles=bundles, key="band_score", requested_locale="en-GB") == "Điểm chuẩn"


def _pack(code: str, prefix: str, **kw) -> VerticalPackConfig:
    return VerticalPackConfig(
        code=code,
        display_name=code,
        taxonomy_root_prefix=prefix,
        assessment_framework_code="F",
        scoring_policy_code="S",
        **kw,
    )


@pytest.mark.parametrize(("first", "second"), [("MATH", "MATH_ADV"), ("MATH_ADV", "MATH"), ("math", "MATH")])
def test_overlapping_taxonomy_prefixes_are_rejected_in_both_directions(first: str, second: str) -> None:
    # Regression: only equal prefixes were detected, MATH versus MATH_ADV slipped through.
    reg = VerticalPackRegistry((_pack("P1", first),))
    with pytest.raises(VerticalPackError, match="overlaps"):
        reg.register(_pack("P2", second))


def test_disjoint_prefixes_are_accepted() -> None:
    reg = VerticalPackRegistry((_pack("A", "MATH"), _pack("B", "PHYS")))
    assert {p.code for p in reg._packs.values()} == {"A", "B"}


def test_duplicate_code_blank_values_and_unknown_codes_are_rejected() -> None:
    reg = VerticalPackRegistry((_pack("A", "AAA"),))
    with pytest.raises(VerticalPackError, match="duplicate"):
        reg.register(_pack("a", "BBB"))
    with pytest.raises(VerticalPackError):
        reg.register(_pack("  ", "CCC"))
    with pytest.raises(VerticalPackError):
        reg.register(_pack("C", "  "))
    for call in (reg.get, reg.activate, reg.deactivate):
        with pytest.raises(VerticalPackError, match="unknown"):
            call("NOPE")


def test_non_string_pack_fields_fail_closed() -> None:
    with pytest.raises(VerticalPackError):
        VerticalPackRegistry().register(_pack(None, "X"))  # type: ignore[arg-type]


def test_activate_and_list_active_are_case_insensitive_and_isolated() -> None:
    reg = VerticalPackRegistry((_pack("A", "AAA"), _pack("B", "BBB", active=True)))
    assert [p.code for p in reg.list_active()] == ["B"]
    reg.activate(" a ")
    assert {p.code for p in reg.list_active()} == {"A", "B"}
    reg.deactivate("b")
    assert [p.code for p in reg.list_active()] == ["A"]


def test_label_falls_back_to_raw_key_and_ignores_blank_entries() -> None:
    bundles = {"vi-VN": {"k": "  "}, "en": {"k": "Hello"}}
    assert resolve_localized_label(bundles=bundles, key="k", requested_locale="fr-FR") == "k"
    assert resolve_localized_label(bundles=bundles, key="k", requested_locale="en-US") == "Hello"
    assert resolve_localized_label(bundles={}, key="missing", requested_locale="") == "missing"
