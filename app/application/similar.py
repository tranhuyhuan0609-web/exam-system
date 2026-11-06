"""Similar papers: from the questions read in a teacher's paper, AI writes new ones of the same kind, knowledge and
level (other numbers, other contexts), with answers and worked solutions; then solves each new question again,
without its answer, and marks those where the two answers differ. The teacher reviews them before giving them.

Questions with a figure cannot be rewritten without a new figure: they are kept as in the paper.
"""

import json
import logging
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field

from app import question_rules as rules
from app.ai.schemas import ExtractedChoice, ExtractedQuestion
from app.domain.latex import repair_latex
from app.question_rules import extract_number

log = logging.getLogger(__name__)
GROUP = 3  # paper questions per call (each becomes `versions` new ones)
MAX_VERSIONS = 3
Progress = Callable[[str, int, int], Awaitable[None]]


class _Variant(BaseModel):
    ref: int = Field(description="Số thứ tự câu gốc (ref)")
    version: int = Field(description="Đề tương tự số mấy (1, 2, …)")
    content: str
    choices: list[str] = Field(default_factory=list, description="4 phương án (trắc nghiệm) hoặc 4 ý (đúng sai)")
    correct: int | None = Field(None, description="Trắc nghiệm: chỉ số phương án đúng, 0 = A")
    truths: list[bool] = Field(default_factory=list, description="Đúng sai: mỗi ý đúng (true) hay sai (false)")
    answer: str = Field("", description="Trả lời ngắn: đáp số")
    explanation: str = Field("", description="Lời giải chi tiết (tự luận: đáp án mẫu)")


class _Variants(BaseModel):
    items: list[_Variant]


PROMPT = """Bạn là giáo viên ra đề giỏi. Với MỖI câu hỏi gốc dưới đây (JSON), hãy viết {versions} câu hỏi TƯƠNG TỰ
(version = 1..{versions}) để làm {versions} đề tương tự:
- Cùng dạng câu, cùng kiến thức – kĩ năng, cùng mức độ khó; ĐỔI số liệu và/hoặc ngữ cảnh, không chép lại câu gốc.
- Số liệu mới phải cho đáp số hợp lí (đẹp như câu gốc) và đúng thực tế.
- MULTIPLE_CHOICE: đúng 4 phương án, đúng 1 phương án đúng (correct = 0..3), phương án nhiễu hợp lí
  (sai lầm thường gặp); không đặt phương án đúng cùng một vị trí ở mọi câu.
- TRUE_FALSE: 4 ý a), b), c), d) trong choices, truths cho từng ý.
- SHORT_ANSWER: answer là một số (tối đa 4 kí tự kể cả dấu phẩy và dấu trừ, như đề THPT).
- ESSAY: explanation là đáp án mẫu kèm thang điểm ngắn.
- Mọi câu đều có explanation: lời giải từng bước, ngắn gọn.
- Công thức viết LaTeX trong $...$. Không nhắc tới hình vẽ.
Trả về DUY NHẤT JSON: {{"items": [{{"ref", "version", "content", "choices", "correct", "truths", "answer",
"explanation"}}, …]}}, đủ {versions} câu cho mỗi ref."""


def _source(i: int, d: dict) -> dict:
    out = {"ref": i, "type": d["type"], "content": d.get("content", ""), "explanation": d.get("explanation") or ""}
    if d["type"] in ("MULTIPLE_CHOICE", "TRUE_FALSE"):
        out["choices"] = list(d.get("choices") or [])
    if d["type"] == "MULTIPLE_CHOICE" and d.get("correct") is not None:
        out["correct"] = d["correct"]
    if d["type"] == "TRUE_FALSE":
        out["truths"] = list(d.get("truths") or [])
    if d["type"] == "SHORT_ANSWER":
        out["answer"] = (d.get("answers") or [""])[0]
    return out


def _draft(original: dict, v: _Variant) -> dict:
    kind = original["type"]
    draft = {
        "number": original.get("number"),
        "type": kind,
        "content": repair_latex(v.content.strip()),
        "choices": [repair_latex(c.strip()) for c in v.choices] if kind in ("MULTIPLE_CHOICE", "TRUE_FALSE") else [],
        "correct": v.correct if kind == "MULTIPLE_CHOICE" else None,
        "truths": list(v.truths) if kind == "TRUE_FALSE" else [],
        "answers": [a] if kind == "SHORT_ANSWER" and (a := extract_number(v.answer) or v.answer.strip()) else [],
        "explanation": repair_latex(v.explanation.strip()),
        "difficulty": original.get("difficulty"),
        "figure": None,
        "flags": [],
        "version": v.version,
        "of": original.get("number"),
        "checked": None,  # True: solved again to the same answer; False: another answer (see flags)
    }
    if problems(draft):
        draft["flags"].append("invalid")
    return draft


def problems(draft: dict) -> list[str]:
    return rules.validate(
        draft["type"],
        draft.get("content", ""),
        list(draft.get("choices") or []),
        draft.get("correct"),
        list(draft.get("truths") or []),
        list(draft.get("answers") or []),
    )


def _kept(original: dict, version: int) -> dict:
    """A question with a figure, as in the paper (its options in another order for versions after the first)."""
    draft = {**original, "version": version, "of": original.get("number"), "checked": None}
    draft["flags"] = [*original.get("flags", []), "kept_original"]
    if original["type"] == "MULTIPLE_CHOICE" and version > 1 and original.get("correct") is not None:
        choices = list(original["choices"])
        shift = (version - 1) % len(choices) if choices else 0
        draft["choices"] = choices[shift:] + choices[:shift]
        draft["correct"] = (original["correct"] - shift) % len(choices)
    return draft


async def _write(group: list[tuple[int, dict]], versions: int, models, gemini) -> list[_Variant]:
    payload = json.dumps([_source(i, d) for i, d in group], ensure_ascii=False)
    prompt = PROMPT.format(versions=versions)
    schema = json.dumps(_Variants.model_json_schema(), ensure_ascii=False)
    for model in models:
        try:
            if not await model.available():
                continue
            raw = await model.ask_json(prompt + "\nSchema: " + schema, payload, len(group) * versions)
            return _Variants.model_validate_json(raw).items
        except Exception as exc:  # quota, a broken answer: the next model
            log.warning("Similar questions with %s:%s failed: %s", model.name, model.model, exc)
    if gemini is None:
        return []
    result = await gemini._generate([prompt, payload], _Variants)
    return result.items


async def _check(drafts: list[dict], models, gemini) -> None:
    """Solve the new questions again without their answers; compare."""
    checkable = [d for d in drafts if d["type"] in ("MULTIPLE_CHOICE", "SHORT_ANSWER") and "invalid" not in d["flags"]]
    for start in range(0, len(checkable), 8):
        chunk = checkable[start : start + 8]
        questions = [
            ExtractedQuestion(
                number=n,
                page=1,
                type=d["type"],
                content=d["content"],
                choices=[ExtractedChoice(label="ABCD"[i], content=c) for i, c in enumerate(d["choices"][:4])],
            )
            for n, d in enumerate(chunk, start=1)
        ]
        solutions = None
        for solver in [*models, *([gemini] if gemini else [])]:
            try:
                if hasattr(solver, "available") and not await solver.available():
                    continue
                solutions = {s.number: s for s in await solver.solve(questions, [])}
                break
            except Exception as exc:
                log.warning("Checking similar questions failed: %s", exc)
        if solutions is None:
            continue
        for n, d in enumerate(chunk, start=1):
            sol = solutions.get(n)
            if sol is None:
                continue
            if d["type"] == "MULTIPLE_CHOICE":
                same = sol.correct_label is not None and "ABCD".find(sol.correct_label) == d["correct"]
            else:
                same = (extract_number(sol.short_answer or sol.final_value or "") or "") in d["answers"]
            d["checked"] = same
            if not same:
                d["flags"].append("check_mismatch")
                d["check_answer"] = sol.correct_label or sol.short_answer or sol.final_value or ""


async def make_variants(
    drafts: list[dict], versions: int, models, gemini, progress: Progress | None = None
) -> list[dict]:
    """`versions` similar papers from the drafts of a paper: drafts with "version" (1..versions) and "of"."""
    versions = max(1, min(versions, MAX_VERSIONS))
    usable = [
        (i, d)
        for i, d in enumerate(drafts)
        if d.get("type") in ("MULTIPLE_CHOICE", "TRUE_FALSE", "SHORT_ANSWER", "ESSAY")
    ]
    out: list[dict] = []
    written = [(i, d) for i, d in usable if not d.get("figure")]
    for start in range(0, len(written), GROUP):
        group = written[start : start + GROUP]
        if progress:
            await progress(
                f"AI đang viết câu tương tự {start + 1}–{start + len(group)} / {len(written)}…", start, len(written)
            )
        by_ref = {i: d for i, d in group}
        for v in await _write(group, versions, models, gemini):
            if v.ref in by_ref and 1 <= v.version <= versions:
                out.append(_draft(by_ref[v.ref], v))
    for _, d in usable:
        if d.get("figure"):
            out += [_kept(d, version) for version in range(1, versions + 1)]
    if progress:
        await progress("AI đang giải lại để kiểm tra đáp án…", len(written), len(written))
    await _check(out, models, gemini)
    order = {d.get("number"): n for n, (_, d) in enumerate(usable)}
    out.sort(key=lambda d: (d["version"], order.get(d["of"], 0)))
    return out
