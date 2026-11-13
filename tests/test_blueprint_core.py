"""Unit tests for C03 THPTQG 2025+ blueprint validation and shortage report (pure, no conftest)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.contracts.blueprints import (
    BlueprintContract,
    BlueprintMatrixCell,
    FrameworkSpecContract,
    SectionSpecContract,
)
from app.contracts.enums import QuestionItemType
from app.modules.frameworks.public import (
    THPTQG_2025_BIOLOGY_FRAMEWORK,
    THPTQG_2025_CHEMISTRY_FRAMEWORK,
    THPTQG_2025_MATH_FRAMEWORK,
    THPTQG_2025_PHYSICS_FRAMEWORK,
    shortage_report,
    validate_blueprint,
)


def _blueprint_from_framework(
    fw: FrameworkSpecContract,
    *,
    matrix_cells: tuple[BlueprintMatrixCell, ...] = (),
) -> BlueprintContract:
    return BlueprintContract(
        framework_code=fw.framework_code,
        subject_code=fw.subject_code,
        duration_seconds=fw.duration_seconds,
        total_points=fw.total_points,
        sections=fw.sections,
        matrix_cells=matrix_cells,
    )


def test_standard_math_framework_is_valid_and_totals_10_decimal() -> None:
    fw = THPTQG_2025_MATH_FRAMEWORK
    assert fw.duration_seconds == 5400
    assert fw.total_questions == 22
    computed = sum((s.section_points for s in fw.sections), Decimal("0.00"))
    assert isinstance(computed, Decimal)
    assert computed == Decimal("10.00")
    assert sum(s.question_count for s in fw.sections) == 22
    assert validate_blueprint(_blueprint_from_framework(fw)) == ()


@pytest.mark.parametrize(
    "fw",
    [
        THPTQG_2025_PHYSICS_FRAMEWORK,
        THPTQG_2025_CHEMISTRY_FRAMEWORK,
        THPTQG_2025_BIOLOGY_FRAMEWORK,
    ],
)
def test_standard_natural_science_frameworks_are_valid_and_total_10_decimal(
    fw: FrameworkSpecContract,
) -> None:
    assert fw.duration_seconds == 3000
    assert fw.total_questions == 28
    computed = sum((s.section_points for s in fw.sections), Decimal("0.00"))
    assert isinstance(computed, Decimal)
    assert computed == Decimal("10.00")
    assert sum(s.question_count for s in fw.sections) == 28
    assert validate_blueprint(_blueprint_from_framework(fw)) == ()


def test_validate_blueprint_unknown_framework_and_subject() -> None:
    bp_fw = _blueprint_from_framework(THPTQG_2025_MATH_FRAMEWORK).model_copy(
        update={"framework_code": "UNKNOWN_FW"}
    )
    assert [e.code for e in validate_blueprint(bp_fw)] == ["UNKNOWN_FRAMEWORK"]

    bp_subj = _blueprint_from_framework(THPTQG_2025_MATH_FRAMEWORK).model_copy(
        update={"subject_code": "HISTORY"}
    )
    assert [e.code for e in validate_blueprint(bp_subj)] == ["UNKNOWN_SUBJECT"]


def test_validate_blueprint_wrong_duration() -> None:
    bp = _blueprint_from_framework(THPTQG_2025_MATH_FRAMEWORK).model_copy(
        update={"duration_seconds": 3000}
    )
    codes = {e.code for e in validate_blueprint(bp)}
    assert codes == {"INVALID_DURATION"}


def test_validate_blueprint_wrong_section_count_and_total_questions() -> None:
    sections = list(THPTQG_2025_MATH_FRAMEWORK.sections)
    sections[0] = sections[0].model_copy(update={"question_count": 10})
    bp = _blueprint_from_framework(THPTQG_2025_MATH_FRAMEWORK).model_copy(
        update={"sections": tuple(sections)}
    )
    codes = {e.code for e in validate_blueprint(bp)}
    assert "INVALID_SECTION_QUESTION_COUNT" in codes
    assert "INVALID_TOTAL_QUESTIONS" in codes
    assert "INVALID_TOTAL_POINTS" in codes


def test_validate_blueprint_wrong_section_points() -> None:
    sections = list(THPTQG_2025_PHYSICS_FRAMEWORK.sections)
    sections[2] = sections[2].model_copy(update={"points_per_question": Decimal("0.50")})
    bp = _blueprint_from_framework(THPTQG_2025_PHYSICS_FRAMEWORK).model_copy(
        update={"sections": tuple(sections)}
    )
    codes = {e.code for e in validate_blueprint(bp)}
    assert "INVALID_SECTION_POINTS" in codes
    assert "INVALID_TOTAL_POINTS" in codes


def test_validate_blueprint_wrong_declared_total_points() -> None:
    bp = _blueprint_from_framework(THPTQG_2025_MATH_FRAMEWORK).model_copy(
        update={"total_points": Decimal("12.00")}
    )
    codes = {e.code for e in validate_blueprint(bp)}
    assert codes == {"INVALID_TOTAL_POINTS"}


def test_validate_blueprint_wrong_section_item_type() -> None:
    sections = list(THPTQG_2025_MATH_FRAMEWORK.sections)
    sections[0] = sections[0].model_copy(update={"item_type": QuestionItemType.ESSAY})
    bp = _blueprint_from_framework(THPTQG_2025_MATH_FRAMEWORK).model_copy(
        update={"sections": tuple(sections)}
    )
    codes = {e.code for e in validate_blueprint(bp)}
    assert codes == {"INVALID_SECTION_ITEM_TYPE"}


def test_validate_blueprint_missing_duplicate_and_unexpected_sections() -> None:
    s0, s1, _ = THPTQG_2025_MATH_FRAMEWORK.sections
    extra = SectionSpecContract(
        section_code="PART_IV_ESSAY",
        item_type=QuestionItemType.ESSAY,
        question_count=1,
        points_per_question=Decimal("1.00"),
    )
    bp = _blueprint_from_framework(THPTQG_2025_MATH_FRAMEWORK).model_copy(
        update={"sections": (s0, s0, s1, extra)}
    )
    codes = {e.code for e in validate_blueprint(bp)}
    assert "DUPLICATE_SECTION" in codes
    assert "MISSING_SECTION" in codes
    assert "UNEXPECTED_SECTION" in codes


def test_validate_blueprint_matrix_mismatch() -> None:
    cells = (
        BlueprintMatrixCell(
            taxonomy_node_code="MATH.PROB.BAYES",
            cognitive_level="VD",
            item_type=QuestionItemType.SINGLE_CHOICE,
            required_count=5,
        ),
    )
    bp = _blueprint_from_framework(THPTQG_2025_MATH_FRAMEWORK, matrix_cells=cells)
    codes = {e.code for e in validate_blueprint(bp)}
    assert "MATRIX_ITEM_COUNT_MISMATCH" in codes


def _sample_matrix_blueprint() -> BlueprintContract:
    cells = (
        BlueprintMatrixCell(
            taxonomy_node_code="MATH.CALC.DERIV",
            cognitive_level="NB",
            item_type=QuestionItemType.SINGLE_CHOICE,
            required_count=6,
        ),
        BlueprintMatrixCell(
            taxonomy_node_code="MATH.PROB.BAYES",
            cognitive_level="TH",
            item_type=QuestionItemType.SINGLE_CHOICE,
            required_count=6,
        ),
        BlueprintMatrixCell(
            taxonomy_node_code="MATH.GEOM.SPACE",
            cognitive_level="VD",
            item_type=QuestionItemType.CLUSTER_TRUE_FALSE,
            required_count=4,
        ),
        BlueprintMatrixCell(
            taxonomy_node_code="MATH.PROB.COND",
            cognitive_level="VDC",
            item_type=QuestionItemType.SHORT_NUMERIC,
            required_count=6,
        ),
    )
    bp = _blueprint_from_framework(THPTQG_2025_MATH_FRAMEWORK, matrix_cells=cells)
    assert validate_blueprint(bp) == ()
    return bp


def test_shortage_report_when_pool_sufficient() -> None:
    bp = _sample_matrix_blueprint()
    available = {
        ("MATH.CALC.DERIV", "NB", QuestionItemType.SINGLE_CHOICE): 20,
        ("MATH.PROB.BAYES", "TH", QuestionItemType.SINGLE_CHOICE): 6,
        ("MATH.GEOM.SPACE", "VD", QuestionItemType.CLUSTER_TRUE_FALSE): 10,
        ("MATH.PROB.COND", "VDC", QuestionItemType.SHORT_NUMERIC): 8,
    }
    report = shortage_report(bp, available)
    assert report.has_shortage is False
    assert report.total_required == 22
    assert report.total_available_matched == 22
    assert report.total_shortage == 0
    assert report.shortages == ()


def test_shortage_report_single_cell_short() -> None:
    bp = _sample_matrix_blueprint()
    available = {
        ("MATH.CALC.DERIV", "NB", QuestionItemType.SINGLE_CHOICE): 10,
        ("MATH.PROB.BAYES", "TH", QuestionItemType.SINGLE_CHOICE): 4,  # short by 2
        ("MATH.GEOM.SPACE", "VD", QuestionItemType.CLUSTER_TRUE_FALSE): 4,
        ("MATH.PROB.COND", "VDC", QuestionItemType.SHORT_NUMERIC): 6,
    }
    report = shortage_report(bp, available)
    assert report.has_shortage is True
    assert report.total_required == 22
    assert report.total_available_matched == 20
    assert report.total_shortage == 2
    assert len(report.shortages) == 1
    assert report.shortages[0].taxonomy_node_code == "MATH.PROB.BAYES"
    assert report.shortages[0].shortage_count == 2


def test_shortage_report_multiple_cells_short() -> None:
    bp = _sample_matrix_blueprint()
    available = {
        ("MATH.CALC.DERIV", "NB", QuestionItemType.SINGLE_CHOICE): 5,  # short 1
        ("MATH.PROB.BAYES", "TH", QuestionItemType.SINGLE_CHOICE): 6,  # ok
        ("MATH.GEOM.SPACE", "VD", QuestionItemType.CLUSTER_TRUE_FALSE): 1,  # short 3
        ("MATH.PROB.COND", "VDC", QuestionItemType.SHORT_NUMERIC): 2,  # short 4
    }
    report = shortage_report(bp, available)
    assert report.has_shortage is True
    assert report.total_required == 22
    assert report.total_available_matched == 14
    assert report.total_shortage == 8
    assert len(report.shortages) == 3
    by_node = {s.taxonomy_node_code: s.shortage_count for s in report.shortages}
    assert by_node == {
        "MATH.CALC.DERIV": 1,
        "MATH.GEOM.SPACE": 3,
        "MATH.PROB.COND": 4,
    }


def test_shortage_report_empty_pool() -> None:
    bp = _sample_matrix_blueprint()
    report = shortage_report(bp, {})
    assert report.has_shortage is True
    assert report.total_required == 22
    assert report.total_available_matched == 0
    assert report.total_shortage == 22
    assert len(report.shortages) == 4
