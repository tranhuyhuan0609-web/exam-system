"""Allowlist-based exam paper sanitizer for active exam sessions (module C05)."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from app.contracts.delivery import (
    NumericParsePolicyPublicContract,
    SanitizedExamPaperContract,
    SanitizedOptionContract,
    SanitizedQuestionContract,
)
from app.contracts.enums import QuestionItemType
from app.modules.assembly.public import AttemptPermutation

_DISPLAY_CHOICE_LABELS: tuple[str, ...] = ("A", "B", "C", "D")
_DISPLAY_CLUSTER_LABELS: tuple[str, ...] = ("a", "b", "c", "d")
_MAX_CONTENT_JSON_DEPTH = 32

# Strict allowlist for rich-text/media content dicts inside stem_json / content_json / stimulus_json.
# Answer, solution, rubric, grading points, and original option identity keys are never allowlisted.
_ALLOWED_CONTENT_KEYS: frozenset[str] = frozenset(
    {
        "text",
        "html",
        "markdown",
        "latex",
        "blocks",
        "type",
        "children",
        "content",
        "runs",
        "media",
        "assets",
        "asset_id",
        "url",
        "alt",
        "caption",
        "width",
        "height",
        "format",
        "lang",
        "items",
        "statement",
        "title",
        "prompt",
    }
)


def _sanitize_json_value(value: Any, *, depth: int = 0) -> Any:
    if depth > _MAX_CONTENT_JSON_DEPTH:
        raise ValueError("content_json exceeds maximum nesting depth")
    if isinstance(value, Mapping):
        return {
            str(k): _sanitize_json_value(v, depth=depth + 1)
            for k, v in value.items()
            if str(k) in _ALLOWED_CONTENT_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_json_value(item, depth=depth + 1) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float in content_json")
        return value
    return str(value)


def _sanitize_content_dict(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    result = _sanitize_json_value(raw, depth=0)
    return result if isinstance(result, dict) else {}


def _extract_raw_options(raw_question: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    raw_opts = raw_question.get("options")
    if isinstance(raw_opts, Sequence) and not isinstance(raw_opts, (str, bytes)):
        return [o for o in raw_opts if isinstance(o, Mapping)]
    raw_opts_json = raw_question.get("options_json")
    if isinstance(raw_opts_json, Sequence) and not isinstance(raw_opts_json, (str, bytes)):
        return [o for o in raw_opts_json if isinstance(o, Mapping)]
    return ()


def _sanitize_options(
    raw_question: Mapping[str, Any],
    q_id: UUID,
    item_type: QuestionItemType,
    permutation: AttemptPermutation,
) -> tuple[SanitizedOptionContract, ...]:
    raw_options = _extract_raw_options(raw_question)

    if item_type in (QuestionItemType.SHORT_NUMERIC, QuestionItemType.ESSAY, QuestionItemType.GROUP_STEM):
        return ()

    if item_type == QuestionItemType.SINGLE_CHOICE:
        opt_perm = permutation.option_permutations.get(str(q_id))
        if opt_perm is None:
            raise ValueError(f"Missing option permutation for SINGLE_CHOICE question {q_id}")
        by_canonical_key: dict[str, Mapping[str, Any]] = {}
        for opt in raw_options:
            key = str(opt.get("option_key", "")).strip().upper()
            if key:
                by_canonical_key[key] = opt
        if len(opt_perm) != len(_DISPLAY_CHOICE_LABELS):
            raise ValueError(f"Expected 4 permuted option keys for {q_id}, got {len(opt_perm)}")

        sanitized: list[SanitizedOptionContract] = []
        for display_idx, canonical_key in enumerate(opt_perm):
            matched = by_canonical_key.get(canonical_key.upper())
            if matched is None:
                raise ValueError(f"Option key {canonical_key!r} referenced by permutation missing on question {q_id}")
            content_raw = matched.get("content_json") if "content_json" in matched else matched
            sanitized.append(
                SanitizedOptionContract(
                    display_label=_DISPLAY_CHOICE_LABELS[display_idx],
                    content_json=_sanitize_content_dict(content_raw),
                )
            )
        return tuple(sanitized)

    if item_type == QuestionItemType.CLUSTER_TRUE_FALSE:
        ordered = sorted(
            raw_options,
            key=lambda o: (
                int(o.get("sequence_order", 0)) if isinstance(o.get("sequence_order"), int) else 0,
                str(o.get("option_key", "")).casefold(),
            ),
        )
        sanitized_tf: list[SanitizedOptionContract] = []
        for idx, opt in enumerate(ordered):
            label = _DISPLAY_CLUSTER_LABELS[idx] if idx < len(_DISPLAY_CLUSTER_LABELS) else str(idx + 1)
            content_raw = opt.get("content_json") if "content_json" in opt else opt
            sanitized_tf.append(
                SanitizedOptionContract(
                    display_label=label,
                    content_json=_sanitize_content_dict(content_raw),
                )
            )
        return tuple(sanitized_tf)

    # MULTIPLE_SELECT or other choice variants: relabel deterministically without original keys/IDs.
    ordered_multi = sorted(
        raw_options,
        key=lambda o: (
            int(o.get("sequence_order", 0)) if isinstance(o.get("sequence_order"), int) else 0,
            str(o.get("option_key", "")).upper(),
        ),
    )
    return tuple(
        SanitizedOptionContract(
            display_label=(_DISPLAY_CHOICE_LABELS[idx] if idx < len(_DISPLAY_CHOICE_LABELS) else str(idx + 1)),
            content_json=_sanitize_content_dict(opt.get("content_json") if "content_json" in opt else opt),
        )
        for idx, opt in enumerate(ordered_multi)
    )


def _sanitize_numeric_policy(
    raw_question: Mapping[str, Any],
    item_type: QuestionItemType,
) -> NumericParsePolicyPublicContract | None:
    if item_type != QuestionItemType.SHORT_NUMERIC:
        return None
    raw_policy = raw_question.get("numeric_parse_policy_json")
    if not isinstance(raw_policy, Mapping):
        return NumericParsePolicyPublicContract()
    decimal_sep = str(raw_policy.get("decimal_sep", "COMMA_OR_DOT"))
    allow_thousand_sep = bool(raw_policy.get("allow_thousand_sep", False))
    raw_max_chars = raw_policy.get("max_chars", 4)
    max_chars = int(raw_max_chars) if isinstance(raw_max_chars, int) and raw_max_chars >= 1 else 4
    return NumericParsePolicyPublicContract(
        decimal_sep=decimal_sep,
        allow_thousand_sep=allow_thousand_sep,
        max_chars=max_chars,
    )


def sanitize_exam_paper(
    *,
    attempt_id: UUID,
    assessment_version_id: UUID,
    deadline_at: datetime,
    raw_questions: Sequence[Mapping[str, Any]],
    permutation: AttemptPermutation,
    stimuli_by_id: Mapping[UUID, Mapping[str, Any]] | None = None,
) -> SanitizedExamPaperContract:
    """Build an allowlisted client-safe exam paper ordered by `AttemptPermutation`.

    Raises ValueError if timestamps lack tzinfo or if any permuted question/option is missing.
    """
    if deadline_at.tzinfo is None or deadline_at.utcoffset() is None:
        raise ValueError("deadline_at must be timezone-aware")

    by_qv_id: dict[UUID, Mapping[str, Any]] = {}
    for q in raw_questions:
        raw_id = q.get("question_version_id") if "question_version_id" in q else q.get("id")
        if raw_id is None:
            raise ValueError("raw question missing question_version_id / id")
        qv_id = raw_id if isinstance(raw_id, UUID) else UUID(str(raw_id))
        by_qv_id[qv_id] = q

    sanitized_questions: list[SanitizedQuestionContract] = []
    for display_idx, qv_id in enumerate(permutation.ordered_question_ids, start=1):
        raw_q = by_qv_id.get(qv_id)
        if raw_q is None:
            raise ValueError(f"Permuted question_version_id {qv_id} missing from raw_questions")

        item_type = QuestionItemType(str(raw_q["item_type"]))
        section_code = str(raw_q["section_code"])
        raw_stim_id = raw_q.get("stimulus_id")
        stimulus_id = (
            raw_stim_id
            if isinstance(raw_stim_id, UUID)
            else (UUID(str(raw_stim_id)) if raw_stim_id is not None else None)
        )
        raw_stim = raw_q.get("stimulus_json")
        if raw_stim is None and stimulus_id is not None and stimuli_by_id is not None:
            raw_stim = stimuli_by_id.get(stimulus_id)

        sanitized_questions.append(
            SanitizedQuestionContract(
                question_version_id=qv_id,
                section_code=section_code,
                item_type=item_type,
                display_position=display_idx,
                stimulus_id=stimulus_id,
                stimulus_json=_sanitize_content_dict(raw_stim) if raw_stim is not None else None,
                stem_json=_sanitize_content_dict(raw_q.get("stem_json")),
                options=_sanitize_options(raw_q, qv_id, item_type, permutation),
                numeric_parse_policy=_sanitize_numeric_policy(raw_q, item_type),
            )
        )

    return SanitizedExamPaperContract(
        attempt_id=attempt_id,
        assessment_version_id=assessment_version_id,
        deadline_at=deadline_at,
        questions=tuple(sanitized_questions),
    )
