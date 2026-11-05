"""Rules shared by every way a question enters the bank (form, file import, AI) and by grading.

Three question types, following the 2025 Vietnamese THPT exam format:

    MULTIPLE_CHOICE  choices = [{id, content, is_correct}], exactly one correct
    TRUE_FALSE       choices = 4 statements [{id, content, is_correct}], is_correct = the statement is true
    SHORT_ANSWER     no choices; accepted_answers = ["30", "-1,5"] (numbers or very short text)
    ESSAY            no choices; explanation = model solution; graded by AI (points, default 1)
"""

import re
import secrets
from decimal import Decimal
from typing import Any

from app.models import QUESTION_MAX_CHOICES, QUESTION_MIN_CHOICES, QuestionType

TRUE_FALSE_STATEMENTS = 4
SHORT_ANSWER_MAX_LEN = 20
ESSAY_ANSWER_MAX_LEN = 5000

TYPE_LABELS = {
    QuestionType.MULTIPLE_CHOICE: "Trắc nghiệm",
    QuestionType.TRUE_FALSE: "Đúng/Sai",
    QuestionType.SHORT_ANSWER: "Trả lời ngắn",
    QuestionType.ESSAY: "Tự luận",
}

# THPT 2025 weights (physics). Scores are normalised to a 10-point scale, so only the ratios matter.
MULTIPLE_CHOICE_POINTS = Decimal("0.25")
SHORT_ANSWER_POINTS = Decimal("0.25")
TRUE_FALSE_POINTS = {0: Decimal("0"), 1: Decimal("0.1"), 2: Decimal("0.25"), 3: Decimal("0.5"), 4: Decimal("1")}
ESSAY_POINTS = Decimal("1")
MAX_ESSAY_POINTS = Decimal("10")


def max_points(question_type: str, points: Decimal | str | None = None) -> Decimal:
    if question_type == QuestionType.ESSAY:
        return Decimal(str(points)) if points not in (None, "") else ESSAY_POINTS
    if question_type == QuestionType.TRUE_FALSE:
        return TRUE_FALSE_POINTS[TRUE_FALSE_STATEMENTS]
    if question_type == QuestionType.SHORT_ANSWER:
        return SHORT_ANSWER_POINTS
    return MULTIPLE_CHOICE_POINTS


def true_false_points(correct_statements: int, total: int = TRUE_FALSE_STATEMENTS) -> Decimal:
    if total == TRUE_FALSE_STATEMENTS:
        return TRUE_FALSE_POINTS[correct_statements]
    return TRUE_FALSE_POINTS[TRUE_FALSE_STATEMENTS] * correct_statements / max(total, 1)


# ---------------------------------------------------------------- short answers

_NUMBER = re.compile(r"[-+−]?\s*\d+(?:[.,]\d+)?")


def normalize_short_answer(value: str) -> str:
    """Canonical form used for comparison: no spaces, Vietnamese decimal comma, unicode minus → '-'."""
    text = re.sub(r"\s+", "", str(value)).replace("−", "-").replace(".", ",").lower()
    if re.fullmatch(r"[-+]?\d+(,\d+)?", text):
        sign = "-" if text.startswith("-") else ""
        digits = text.lstrip("+-")
        whole, _, frac = digits.partition(",")
        whole = whole.lstrip("0") or "0"
        frac = frac.rstrip("0")
        text = sign + whole + ("," + frac if frac else "")
        if text == "-0":
            text = "0"
    return text


def extract_number(text: str) -> str | None:
    """'30 cm' → '30', '≈ 1,25.10^5' → '1,25' (first number), used for AI answers that include units."""
    match = _NUMBER.search(text or "")
    return normalize_short_answer(match.group()) if match else None


def short_answer_matches(given: Any, accepted: list[str]) -> bool:
    if not isinstance(given, str) or not given.strip():
        return False
    candidate = normalize_short_answer(given)
    return any(candidate == normalize_short_answer(a) for a in accepted)


def split_accepted(raw: str) -> list[str]:
    """Admin input "30; 30,0" → ["30", "30,0"]."""
    return [part.strip() for part in re.split(r"[;|\n]", raw or "") if part.strip()]


# ---------------------------------------------------------------- validation & construction


def validate(
    question_type: str,
    content: str,
    choices: list[str],
    correct: int | None = None,
    truths: list[bool | None] | None = None,
    accepted: list[str] | None = None,
) -> list[str]:
    """User-facing problems with a question (empty list = valid)."""
    errors = []
    if not (content or "").strip():
        errors.append("Thiếu nội dung câu hỏi.")
    if question_type == QuestionType.MULTIPLE_CHOICE:
        if not QUESTION_MIN_CHOICES <= len(choices) <= QUESTION_MAX_CHOICES:
            errors.append(
                f"Cần từ {QUESTION_MIN_CHOICES} đến {QUESTION_MAX_CHOICES} lựa chọn (đang có {len(choices)})."
            )
        if any(not c.strip() for c in choices):
            errors.append("Có lựa chọn bị trống.")
        if correct is None:
            errors.append("Chưa có đáp án đúng (dòng “Đáp án: B” hoặc dấu * trước lựa chọn đúng).")
        elif not 0 <= correct < len(choices):
            errors.append("Đáp án đúng không nằm trong các lựa chọn.")
    elif question_type == QuestionType.TRUE_FALSE:
        if len(choices) != TRUE_FALSE_STATEMENTS:
            errors.append(f"Câu Đúng/Sai cần đúng {TRUE_FALSE_STATEMENTS} ý a, b, c, d (đang có {len(choices)}).")
        if any(not c.strip() for c in choices):
            errors.append("Có ý bị trống.")
        truths = truths or []
        if len(truths) != len(choices) or any(t is None for t in truths):
            errors.append("Hãy đánh dấu Đúng hoặc Sai cho từng ý (VD: “Đáp án: Đ S Đ S”).")
    elif question_type == QuestionType.ESSAY:
        pass  # only the text is required; the model solution (explanation) is optional
    elif question_type == QuestionType.SHORT_ANSWER:
        accepted = [a for a in (accepted or []) if a.strip()]
        if not accepted:
            errors.append("Câu trả lời ngắn cần đáp án (VD: “Đáp án: 30”).")
        if any(len(a) > SHORT_ANSWER_MAX_LEN for a in accepted):
            errors.append(f"Đáp án trả lời ngắn tối đa {SHORT_ANSWER_MAX_LEN} ký tự.")
    else:
        errors.append("Dạng câu hỏi không được hỗ trợ.")
    return errors


def build_choices(
    question_type: str,
    choices: list[str],
    correct: int | None = None,
    truths: list[bool | None] | None = None,
    ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Stored JSONB choices. Existing ids are kept so recorded answers stay valid after edits."""
    if question_type in (QuestionType.SHORT_ANSWER, QuestionType.ESSAY):
        return []
    ids = ids or []
    out, seen = [], set()
    for i, text in enumerate(choices):
        choice_id = ids[i] if i < len(ids) and re.fullmatch(r"[a-z0-9]{1,16}", ids[i] or "") else ""
        if not choice_id or choice_id in seen:
            choice_id = secrets.token_hex(4)
        seen.add(choice_id)
        if question_type == QuestionType.TRUE_FALSE:
            is_correct = bool(truths[i]) if truths and i < len(truths) else False
        else:
            is_correct = i == correct
        out.append({"id": choice_id, "content": text.strip(), "is_correct": is_correct})
    return out


_TRUE_TOKENS = {"đ", "d", "đúng", "dung", "t", "true", "1", "x"}
_FALSE_TOKENS = {"s", "sai", "f", "false", "0"}


def parse_truths(text: str) -> list[bool | None]:
    """'Đ S Đ S', 'ĐSĐS', 'a) Đúng b) Sai ...', 'a-Đ, b-S' → [True, False, True, False]."""
    cleaned = re.sub(r"(?i)\b[a-f]\s*[).:\-]\s*", " ", text)
    tokens = [t for t in re.split(r"[\s,;/|]+", cleaned.strip().lower()) if t]
    if len(tokens) == 1 and all(ch in "đdstf" for ch in tokens[0]):  # compact "ĐSĐS"
        tokens = list(tokens[0])
    out: list[bool | None] = []
    for token in tokens:
        token = token.strip(".")
        if token in _TRUE_TOKENS:
            out.append(True)
        elif token in _FALSE_TOKENS:
            out.append(False)
        else:
            out.append(None)
    return out


def parse_essay_points(raw: str | None) -> tuple[Decimal | None, str | None]:
    """Admin input for essay points: blank = default. Returns (points, error)."""
    text = (raw or "").strip().replace(",", ".")
    if not text:
        return None, None
    try:
        value = Decimal(text)
    except ArithmeticError:
        return None, "Điểm câu tự luận phải là một số."
    if not Decimal("0.25") <= value <= MAX_ESSAY_POINTS:
        return None, f"Điểm câu tự luận phải từ 0,25 đến {MAX_ESSAY_POINTS}."
    return value.quantize(Decimal("0.01")), None
