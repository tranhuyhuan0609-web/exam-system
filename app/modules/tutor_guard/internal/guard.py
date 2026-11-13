"""E02 AI Tutor pure guards: Exam Lock, RAG visibility filter, prompt injection & HINT_MODE answer shield."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TutorMode(StrEnum):
    """Exact DDL CHECK values for `tutor_sessions.tutor_mode`."""

    HINT_MODE = "HINT_MODE"
    EXPLAIN_MODE = "EXPLAIN_MODE"
    CHALLENGE_MODE = "CHALLENGE_MODE"


class TutorSenderRole(StrEnum):
    """Exact DDL CHECK values for `tutor_messages.sender_role`."""

    USER = "USER"
    ASSISTANT = "ASSISTANT"
    SYSTEM_GUARD = "SYSTEM_GUARD"


class TutorGuardError(PermissionError):
    """Raised when E02 blocks a tutor session due to exam lock or policy."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class LearnerAttemptSnapshot:
    attempt_id: str
    status: str  # e.g. IN_PROGRESS, SUBMITTED, GRADED


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    document_id: str
    tenant_id: str
    publication_status: str
    visibility_scope: str
    shared_tenant_ids: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class PromptCheckResult:
    allowed: bool
    flagged_injection: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class OutputFilterResult:
    safe_message: str
    blocked_leakage: bool
    sender_role: TutorSenderRole
    reason_code: str


def _fold_ascii_lower(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    out: list[str] = []
    for ch in text:
        if unicodedata.category(ch).startswith("L"):
            out.append("".join(c for c in unicodedata.normalize("NFD", ch) if unicodedata.category(c) != "Mn"))
        elif unicodedata.category(ch) != "Mn":
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out).lower()).strip()


_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bignore\s+(all\s+)?(previous|prior|above)\s+instructions\b"),
    re.compile(r"\breveal\s+(the\s+)?(system\s+prompt|answer(\s+key)?|solution)\b"),
    re.compile(r"\bbo qua\s+(chi thi|huong dan|quy tac)\b"),
    re.compile(r"\b(cho\s+toi|noi\s+thang|in\s+ra|xuat\s+ra)\s+(dap\s+an(\s+dung)?|ket\s+qua\s+cuoi)\b"),
    re.compile(r"\bchi\s+can\s+(ghi|tra\s+loi|chon)\s+(a|b|c|d|dap\s+an)\b"),
)

_EXPLICIT_OPTION_REVEAL_RE = re.compile(
    r"\b(?:dap\s+an(?:\s+dung)?\s*(?:la|:)|chon(?:\s+phuong\s+an)?|correct\s+(?:answer|option)\s*(?:is|:))\s*([a-d])\b"
)

_EXPLICIT_TF_REVEAL_RE = re.compile(
    r"\b(?:a|b|c|d)\s*(?:la|:|-)\s*(?:dung|sai|true|false)\b"
)


def assert_exam_not_active(attempts: Sequence[LearnerAttemptSnapshot]) -> None:
    """Block 100% of AI Tutor calls while learner has an active `IN_PROGRESS` exam attempt."""
    for att in attempts:
        if att.status.upper() == "IN_PROGRESS":
            raise TutorGuardError(
                "EXAM_SESSION_ACTIVE",
                f"AI Tutor disabled while exam attempt {att.attempt_id} is IN_PROGRESS",
            )


def assert_tutor_mode_allowed(
    mode: TutorMode,
    *,
    active_attempts: Sequence[LearnerAttemptSnapshot],
    target_attempt_submitted: bool = True,
) -> None:
    """Enforce Exam Lock across all modes, plus submission prerequisite for EXPLAIN_MODE."""
    assert_exam_not_active(active_attempts)
    if mode == TutorMode.EXPLAIN_MODE and not target_attempt_submitted:
        raise TutorGuardError(
            "EXPLAIN_REQUIRES_SUBMISSION",
            "EXPLAIN_MODE is only available after the learner submits the attempt",
        )


def filter_grounded_retrieval_candidates(
    candidates: Sequence[RetrievalCandidate],
    *,
    learner_tenant_id: str,
) -> tuple[RetrievalCandidate, ...]:
    """Allow ONLY `PUBLISHED` materials visible to `learner_tenant_id` in RAG context."""
    approved: list[RetrievalCandidate] = []
    for item in candidates:
        if item.publication_status != "PUBLISHED":
            continue
        if item.tenant_id == learner_tenant_id:
            approved.append(item)
        elif item.visibility_scope == "SYSTEM_PUBLIC":
            approved.append(item)
        elif item.visibility_scope == "SHARED_PARTNER" and learner_tenant_id in item.shared_tenant_ids:
            approved.append(item)
    return tuple(approved)


def screen_user_prompt(prompt: str, *, mode: TutorMode) -> PromptCheckResult:
    """Detect empty prompts or adversarial answer-extraction attempts in HINT_MODE."""
    if not prompt or not prompt.strip():
        return PromptCheckResult(allowed=False, flagged_injection=False, reason_code="EMPTY_PROMPT")
    if mode != TutorMode.HINT_MODE:
        return PromptCheckResult(allowed=True, flagged_injection=False, reason_code="OK")

    folded = _fold_ascii_lower(prompt)
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(folded):
            return PromptCheckResult(
                allowed=False,
                flagged_injection=True,
                reason_code="PROMPT_INJECTION_ANSWER_REQUEST",
            )
    return PromptCheckResult(allowed=True, flagged_injection=False, reason_code="OK")


def filter_tutor_output(
    candidate_reply: str,
    *,
    mode: TutorMode,
    answer_schema: Mapping[str, Any] | None = None,
    safe_fallback_hint: str = (
        "Chế độ Gợi ý chỉ hướng dẫn phương pháp và công thức bước tiếp theo. "
        "Hãy thử biến đổi biểu thức từ dữ kiện đề bài trước nhé!"
    ),
) -> OutputFilterResult:
    """Post-filter LLM output in HINT_MODE to guarantee 0% direct answer leakage."""
    if mode != TutorMode.HINT_MODE:
        return OutputFilterResult(
            safe_message=candidate_reply,
            blocked_leakage=False,
            sender_role=TutorSenderRole.ASSISTANT,
            reason_code="OK",
        )

    folded = _fold_ascii_lower(candidate_reply)

    if _EXPLICIT_OPTION_REVEAL_RE.search(folded) or _EXPLICIT_TF_REVEAL_RE.search(folded):
        return OutputFilterResult(
            safe_message=safe_fallback_hint,
            blocked_leakage=True,
            sender_role=TutorSenderRole.SYSTEM_GUARD,
            reason_code="HINT_ANSWER_KEY_LEAK_BLOCKED",
        )

    if answer_schema:
        # Check exact numeric conclusion leak (e.g. "kết quả bằng <value>" or "= <value>")
        numeric_val = answer_schema.get("value")
        if numeric_val is not None:
            num_norm = _fold_ascii_lower(str(numeric_val)).replace(",", ".")
            folded_dot = folded.replace(",", ".")
            if num_norm and re.search(
                rf"\b(?:ket qua|dap so|gia tri|result|answer)\s*(?:la|bang|is|=)\s*{re.escape(num_norm)}\b",
                folded_dot,
            ):
                return OutputFilterResult(
                    safe_message=safe_fallback_hint,
                    blocked_leakage=True,
                    sender_role=TutorSenderRole.SYSTEM_GUARD,
                    reason_code="HINT_NUMERIC_LEAK_BLOCKED",
                )

        # Check option letter explicitly framed as answer even with extra phrasing
        correct_opt = answer_schema.get("correct_option")
        if isinstance(correct_opt, str) and correct_opt.strip():
            opt = correct_opt.strip().lower()
            if re.search(rf"\b(?:chon|dap an|answer|option)\s+{re.escape(opt)}\b", folded):
                return OutputFilterResult(
                    safe_message=safe_fallback_hint,
                    blocked_leakage=True,
                    sender_role=TutorSenderRole.SYSTEM_GUARD,
                    reason_code="HINT_OPTION_LEAK_BLOCKED",
                )

    return OutputFilterResult(
        safe_message=candidate_reply,
        blocked_leakage=False,
        sender_role=TutorSenderRole.ASSISTANT,
        reason_code="OK",
    )
