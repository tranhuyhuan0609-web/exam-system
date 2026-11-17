"""A02: questions out of a fetched page, HTML or JSON (pure: bytes in, raw questions out; no network, no DB).

* JSON: the Open Trivia DB shape (`{"results": [{"type": "multiple"|"boolean", "question", "correct_answer",
  "incorrect_answers"}]}`, HTML entities decoded) and a plain list of `{"question"|"stem", "options", "answer"}`;
* HTML: scripts, styles and navigation are dropped, the text is read line by line: a question starts at
  `Câu N` / `Question N` / `Bài N`, `A.`..`D.` lines (or one line holding all four) are its options, and
  `Đáp án: B` / `Chọn B` / `Answer: B` is the key the page prints, when it prints one.

The options of an Open Trivia multiple-choice question are put in a stable order (sorted), so the same page always
gives the same question and the A..D key of the correct answer.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable
from typing import Any

import lxml.html  # type: ignore[import-untyped]
from lxml import etree  # type: ignore[import-untyped]

from app.modules.questions.public import RawQuestion

MAX_QUESTIONS_PER_PAGE = 200
_DROP_TAGS = ("script", "style", "noscript", "nav", "footer", "header", "form", "iframe", "svg")
_BLOCK_TAGS = {"p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "td", "pre"}
_QUESTION_START = re.compile(r"^\s*(?:Câu|Cau|Question|Bài|Bai)\s*(\d{1,3})\s*[.:)]?\s*(.*)$", re.IGNORECASE)
_OPTION = re.compile(r"^\s*([A-D])\s*[.)]\s*(.+)$")
_INLINE_OPTIONS = re.compile(r"(?:^|\s)([A-D])\s*[.)]\s*(.+?)(?=\s+[A-D]\s*[.)]\s|$)")
_ANSWER = re.compile(
    r"^\s*(?:Đáp\s*án|Dap\s*an|Chọn|Chon|Answer|Lời\s*giải|Loi\s*giai)\s*(?:đúng)?\s*[:.]?\s*([A-D])\b", re.IGNORECASE
)


class ExtractionError(ValueError):
    """The page is not readable as HTML or JSON."""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


# --- JSON ------------------------------------------------------------------------------------------------------------


def _trivia(item: dict[str, Any], index: int) -> RawQuestion | None:
    question = _clean(str(item.get("question") or ""))
    correct = _clean(str(item.get("correct_answer") or ""))
    wrong = [_clean(str(w)) for w in item.get("incorrect_answers") or [] if str(w).strip()]
    if not question or not correct:
        return None
    if item.get("type") == "boolean":
        options = ["True", "False"]
    else:
        options = sorted({correct, *wrong})
    if correct not in options:
        return None
    letter = "ABCDEFGH"[options.index(correct)]
    return RawQuestion("SINGLE_CHOICE", question, tuple(options), letter, 1.0, f"result {index + 1}")


def _plain(item: dict[str, Any], index: int) -> RawQuestion | None:
    stem = _clean(str(item.get("question") or item.get("stem") or item.get("stem_text") or ""))
    if not stem:
        return None
    options = [_clean(str(o)) for o in item.get("options") or [] if str(o).strip()]
    answer = item.get("answer", item.get("correct_option"))
    item_type = str(item.get("item_type") or ("SINGLE_CHOICE" if options else "SHORT_NUMERIC")).upper()
    return RawQuestion(item_type, stem, tuple(options), answer, float(item.get("points") or 1.0), f"item {index + 1}")


def extract_questions_from_json(payload: Any) -> list[RawQuestion]:
    items: Iterable[Any]
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        items, reader = payload["results"], _trivia
    elif isinstance(payload, dict) and isinstance(payload.get("questions"), list):
        items, reader = payload["questions"], _plain
    elif isinstance(payload, list):
        items, reader = payload, _plain
    else:
        return []
    out: list[RawQuestion] = []
    for index, item in enumerate(items):
        if isinstance(item, dict):
            found = reader(item, index)
            if found is not None:
                out.append(found)
        if len(out) >= MAX_QUESTIONS_PER_PAGE:
            break
    return out


# --- HTML ------------------------------------------------------------------------------------------------------------


def _text_lines(markup: str) -> list[str]:
    try:
        root = lxml.html.fromstring(markup)
    except (etree.ParserError, ValueError) as exc:
        raise ExtractionError(f"not readable as HTML: {exc}") from exc
    for tag in _DROP_TAGS:
        for node in root.iter(tag):
            node.drop_tree()
    chunks: list[str] = []
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        if node.tag in _BLOCK_TAGS:
            chunks.append("\n")
        if node.text:
            chunks.append(node.text)
        if node.tag in _BLOCK_TAGS:
            chunks.append("\n")
        if node.tail:
            chunks.append(node.tail)
    return [line for line in (_clean(x) for x in "".join(chunks).split("\n")) if line]


def extract_questions_from_html(markup: str) -> list[RawQuestion]:
    out: list[RawQuestion] = []
    current: dict[str, Any] | None = None

    def close() -> None:
        if current is None:
            return
        stem = " ".join(current["stem"]).strip()
        options = [current["options"][k] for k in "ABCD" if k in current["options"]]
        if stem:
            item_type = "SINGLE_CHOICE" if len(options) >= 2 else "ESSAY"
            out.append(RawQuestion(item_type, stem, tuple(options), current["answer"], 1.0, current["label"]))

    for line in _text_lines(markup):
        start = _QUESTION_START.match(line)
        if start is not None:
            close()
            current = {"label": f"Câu {start.group(1)}", "stem": [start.group(2)], "options": {}, "answer": None}
            continue
        if current is None:
            continue
        answer = _ANSWER.match(line)
        if answer is not None:
            current["answer"] = answer.group(1).upper()
            continue
        option = _OPTION.match(line)
        if option is not None:
            inline = _INLINE_OPTIONS.findall(line)
            pairs = inline if len(inline) >= 2 else [(option.group(1), option.group(2))]
            for key, text in pairs:
                current["options"].setdefault(key.upper(), text.strip())
            continue
        if not current["options"]:
            current["stem"].append(line)
        if len(out) >= MAX_QUESTIONS_PER_PAGE:
            break
    close()
    return out[:MAX_QUESTIONS_PER_PAGE]


def extract_questions(body: bytes, content_type: str | None) -> list[RawQuestion]:
    """Pick the reader from the content type (JSON first when the body parses as JSON)."""
    kind = (content_type or "").split(";", 1)[0].strip().lower()
    text = body.decode("utf-8", errors="replace")
    if kind.endswith("json") or text.lstrip().startswith(("{", "[")):
        try:
            return extract_questions_from_json(json.loads(text))
        except json.JSONDecodeError:
            if kind.endswith("json"):
                raise ExtractionError("the page says JSON but does not parse") from None
    return extract_questions_from_html(text)
