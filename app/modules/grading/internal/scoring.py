"""Pure deterministic scoring functions for THPTQG 2025+ (module C07).

No I/O, no floats in score arithmetic. All monetary/score math uses Decimal.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.contracts.enums import QuestionItemType
from app.contracts.scoring import NumericParsePolicy

TF4_SCORING_MAP: dict[int, Decimal] = {
    0: Decimal("0.00"),
    1: Decimal("0.10"),
    2: Decimal("0.25"),
    3: Decimal("0.50"),
    4: Decimal("1.00"),
}

_THOUSAND_DOT_RE = re.compile(r"^-?\d{1,3}\.\d{3}$")
_PLAIN_DECIMAL_RE = re.compile(r"^-?(?:\d+(?:\.\d*)?|\.\d+)$")
_VALID_OPTION_KEYS = ("A", "B", "C", "D")
_VALID_TF_KEYS = ("a", "b", "c", "d")


@dataclass(frozen=True)
class NumericParseResult:
    """Result of parsing a Part III short-numeric response."""

    value: Decimal | None
    error_code: str | None = None

    @property
    def ok(self) -> bool:
        return self.value is not None and self.error_code is None


@dataclass(frozen=True)
class SingleChoiceAnswerKey:
    correct_key: Literal["A", "B", "C", "D"]


@dataclass(frozen=True)
class ClusterTrueFalseAnswerKey:
    sub_items: Mapping[Literal["a", "b", "c", "d"], bool]


@dataclass(frozen=True)
class ShortNumericAnswerKey:
    target_value: Decimal
    policy: NumericParsePolicy = NumericParsePolicy()


AnswerKey = SingleChoiceAnswerKey | ClusterTrueFalseAnswerKey | ShortNumericAnswerKey


@dataclass(frozen=True)
class SingleChoiceResponse:
    selected_key: str | None = None


@dataclass(frozen=True)
class ClusterTrueFalseResponse:
    sub_answers: Mapping[str, bool]


@dataclass(frozen=True)
class ShortNumericResponse:
    text_value: str = ""


StudentResponse = SingleChoiceResponse | ClusterTrueFalseResponse | ShortNumericResponse


@dataclass(frozen=True)
class GradingItemInput:
    """Input for grading a single exam item deterministically."""

    item_id: str
    item_type: QuestionItemType
    max_points: Decimal
    answer_key: AnswerKey
    student_response: StudentResponse
    option_permutation: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ItemGradeResult:
    """Deterministic grading outcome for a single item."""

    item_id: str
    item_type: QuestionItemType
    earned_points: Decimal
    max_points: Decimal
    is_correct: bool
    status: Literal["GRADED", "REQUIRES_RUBRIC_GRADING"] = "GRADED"
    displayed_key: str | None = None
    canonical_key: str | None = None
    matched_sub_items: int | None = None
    parsed_value: Decimal | None = None
    parse_error: str | None = None


@dataclass(frozen=True)
class AssessmentGradeResult:
    """Aggregated deterministic grading outcome for an entire assessment."""

    total_score: Decimal
    max_score: Decimal
    item_results: tuple[ItemGradeResult, ...]


def parse_short_numeric_vn(
    raw_input: str,
    policy: NumericParsePolicy | None = None,
) -> NumericParseResult:
    """Parse a Vietnamese short-numeric answer according to ``NumericParsePolicy``."""
    active_policy = policy or NumericParsePolicy()
    s = (raw_input or "").strip().replace(" ", "")
    if not s:
        return NumericParseResult(value=None, error_code="EMPTY")

    if len(s) > active_policy.max_chars:
        return NumericParseResult(
            value=None,
            error_code=f"MAX_CHARS_EXCEEDED_LIMIT_{active_policy.max_chars}",
        )

    if s.count(",") + s.count(".") > 1:
        return NumericParseResult(value=None, error_code="AMBIGUOUS_MULTIPLE_SEPARATORS")

    if not active_policy.allow_thousand_sep and _THOUSAND_DOT_RE.match(s):
        return NumericParseResult(
            value=None,
            error_code="THOUSAND_SEPARATOR_NOT_ALLOWED_USE_PLAIN_DIGITS",
        )

    if active_policy.decimal_sep == "COMMA_ONLY" and "." in s:
        return NumericParseResult(value=None, error_code="DISALLOWED_DECIMAL_SEPARATOR")
    if active_policy.decimal_sep == "DOT_ONLY" and "," in s:
        return NumericParseResult(value=None, error_code="DISALLOWED_DECIMAL_SEPARATOR")

    normalized = s.replace(",", ".")
    if not _PLAIN_DECIMAL_RE.fullmatch(normalized):
        return NumericParseResult(value=None, error_code="INVALID_NUMBER_FORMAT")

    try:
        val = Decimal(normalized)
    except InvalidOperation:
        return NumericParseResult(value=None, error_code="INVALID_NUMBER_FORMAT")

    if not val.is_finite():
        return NumericParseResult(value=None, error_code="INVALID_NUMBER_FORMAT")

    return NumericParseResult(value=val, error_code=None)


def grade_item(item: GradingItemInput) -> ItemGradeResult:
    """Grade one THPTQG 2025+ item deterministically using Decimal arithmetic."""
    if item.item_type == QuestionItemType.SINGLE_CHOICE:
        if not isinstance(item.answer_key, SingleChoiceAnswerKey) or not isinstance(
            item.student_response, SingleChoiceResponse
        ):
            raise TypeError("SINGLE_CHOICE requires SingleChoiceAnswerKey and SingleChoiceResponse")

        displayed_key = item.student_response.selected_key
        canonical_key = displayed_key
        if item.option_permutation and displayed_key in _VALID_OPTION_KEYS:
            pos_idx = ord(displayed_key) - ord("A")
            if 0 <= pos_idx < len(item.option_permutation):
                canonical_key = item.option_permutation[pos_idx]

        is_ok = canonical_key == item.answer_key.correct_key
        earned = item.max_points.quantize(Decimal("0.01")) if is_ok else Decimal("0.00")
        return ItemGradeResult(
            item_id=item.item_id,
            item_type=item.item_type,
            earned_points=earned,
            max_points=item.max_points,
            is_correct=is_ok,
            displayed_key=displayed_key,
            canonical_key=canonical_key,
        )

    if item.item_type == QuestionItemType.CLUSTER_TRUE_FALSE:
        if not isinstance(item.answer_key, ClusterTrueFalseAnswerKey) or not isinstance(
            item.student_response, ClusterTrueFalseResponse
        ):
            raise TypeError("CLUSTER_TRUE_FALSE requires ClusterTrueFalseAnswerKey and ClusterTrueFalseResponse")

        correct_map = item.answer_key.sub_items
        student_map = item.student_response.sub_answers
        match_count = sum(
            1 for k in _VALID_TF_KEYS if k in student_map and k in correct_map and student_map[k] == correct_map[k]
        )
        raw_pts = TF4_SCORING_MAP.get(match_count, Decimal("0.00"))
        earned = (raw_pts * item.max_points).quantize(Decimal("0.01"))
        return ItemGradeResult(
            item_id=item.item_id,
            item_type=item.item_type,
            earned_points=earned,
            max_points=item.max_points,
            is_correct=(match_count == len(_VALID_TF_KEYS)),
            matched_sub_items=match_count,
        )

    if item.item_type == QuestionItemType.SHORT_NUMERIC:
        if not isinstance(item.answer_key, ShortNumericAnswerKey) or not isinstance(
            item.student_response, ShortNumericResponse
        ):
            raise TypeError("SHORT_NUMERIC requires ShortNumericAnswerKey and ShortNumericResponse")

        parsed = parse_short_numeric_vn(
            item.student_response.text_value,
            policy=item.answer_key.policy,
        )
        if not parsed.ok or parsed.value is None:
            return ItemGradeResult(
                item_id=item.item_id,
                item_type=item.item_type,
                earned_points=Decimal("0.00"),
                max_points=item.max_points,
                is_correct=False,
                parse_error=parsed.error_code,
            )

        is_ok = abs(parsed.value - item.answer_key.target_value) <= item.answer_key.policy.tolerance
        earned = item.max_points.quantize(Decimal("0.01")) if is_ok else Decimal("0.00")
        return ItemGradeResult(
            item_id=item.item_id,
            item_type=item.item_type,
            earned_points=earned,
            max_points=item.max_points,
            is_correct=is_ok,
            parsed_value=parsed.value,
        )

    return ItemGradeResult(
        item_id=item.item_id,
        item_type=item.item_type,
        earned_points=Decimal("0.00"),
        max_points=item.max_points,
        is_correct=False,
        status="REQUIRES_RUBRIC_GRADING",
    )


def grade_assessment(items: Sequence[GradingItemInput]) -> AssessmentGradeResult:
    """Grade all items in an assessment and sum total score using Decimal."""
    results = tuple(grade_item(item) for item in items)
    total_score = sum((r.earned_points for r in results), Decimal("0.00")).quantize(Decimal("0.01"))
    max_score = sum((r.max_points for r in results), Decimal("0.00")).quantize(Decimal("0.01"))
    return AssessmentGradeResult(
        total_score=total_score,
        max_score=max_score,
        item_results=results,
    )
