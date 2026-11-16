"""C04: a teacher's PDF becomes a published paper (and, with a class, an open assignment) in one transaction.

1. the file (a PDF, a Word file or a photo of the paper) is registered as a bronze asset + document version
   (A01 `submit_import`: magic bytes, size cap, SHA-256);
2. its questions are split (`extract_exam_structure_from_asset`, the reader of the BBox review), then each one is
   solved and tagged by the AI solver and put into the tenant's bank (`solve_many` + `store_prepared`: the file's own
   key wins over the AI, dedup on the content hash);
3. each question is published under the A09 gate. Uploading the paper counts as the teacher's review: a
   QUESTION_VERSION review task is opened and APPROVED by the teacher (audited, reason "teacher PDF upload"), which
   makes the question VERIFIED, then `publish_question_version(require_version_review=True)` freezes it. Rights:
   the teacher claims the paper as their own (OWNED), as an author does in `POST /questions`;
4. a paper holding exactly those questions, in the PDF's order and with its points, is created and published
   (`publish_assessment` writes the grading key to `assessment_answer_keys`);
5. with `class_id` (a class the teacher runs) the paper is assigned to it, which opens the exam room for its students.

A question that cannot go into a paper is left out and counted (no key the solver trusts, a SINGLE_CHOICE without
exactly four options A..D, a CLUSTER_TRUE_FALSE without a..d, a question already rejected by a reviewer).

Three phases, so no database connection is held while the AI works: (1) a short transaction checks the class and
reads the topics; (2) the PDF is read, split and solved with NO transaction open (`SOLVE_CONCURRENCY` questions at a
time); (3) one transaction stores, publishes and assigns everything, or nothing.

The teacher is answered with the paper and the assignment only: no question, no answer, no bank link.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.ai_solver import AiSolver
from app.modules.assembly.internal.service import (
    AssessmentConflictError,
    AssessmentNotFoundError,
    assemble_from_question_versions,
    create_assessment,
    publish_assessment,
)
from app.modules.assignments.public import create_assignment
from app.modules.classes.public import can_manage_class
from app.modules.ingestion.public import IngestionValidationError, submit_import
from app.modules.parsers.public import extract_exam_structure_from_asset
from app.modules.questions.public import (
    OptionInput,
    QuestionConflictError,
    QuestionValidationFailedError,
    RawQuestion,
    find_question_by_content,
    get_question,
    keyed_options,
    load_taxonomy_hints,
    pictures_for,
    publish_question_version,
    solve_many,
    store_prepared,
)
from app.modules.questions.public import (
    PublishGateBlockedError as QuestionPublishBlocked,
)
from app.modules.review.public import ReviewConflictError, create_review_task, decide_review_task
from app.platform.file_magic import FileSignatureError, inspect_uploaded_bytes
from app.platform.tenancy import DbRole, tenant_transaction

FRAMEWORK_CODE = "THPTQG_2025"  # gives the scoring scheme; the paper's structure is the PDF's own
SNAPSHOT_FRAMEWORK_CODE = "TEACHER_PDF"
DURATIONS_MINUTES = (15, 30, 45, 60, 90, 120, 150, 180)
SOLVE_CONCURRENCY = 4
_PAPER_SHAPES = {"SINGLE_CHOICE": ("A", "B", "C", "D"), "CLUSTER_TRUE_FALSE": ("a", "b", "c", "d")}
REVIEW_REASON = "Giáo viên tải đề PDF lên để tạo đề thi: giáo viên xác nhận đề và đáp án (teacher PDF upload)"


class TeacherPaperError(ValueError):
    """The PDF cannot become a paper; `code` says why (NO_QUESTIONS_FOUND, INVALID_FILE, ...)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TeacherPaperResult:
    assessment_id: uuid.UUID
    assessment_version_id: uuid.UUID
    assignment_id: uuid.UUID | None
    class_id: uuid.UUID | None
    title: str
    question_count: int
    skipped_question_count: int
    duration_minutes: int


async def _publish_for_paper(
    session: AsyncSession, *, tenant_id: uuid.UUID, teacher_id: uuid.UUID, question_id: uuid.UUID,
    version_id: uuid.UUID, correlation_id: str,
) -> uuid.UUID | None:  # fmt: skip
    """The frozen current version to put in the paper, publishing the draft first; None when it cannot be."""
    question = await get_question(session, question_id)
    if question is None or question.tenant_id != tenant_id:
        return None
    if question.publication_status == "PUBLISHED" and question.current_version_id is not None:
        return question.current_version_id
    if question.publication_status != "DRAFT":
        return None
    try:
        task = await create_review_task(
            session, tenant_id=tenant_id, entity_type="QUESTION_VERSION", entity_id=version_id,
            queue_name="teacher-pdf", severity="NORMAL",
        )  # fmt: skip
        await decide_review_task(
            session, task.id, tenant_id=tenant_id, reviewer_id=teacher_id, decision="APPROVE", reason=REVIEW_REASON
        )
        published = await publish_question_version(
            session, version_id, approved_by=teacher_id, correlation_id=correlation_id, require_version_review=True
        )
    except (ReviewConflictError, QuestionConflictError, QuestionValidationFailedError, QuestionPublishBlocked):
        return None
    return published.version.id


def _fits_a_paper(raw: RawQuestion) -> bool:
    shape = _PAPER_SHAPES.get(raw.item_type)
    return shape is None or len([o for o in raw.options if str(o).strip()]) == len(shape)


async def create_paper_from_pdf(
    *,
    tenant_id: uuid.UUID,
    teacher_id: uuid.UUID,
    teacher_membership_id: uuid.UUID,
    tenant_wide: bool,
    raw_pdf: bytes,
    filename: str,
    title: str,
    subject_code: str,
    grade_band: str,
    duration_minutes: int,
    solver: AiSolver,
    class_id: uuid.UUID | None = None,
    starts_at: datetime | None = None,
    due_at: datetime | None = None,
    correlation_id: str = "teacher-pdf-paper",
) -> TeacherPaperResult:
    """See the module docstring. Raises `TeacherPaperError` (422), `AssessmentNotFoundError` (404: a class the
    teacher does not run), the publish gate errors of C04. Opens its own short transactions."""
    if duration_minutes not in DURATIONS_MINUTES:
        raise TeacherPaperError("INVALID_DURATION", f"duration_minutes must be one of {DURATIONS_MINUTES}")
    try:
        verified = inspect_uploaded_bytes(filename=filename, raw_bytes=raw_pdf)
    except FileSignatureError as exc:
        raise TeacherPaperError("INVALID_FILE", f"file validation failed: {exc}") from exc

    # phase 1: the class and the topics (short transaction)
    async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
        if (
            class_id is not None
            and not tenant_wide
            and not await can_manage_class(
                session, tenant_id=tenant_id, class_id=class_id, membership_id=teacher_membership_id
            )
        ):
            raise AssessmentNotFoundError(f"class {class_id} not found in tenant")
        # only the topics of the paper's own subject: a physics question is never tagged with a maths topic
        subject_root = subject_code.strip().upper()
        hints = await load_taxonomy_hints(session, root_path=subject_root if subject_root.isalnum() else None)

    # phase 2: read the PDF and solve its questions, no transaction open
    artifact = await asyncio.to_thread(
        lambda: extract_exam_structure_from_asset(mime_type=verified.mime_type, filename=filename, raw_bytes=raw_pdf)
    )
    drafts = [q for section in artifact.sections for q in section.questions]
    if not drafts:
        raise TeacherPaperError("NO_QUESTIONS_FOUND", "no question could be read from this file (Câu 1, Câu 2, ...)")
    # a PDF's questions are shown to the model as pictures too, and their figures are cut out to be shown to students
    pictures = (
        await asyncio.to_thread(pictures_for, raw_pdf, drafts)
        if verified.mime_type == "application/pdf"
        else [(None, ())] * len(drafts)
    )
    raws = [
        RawQuestion(
            item_type=d.item_type,
            stem_text=d.stem_md,
            options=d.options_md,
            source_answer=d.paired_answer,
            points=d.points,
            source_label=d.original_question_label,
            image_png=image,
            figure_pngs=figures,
        )
        for d, (image, figures) in zip(drafts, pictures, strict=True)
    ]
    fits = [_fits_a_paper(r) for r in raws]
    # a question this school already holds (same text, and its figure when this copy has one) is not solved again:
    # a short read, so that re-uploading a paper costs no AI call for what is already known
    known: dict[int, tuple[Any, Any]] = {}
    async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
        for index, (raw, ok) in enumerate(zip(raws, fits, strict=True)):
            if not ok or raw.item_type not in ("SINGLE_CHOICE", "CLUSTER_TRUE_FALSE", "SHORT_NUMERIC", "ESSAY"):
                continue
            found = await find_question_by_content(
                session,
                tenant_id=tenant_id,
                stem_text=raw.stem_text.strip(),
                options=tuple(OptionInput(key=k, text=t) for k, t in keyed_options(raw.item_type.upper(), raw.options)),
                needs_media=bool(raw.figure_pngs),
            )
            if found is not None and found[0].publication_status == "PUBLISHED":
                known[index] = found
    to_solve = [index for index, ok in enumerate(fits) if ok and index not in known]
    solved_by_index = dict(
        zip(
            to_solve,
            await solve_many(solver, [raws[i] for i in to_solve], hints, concurrency=SOLVE_CONCURRENCY),
            strict=True,
        )
    )
    prepared = [solved_by_index.get(i) for i in range(len(raws))]

    # phase 3: store, publish and assign, all or nothing
    paper_code = hashlib.sha256(raw_pdf).hexdigest()[:10].upper()
    async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
        try:
            registered = await submit_import(
                session, tenant_id=tenant_id, raw_stream=raw_pdf, filename=filename, mime_type=verified.mime_type,
                job_type="EXAM_IMPORT", title=title, correlation_id=correlation_id,
            )  # fmt: skip
        except IngestionValidationError as exc:
            raise TeacherPaperError("INVALID_FILE", str(exc)) from exc
        items: list[tuple[uuid.UUID, Decimal]] = []
        skipped = 0
        for n, (draft, item) in enumerate(zip(drafts, prepared, strict=True), start=1):
            if item is None and (n - 1) not in known:
                skipped += 1
                continue
            if item is None:
                question_id, version_id_known = known[n - 1][0].id, known[n - 1][1].id
                version_id = await _publish_for_paper(
                    session, tenant_id=tenant_id, teacher_id=teacher_id, question_id=question_id,
                    version_id=version_id_known, correlation_id=correlation_id,
                )  # fmt: skip
                if version_id is None or any(v == version_id for v, _ in items):
                    skipped += 1
                    continue
                items.append((version_id, Decimal(str(draft.points))))
                continue
            outcome = await store_prepared(
                session,
                prepared=item,
                tenant_id=tenant_id,
                created_by=teacher_id,
                stable_code=f"PDF-{paper_code}-Q{n:03d}",
                rights_status="OWNED",
                provenance={
                    "document_version_id": str(registered.document_version.id),
                    "page_number": draft.page_number,
                    "label": draft.original_question_label,
                    "bbox": draft.bbox_json,
                },
            )
            if outcome.ingested is None:
                skipped += 1
                continue
            version_id = await _publish_for_paper(
                session, tenant_id=tenant_id, teacher_id=teacher_id, question_id=outcome.ingested.question.id,
                version_id=outcome.ingested.version.id, correlation_id=correlation_id,
            )  # fmt: skip
            if version_id is None or any(v == version_id for v, _ in items):
                skipped += 1
                continue
            items.append((version_id, Decimal(str(draft.points))))
        if not items:
            raise TeacherPaperError(
                "NO_USABLE_QUESTION", f"none of the {len(drafts)} questions has a key that could be confirmed"
            )

        paper = await create_assessment(
            session, tenant_id=tenant_id, title=title, subject_code=subject_code, grade_band=grade_band,
            exam_type_code="TEACHER_PDF", rights_status="OWNED",
        )  # fmt: skip
        try:
            await assemble_from_question_versions(
                session, paper.id, items=items, framework_code=FRAMEWORK_CODE,
                random_seed=int(paper_code[:8], 16), duration_seconds=duration_minutes * 60,
                snapshot_framework_code=SNAPSHOT_FRAMEWORK_CODE,
            )  # fmt: skip
        except AssessmentConflictError as exc:
            raise TeacherPaperError(exc.code, str(exc)) from exc
        published = await publish_assessment(session, paper.id, published_by=teacher_id, correlation_id=correlation_id)

        assignment_id = None
        if class_id is not None:
            assignment = await create_assignment(
                session, tenant_id=tenant_id, assessment_version_id=published.version.id, assigned_by=teacher_id,
                class_id=class_id, available_from=starts_at, due_at=due_at, max_attempts=1,
            )  # fmt: skip
            assignment_id = assignment.id
    return TeacherPaperResult(
        assessment_id=paper.id,
        assessment_version_id=published.version.id,
        assignment_id=assignment_id,
        class_id=class_id,
        title=paper.title,
        question_count=len(items),
        skipped_question_count=skipped,
        duration_minutes=duration_minutes,
    )
