"""C01 typed validation for 6 Vietnamese exam item formats & immutable question version progression."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
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


def _plain(value: Any) -> Any:
    """Convert nested mappings/sequences into plain JSON-able dict/list values (a detached copy)."""
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_plain(v) for v in value]
        return sorted(items, key=repr) if isinstance(value, (set, frozenset)) else items
    return value


def _freeze(value: Any) -> Any:
    """Deep-freeze nested mappings/sequences: mappings become read-only proxies, sequences tuples."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _compute_draft_hash(draft: QuestionDraft) -> str:
    canonical = {
        "item_type": str(draft.item_type),
        "difficulty_level": str(draft.difficulty_level),
        "stem_text": draft.stem_text.strip(),
        "options": [{"label": o.label.strip(), "content_text": o.content_text.strip()} for o in draft.options],
        "answer_schema": _plain(draft.answer_schema),
        "stimulus_id": draft.stimulus_id,
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
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


_NUMERIC_RE = re.compile(r"-?[0-9]+(?:[.,][0-9]+)?")
# "1.000" / "12,345": one to three leading digits then exactly three decimals can be read as a thousands group.
_AMBIGUOUS_GROUPING_RE = re.compile(r"-?[1-9][0-9]{0,2}[.,][0-9]{3}")


def _validate_short_numeric(answer_schema: Mapping[str, Any], *, max_chars: int) -> None:
    raw_val = answer_schema.get("value")
    if raw_val is None or isinstance(raw_val, bool) or not str(raw_val).strip():
        raise QuestionAuthoringError("MISSING_NUMERIC_ANSWER", "SHORT_NUMERIC requires answer_schema['value']")
    val_str = str(raw_val).strip()
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
        raise QuestionAuthoringError("INVALID_NUMERIC_LIMIT", "max_short_numeric_chars must be a positive integer")
    if len(val_str) > max_chars:
        raise QuestionAuthoringError(
            "NUMERIC_ANSWER_TOO_LONG",
            f"SHORT_NUMERIC answer {val_str!r} exceeds THPTQG limit ({max_chars} chars)",
        )
    if not _NUMERIC_RE.fullmatch(val_str):
        raise QuestionAuthoringError(
            "INVALID_NUMERIC_ANSWER",
            f"SHORT_NUMERIC answer {val_str!r} is not a plain decimal number",
        )
    if _AMBIGUOUS_GROUPING_RE.fullmatch(val_str):
        raise QuestionAuthoringError(
            "AMBIGUOUS_DECIMAL_SEPARATOR",
            f"{val_str!r} can be read as a thousands group or as a decimal; write it unambiguously",
        )


def validate_question_draft(draft: QuestionDraft) -> None:
    """Validate structure and answer schema completeness for all 6 V11.3 item types."""
    if not draft.stem_text or not draft.stem_text.strip():
        raise QuestionAuthoringError("MISSING_STEM", "Question stem_text must be non-empty")

    if draft.difficulty_level not in set(DifficultyLevel) or not isinstance(draft.difficulty_level, DifficultyLevel):
        raise QuestionAuthoringError("INVALID_DIFFICULTY", "difficulty_level must be NB, TH, VD or VDC")

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
        if len(set(correct_list)) != len(correct_list):
            raise QuestionAuthoringError(
                "DUPLICATE_CORRECT_OPTION",
                "MULTIPLE_SELECT correct_options must not repeat an option",
            )
        return

    if item_type == QuestionItemType.CLUSTER_TRUE_FALSE:
        labels = _option_labels(draft.options)
        if len(labels) != 4 or set(labels) != {"a", "b", "c", "d"}:
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
        valid_type = isinstance(max_points, (int, float, Decimal)) and not isinstance(max_points, bool)
        if not valid_type or not math.isfinite(max_points) or max_points <= 0:
            raise QuestionAuthoringError("INVALID_ESSAY_POINTS", "ESSAY requires finite positive max_points")
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
        if not all(isinstance(c, str) and c.strip() for c in child_ids) or len(set(child_ids)) != len(child_ids):
            raise QuestionAuthoringError(
                "INVALID_GROUP_CHILDREN",
                "child_question_ids must be unique non-empty strings",
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
    detached = replace(new_draft, answer_schema=_plain(new_draft.answer_schema))
    return replace(version, draft=detached, content_hash=_compute_draft_hash(detached))


def publish_version(version: QuestionVersionState, *, approved_by: str) -> QuestionVersionState:
    """Freeze a validated version: recompute the content hash and make `answer_schema` read-only."""
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise QuestionAuthoringError("MISSING_APPROVER", "approved_by is required to freeze version")
    assert_version_mutable(version)
    validate_question_draft(version.draft)
    frozen_draft = replace(version.draft, answer_schema=_freeze(version.draft.answer_schema))
    return replace(
        version,
        draft=frozen_draft,
        content_hash=_compute_draft_hash(frozen_draft),
        is_immutable=True,
        approved_by=approved_by,
    )


def fork_next_version(
    current: QuestionVersionState,
    *,
    new_draft: QuestionDraft,
    change_reason: str,
    created_by: str,
) -> QuestionVersionState:
    """Create `version_no + 1` as a new mutable draft; only an immutable (published) version may be forked."""
    if not current.is_immutable:
        raise QuestionAuthoringError(
            "VERSION_NOT_PUBLISHED",
            f"Question version v{current.version_no} is still a draft; edit it instead of forking",
        )
    if not isinstance(change_reason, str) or not change_reason.strip():
        raise QuestionAuthoringError("MISSING_CHANGE_REASON", "Creating next version requires change_reason")
    if not isinstance(created_by, str) or not created_by.strip():
        raise QuestionAuthoringError("MISSING_AUTHOR", "created_by is required")
    validate_question_draft(new_draft)
    detached = replace(new_draft, answer_schema=_plain(new_draft.answer_schema))
    return QuestionVersionState(
        question_id=current.question_id,
        tenant_id=current.tenant_id,
        version_no=current.version_no + 1,
        draft=detached,
        content_hash=_compute_draft_hash(detached),
        is_immutable=False,
        change_reason=change_reason.strip(),
        created_by=created_by,
        approved_by=None,
    )
