"""Library file → AI import → bank questions + online exam + clean PDFs that replace the original.

When the worker finishes the AI import of a library file (one exam, often cut from a bundle), every draft
passed the automatic checks, and every answer agrees with the official answer key (application/answer_keys.py),
the questions go to the bank, an exam is created for practice online, and two PDFs are typeset from the
questions — the paper and the solutions — without the logos, links and banners of the file it came from.
They take the original's place in the library (same folder, status, rights); the original stays for admins
only. A file whose drafts need a human waits in the review screen, and the same PDFs are made when an admin
publishes it there with an exam.

The PDFs keep one line naming where the exam comes from (the document's licence note): the questions are the
exam author's; the worked solutions are ours (solved by the AI import).
"""

import hashlib
import logging
import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ai_publish import create_exam, drafts_of, publish_drafts, publish_problems
from app.application.answer_keys import compare, official_answers
from app.application.exam_pdf import PdfError, Renderer, build_parts, chromium_pdf, exam_html
from app.infrastructure.storage import get_bronze_storage, get_storage
from app.models import (
    Category,
    Exam,
    ExamQuestion,
    ImportJob,
    JobStatus,
    LibraryDocument,
    LibraryStatus,
    Question,
)

log = logging.getLogger(__name__)
GENERATED = "exam-system"  # LibraryDocument.source of the PDFs typeset here
MIN_QUESTIONS = 5  # fewer usually means the file was not an exam (or the AI read only part of it)
BLOCKING_FLAGS = {
    "no_match": "đáp án AI tính ra không khớp lựa chọn nào",
    "no_solution": "AI chưa giải được",
    "figure_failed": "chưa cắt được hình",
    "unsupported_type": "có câu tự luận",
    "key_mismatch": "AI giải khác đáp án in trong file",
}


def auto_problems(drafts: list[dict]) -> str | None:
    """Why these drafts cannot be published without an admin (None = they can)."""
    if len(drafts) < MIN_QUESTIONS:
        return f"chỉ đọc được {len(drafts)} câu"
    for d in drafts:
        if flags := [BLOCKING_FLAGS[f] for f in d.get("flags", []) if f in BLOCKING_FLAGS]:
            return f"câu {d.get('number')}: {', '.join(flags)}"
        if problems := publish_problems(d):
            return f"câu {d.get('number')}: {problems[0]}"
    return None


def answered_in_file(drafts: list[dict]) -> str | None:
    """For papers whose answers are printed in the file itself (essays with their marking scheme, multiple choice
    with an answer table): "" when every question has it, a reason when one lacks it. None: not such a paper, the
    official answers decide."""
    kinds = {d.get("type") for d in drafts}
    if not kinds <= {"ESSAY", "MULTIPLE_CHOICE"}:
        return None
    if "MULTIPLE_CHOICE" in kinds and not all(d.get("printed") for d in drafts if d.get("type") == "MULTIPLE_CHOICE"):
        return None
    missing = [d for d in drafts if d.get("type") == "ESSAY" and not d.get("key_from_file")]
    if missing:
        return f"câu {missing[0].get('number')}: tự luận chưa có hướng dẫn chấm trong file"
    return ""


async def library_document(session: AsyncSession, job: ImportJob) -> LibraryDocument | None:
    """The library file this import came from: the exam cut from a bundle rather than the bundle itself."""
    docs = (
        await session.scalars(
            select(LibraryDocument).where(LibraryDocument.import_job_id == job.id, LibraryDocument.source != GENERATED)
        )
    ).all()
    return next((d for d in docs if d.parent_id is not None), docs[0] if docs else None)


def minutes_for(subject: str, questions: int) -> int:
    """Duration of the THPT exam in that subject (an estimate when the subject is unknown)."""
    name = subject.casefold()
    if "văn" in name:
        return 120
    if "toán" in name:
        return 90
    if "anh" in name or "ngoại ngữ" in name or questions > 40:
        return 60
    return 50


async def _names(session: AsyncSession, job: ImportJob) -> tuple[str, str]:
    """(subject, exam group), e.g. ("Toán", "Đề thi THPT 2024") from "Ôn thi THPT – Toán › Đề thi THPT 2024"."""
    root = await session.get(Category, job.root_id)
    category = await session.get(Category, job.category_id)
    subject = root.name.split("–")[-1].strip() if root else ""
    return subject, category.name if category else ""


_SUBJECTS = [
    "Toán", "Vật lí", "Vật lý", "Hóa học", "Hoá học", "Sinh học", "Ngữ văn", "Tiếng Anh", "Lịch sử", "Địa lí",
    "Địa lý", "Tin học", "GDCD", "Giáo dục kinh tế và pháp luật", "Công nghệ", "Khoa học tự nhiên", "Tiếng Việt",
]  # fmt: skip
_CANONICAL = {"Vật lý": "Vật lí", "Hoá học": "Hóa học", "Địa lý": "Địa lí"}


def exam_code(names: list[str]) -> str:
    """The exam's short name from its folders / categories: THPTQG, TS10, HSG-DHBB, HSG, or initials."""
    text = " ".join(names)
    plain = _plain(text)
    if "hsg" in plain or "hoc sinh gioi" in plain:
        return "HSG-DHBB" if "duyen hai" in plain or "dhbb" in plain or "dh&db" in plain else "HSG"
    if "vao 10" in plain or "tuyen sinh 10" in plain or "lop 10 thpt" in plain:
        return "TS10"
    if "thpt" in plain or "tot nghiep" in plain:
        return "THPTQG"
    words = [w for w in re.findall(r"[A-Za-z0-9]+", plain.upper()) if not w.isdigit()]
    return "".join(w[0] for w in words[:4]) or "DE"


def subject_name(names: list[str]) -> str:
    for name in names:
        for subject in _SUBJECTS:
            if subject.casefold() in name.casefold():
                return _CANONICAL.get(subject, subject)
    return "Tổng hợp"


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn").lower()


async def exam_title(session: AsyncSession, job: ImportJob, doc: LibraryDocument) -> str:
    """ "<KỲ THI>-<Môn>-<số thứ tự>", e.g. "THPTQG-Toán-001": the next number of that exam and subject."""
    category = await session.get(Category, job.category_id)
    path = [c.name for c in await _ancestors(session, category)] if category else []
    names = [*path, *doc.folder_path, doc.title]
    prefix = f"{exam_code(names)}-{subject_name(names)}-"
    taken = await session.scalars(select(Exam.title).where(Exam.title.like(f"{prefix}%")))
    numbers = [int(m.group(1)) for t in taken if (m := re.match(re.escape(prefix) + r"(\d+)$", t))]
    return f"{prefix}{max(numbers, default=0) + 1:03d}"


async def _ancestors(session: AsyncSession, category: Category) -> list[Category]:
    ids = [int(i) for i in category.path.strip("/").split("/") if i]
    found = {c.id: c for c in await session.scalars(select(Category).where(Category.id.in_(ids)))}
    return [found[i] for i in ids if i in found]


async def auto_publish(session: AsyncSession, job: ImportJob, render: Renderer | None = None) -> Exam | None:
    """Called by the worker once an import is done. Publishes it when it came from the library and passes the
    checks; otherwise leaves it for the admin and says why in the job's stage. Commits."""
    doc = await library_document(session, job)
    drafts = drafts_of(job)
    if doc is None or any(d.get("question_id") for d in drafts):
        return None
    reason = auto_problems(drafts)
    if reason is None and (keyed := answered_in_file(drafts)) is not None:
        reason = keyed or None  # "" when the file's own answers settle every question
    elif reason is None:  # a misread question still passes the checks above: the official answers decide
        answers = await official_answers(session, job, doc)
        if answers is None:
            reason = "chưa có đáp án chính thức để đối chiếu"
        elif problems := compare(drafts, answers):
            job.drafts = drafts  # the disagreeing drafts are marked for the review screen
            more = f" (và {len(problems) - 1} chỗ khác)" if len(problems) > 1 else ""
            reason = f"lệch đáp án chính thức – {problems[0]}{more}"
    if reason:
        job.stage = f"{job.stage or ''} · Chờ admin duyệt: {reason}"[:200]
        await session.commit()
        return None
    # The checks above and the official answers stand in for the admin's review.
    questions = await publish_drafts(session, job, list(range(len(drafts))), verified=True, reuse_duplicates=True)
    subject, _ = await _names(session, job)
    exam = await create_exam(
        session,
        job,
        questions,
        title=await exam_title(session, job, doc),
        minutes=minutes_for(subject, len(questions)),
        published=True,
        user_id=None,
    )
    job.stage = f"Đã tự xuất bản {len(questions)} câu và đề “{exam.title}”"[:200]
    await session.commit()
    await publish_to_library(session, job, exam, render)
    await session.commit()
    return exam


async def recheck_waiting(session: AsyncSession, category_id: int, render: Renderer | None = None) -> int:
    """Once the official answers of a paper are known, try again the imports of that paper that were waiting."""
    jobs = await session.scalars(
        select(ImportJob).where(ImportJob.category_id == category_id, ImportJob.status == JobStatus.DONE)
    )
    published = 0
    for job in list(jobs):
        if job.drafts and not any(d.get("question_id") for d in job.drafts):
            job.stage = (job.stage or "").split(" · Chờ admin duyệt")[0]
            published += await auto_publish(session, job, render) is not None
    return published


def answers_title(paper_title: str) -> str:
    """The answer copy of a paper in the library: "THPTQG-Toán-001 – Đáp án" (linked to it by exam_id)."""
    return f"{paper_title} – Đáp án"


async def publish_to_library(
    session: AsyncSession, job: ImportJob, exam: Exam, render: Renderer | None = None
) -> list[LibraryDocument]:
    """Typeset the exam's paper and solutions into the library in place of the file it was imported from.
    Nothing happens for imports that did not come from the library, or if printing fails."""
    doc = await library_document(session, job)
    if doc is None:
        return []
    rows = await session.execute(
        select(Question).join(ExamQuestion).where(ExamQuestion.exam_id == exam.id).order_by(ExamQuestion.position)
    )
    parts = await build_parts(list(rows.scalars()), get_storage())
    subject, group = await _names(session, job)
    origin = doc.license_note or (doc.folder_path[0] if doc.folder_path else "") or doc.title
    source = f"Nguồn đề: {origin}"
    bronze, render = get_bronze_storage(), render or chromium_pdf
    made = []
    try:
        for solutions in (False, True):
            html = exam_html(
                title=exam.title,
                subtitle=group or subject,
                minutes=exam.duration_seconds // 60,
                source=source,
                parts=parts,
                solutions=solutions,
            )
            # Each copy names its pair, so a printed paper and its answers are never mixed up
            footer = f"{answers_title(exam.title)} · đáp án của đề {exam.title}" if solutions else f"Đề {exam.title}"
            made.append((solutions, await render(html, footer)))
    except PdfError as exc:
        log.warning("Exam %s: %s", exam.id, exc)
        return []

    docs = []
    for solutions, pdf in made:
        suffix = "loi-giai" if solutions else "de"
        file_id = f"exam:{exam.id}:{suffix}"
        key = f"generated/exam-{exam.id}-{suffix}.pdf"
        await bronze.put(key, pdf, "application/pdf")
        clean = await session.scalar(select(LibraryDocument).where(LibraryDocument.source_file_id == file_id))
        if clean is None:
            clean = LibraryDocument(source=GENERATED, source_file_id=file_id, status=doc.status)
            session.add(clean)
        clean.title = answers_title(exam.title) if solutions else exam.title
        clean.kind, clean.folder_path, clean.category_id = doc.kind, doc.folder_path, job.category_id
        clean.checksum, clean.storage_key = hashlib.sha256(pdf).hexdigest(), key
        clean.mime, clean.size = "application/pdf", len(pdf)
        clean.downloadable, clean.license_note = doc.downloadable, source[:300]
        clean.exam_id, clean.origin_id = exam.id, doc.id
        docs.append(clean)
    if doc.status == LibraryStatus.PUBLISHED:  # the original, with its banners, is for admins only from now on
        doc.status = LibraryStatus.HIDDEN
    if doc.parent_id is not None:  # the bundle goes too once every exam cut from it has a clean version
        siblings = await session.scalars(select(LibraryDocument).where(LibraryDocument.parent_id == doc.parent_id))
        if all(s.status == LibraryStatus.HIDDEN for s in siblings):
            parent = await session.get(LibraryDocument, doc.parent_id)
            if parent.status == LibraryStatus.PUBLISHED:
                parent.status = LibraryStatus.HIDDEN
    await session.flush()
    log.info("Exam %s typeset into the library (%s)", exam.id, doc.title)
    return docs
