"""Questions stuck in "needs review" that a machine can finish, so they reach the bank without an admin.

- A formula picture left only in the solution, a solution written out in words around it: the placeholder goes,
  the question goes on (the bank shows no pictures inside text yet). In the stem or the options it is needed:
  the question goes back to the AI queue, for a model that reads pictures.
- No options, no answer, no final number (the source page had none, or the parser could not read them): a free
  text model (Groq, OpenRouter) reads the question and its solution and decides —
    a numeric final answer           -> a short-answer question (the number),
    a short statement or a choice    -> a multiple-choice question (the options, three wrong ones written if
                                        the source had none, and which one is right),
    neither (prove, explain, several quantities) -> an essay question, its solution as the model answer.
  It also writes the solution when the source gave none.
"""

import json
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.latex import repair_latex
from app.models import CrawledQuestion, CrawlItemStatus, QuestionType

log = logging.getLogger(__name__)

IMAGES_LEFT = "AI chưa chuyển hết công thức dạng ảnh."
SOLUTION_FIGURE = "Lời giải gốc có hình minh hoạ (không đưa vào)."
ANSWER_ISSUES = {
    "Không tách được các lựa chọn A–D.",
    "Không tìm thấy đáp án.",
    "Không tìm thấy phần đáp án.",
    "Không tìm thấy đáp số.",
}
_PLACEHOLDER = r"\[\[img:\d+\]\]"
GROUP = 3  # questions per call: Groq's free tier allows 8,000 tokens a minute

RESCUE_PROMPT = """Bạn là giáo viên giỏi. Mỗi câu hỏi dưới đây (JSON) bị thiếu lựa chọn, thiếu đáp án hoặc \
thiếu đáp số. Dựa vào đề bài và lời giải (nếu có), hãy hoàn thiện từng câu để học sinh làm được:
- Nếu đáp án cuối cùng là MỘT con số: type = SHORT_ANSWER, short_answer = chỉ con số (dấu phẩy thập phân, \
không đơn vị).
- Nếu đáp án là một khẳng định/kết quả ngắn, hoặc câu đã có các lựa chọn: type = MULTIPLE_CHOICE, choices = 4 \
lựa chọn (giữ nguyên lựa chọn có sẵn; nếu chưa có thì viết 1 đáp án đúng và 3 đáp án sai hợp lý, dễ nhầm), \
correct = vị trí lựa chọn đúng (0–3).
- Nếu phải trình bày, chứng minh hoặc tính nhiều đại lượng: type = ESSAY, choices = [].
- explanation: lời giải từng bước bằng tiếng Việt (giữ lời giải có sẵn nếu đã đủ), công thức LaTeX trong $...$.
- Giải cẩn thận; KHÔNG đổi đề bài.
Trả về DUY NHẤT một JSON: {"questions": [{"ref": ..., "type": ..., "choices": [...], "correct": ..., \
"short_answer": ..., "explanation": "..."}]}"""


class Rescued(BaseModel):
    ref: int
    type: Literal["MULTIPLE_CHOICE", "SHORT_ANSWER", "ESSAY"]
    choices: list[str] = Field(default_factory=list)
    correct: int | None = None
    short_answer: str | None = None
    explanation: str = ""


class RescueResult(BaseModel):
    questions: list[Rescued]


def _payload(item: CrawledQuestion) -> dict:
    return {
        "ref": item.id,
        "content": item.content,
        "choices": [c["content"] for c in item.choices or []],
        "solution": item.explanation or "",
    }


def _apply(item: CrawledQuestion, answer: Rescued, provider: str) -> bool:
    """Write the completed question; False when the answer does not hold together (the item stays as it was)."""
    if answer.type == "MULTIPLE_CHOICE":
        choices = [repair_latex(c) for c in answer.choices if c and c.strip()]
        if not 2 <= len(choices) <= 6 or answer.correct is None or not 0 <= answer.correct < len(choices):
            return False
        item.question_type = QuestionType.MULTIPLE_CHOICE
        item.choices = [
            {"id": "abcdef"[i], "content": text, "is_correct": i == answer.correct} for i, text in enumerate(choices)
        ]
        item.accepted_answers = None
    elif answer.type == "SHORT_ANSWER":
        if not (answer.short_answer or "").strip():
            return False
        item.question_type, item.choices = QuestionType.SHORT_ANSWER, []
        item.accepted_answers = [answer.short_answer.strip()]
    else:
        if not answer.explanation.strip():
            return False  # an essay needs its model answer
        item.question_type, item.choices, item.accepted_answers = QuestionType.ESSAY, [], None
    if answer.explanation.strip() and (not item.explanation or len(answer.explanation) > len(item.explanation)):
        item.explanation = repair_latex(answer.explanation.strip())
        item.explanation_by_ai = True
    item.ai_provider = provider[:30]
    item.issues = [i for i in item.issues if i not in ANSWER_ISSUES]
    return True


def _images_in(text: str | None) -> bool:
    return bool(re.search(_PLACEHOLDER, text or ""))


# What a question asks: a question mark, an asking word, or a stem that ends in "là", "bằng", ":" …
_ASKS = re.compile(
    r"\?|[:…]\s*$|(?:^|\s)(là|bằng|gồm|thuộc|có|tính|tìm|chọn|xác định|giải|chứng minh|viết|nêu|so sánh"
    r"|giải thích|bao nhiêu|mấy|nào|gì|đâu|sao)\s*\.?\s*$|^(tính|tìm|chọn|giải|chứng minh|xác định|viết|nêu|cho)\s",
    re.I,
)


def is_a_question(item: CrawledQuestion) -> bool:
    """False for a lesson or chapter title read as a question (a table of contents): the model would make up
    a question from it."""
    text = re.sub(_PLACEHOLDER, "", item.content or "").strip()
    return bool(item.choices) or len(text.split()) >= 12 or bool(_ASKS.search(text))


def options_in_a_picture(item: CrawledQuestion) -> bool:
    """No options read but pictures with the question: the options are most likely one picture (formulas)."""
    return not item.choices and bool(item.images) and item.question_type != QuestionType.ESSAY


def drop_solution_images(item: CrawledQuestion) -> str | None:
    """ "ok" when only the solution held formula pictures (dropped), PENDING_AI when the stem or options hold some,
    None when the solution is mostly a picture."""
    if _images_in(item.content) or any(_images_in(c.get("content")) for c in item.choices or []):
        return CrawlItemStatus.PENDING_AI.value  # needed to answer: a model that reads pictures, again
    words = re.sub(_PLACEHOLDER, "", item.explanation or "")
    if len(words.strip()) < 40:
        return None  # the solution is mostly the picture: a person has to look
    item.explanation = "\n".join(line for line in words.split("\n") if line.strip()).strip()
    item.issues = [i for i in item.issues if i != IMAGES_LEFT] + [SOLUTION_FIGURE] * (
        SOLUTION_FIGURE not in item.issues
    )
    return "ok"


async def rescue_waiting(session: AsyncSession, models, limit: int = 20) -> dict[str, int]:
    """One pass over the questions waiting for review. `models`: chat models (app.ai.frugal.ChatModel), tried in
    order for the missing answers. Returns what happened. Items ready again are left READY for the caller."""
    stats = {"pictures_dropped": 0, "back_to_ai": 0, "completed": 0, "unchanged": 0, "not_a_question": 0}
    items = (
        await session.scalars(
            select(CrawledQuestion).where(CrawledQuestion.status == CrawlItemStatus.NEEDS_REVIEW).limit(500)
        )
    ).all()
    to_complete = []
    for item in items:
        if IMAGES_LEFT in item.issues:
            outcome = drop_solution_images(item)
            if outcome == CrawlItemStatus.PENDING_AI.value:
                item.status, item.ai_tries = CrawlItemStatus.PENDING_AI, 0
                item.issues = [i for i in item.issues if i != IMAGES_LEFT]
                stats["back_to_ai"] += 1
                continue
            if outcome == "ok":
                stats["pictures_dropped"] += 1
        if ANSWER_ISSUES & set(item.issues):
            if not is_a_question(item):
                item.status = CrawlItemStatus.REJECTED
                stats["not_a_question"] += 1
                continue
            if options_in_a_picture(item):  # a text model cannot see them: one that reads pictures
                item.status, item.ai_tries = CrawlItemStatus.PENDING_AI, 0
                stats["back_to_ai"] += 1
                continue
            to_complete.append(item)
        _ready_if_clean(item)
    for start in range(0, min(len(to_complete), limit), GROUP):
        group = to_complete[start : start + GROUP]
        answers, provider = await _ask(models, group)
        by_ref = {a.ref: a for a in answers}
        for item in group:
            answer = by_ref.get(item.id)
            if answer is not None and _apply(item, answer, provider):
                stats["completed"] += 1
                _ready_if_clean(item)
            else:
                stats["unchanged"] += 1
    await session.commit()
    return stats


def _ready_if_clean(item: CrawledQuestion) -> None:
    blocking = [i for i in item.issues if i != SOLUTION_FIGURE]
    if not blocking and item.status == CrawlItemStatus.NEEDS_REVIEW:
        item.status = CrawlItemStatus.READY


async def _ask(models, group: list[CrawledQuestion]) -> tuple[list[Rescued], str]:
    user = json.dumps([_payload(i) for i in group], ensure_ascii=False)
    for model in models:
        if not await model.available():
            continue
        try:
            raw = await model.ask_json(RESCUE_PROMPT, user, len(group))
            return RescueResult.model_validate_json(raw).questions, f"{model.name}:{model.model}"
        except (ValidationError, ValueError, KeyError) as exc:
            log.warning("%s gave an unusable rescue answer: %s", model.name, exc)
        except Exception as exc:  # noqa: BLE001 - quota, network: the next model
            log.warning("%s could not rescue: %s", model.name, exc)
    return [], ""


# ---------------------------------------------------------------- questions without a place in the table of contents

NO_CATEGORY = "Chưa gắn được chương/bài."
PLACE_PROMPT = """Bạn xếp câu hỏi vào mục lục sách giáo khoa Việt Nam (chương trình 2018). Cho một trang nguồn \
(tiêu đề, URL, vài câu hỏi mẫu) và mục lục hiện có của môn đó, hãy trả về vị trí của trang:
- root: "<Môn> <lớp>" như mục lục hiện có (VD: "Vật lí 11", "Toán 10").
- series: bộ sách (Kết nối tri thức / Chân trời sáng tạo / Cánh diều); "" nếu không rõ.
- chapter: tên chương ĐÚNG như mục lục hiện có nếu bài thuộc chương đã có; nếu chưa có thì viết tên chương chuẩn của \
SGK (VD: "Chương 1: Dao động").
- lesson: "Bài N: <tên bài ĐẦY ĐỦ chuẩn của SGK, có dấu, không rút gọn>" (lấy số bài từ tiêu đề/URL; URL thường \
chứa tên bài không dấu, VD "bai-5-dong-nang-the-nang-su-chuyen-hoa-nang-luong" -> "Bài 5: Động năng. Thế năng. Sự \
chuyển hoá năng lượng trong dao động điều hoà"); dùng đúng tên đã có nếu bài đã có.
Không chắc chắn thì đặt confident = false. Trả về DUY NHẤT một JSON: \
{"root": "...", "series": "...", "chapter": "...", "lesson": "...", "confident": true}"""


class Place(BaseModel):
    root: str
    series: str = ""
    chapter: str
    lesson: str
    confident: bool = True


async def _toc_of(session: AsyncSession, root_name: str) -> list[str]:
    from app.models import Category

    root = await session.scalar(select(Category).where(Category.parent_id.is_(None), Category.name.ilike(root_name)))
    if root is None:
        return []
    rows = (await session.scalars(select(Category).where(Category.path.like(f"{root.path}%")))).all()
    by_id = {c.id: c for c in rows}

    def path(c) -> str:
        names = []
        while c is not None:
            names.append(c.name)
            c = by_id.get(c.parent_id)
        return " › ".join(reversed(names))

    return sorted(path(c) for c in rows if c.depth >= 2)[:120]


_ROOT_HINT = re.compile(
    r"(toán|vật lí|vật lý|vat-li|toan|hoá học|hóa học|hoa-hoc|sinh học|sinh|tiếng anh|lịch sử|địa lí)[\s-]*(\d{1,2})",
    re.I,
)
_ROOT_NAMES = {"toan": "Toán", "vat-li": "Vật lí", "vật lý": "Vật lí", "hoa-hoc": "Hoá học", "hóa học": "Hoá học"}


def _root_guess(text: str) -> str | None:
    found = _ROOT_HINT.search(text)
    if not found:
        return None
    subject = found.group(1).lower()
    return f"{_ROOT_NAMES.get(subject, subject.capitalize())} {found.group(2)}"


async def place_uncategorised(session: AsyncSession, models) -> dict[str, int]:
    """Pages whose questions have no place in our table of contents (crawled before auto_toc): a text model
    reads the page title, URL and a few questions, with the subject's current table of contents, and names the
    root › series › chapter › lesson; the page is mapped there and its questions go on to the bank."""
    from app.models import CrawlCategoryMap
    from app.pipeline.use_cases.toc import ensure_path

    stats = {"pages": 0, "placed": 0, "unsure": 0}
    items = (
        await session.scalars(
            select(CrawledQuestion).where(
                CrawledQuestion.status == CrawlItemStatus.NEEDS_REVIEW,
                CrawledQuestion.issues.contains([NO_CATEGORY]),
            )
        )
    ).all()
    pages: dict[tuple[str, str], list[CrawledQuestion]] = {}
    for item in items:
        pages.setdefault((item.site_id, item.source_url), []).append(item)
    for (site_id, url), questions in pages.items():
        stats["pages"] += 1
        title = questions[0].source_title or ""
        root_hint = _root_guess(f"{title} {url}")
        user = json.dumps(
            {
                "title": title,
                "url": url,
                "questions": [q.content[:300] for q in questions[:4]],
                "current_table_of_contents": await _toc_of(session, root_hint) if root_hint else [],
            },
            ensure_ascii=False,
        )
        place = None
        for model in models:
            if not await model.available():
                continue
            try:
                place = Place.model_validate_json(await model.ask_json(PLACE_PROMPT, user, 1))
                break
            except Exception as exc:  # noqa: BLE001 - unusable answer, quota: the next model
                log.warning("%s could not place %s: %s", model.name, url, exc)
        if place is None or not place.confident or not (place.root and place.chapter and place.lesson):
            stats["unsure"] += 1
            continue
        target = await ensure_path(session, [place.root, place.series, place.chapter, place.lesson])
        if await session.get(CrawlCategoryMap, (site_id, url)) is None:
            session.add(
                CrawlCategoryMap(site_id=site_id, source_url=url, category_id=target.id, source_title=title[:500])
            )
        for q in questions:
            q.issues = [i for i in q.issues if i != NO_CATEGORY]
            _ready_if_clean(q)
            stats["placed"] += 1
        await session.flush()
    await session.commit()
    return stats


# ==============================================================================
# Self-Healing & Double-Blind Consensus Verification Routines
# ==============================================================================
BROKEN_MATH_OR_FONT_RE = re.compile(
    r"([\ufffd]|\$[0O]_{(?:max|min)}\$|\$Im\^\{?2\}?\$|\s{3,}[,\.]|¸p suÊt|chuyÓn ®éng)"
)


def detect_needs_healing(question_text: str, category_id: int | None) -> list[str]:
    flags = []
    if category_id is None:
        flags.append("UNCLASSIFIED")
    if BROKEN_MATH_OR_FONT_RE.search(question_text or ""):
        flags.append("BROKEN_FONT_OR_MATH")
    return flags


def build_taxonomy_and_healing_prompt(
    questions_batch: list[dict[str, Any]], available_categories: list[dict[str, Any]]
) -> str:
    """
    Prompt cho Job 1:
    - Chữa lỗi bài Toán xác suất (bối cảnh xét nghiệm bệnh/virus) bị phân nhầm sang Sinh học.
    - Cộng dồn thêm nhãn kỳ thi ('THPTQG', 'DGTD_TSA', 'HSG') cho các bài Lớp 11, 12.
    - Đề xuất tên Dạng bài mới nếu chưa có trong cây mục lục.
    """
    return f"""
Bạn là bộ máy kiểm định Ngân hàng câu hỏi chuẩn SGK Kết nối tri thức.

QUY TẮC BẮT BUỘC:
1. CHỐNG NHẦM MÔN (TOÁN vs SINH/HÓA):
   - Xét CÔNG CỤ TOÁN/KHOA HỌC dùng để giải bài, KHÔNG xét bối cảnh câu chuyện.
   - Nếu đề bài nói về "xét nghiệm virus SARS-CoV-2, bệnh hiểm nghèo, vi khuẩn, đàn bò" nhưng yêu cầu "Tính xác suất..." bằng xác suất có điều kiện / công thức Bayes -> BẮT BUỘC gán `subject = "MATH"`, TUYỆT ĐỐI KHÔNG gán vào `"BIOLOGY"`.
2. SỬA LỖI FONT & OCR CÔNG THỨC:
   - Sửa `$0_{{max}}$` / `$O_{{max}}$` trong bài dao động/điện từ thành `$\\omega_{{\\max}}$`, `$Im^2$` thành `$1\\text{{ m}}^2$`.
3. CỘNG DỒN NHÃN KỲ THI (`append_target_tags`):
   - Giữ nguyên bài học gốc (VD: Vật lí 11 - Chương Dao động), đồng thời trả về mảng `append_target_tags` chứa `"THPTQG"` nếu kiến thức nằm trong phạm vi thi tốt nghiệp THPT, `"DGTD_TSA"` nếu là câu hỏi tư duy thực tế/đọc đồ thị, hoặc `"HSG"` nếu là câu tự luận nâng cao.

DANH MỤC CHỦ ĐỀ HIỆN CÓ:
{json.dumps(available_categories, ensure_ascii=False)}

DANH SÁCH CÂU HỎI CẦN QUÉT:
{json.dumps(questions_batch, ensure_ascii=False)}
""".strip()  # noqa: E501


# =====================================================================
# 1. BỘ CHUẨN HÓA ĐÁP ÁN ĐỂ SO SÁNH ĐỒNG THUẬN 2 LẦN AI GIẢI (DOUBLE-BLIND)
# =====================================================================

def canonicalize_answer(raw_ans: str | None, q_type: str | Any) -> str:
    """Chuẩn hóa đáp án từ các định dạng văn bản tự do về định dạng so sánh chuẩn."""
    if not raw_ans:
        return ""
    s = str(raw_ans).strip().upper()
    q_type_str = q_type.value if hasattr(q_type, "value") else str(q_type).upper()

    if q_type_str in ("SINGLE_CHOICE", "MULTIPLE_CHOICE"):
        m = re.search(r"\b([A-D])\b", s)
        return m.group(1) if m else s[:1]

    if q_type_str in ("TRUE_FALSE_4", "TRUE_FALSE"):
        # Chuyển mọi kiểu "a) Đúng, b) Sai..." hoặc "Đ-Đ-S-S", "True/False" về dạng chuẩn "TTFF"
        s_norm = s.replace("ĐÚNG", "T").replace("SAI", "F").replace("TRUE", "T").replace("FALSE", "F")
        tokens = re.findall(r"[TFĐS]", s_norm)
        mapped = ["T" if c in ("T", "Đ") else "F" for c in tokens[:4]]
        return "".join(mapped) if len(mapped) == 4 else s

    if q_type_str == "SHORT_ANSWER":
        # Đổi dấu phẩy thập phân tiếng Việt sang dấu chấm và rút số/phân số
        s_clean = s.replace(",", ".")
        # Trường hợp phân số a/b
        frac_m = re.search(r"(-?\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", s_clean)
        if frac_m:
            try:
                val = float(frac_m.group(1)) / float(frac_m.group(2))
                return f"{val:.2f}"
            except ZeroDivisionError:
                pass
        # Trường hợp số thực / số nguyên
        num_m = re.search(r"-?\d+(?:\.\d+)?", s_clean)
        if num_m:
            return f"{float(num_m.group(0)):.2f}"

    return re.sub(r"\s+", " ", s)


def is_consensus_reached(ans_1: str | None, ans_2: str | None, q_type: str | Any) -> bool:
    """So khớp đồng thuận giữa 2 lần giải độc lập của AI, hỗ trợ dung sai làm tròn số."""
    c1 = canonicalize_answer(ans_1, q_type)
    c2 = canonicalize_answer(ans_2, q_type)
    if not c1 or not c2:
        return False
    q_type_str = q_type.value if hasattr(q_type, "value") else str(q_type).upper()
    if q_type_str == "SHORT_ANSWER":
        try:
            # Cho phép sai số làm tròn <= 0.02 giữa 2 lần giải
            return abs(float(c1) - float(c2)) <= 0.02
        except ValueError:
            return c1 == c2
    return c1 == c2


def evaluate_double_blind_consensus(
    first_ai_answer: str | None,
    second_ai_answer: str,
    q_type: str | Any = "SINGLE_CHOICE",
) -> dict[str, Any]:
    """
    Logic cho Job 2 (Giải lại các câu chưa duyệt / nghi đề gốc sai):
    - Sử dụng `canonicalize_answer` và `is_consensus_reached`.
    - Nếu Lần giải 2 độc lập ra kết quả ĐỒNG THUẬN Lần giải 1 -> Tự động duyệt (is_verified = True) và cho phép gắn vào Đề.
    - Nếu chưa có Lần giải 1 -> Lưu kết quả làm mốc Lần 1.
    - Nếu 2 lần giải mâu thuẫn nhau -> Đẩy sang trạng thái HUMAN_REVIEW.
    """  # noqa: E501
    if not first_ai_answer or not first_ai_answer.strip():
        return {
            "is_verified": False,
            "audit_status": "FIRST_PASS_RECORDED",
            "first_ai_answer": canonicalize_answer(second_ai_answer, q_type) or second_ai_answer.strip(),
            "attach_to_exam": False,
        }

    if is_consensus_reached(first_ai_answer, second_ai_answer, q_type):
        return {
            "is_verified": True,
            "audit_status": "VERIFIED_CONSENSUS",
            "second_ai_answer": canonicalize_answer(second_ai_answer, q_type) or second_ai_answer.strip(),
            "attach_to_exam": True,  # Tự động kích hoạt/lưu vào đề thi
        }

    return {
        "is_verified": False,
        "audit_status": "HUMAN_REVIEW",
        "second_ai_answer": canonicalize_answer(second_ai_answer, q_type) or second_ai_answer.strip(),
        "attach_to_exam": False,
    }


# =====================================================================
# 2. ANTI-EXPLOSION TOPIC GUARDRAIL & AUTO-ATTACH HELPER
# =====================================================================

async def get_or_create_subcategory(session: AsyncSession, parent_id: int, name: str) -> Any:
    """Anti-Explosion Guardrail:
    Khi AI đề xuất tên chủ đề mới, so khớp mờ (similarity >= 0.85) với các chủ đề hiện có
    dưới cùng Chương cha để tránh đẻ ra hàng loạt nhánh tương tự nhau."""
    import difflib

    from app.models import Category

    name_clean = name.strip()
    name_lower = name_clean.lower()

    # 1. Lấy tất cả chủ đề con hiện tại dưới parent_id
    existing_all = list((await session.scalars(
        select(Category).where(Category.parent_id == parent_id)
    )).all())

    # 2. So khớp chính xác
    for cat in existing_all:
        if cat.name.strip().lower() == name_lower:
            return cat

    # 3. So khớp mờ (Fuzzy Similarity >= 0.85)
    for cat in existing_all:
        ratio = difflib.SequenceMatcher(None, cat.name.strip().lower(), name_lower).ratio()
        if ratio >= 0.85:
            log.info("Anti-explosion: Gộp chủ đề mới '%s' vào nhánh có sẵn '%s' (độ khớp: %.2f)", name_clean, cat.name, ratio)  # noqa: E501
            return cat

    # 4. Tạo chủ đề con mới an toàn
    parent = await session.get(Category, parent_id)
    root_id = parent.root_id if parent else parent_id
    depth = (parent.depth + 1) if parent else 2
    path = f"{parent.path}{parent_id}/" if parent and parent.path else f"/{root_id}/{parent_id}/"

    new_cat = Category(
        parent_id=parent_id,
        name=name_clean,
        depth=depth,
        path=path,
    )
    session.add(new_cat)
    await session.flush()
    return new_cat


async def attach_question_to_exam_if_missing(session: AsyncSession, exam_id: int, question_id: int) -> None:
    """Gắn câu hỏi đã được đồng thuận vào đề thi nếu chưa có trong exam_questions."""
    from sqlalchemy import func

    from app.models import ExamQuestion

    exists = await session.scalar(
        select(ExamQuestion).where(
            ExamQuestion.exam_id == exam_id,
            ExamQuestion.question_id == question_id,
        )
    )
    if not exists:
        max_pos = await session.scalar(
            select(func.coalesce(func.max(ExamQuestion.position), 0)).where(ExamQuestion.exam_id == exam_id)
        )
        session.add(ExamQuestion(exam_id=exam_id, question_id=question_id, position=(max_pos or 0) + 1))
        await session.flush()
