"""Open Trivia Database (opentdb.com) JSON parser.

Parses questions from the Open Trivia DB API (response_code, results with multiple-choice and boolean questions).
"""

import hashlib
import html as html_lib
import json
import logging
from typing import Any

from app.pipeline.domain.models import ParsedPage, ParsedQuestion
from app.pipeline.parsers.base import register

log = logging.getLogger(__name__)

DIFFICULTY_MAP = {
    "easy": "RECOGNITION",
    "medium": "UNDERSTANDING",
    "hard": "APPLICATION",
}


@register("opentrivia_quiz")
def parse(html: str, url: str) -> ParsedPage:
    """Parse JSON body returned from Open Trivia Database API."""
    try:
        data: dict[str, Any] = json.loads(html)
    except Exception as exc:
        return ParsedPage(title="Open Trivia API", questions=[], issues=[f"Không thể đọc JSON: {exc}"])

    results = data.get("results", [])
    if not isinstance(results, list):
        return ParsedPage(title="Open Trivia API", questions=[], issues=["Dữ liệu JSON không chứa danh sách results."])

    questions: list[ParsedQuestion] = []
    category_title = ""

    for index, item in enumerate(results, start=1):  # noqa: B007
        if not isinstance(item, dict):
            continue

        raw_question = item.get("question", "")
        if not raw_question:
            continue

        question_text = html_lib.unescape(raw_question).strip()
        category = html_lib.unescape(item.get("category", "General")).strip()
        if not category_title:
            category_title = category

        difficulty_raw = str(item.get("difficulty", "medium")).lower()
        difficulty = DIFFICULTY_MAP.get(difficulty_raw, "UNDERSTANDING")

        correct_ans = html_lib.unescape(str(item.get("correct_answer", ""))).strip()
        incorrect_answers = [html_lib.unescape(str(x)).strip() for x in item.get("incorrect_answers", [])]

        q_type_raw = item.get("type", "multiple")

        if q_type_raw == "boolean":
            # True / False
            choices = [
                {"id": "a", "content": "True", "is_correct": correct_ans.lower() == "true"},
                {"id": "b", "content": "False", "is_correct": correct_ans.lower() == "false"},
            ]
            q_type = "MULTIPLE_CHOICE"
        else:
            # Multiple Choice: 4 options
            all_opts = [correct_ans, *incorrect_answers]
            # Deterministic pseudo-random shuffle based on question hash so order is stable
            def opt_rank(opt: str) -> str:
                return hashlib.sha256(f"{question_text}:{opt}".encode()).hexdigest()  # noqa: B023

            sorted_opts = sorted(all_opts, key=opt_rank)
            letters = "abcdefgh"
            choices = []
            for i, opt in enumerate(sorted_opts):
                letter = letters[i] if i < len(letters) else str(i)
                choices.append({
                    "id": letter,
                    "content": opt,
                    "is_correct": opt == correct_ans,
                })
            q_type = "MULTIPLE_CHOICE"

        stable_id = f"opentdb-{hashlib.sha1(f'{category}:{question_text}'.encode()).hexdigest()[:14]}"
        explanation = f"Đáp án chính xác: {correct_ans}.\nLĩnh vực: {category}.\nMức độ nhận thức: {difficulty}."

        questions.append(
            ParsedQuestion(
                external_id=stable_id,
                question_type=q_type,
                content=question_text,
                choices=choices,
                explanation=explanation,
                difficulty=difficulty,
            )
        )

    title = f"Open Trivia Quiz – {category_title}" if category_title else "Open Trivia Quiz"
    return ParsedPage(title=title, questions=questions)
