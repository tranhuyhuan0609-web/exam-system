"""Official answer keys: read once from the answer table published with an exam, then used to check every code
of that exam imported from the library before it is published automatically.

A misread question is the danger the other checks miss: the AI solves what it read, its answer matches an option,
and a wrong question would reach students. The official key catches it — the answers differ. So an exam is only
published automatically when its key is known and every answer agrees; otherwise the drafts wait for an admin,
with the disagreements marked (flag "key_mismatch", the official answer next to the AI's).
"""

import io
import logging
import re
import unicodedata

from PIL import Image
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import PageImage
from app.infrastructure.pdf_engine import PDFEngineAdapter
from app.models import AnswerKey, ImportJob, LibraryDocument

log = logging.getLogger(__name__)
MAX_PAGES = 4  # an answer table is one or two pages
_KEY_TITLE = re.compile(r"(?:đáp|dap)\s*(?:án|an)", re.IGNORECASE)
_NOT_A_TABLE = re.compile(
    r"chi\s*ti[ếe]t|l[ờo]i\s*gi[ảa]i|h[ưu][ớo]ng\s*d[ẫa]n|bi[ểe]u\s*[đd]i[ểe]m",
    re.IGNORECASE,
)
_CODE = re.compile(r"(?<!\d)(\d{3,4})(?!\d)")
TRUE, FALSE = set("ĐDTđdt"), set("SFsf")


def is_answer_key(doc: LibraryDocument) -> bool:
    """ "4. Đáp án Toán THPTQG2024", "Dap an, thang diem": an answer table (PDF/image). Worked solutions ("Đáp án chi tiết",
    "Lời giải", "Biểu điểm", or a .docx marking scheme / part cut from a bundle) are not."""  # noqa: E501
    if doc.mime and doc.mime not in ("application/pdf", "image/png", "image/jpeg"):
        return False
    title = unicodedata.normalize("NFC", doc.title)
    return doc.parent_id is None and bool(_KEY_TITLE.search(title)) and not _NOT_A_TABLE.search(title)


def paper_code(title: str) -> str | None:
    """ "Mã đề 101" → "101"; leading zeros do not matter ("0101" is 101)."""
    found = _CODE.findall(title)
    return str(int(found[-1])) if found else None


def document_pages(data: bytes, mime: str) -> list[PageImage]:
    if mime == "application/pdf":
        with PDFEngineAdapter.open(data) as pdf:
            return [
                PageImage(number=i + 1, png=pdf.render_png(i, dpi=150)) for i in range(min(pdf.page_count, MAX_PAGES))
            ]
    with Image.open(io.BytesIO(data)) as picture:  # JPEG or PNG
        if picture.mode not in ("RGB", "RGBA", "L"):
            picture = picture.convert("RGB")  # CMYK, palette, 16-bit: PNG output must be plain RGB
        out = io.BytesIO()
        picture.save(out, format="PNG")
    return [PageImage(number=1, png=out.getvalue())]


async def read_answer_key_document(
    session: AsyncSession, job: ImportJob, doc: LibraryDocument, data: bytes, provider
) -> int:
    """Store the answers of every code in the table; returns how many codes were read."""
    papers = await provider.read_answer_key(document_pages(data, doc.mime))
    stored = 0
    for paper in papers:
        code = paper_code(paper.code)
        answers = {
            "multiple_choice": list(paper.multiple_choice),
            "true_false": list(paper.true_false),
            "short_answer": list(paper.short_answer),
        }
        if code is None or not any(answers.values()):
            continue
        stmt = insert(AnswerKey).values(
            category_id=job.category_id, code=code, answers=answers, source_document_id=doc.id
        )
        await session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_answer_keys_category_code",
                set_={"answers": answers, "source_document_id": doc.id},
            )
        )
        stored += 1
    return stored


def _truths(value: str) -> list[bool | None]:
    return [True if c in TRUE else False if c in FALSE else None for c in value if c in TRUE | FALSE | {"?"}]


def _number(value: str) -> float | None:
    try:
        return float(str(value).strip().replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def compare(drafts: list[dict], answers: dict) -> list[str]:
    """Disagreements between the drafts and the official answers (empty = all agree). Marks each disagreeing
    draft with the flag "key_mismatch" and the official answer ("official"), for the review screen."""
    problems: list[str] = []
    parts = [
        ("MULTIPLE_CHOICE", "multiple_choice", "Phần trắc nghiệm"),
        ("TRUE_FALSE", "true_false", "Phần đúng/sai"),
        ("SHORT_ANSWER", "short_answer", "Phần trả lời ngắn"),
    ]
    for kind, field, label in parts:
        items = [d for d in drafts if d.get("type") == kind]
        official = answers.get(field) or []
        if len(items) != len(official):
            if items or official:
                problems.append(f"{label}: đề đọc được {len(items)} câu, đáp án chính thức có {len(official)}")
            continue
        for position, (draft, expected) in enumerate(zip(items, official, strict=True), start=1):
            if kind == "MULTIPLE_CHOICE":
                correct = draft.get("correct")
                mine = "ABCDEF"[correct] if isinstance(correct, int) and 0 <= correct < 6 else None
                agree = expected != "?" and mine == expected
            elif kind == "TRUE_FALSE":
                want = _truths(expected)
                mine = "".join("Đ" if t else "S" for t in draft.get("truths") or [])
                agree = None not in want and want == list(draft.get("truths") or [])
            else:
                mine = (draft.get("answers") or [None])[0]
                a, b = _number(mine or ""), _number(expected)
                agree = a is not None and b is not None and abs(a - b) <= 1e-6 * max(1.0, abs(b))
            if not agree:
                draft["flags"] = [*{*draft.get("flags", []), "key_mismatch"}]
                draft["official"] = expected
                problems.append(f"{label} câu {position}: AI {mine or '—'}, đáp án chính thức {expected}")
    return problems


async def official_answers(session: AsyncSession, job: ImportJob, doc: LibraryDocument) -> dict | None:
    code = paper_code(doc.title)
    if code is None:
        return None
    key = await session.scalar(
        select(AnswerKey).where(AnswerKey.category_id == job.category_id, AnswerKey.code == code)
    )
    return key.answers if key else None
