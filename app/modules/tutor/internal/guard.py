"""E02 AI Tutor pure guards: Exam Lock, RAG visibility filter, prompt injection & HINT_MODE answer shield."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
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
    status: str  # exact DDL `attempts.status`: IN_PROGRESS, SUBMITTED, AUTO_SUBMITTED, GRADED, VOIDED


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    document_id: str
    tenant_id: str
    publication_status: str
    visibility_scope: str
    shared_tenant_ids: frozenset[str] = field(default_factory=frozenset)
    is_immutable: bool = True


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


# Exact DDL `attempts.status` values; only IN_PROGRESS keeps the exam lock on, anything else is "unknown".
KNOWN_ATTEMPT_STATUSES: frozenset[str] = frozenset({"IN_PROGRESS", "SUBMITTED", "AUTO_SUBMITTED", "GRADED", "VOIDED"})
_UNFILTERED_MODES: frozenset[TutorMode] = frozenset({TutorMode.EXPLAIN_MODE, TutorMode.CHALLENGE_MODE})


def _fold_ascii(text: str) -> str:
    """Strip Vietnamese diacritics and invisible format characters but keep the original letter case."""
    text = unicodedata.normalize("NFC", text).replace("đ", "d").replace("Đ", "D")
    out: list[str] = []
    for ch in text:
        category = unicodedata.category(ch)
        if category.startswith("L"):
            out.append("".join(c for c in unicodedata.normalize("NFD", ch) if unicodedata.category(c) != "Mn"))
        elif category not in {"Mn", "Cf"}:
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _fold_ascii_lower(text: str) -> str:
    return _fold_ascii(text).lower()


_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bignore\s+(all\s+)?(previous|prior|above)\s+instructions\b"),
    re.compile(r"\breveal\s+(the\s+)?(system\s+prompt|answer(\s+key)?|solution)\b"),
    re.compile(r"\bbo qua\s+(chi thi|huong dan|quy tac)\b"),
    re.compile(r"\b(cho\s+toi|noi\s+thang|in\s+ra|xuat\s+ra)\s+(dap\s+an(\s+dung)?|ket\s+qua\s+cuoi)\b"),
    re.compile(r"\bchi\s+can\s+(ghi|tra\s+loi|chon)\s+(a|b|c|d|dap\s+an)\b"),
)

# These run on the case-preserved, diacritics-free text. Strong markers accept either letter case; weak
# markers ("Vậy C", "Câu C") only fire for an uppercase option letter so "vậy a là hằng số" stays legal.
_STRONG_OPTION_RE = re.compile(
    r"(?i:\b(?:dap\s+an(?:\s+dung)?|chon(?:\s+(?:cau|phuong\s+an|dap\s+an))?|"
    r"(?:the\s+)?(?:correct\s+)?(?:answer|option)|nen\s+chon|ket\s+luan)\s*(?:la|is|:|=|-)?\s*"
    r"(?:cau\s+|phuong\s+an\s+|option\s+|dap\s+an\s+)?)([a-dA-D])\b"
)
_WEAK_OPTION_RE = re.compile(r"(?i:\b(?:vay|suy\s+ra|do\s+do|cau|phuong\s+an)\s*[:,]?\s*)([A-D])\b")
_LETTER_LIST_RE = re.compile(r"\b([A-D](?:\s*(?:,|;|/|&|va|and)\s*[A-D])+)\b")
_TF_SINGLE_RE = re.compile(
    r"(?i:\b(?:y\s+|menh\s+de\s+|phat\s+bieu\s+|cau\s+)?([a-d])\s*(?:\)|:|-|\.|\s+la\s+)\s*(dung|sai|true|false)\b)"
)
_TF_PREFIXED_RE = re.compile(r"(?i:\b(?:y|menh\s+de|phat\s+bieu|cau)\s+([a-d])\s+(dung|sai|true|false)\b)")
_TF_LIST_RE = re.compile(
    r"\b([a-dA-D](?:\s*(?:,|;|/|&|va|and)\s*[a-dA-D])+)\s*(?:\)|:|-)?\s*(?:deu\s+)?(?:la\s+)?"
    r"(?i:(dung|sai|true|false))\b"
)
_ALL_TF_RE = re.compile(r"\btat\s+ca\b.{0,40}?\b(?:deu\s+)?(dung|sai)\b", re.IGNORECASE)
_NUMBER_AFTER_MARKER_RE = re.compile(
    r"\b(?:ket\s+qua|dap\s+so|gia\s+tri|result|answer)(?:\s+cuoi\s+cung)?\s*(?:la|bang|is|=|:)\s*"
    r"(-?\d+(?:[.,]\d+)?)"
)
_BOXED_RE = re.compile(r"\\boxed\s*\{")


def _as_decimal(raw: object) -> Decimal | None:
    try:
        value = Decimal(str(raw).strip().replace(",", "."))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def assert_exam_not_active(attempts: Sequence[LearnerAttemptSnapshot]) -> None:
    """Block 100% of AI Tutor calls while any attempt is `IN_PROGRESS` or has a status we do not recognise."""
    for att in attempts:
        status = att.status
        if isinstance(status, str) and status.strip().upper() == "IN_PROGRESS":
            raise TutorGuardError(
                "EXAM_SESSION_ACTIVE",
                f"AI Tutor disabled while exam attempt {att.attempt_id} is IN_PROGRESS",
            )
        if not isinstance(status, str) or status not in KNOWN_ATTEMPT_STATUSES:
            raise TutorGuardError(
                "EXAM_STATUS_UNKNOWN",
                f"AI Tutor locked: attempt {att.attempt_id} has an unrecognised status",
            )


def assert_tutor_mode_allowed(
    mode: TutorMode,
    *,
    active_attempts: Sequence[LearnerAttemptSnapshot],
    target_attempt_submitted: bool,
) -> None:
    """Enforce Exam Lock across all modes, plus submission prerequisite for EXPLAIN_MODE."""
    if mode not in set(TutorMode):
        raise TutorGuardError("UNKNOWN_TUTOR_MODE", "Unknown tutor mode")
    assert_exam_not_active(active_attempts)
    if mode == TutorMode.EXPLAIN_MODE and target_attempt_submitted is not True:
        raise TutorGuardError(
            "EXPLAIN_REQUIRES_SUBMISSION",
            "EXPLAIN_MODE is only available after the learner submits the attempt",
        )


def filter_grounded_retrieval_candidates(
    candidates: Sequence[RetrievalCandidate],
    *,
    learner_tenant_id: str,
) -> tuple[RetrievalCandidate, ...]:
    """Allow ONLY immutable (`is_immutable=True`, H-01) `PUBLISHED` materials visible to `learner_tenant_id`."""
    if not isinstance(learner_tenant_id, str) or not learner_tenant_id.strip():
        return ()
    approved: list[RetrievalCandidate] = []
    for item in candidates:
        if not isinstance(item.tenant_id, str) or not item.tenant_id.strip():
            continue
        if item.is_immutable is not True:
            continue
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
    """Detect empty prompts or adversarial answer-extraction attempts in HINT_MODE (and unknown modes)."""
    if not isinstance(prompt, str) or not prompt.strip():
        return PromptCheckResult(allowed=False, flagged_injection=False, reason_code="EMPTY_PROMPT")
    if mode in _UNFILTERED_MODES:
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
    """Post-filter LLM output in HINT_MODE (and any unrecognised mode) to guarantee 0% direct answer leakage."""
    if mode in _UNFILTERED_MODES:
        return OutputFilterResult(
            safe_message=candidate_reply,
            blocked_leakage=False,
            sender_role=TutorSenderRole.ASSISTANT,
            reason_code="OK",
        )

    def _blocked(reason: str) -> OutputFilterResult:
        return OutputFilterResult(
            safe_message=safe_fallback_hint,
            blocked_leakage=True,
            sender_role=TutorSenderRole.SYSTEM_GUARD,
            reason_code=reason,
        )

    if not isinstance(candidate_reply, str):
        return _blocked("HINT_INVALID_REPLY_BLOCKED")

    cased = _fold_ascii(candidate_reply)
    folded = cased.lower()

    if _STRONG_OPTION_RE.search(cased) or _WEAK_OPTION_RE.search(cased) or _BOXED_RE.search(cased):
        return _blocked("HINT_ANSWER_KEY_LEAK_BLOCKED")
    if _TF_SINGLE_RE.search(cased) or _TF_PREFIXED_RE.search(cased) or _TF_LIST_RE.search(cased):
        return _blocked("HINT_ANSWER_KEY_LEAK_BLOCKED")

    schema: Mapping[str, Any] = answer_schema or {}

    numeric_val = schema.get("value")
    if numeric_val is not None:
        expected = _as_decimal(numeric_val)
        for match in _NUMBER_AFTER_MARKER_RE.finditer(folded):
            said = _as_decimal(match.group(1))
            if expected is not None and said is not None and said == expected:
                return _blocked("HINT_NUMERIC_LEAK_BLOCKED")
            if expected is None and _fold_ascii_lower(str(numeric_val)) == match.group(1):
                return _blocked("HINT_NUMERIC_LEAK_BLOCKED")

    correct_opt = schema.get("correct_option")
    if isinstance(correct_opt, str) and correct_opt.strip():
        label = re.escape(_fold_ascii(correct_opt).strip())
        if re.fullmatch(rf"\W*{label}\W*", cased, re.IGNORECASE):
            return _blocked("HINT_OPTION_LEAK_BLOCKED")
        if re.search(rf"(?i:\b(?:chon|dap an|answer|option|cau|phuong an)\s*[:=]?\s*){label}\b", cased):
            return _blocked("HINT_OPTION_LEAK_BLOCKED")

    correct_options = schema.get("correct_options")
    if isinstance(correct_options, (list, tuple)) and correct_options:
        wanted = {_fold_ascii(str(c)).strip().upper() for c in correct_options}
        for match in _LETTER_LIST_RE.finditer(cased):
            if set(re.findall(r"[A-D]", match.group(1))) == wanted:
                return _blocked("HINT_MULTI_OPTION_LEAK_BLOCKED")
        if len(wanted) == 1 and re.fullmatch(rf"\W*{re.escape(next(iter(wanted)))}\W*", cased, re.IGNORECASE):
            return _blocked("HINT_MULTI_OPTION_LEAK_BLOCKED")

    statements = schema.get("statements")
    if isinstance(statements, Mapping) and statements:
        values = {v for v in statements.values() if isinstance(v, bool)}
        for match in _ALL_TF_RE.finditer(folded):
            if values == {match.group(1) == "dung"}:
                return _blocked("HINT_CLUSTER_LEAK_BLOCKED")

    return OutputFilterResult(
        safe_message=candidate_reply,
        blocked_leakage=False,
        sender_role=TutorSenderRole.ASSISTANT,
        reason_code="OK",
    )
