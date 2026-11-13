"""Pure THPTQG 2025+ blueprint validation and shortage reporting (module C03).

No I/O, no floats in score calculations; all point math uses Decimal.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from decimal import Decimal

from app.contracts.blueprints import (
    BlueprintContract,
    BlueprintValidationError,
    CognitiveLevel,
    FrameworkSpecContract,
    SectionCode,
    SectionSpecContract,
    ShortageCell,
    ShortageReport,
)
from app.contracts.enums import QuestionItemType

MatrixKey = tuple[str, CognitiveLevel, QuestionItemType]

THPTQG_2025_MATH_FRAMEWORK = FrameworkSpecContract(
    framework_code="THPTQG_2025",
    subject_code="MATH",
    duration_seconds=90 * 60,
    total_questions=22,
    total_points=Decimal("10.00"),
    sections=(
        SectionSpecContract(
            section_code="PART_I_MCQ",
            item_type=QuestionItemType.SINGLE_CHOICE,
            question_count=12,
            points_per_question=Decimal("0.25"),
        ),
        SectionSpecContract(
            section_code="PART_II_TF4",
            item_type=QuestionItemType.CLUSTER_TRUE_FALSE,
            question_count=4,
            points_per_question=Decimal("1.00"),
        ),
        SectionSpecContract(
            section_code="PART_III_SHORT",
            item_type=QuestionItemType.SHORT_NUMERIC,
            question_count=6,
            points_per_question=Decimal("0.50"),
        ),
    ),
)


def _make_natural_science_framework(subject_code: str) -> FrameworkSpecContract:
    return FrameworkSpecContract(
        framework_code="THPTQG_2025",
        subject_code=subject_code,
        duration_seconds=50 * 60,
        total_questions=28,
        total_points=Decimal("10.00"),
        sections=(
            SectionSpecContract(
                section_code="PART_I_MCQ",
                item_type=QuestionItemType.SINGLE_CHOICE,
                question_count=18,
                points_per_question=Decimal("0.25"),
            ),
            SectionSpecContract(
                section_code="PART_II_TF4",
                item_type=QuestionItemType.CLUSTER_TRUE_FALSE,
                question_count=4,
                points_per_question=Decimal("1.00"),
            ),
            SectionSpecContract(
                section_code="PART_III_SHORT",
                item_type=QuestionItemType.SHORT_NUMERIC,
                question_count=6,
                points_per_question=Decimal("0.25"),
            ),
        ),
    )


THPTQG_2025_PHYSICS_FRAMEWORK = _make_natural_science_framework("PHYSICS")
THPTQG_2025_CHEMISTRY_FRAMEWORK = _make_natural_science_framework("CHEMISTRY")
THPTQG_2025_BIOLOGY_FRAMEWORK = _make_natural_science_framework("BIOLOGY")

THPTQG_2025_FRAMEWORKS: dict[str, FrameworkSpecContract] = {
    "MATH": THPTQG_2025_MATH_FRAMEWORK,
    "PHYSICS": THPTQG_2025_PHYSICS_FRAMEWORK,
    "CHEMISTRY": THPTQG_2025_CHEMISTRY_FRAMEWORK,
    "BIOLOGY": THPTQG_2025_BIOLOGY_FRAMEWORK,
}


def get_thptqg_2025_framework(subject_code: str) -> FrameworkSpecContract | None:
    """Look up the standard THPTQG 2025+ framework for a subject code."""
    return THPTQG_2025_FRAMEWORKS.get(subject_code.upper())


def validate_blueprint(
    blueprint: BlueprintContract,
    reference: FrameworkSpecContract | None = None,
) -> tuple[BlueprintValidationError, ...]:
    """Validate a blueprint against a reference framework (defaults to THPTQG 2025+)."""
    target = reference
    if target is None:
        if blueprint.framework_code != "THPTQG_2025":
            return (
                BlueprintValidationError(
                    code="UNKNOWN_FRAMEWORK",
                    message=f"Unsupported framework_code: {blueprint.framework_code}",
                    field="framework_code",
                ),
            )
        target = get_thptqg_2025_framework(blueprint.subject_code)
        if target is None:
            return (
                BlueprintValidationError(
                    code="UNKNOWN_SUBJECT",
                    message=f"Unsupported subject_code for THPTQG_2025: {blueprint.subject_code}",
                    field="subject_code",
                ),
            )

    errors: list[BlueprintValidationError] = []

    if blueprint.duration_seconds != target.duration_seconds:
        errors.append(
            BlueprintValidationError(
                code="INVALID_DURATION",
                message=(
                    f"Expected duration {target.duration_seconds}s for {target.subject_code}, "
                    f"got {blueprint.duration_seconds}s"
                ),
                field="duration_seconds",
            )
        )

    ref_by_code: dict[SectionCode, SectionSpecContract] = {s.section_code: s for s in target.sections}
    seen_sections: set[SectionCode] = set()
    actual_by_code: dict[SectionCode, SectionSpecContract] = {}

    for sec in blueprint.sections:
        if sec.section_code in seen_sections:
            errors.append(
                BlueprintValidationError(
                    code="DUPLICATE_SECTION",
                    message=f"Duplicate section {sec.section_code}",
                    field=f"sections.{sec.section_code}",
                )
            )
            continue
        seen_sections.add(sec.section_code)
        actual_by_code[sec.section_code] = sec

        if sec.section_code not in ref_by_code:
            errors.append(
                BlueprintValidationError(
                    code="UNEXPECTED_SECTION",
                    message=f"Section {sec.section_code} is not part of {target.framework_code}/{target.subject_code}",
                    field=f"sections.{sec.section_code}",
                )
            )

    for code, ref_sec in ref_by_code.items():
        actual_sec = actual_by_code.get(code)
        if actual_sec is None:
            errors.append(
                BlueprintValidationError(
                    code="MISSING_SECTION",
                    message=f"Missing required section {code}",
                    field=f"sections.{code}",
                )
            )
            continue

        if actual_sec.item_type != ref_sec.item_type:
            errors.append(
                BlueprintValidationError(
                    code="INVALID_SECTION_ITEM_TYPE",
                    message=(
                        f"Section {code} expected item_type {ref_sec.item_type.value}, "
                        f"got {actual_sec.item_type.value}"
                    ),
                    field=f"sections.{code}.item_type",
                )
            )
        if actual_sec.question_count != ref_sec.question_count:
            errors.append(
                BlueprintValidationError(
                    code="INVALID_SECTION_QUESTION_COUNT",
                    message=(
                        f"Section {code} expected {ref_sec.question_count} questions, "
                        f"got {actual_sec.question_count}"
                    ),
                    field=f"sections.{code}.question_count",
                )
            )
        if actual_sec.points_per_question != ref_sec.points_per_question:
            errors.append(
                BlueprintValidationError(
                    code="INVALID_SECTION_POINTS",
                    message=(
                        f"Section {code} expected {ref_sec.points_per_question} pts/question, "
                        f"got {actual_sec.points_per_question}"
                    ),
                    field=f"sections.{code}.points_per_question",
                )
            )

    total_questions = sum(s.question_count for s in blueprint.sections)
    if total_questions != target.total_questions:
        errors.append(
            BlueprintValidationError(
                code="INVALID_TOTAL_QUESTIONS",
                message=f"Expected {target.total_questions} total questions, got {total_questions}",
                field="sections",
            )
        )

    computed_points = sum((s.section_points for s in blueprint.sections), Decimal("0.00")).quantize(Decimal("0.01"))
    declared_points = blueprint.total_points.quantize(Decimal("0.01"))
    if computed_points != target.total_points or declared_points != target.total_points:
        errors.append(
            BlueprintValidationError(
                code="INVALID_TOTAL_POINTS",
                message=(
                    f"Expected total points {target.total_points}, "
                    f"got declared={blueprint.total_points} computed={computed_points}"
                ),
                field="total_points",
            )
        )

    if blueprint.matrix_cells:
        section_counts_by_type: dict[QuestionItemType, int] = defaultdict(int)
        for sec in blueprint.sections:
            section_counts_by_type[sec.item_type] += sec.question_count

        matrix_counts_by_type: dict[QuestionItemType, int] = defaultdict(int)
        for cell in blueprint.matrix_cells:
            matrix_counts_by_type[cell.item_type] += cell.required_count

        all_types = set(section_counts_by_type) | set(matrix_counts_by_type)
        for itype in sorted(all_types, key=lambda t: t.value):
            sec_cnt = section_counts_by_type.get(itype, 0)
            mat_cnt = matrix_counts_by_type.get(itype, 0)
            if sec_cnt != mat_cnt:
                errors.append(
                    BlueprintValidationError(
                        code="MATRIX_ITEM_COUNT_MISMATCH",
                        message=(
                            f"Matrix requests {mat_cnt} items of {itype.value}, "
                            f"but sections specify {sec_cnt}"
                        ),
                        field="matrix_cells",
                    )
                )

    return tuple(errors)


def shortage_report(
    blueprint: BlueprintContract,
    available: Mapping[MatrixKey, int],
) -> ShortageReport:
    """Compute per-cell question shortages between blueprint matrix and available pool."""
    required_by_key: dict[MatrixKey, int] = defaultdict(int)
    for cell in blueprint.matrix_cells:
        key: MatrixKey = (cell.taxonomy_node_code, cell.cognitive_level, cell.item_type)
        required_by_key[key] += cell.required_count

    total_required = 0
    total_available_matched = 0
    total_shortage = 0
    shortages: list[ShortageCell] = []

    for key, req in required_by_key.items():
        node_code, level, itype = key
        avail = max(0, available.get(key, 0))
        matched = min(req, avail)
        shortage = max(0, req - avail)

        total_required += req
        total_available_matched += matched
        total_shortage += shortage

        if shortage > 0:
            shortages.append(
                ShortageCell(
                    taxonomy_node_code=node_code,
                    cognitive_level=level,
                    item_type=itype,
                    required_count=req,
                    available_count=avail,
                    shortage_count=shortage,
                )
            )

    return ShortageReport(
        total_required=total_required,
        total_available_matched=total_available_matched,
        total_shortage=total_shortage,
        has_shortage=total_shortage > 0,
        shortages=tuple(shortages),
    )
