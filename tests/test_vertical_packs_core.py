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
