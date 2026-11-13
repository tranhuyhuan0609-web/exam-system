"""C01 typed validation for 6 Vietnamese exam item formats & immutable question version progression."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


class QuestionItemType(StrEnum):
    """Exact DDL CHECK values for `questions.item_type`."""

    SINGLE_CHOICE = "SINGLE_CHOICE"
    MULTIPLE_SELECT = "MULTIPLE_SELECT"
    CLUSTER_TRUE_FALSE = "CLUSTER_TRUE_FALSE"
    SHORT_NUMERIC = "SHORT_NUMERIC"
    ESSAY = "ESSAY"
    GROUP_STEM = "GROUP_STEM"


class DifficultyLevel(StrEnum):
    """Exact DDL CHECK values for `questions.difficulty_level`."""

    NB = "NB"
    TH = "TH"
    VD = "VD"
    VDC = "VDC"


class StimulusType(StrEnum):
    """Exact DDL CHECK values for `question_stimuli.stimulus_type`."""

    READING_PASSAGE = "READING_PASSAGE"
    EXPERIMENT_CONTEXT = "EXPERIMENT_CONTEXT"
    DATA_TABLE = "DATA_TABLE"
    SHARED_FIGURE = "SHARED_FIGURE"


class QuestionAuthoringError(ValueError):
    """Raised when typed authoring validation or immutability rules are violated."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class QuestionOptionDraft:
    label: str
    content_text: str


@dataclass(frozen=True, slots=True)
class QuestionDraft:
    item_type: QuestionItemType
    difficulty_level: DifficultyLevel
    stem_text: str
    options: tuple[QuestionOptionDraft, ...] = ()
    answer_schema: Mapping[str, Any] = field(default_factory=dict)
    stimulus_id: str | None = None
    max_short_numeric_chars: int = 4


@dataclass(frozen=True, slots=True)
class QuestionVersionState:
    question_id: str
    tenant_id: str
    version_no: int
    draft: QuestionDraft
    content_hash: str
    is_immutable: bool = False
    change_reason: str | None = None
    created_by: str | None = None
    approved_by: str | None = None


def _compute_draft_hash(draft: QuestionDraft) -> str:
    canonical = {
        "item_type": draft.item_type.value,
        "difficulty_level": draft.difficulty_level.value,
        "stem_text": draft.stem_text.strip(),
        "options": [{"label": o.label.strip(), "content_text": o.content_text.strip()} for o in draft.options],
        "answer_schema": dict(draft.answer_schema),
        "stimulus_id": draft.stimulus_id,
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _option_labels(options: Sequence[QuestionOptionDraft]) -> list[str]:
    seen: set[str] = set()
    labels: list[str] = []
    for opt in options:
        lbl = opt.label.strip()
        if not lbl:
            raise QuestionAuthoringError("EMPTY_OPTION_LABEL", "Option label must be non-empty")
        if not opt.content_text.strip():
            raise QuestionAuthoringError("EMPTY_OPTION_CONTENT", f"Option {lbl!r} content must be non-empty")
        if lbl in seen:
            raise QuestionAuthoringError("DUPLICATE_OPTION_LABEL", f"Duplicate option label {lbl!r}")
        seen.add(lbl)
        labels.append(lbl)
    return labels


def _validate_short_numeric(answer_schema: Mapping[str, Any], *, max_chars: int) -> None:
    raw_val = answer_schema.get("value")
    if raw_val is None or not str(raw_val).strip():
        raise QuestionAuthoringError("MISSING_NUMERIC_ANSWER", "SHORT_NUMERIC requires answer_schema['value']")
    val_str = str(raw_val).strip()
    if len(val_str) > max_chars:
        raise QuestionAuthoringError(
            "NUMERIC_ANSWER_TOO_LONG",
            f"SHORT_NUMERIC answer {val_str!r} exceeds THPTQG limit ({max_chars} chars)",
        )
    # Reject thousand-separator formatting (e.g. '1.000' when integer intended or ambiguous multi-dot)
    if val_str.count(".") + val_str.count(",") > 1:
        raise QuestionAuthoringError(
            "AMBIGUOUS_DECIMAL_SEPARATOR",
            f"Multiple decimal/thousand separators in {val_str!r}",
        )


def validate_question_draft(draft: QuestionDraft) -> None:
    """Validate structure and answer schema completeness for all 6 V11.3 item types."""
    if not draft.stem_text or not draft.stem_text.strip():
        raise QuestionAuthoringError("MISSING_STEM", "Question stem_text must be non-empty")

    item_type = draft.item_type

    if item_type == QuestionItemType.SINGLE_CHOICE:
        labels = _option_labels(draft.options)
        if len(labels) < 2:
            raise QuestionAuthoringError("INSUFFICIENT_OPTIONS", "SINGLE_CHOICE requires at least 2 options")
        correct = draft.answer_schema.get("correct_option")
        if not isinstance(correct, str) or correct not in labels:
            raise QuestionAuthoringError(
                "INVALID_ANSWER_KEY",
                f"SINGLE_CHOICE correct_option {correct!r} must match one of {labels}",
            )
        return

    if item_type == QuestionItemType.MULTIPLE_SELECT:
        labels = _option_labels(draft.options)
        if len(labels) < 2:
            raise QuestionAuthoringError("INSUFFICIENT_OPTIONS", "MULTIPLE_SELECT requires at least 2 options")
        correct_list = draft.answer_schema.get("correct_options")
        if not isinstance(correct_list, (list, tuple)) or len(correct_list) == 0:
            raise QuestionAuthoringError("INVALID_ANSWER_KEY", "MULTIPLE_SELECT requires non-empty correct_options")
        if not all(isinstance(c, str) and c in labels for c in correct_list):
            raise QuestionAuthoringError(
                "INVALID_ANSWER_KEY",
                f"Every correct_options entry must match {labels}",
            )
        return

    if item_type == QuestionItemType.CLUSTER_TRUE_FALSE:
        labels = _option_labels(draft.options)
        if len(labels) != 4:
            raise QuestionAuthoringError(
                "INVALID_CLUSTER_TF_OPTIONS",
                "CLUSTER_TRUE_FALSE requires exactly 4 sub-statements (a, b, c, d)",
            )
        booleans = draft.answer_schema.get("statements")
        if not isinstance(booleans, Mapping) or set(booleans.keys()) != set(labels):
            raise QuestionAuthoringError(
                "INVALID_ANSWER_KEY",
                "CLUSTER_TRUE_FALSE requires boolean truth values for all 4 statements",
            )
        if not all(isinstance(v, bool) for v in booleans.values()):
            raise QuestionAuthoringError(
                "INVALID_ANSWER_KEY",
                "CLUSTER_TRUE_FALSE answer_schema['statements'] values must all be booleans",
            )
        return

    if item_type == QuestionItemType.SHORT_NUMERIC:
        _validate_short_numeric(draft.answer_schema, max_chars=draft.max_short_numeric_chars)
        return

    if item_type == QuestionItemType.ESSAY:
        rubric = draft.answer_schema.get("rubric")
        max_points = draft.answer_schema.get("max_points")
        if not rubric or not str(rubric).strip():
            raise QuestionAuthoringError("MISSING_ESSAY_RUBRIC", "ESSAY requires answer_schema['rubric']")
        if not isinstance(max_points, (int, float)) or isinstance(max_points, bool) or max_points <= 0:
            raise QuestionAuthoringError("INVALID_ESSAY_POINTS", "ESSAY requires positive max_points")
        return

    if item_type == QuestionItemType.GROUP_STEM:
        if not draft.stimulus_id or not draft.stimulus_id.strip():
            raise QuestionAuthoringError("MISSING_STIMULUS_ID", "GROUP_STEM requires a linked stimulus_id")
        child_ids = draft.answer_schema.get("child_question_ids")
        if not isinstance(child_ids, (list, tuple)) or len(child_ids) < 2:
            raise QuestionAuthoringError(
                "INSUFFICIENT_GROUP_CHILDREN",
                "GROUP_STEM requires at least 2 child_question_ids",
            )
        return

    raise QuestionAuthoringError("UNSUPPORTED_ITEM_TYPE", f"Unsupported item_type: {item_type}")


def assert_version_mutable(version: QuestionVersionState) -> None:
    """Prevent in-place edits on frozen/published versions (`is_immutable=True`)."""
    if version.is_immutable:
        raise QuestionAuthoringError(
            "QUESTION_VERSION_IMMUTABLE",
            f"Question version v{version.version_no} is immutable; create v{version.version_no + 1} instead",
        )


def edit_draft_version(version: QuestionVersionState, *, new_draft: QuestionDraft) -> QuestionVersionState:
    """Edit an unpublished draft version in place after validating the updated draft."""
    assert_version_mutable(version)
    validate_question_draft(new_draft)
    return replace(version, draft=new_draft, content_hash=_compute_draft_hash(new_draft))


def publish_version(version: QuestionVersionState, *, approved_by: str) -> QuestionVersionState:
    """Freeze a validated version as immutable upon publication."""
    if not approved_by:
        raise QuestionAuthoringError("MISSING_APPROVER", "approved_by is required to freeze version")
    validate_question_draft(version.draft)
    return replace(version, is_immutable=True, approved_by=approved_by)


def fork_next_version(
    current: QuestionVersionState,
    *,
    new_draft: QuestionDraft,
    change_reason: str,
    created_by: str,
) -> QuestionVersionState:
    """Create `version_no + 1` as a new mutable draft without altering the historical published version."""
    if not change_reason or not change_reason.strip():
        raise QuestionAuthoringError("MISSING_CHANGE_REASON", "Creating next version requires change_reason")
    if not created_by:
        raise QuestionAuthoringError("MISSING_AUTHOR", "created_by is required")
    validate_question_draft(new_draft)
    return QuestionVersionState(
        question_id=current.question_id,
        tenant_id=current.tenant_id,
        version_no=current.version_no + 1,
        draft=new_draft,
        content_hash=_compute_draft_hash(new_draft),
        is_immutable=False,
        change_reason=change_reason.strip(),
        created_by=created_by,
        approved_by=None,
    )
