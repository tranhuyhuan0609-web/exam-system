"""DB application service for A04 complete exam import, provenance, and branded PDF generation."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Uuid, bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.enums import RightsStatus
from app.contracts.events import EventEnvelopeContract
from app.infrastructure.pdf_engine import PDFEngineAdapter, Rect, TextStamp, extend_page_top, stamp_pages
from app.infrastructure.storage import Storage, get_bronze_storage
from app.modules.assets.public import (
    AssetVisibility,
    VirusScanStatus,
    finalize_asset_upload,
    get_asset_by_id,
    initiate_asset_upload_session,
)
from app.modules.dedup.public import compute_question_hashes
from app.modules.exam_import.internal.models import ExamImportSession as ExamImportSessionModel
from app.modules.exam_import.internal.models import ProvenanceRecord
from app.modules.exam_import.internal.structure import (
    BannerCanvasPlan,
    ExamImportError,
    ExamImportStatus,
    ExamSectionDraft,
    ImportedQuestionRef,
    StimulusBlock,
    StructureValidationReport,
    approve_exam_import_session,
    compute_banner_canvas_adjustment,
    is_branded_pdf_allowed,
    validate_exam_import_structure,
)
from app.modules.exam_import.internal.structure import (
    ExamImportSession as CoreExamImportSession,
)
from app.modules.ingestion.public import ImportJobNotFoundError, get_import_job
from app.modules.outbox.public import enqueue_event
from app.modules.parsers.public import (
    ExtractionRunNotFoundError,
    ParserValidationError,
    get_extraction_run,
    get_extraction_run_structure,
    start_document_extraction,
)
from app.modules.questions.public import (
    OptionInput,
    QuestionValidationFailedError,
    create_question,
)
from app.platform.pagination import CursorError, clamp_limit, decode_cursor, encode_cursor

DEFAULT_PAGE_HEIGHT_PT = 842.0


class ExamImportServiceError(RuntimeError):
    """Base error for A04 DB operations."""


class ExamImportNotFoundError(ExamImportServiceError):
    """Raised when exam import session or run is missing under RLS (HTTP 404)."""


class ExamImportInvalidStateError(ExamImportServiceError):
    """Raised when modifying or approving an already resolved session (HTTP 409)."""


class BrandedPdfRightsDeniedError(PermissionError, ExamImportServiceError):
    """Raised when BRANDED_PDF generation is attempted for non-OWNED/ALLOWED_DERIVATIVE rights (HTTP 403)."""


class ExamImportValidationError(ValueError, ExamImportServiceError):
    """Raised when exam structure validation fails (HTTP 422)."""


@dataclass(frozen=True, slots=True)
class ProvenanceInput:
    page_no: int
    bbox_json: dict[str, Any] | list[Any] | None = None
    section_label: str | None = None
    original_question_label: str | None = None
    raw_snippet_text: str | None = None


@dataclass(frozen=True, slots=True)
class QuestionDraftInput:
    question_no: int
    points: float
    page_number: int = 1
    candidate_id: uuid.UUID | None = None
    stimulus_id: str | None = None
    paired_answer: str | None = None
    item_type: str = "SINGLE_CHOICE"
    stem_md: str | None = None
    options_md: tuple[str, ...] = ()
    bbox_json: dict[str, Any] | None = None
    original_question_label: str | None = None


@dataclass(frozen=True, slots=True)
class SectionDraftInput:
    section_code: str
    title: str
    questions: tuple[QuestionDraftInput, ...]


@dataclass(frozen=True, slots=True)
class StimulusInput:
    stimulus_id: str
    title: str
    page_number: int


@dataclass(frozen=True, slots=True)
class ProvenanceRecordView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    document_version_id: uuid.UUID
    extraction_run_id: uuid.UUID
    page_no: int
    bbox_json: dict[str, Any] | list[Any] | None
    section_label: str | None
    original_question_label: str | None
    raw_snippet_text: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ExamImportStructureView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    extraction_run_id: uuid.UUID
    document_version_id: uuid.UUID
    source_document_id: uuid.UUID
    rights_status: str
    status: str
    proposed_structure_json: dict[str, Any]
    validation: StructureValidationReport
    banner_plan: BannerCanvasPlan
    branded_pdf_asset_id: uuid.UUID | None
    provenance_records: tuple[ProvenanceRecordView, ...]
    created_at: datetime
    created_question_ids: tuple[uuid.UUID, ...] = ()


def _prov_to_view(row: ProvenanceRecord) -> ProvenanceRecordView:
    return ProvenanceRecordView(
        id=row.id,
        tenant_id=row.tenant_id,
        document_version_id=row.document_version_id,
        extraction_run_id=row.extraction_run_id,
        page_no=row.page_no,
        bbox_json=row.bbox_json,
        section_label=row.section_label,
        original_question_label=row.original_question_label,
        raw_snippet_text=row.raw_snippet_text,
        created_at=row.created_at,
    )


async def _resolve_doc_and_rights(
    session: AsyncSession,
    *,
    document_version_id: uuid.UUID,
) -> tuple[uuid.UUID, str, str | None]:
    row = (
        await session.execute(
            text(
                """
                SELECT dv.source_document_id, s.rights_status, sd.title
                FROM document_versions dv
                JOIN source_documents sd
                  ON sd.id = dv.source_document_id AND sd.tenant_id = dv.tenant_id
                JOIN sources s
                  ON s.id = sd.source_id AND s.tenant_id = sd.tenant_id
                WHERE dv.id = :doc_ver_id
                """
            ),
            {"doc_ver_id": document_version_id},
        )
    ).mappings().first()
    if row is None:
        raise ExamImportNotFoundError(f"document_version {document_version_id} not found")
    return (
        uuid.UUID(str(row["source_document_id"])),
        str(row["rights_status"]),
        str(row["title"]) if row["title"] else None,
    )


def _extract_pages_metadata_from_pdf(pdf_bytes: bytes | None) -> list[dict[str, Any]]:
    if pdf_bytes is None:
        return []
    try:
        with PDFEngineAdapter.open(pdf_bytes) as pdf:
            out: list[dict[str, Any]] = []
            for idx in range(pdf.page_count):
                w, h = pdf.page_size(idx)
                out.append(
                    {
                        "page_no": idx + 1,
                        "width_pt": round(float(w), 1),
                        "height_pt": round(float(h), 1),
                    }
                )
            return out
    except Exception:
        return []


async def _detect_banner_plan_from_pdf(
    pdf_bytes: bytes | None,
    *,
    first_item_y0_pt: float | None,
) -> BannerCanvasPlan:
    page_height = DEFAULT_PAGE_HEIGHT_PT
    detected_y0 = first_item_y0_pt

    if pdf_bytes is not None:
        with PDFEngineAdapter.open(pdf_bytes) as pdf:
            if pdf.page_count > 0:
                _, page_height = pdf.page_size(0)
                if detected_y0 is None:
                    lines = pdf.lines(0)
                    if lines:
                        detected_y0 = min(ln.rect.y0 for ln in lines)

    if detected_y0 is None:
        detected_y0 = 150.0
    try:
        return compute_banner_canvas_adjustment(
            page_height_pt=page_height,
            first_item_y0_pt=detected_y0,
        )
    except ExamImportError as exc:
        raise ExamImportValidationError(str(exc)) from exc


async def _load_raw_pdf_bytes(
    session: AsyncSession,
    *,
    raw_asset_id: uuid.UUID,
    storage: Storage,
) -> bytes | None:
    asset = await get_asset_by_id(session, asset_id=raw_asset_id)
    if asset.mime_type != "application/pdf":
        return None
    if not await storage.exists(asset.object_key):
        return None
    return await storage.get(asset.object_key)


def _core_session_from_json(
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    structure_json: dict[str, Any],
    db_status: str,
) -> CoreExamImportSession:
    raw_sections = structure_json.get("sections") or []
    sections: list[ExamSectionDraft] = []
    for sec in raw_sections:
        questions = tuple(
            ImportedQuestionRef(
                question_no=int(q["question_no"]),
                candidate_id=str(q.get("candidate_id") or ""),
                points=float(q["points"]),
                page_number=int(q.get("page_number", 1)),
                stimulus_id=q.get("stimulus_id"),
                paired_answer=q.get("paired_answer"),
            )
            for q in (sec.get("questions") or [])
        )
        sections.append(
            ExamSectionDraft(
                section_code=str(sec["section_code"]),
                title=str(sec["title"]),
                questions=questions,
            )
        )

    stimuli = tuple(
        StimulusBlock(
            stimulus_id=str(s["stimulus_id"]),
            title=str(s["title"]),
            page_number=int(s.get("page_number", 1)),
        )
        for s in (structure_json.get("stimuli") or [])
    )

    core_status = (
        ExamImportStatus.APPROVED
        if db_status == "APPROVED"
        else ExamImportStatus.REJECTED
        if db_status == "REJECTED"
        else ExamImportStatus.STRUCTURE_READY
    )
    return CoreExamImportSession(
        session_id=str(session_id),
        tenant_id=str(tenant_id),
        source_document_id=str(structure_json.get("source_document_id") or ""),
        rights_status=RightsStatus(str(structure_json.get("rights_status") or RightsStatus.PENDING_REVIEW.value)),
        sections=tuple(sections),
        stimuli=stimuli,
        expected_total_points=structure_json.get("expected_total_points", 10.0),
        status=core_status,
    )


def _banner_plan_from_json(raw: dict[str, Any] | None) -> BannerCanvasPlan:
    data = raw or {}
    return BannerCanvasPlan(
        original_page_height_pt=float(data.get("original_page_height_pt", DEFAULT_PAGE_HEIGHT_PT)),
        expanded_page_height_pt=float(data.get("expanded_page_height_pt", DEFAULT_PAGE_HEIGHT_PT)),
        top_expansion_pt=float(data.get("top_expansion_pt", 0.0)),
        shifted_first_item_y0_pt=float(data.get("shifted_first_item_y0_pt", 150.0)),
        header_expanded=bool(data.get("header_expanded", False)),
    )


def _to_structure_view(
    row: ExamImportSessionModel,
    provenances: Sequence[ProvenanceRecord],
) -> ExamImportStructureView:
    structure_json = dict(row.proposed_structure_json or {})
    core_session = _core_session_from_json(
        session_id=row.id,
        tenant_id=row.tenant_id,
        structure_json=structure_json,
        db_status=row.status,
    )
    report = validate_exam_import_structure(core_session, require_answer_keys=False)
    branded_raw = structure_json.get("branded_pdf_asset_id")
    branded_id = uuid.UUID(str(branded_raw)) if branded_raw else None
    raw_qids = structure_json.get("created_question_ids") or []
    created_qids: list[uuid.UUID] = []
    for raw_qid in raw_qids:
        try:
            created_qids.append(uuid.UUID(str(raw_qid)))
        except ValueError:
            continue

    return ExamImportStructureView(
        id=row.id,
        tenant_id=row.tenant_id,
        extraction_run_id=row.extraction_run_id,
        document_version_id=uuid.UUID(str(structure_json["document_version_id"])),
        source_document_id=uuid.UUID(str(structure_json["source_document_id"])),
        rights_status=str(structure_json.get("rights_status", RightsStatus.PENDING_REVIEW.value)),
        status=row.status,
        proposed_structure_json=structure_json,
        validation=report,
        banner_plan=_banner_plan_from_json(structure_json.get("banner_plan")),
        branded_pdf_asset_id=branded_id,
        provenance_records=tuple(_prov_to_view(p) for p in provenances),
        created_at=row.created_at,
        created_question_ids=tuple(created_qids),
    )


async def _serialize_sections_and_create_provenance(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    document_version_id: uuid.UUID,
    extraction_run_id: uuid.UUID,
    rights_status: str,
    sections: Sequence[SectionDraftInput],
    create_question_candidates: bool,
) -> list[dict[str, Any]]:
    serialized_sections: list[dict[str, Any]] = []
    for sec in sections:
        sec_questions: list[dict[str, Any]] = []
        for q in sec.questions:
            prov_row = ProvenanceRecord(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                document_version_id=document_version_id,
                extraction_run_id=extraction_run_id,
                page_no=q.page_number,
                bbox_json=q.bbox_json,
                section_label=sec.title[:120],
                original_question_label=(q.original_question_label or f"Cau {q.question_no}")[:60],
                raw_snippet_text=q.stem_md,
            )
            session.add(prov_row)
            await session.flush()

            candidate_id = q.candidate_id
            if candidate_id is None and create_question_candidates:
                candidate_id = uuid.uuid4()
                stem_text = q.stem_md or f"Cau {q.question_no}"
                hashes = compute_question_hashes(stem_text, q.options_md)
                payload = {
                    "question_no": q.question_no,
                    "stem_md": stem_text,
                    "options_md": list(q.options_md),
                    "paired_answer": q.paired_answer,
                }
                await session.execute(
                    text(
                        """
                        INSERT INTO question_candidates (
                            id, tenant_id, extraction_run_id, provenance_id,
                            acquisition_method, item_type, payload_json,
                            content_hash, structural_hash, rights_status
                        )
                        VALUES (
                            :id, :tenant_id, :run_id, :prov_id,
                            'DOCUMENT_IMPORT', :item_type, CAST(:payload_json AS jsonb),
                            :content_hash, :structural_hash, :rights_status
                        )
                        """
                    ),
                    {
                        "id": candidate_id,
                        "tenant_id": tenant_id,
                        "run_id": extraction_run_id,
                        "prov_id": prov_row.id,
                        "item_type": q.item_type,
                        "payload_json": json.dumps(payload),
                        "content_hash": hashes.content_hash,
                        "structural_hash": hashes.structural_hash,
                        "rights_status": rights_status,
                    },
                )

            sec_questions.append(
                {
                    "question_no": q.question_no,
                    "candidate_id": str(candidate_id or uuid.uuid4()),
                    "points": float(q.points),
                    "page_number": q.page_number,
                    "stimulus_id": q.stimulus_id,
                    "paired_answer": q.paired_answer,
                    "provenance_id": str(prov_row.id),
                    "item_type": q.item_type,
                    "stem_md": q.stem_md,
                    "options_md": list(q.options_md),
                    "bbox_json": q.bbox_json,
                    "original_question_label": q.original_question_label,
                }
            )
        serialized_sections.append(
            {
                "section_code": sec.section_code,
                "title": sec.title,
                "questions": sec_questions,
            }
        )
    return serialized_sections


async def create_exam_import_session(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    extraction_run_id: uuid.UUID,
    sections: Sequence[SectionDraftInput],
    stimuli: Sequence[StimulusInput] = (),
    expected_total_points: float | None = 10.0,
    rights_status_override: str | None = None,
    first_item_y0_pt: float | None = None,
    create_question_candidates: bool = True,
    correlation_id: str = "exam-import-create",
    storage: Storage | None = None,
) -> ExamImportStructureView:
    """Create ProvenanceRecords + ExamImportSession with header banner geometry detection."""
    try:
        run_ctx = await get_extraction_run(session, run_id=extraction_run_id)
    except ExtractionRunNotFoundError as exc:
        raise ExamImportNotFoundError(str(exc)) from exc

    source_doc_id, default_rights, doc_title = await _resolve_doc_and_rights(
        session,
        document_version_id=run_ctx.document_version_id,
    )
    resolved_rights = (
        RightsStatus(rights_status_override).value if rights_status_override else default_rights
    )

    active_storage = storage or get_bronze_storage()
    pdf_bytes = await _load_raw_pdf_bytes(session, raw_asset_id=run_ctx.raw_asset_id, storage=active_storage)
    banner_plan = await _detect_banner_plan_from_pdf(pdf_bytes, first_item_y0_pt=first_item_y0_pt)
    pages_meta = _extract_pages_metadata_from_pdf(pdf_bytes)

    serialized_sections = await _serialize_sections_and_create_provenance(
        session,
        tenant_id=tenant_id,
        document_version_id=run_ctx.document_version_id,
        extraction_run_id=extraction_run_id,
        rights_status=resolved_rights,
        sections=sections,
        create_question_candidates=create_question_candidates,
    )

    proposed_json: dict[str, Any] = {
        "source_document_id": str(source_doc_id),
        "document_version_id": str(run_ctx.document_version_id),
        "raw_asset_id": str(run_ctx.raw_asset_id),
        "document_title": doc_title,
        "pages": pages_meta,
        "rights_status": resolved_rights,
        "expected_total_points": expected_total_points,
        "sections": serialized_sections,
        "stimuli": [
            {
                "stimulus_id": s.stimulus_id,
                "title": s.title,
                "page_number": s.page_number,
            }
            for s in stimuli
        ],
        "banner_plan": {
            "original_page_height_pt": banner_plan.original_page_height_pt,
            "expanded_page_height_pt": banner_plan.expanded_page_height_pt,
            "top_expansion_pt": banner_plan.top_expansion_pt,
            "shifted_first_item_y0_pt": banner_plan.shifted_first_item_y0_pt,
            "header_expanded": banner_plan.header_expanded,
        },
        "branded_pdf_asset_id": None,
    }

    session_row = ExamImportSessionModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        extraction_run_id=extraction_run_id,
        proposed_structure_json=proposed_json,
        status="PENDING_REVIEW",
    )
    session.add(session_row)
    await session.flush()

    await enqueue_event(
        session,
        EventEnvelopeContract(
            event_type="exam_import.session_created",
            tenant_id=tenant_id,
            aggregate_type="exam_import_session",
            aggregate_id=session_row.id,
            correlation_id=correlation_id,
            payload={
                "exam_import_session_id": str(session_row.id),
                "extraction_run_id": str(extraction_run_id),
                "rights_status": resolved_rights,
                "header_expanded": banner_plan.header_expanded,
            },
        ),
    )
    await session.flush()

    provenances = (
        await session.execute(
            select(ProvenanceRecord)
            .where(ProvenanceRecord.extraction_run_id == extraction_run_id)
            .order_by(ProvenanceRecord.page_no, ProvenanceRecord.created_at)
        )
    ).scalars().all()
    return _to_structure_view(session_row, provenances)


async def get_exam_import_structure(
    session: AsyncSession,
    *,
    import_id: uuid.UUID,
    storage: Storage | None = None,
) -> ExamImportStructureView:
    """Fetch reconstructed exam structure and provenance records under current tenant RLS."""
    row = (
        await session.execute(
            select(ExamImportSessionModel).where(ExamImportSessionModel.id == import_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise ExamImportNotFoundError(f"exam import session {import_id} not found")

    structure_json = dict(row.proposed_structure_json or {})
    if not structure_json.get("pages") and structure_json.get("raw_asset_id"):
        try:
            active_storage = storage or get_bronze_storage()
            pdf_bytes = await _load_raw_pdf_bytes(
                session,
                raw_asset_id=uuid.UUID(str(structure_json["raw_asset_id"])),
                storage=active_storage,
            )
            pages_meta = _extract_pages_metadata_from_pdf(pdf_bytes)
            if pages_meta:
                structure_json["pages"] = pages_meta
                row.proposed_structure_json = structure_json
        except Exception:
            pass
    if not structure_json.get("document_title") and structure_json.get("document_version_id"):
        try:
            _, _, doc_title = await _resolve_doc_and_rights(
                session,
                document_version_id=uuid.UUID(str(structure_json["document_version_id"])),
            )
            if doc_title:
                structure_json["document_title"] = doc_title
                row.proposed_structure_json = structure_json
        except Exception:
            pass

    provenances = (
        await session.execute(
            select(ProvenanceRecord)
            .where(ProvenanceRecord.extraction_run_id == row.extraction_run_id)
            .order_by(ProvenanceRecord.page_no, ProvenanceRecord.created_at)
        )
    ).scalars().all()
    return _to_structure_view(row, provenances)


async def render_exam_import_page_png(
    session: AsyncSession,
    *,
    import_id: uuid.UUID,
    page_no: int,
    dpi: int = 150,
    storage: Storage | None = None,
) -> bytes:
    """Rasterize one page of the session's PDF asset as PNG under tenant RLS."""
    row = (
        await session.execute(
            select(ExamImportSessionModel).where(ExamImportSessionModel.id == import_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise ExamImportNotFoundError(f"exam import session {import_id} not found")
    if page_no < 1:
        raise ExamImportValidationError("page_no must be >= 1")

    structure_json = dict(row.proposed_structure_json or {})
    raw_asset_val = structure_json.get("raw_asset_id")
    if not raw_asset_val:
        run_ctx = await get_extraction_run(session, run_id=row.extraction_run_id)
        raw_asset_id = run_ctx.raw_asset_id
    else:
        raw_asset_id = uuid.UUID(str(raw_asset_val))

    active_storage = storage or get_bronze_storage()
    pdf_bytes = await _load_raw_pdf_bytes(session, raw_asset_id=raw_asset_id, storage=active_storage)
    if pdf_bytes is None:
        extracted = await get_extraction_run_structure(session, run_id=row.extraction_run_id, storage=active_storage)
        rendered_key = extracted.get("rendered_pdf_key")
        if isinstance(rendered_key, str) and rendered_key and await active_storage.exists(rendered_key):
            pdf_bytes = await active_storage.get(rendered_key)
    if pdf_bytes is None:
        raise ExamImportValidationError("Source asset is not a stored PDF document")

    clamped_dpi = max(72, min(300, dpi))
    with PDFEngineAdapter.open(pdf_bytes) as pdf:
        if page_no > pdf.page_count:
            raise ExamImportNotFoundError(f"page {page_no} not found in {pdf.page_count}-page PDF")
        return pdf.render_png(page_no - 1, dpi=float(clamped_dpi))


async def auto_extract_exam_import_structure(
    session: AsyncSession,
    *,
    import_id: uuid.UUID,
    storage: Storage | None = None,
) -> ExamImportStructureView:
    """Run deterministic parser extraction for a PENDING_REVIEW session and populate its sections."""
    row = (
        await session.execute(
            select(ExamImportSessionModel).where(ExamImportSessionModel.id == import_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise ExamImportNotFoundError(f"exam import session {import_id} not found")
    if row.status != "PENDING_REVIEW":
        raise ExamImportInvalidStateError(f"cannot modify exam import in status {row.status}")

    extracted = await get_extraction_run_structure(
        session,
        run_id=row.extraction_run_id,
        storage=storage,
    )
    raw_sections = extracted.get("sections") or []
    sections = tuple(
        SectionDraftInput(
            section_code=str(sec.get("section_code") or f"PART_{idx + 1}"),
            title=str(sec.get("title") or f"Phần {idx + 1}"),
            questions=tuple(
                QuestionDraftInput(
                    question_no=int(q.get("question_no") or q_idx + 1),
                    points=float(q.get("points") or 1.0),
                    page_number=int(q.get("page_number") or 1),
                    paired_answer=q.get("paired_answer"),
                    item_type=str(q.get("item_type") or "SINGLE_CHOICE"),
                    stem_md=q.get("stem_md"),
                    options_md=tuple(str(o) for o in (q.get("options_md") or [])),
                    bbox_json=q.get("bbox_json"),
                    original_question_label=q.get("original_question_label"),
                )
                for q_idx, q in enumerate(sec.get("questions") or [])
            ),
        )
        for idx, sec in enumerate(raw_sections)
    )
    if not sections:
        raise ExamImportValidationError(
            "Không tìm thấy văn bản câu hỏi tự động trong PDF (có thể là file scan ảnh chưa OCR)."
        )

    updated = dict(row.proposed_structure_json or {})
    if extracted.get("pages"):
        updated["pages"] = list(extracted["pages"])
        row.proposed_structure_json = updated
        await session.flush()

    computed_total = round(sum(q.points for sec in sections for q in sec.questions), 2)
    return await update_exam_import_structure(
        session,
        import_id=import_id,
        sections=sections,
        expected_total_points=computed_total if computed_total > 0 else None,
    )


async def update_exam_import_structure(
    session: AsyncSession,
    *,
    import_id: uuid.UUID,
    sections: Sequence[SectionDraftInput] | None = None,
    stimuli: Sequence[StimulusInput] | None = None,
    expected_total_points: float | None = None,
) -> ExamImportStructureView:
    """Update draft structure of a PENDING_REVIEW exam import session."""
    row = (
        await session.execute(
            select(ExamImportSessionModel).where(ExamImportSessionModel.id == import_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise ExamImportNotFoundError(f"exam import session {import_id} not found")
    if row.status != "PENDING_REVIEW":
        raise ExamImportInvalidStateError(f"cannot modify exam import in status {row.status}")

    updated = dict(row.proposed_structure_json or {})
    if sections is not None:
        doc_ver_id = uuid.UUID(str(updated["document_version_id"]))
        rights_status = str(updated.get("rights_status", RightsStatus.PENDING_REVIEW.value))
        updated["sections"] = await _serialize_sections_and_create_provenance(
            session,
            tenant_id=row.tenant_id,
            document_version_id=doc_ver_id,
            extraction_run_id=row.extraction_run_id,
            rights_status=rights_status,
            sections=sections,
            create_question_candidates=True,
        )
    if stimuli is not None:
        updated["stimuli"] = [
            {"stimulus_id": s.stimulus_id, "title": s.title, "page_number": s.page_number}
            for s in stimuli
        ]
    if expected_total_points is not None:
        updated["expected_total_points"] = expected_total_points

    row.proposed_structure_json = updated
    await session.flush()

    provenances = (
        await session.execute(
            select(ProvenanceRecord)
            .where(ProvenanceRecord.extraction_run_id == row.extraction_run_id)
            .order_by(ProvenanceRecord.page_no, ProvenanceRecord.created_at)
        )
    ).scalars().all()
    return _to_structure_view(row, provenances)


async def _build_and_store_branded_pdf(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    raw_asset_id: uuid.UUID,
    banner_plan: BannerCanvasPlan,
    banner_text: str,
    correlation_id: str,
    storage: Storage,
) -> uuid.UUID:
    pdf_bytes = await _load_raw_pdf_bytes(session, raw_asset_id=raw_asset_id, storage=storage)
    if pdf_bytes is None:
        raise ExamImportValidationError("BRANDED_PDF generation requires a stored source PDF asset")

    working_bytes = pdf_bytes
    if banner_plan.header_expanded:
        working_bytes = extend_page_top(working_bytes, 0, banner_plan.top_expansion_pt)

    def _build_stamps(page_idx: int, width: float, height: float) -> list[TextStamp]:
        banner_height = 42.0 if page_idx == 0 and banner_plan.header_expanded else 28.0
        return [
            TextStamp(
                text=banner_text,
                rect=Rect(28.0, 12.0, max(120.0, width - 28.0), 12.0 + banner_height),
                size=10.5,
                color=(0.08, 0.24, 0.56),
                align="left",
                background=(0.94, 0.96, 1.0),
            )
        ]

    branded_bytes = stamp_pages(working_bytes, _build_stamps)
    sha256 = hashlib.sha256(branded_bytes).hexdigest()

    init_res = await initiate_asset_upload_session(
        session,
        tenant_id=tenant_id,
        mime_type="application/pdf",
        sha256=sha256,
        size_bytes=len(branded_bytes),
        filename=f"branded_{raw_asset_id}.pdf",
        asset_role="BRANDED_PDF",
        requires_pro=False,
        visibility=AssetVisibility.TENANT_PRIVATE,
        deduplicate=False,
    )
    fin_res = await finalize_asset_upload(
        session,
        tenant_id=tenant_id,
        asset_id=init_res.asset.id,
        observed_sha256=sha256,
        observed_mime_type="application/pdf",
        observed_size_bytes=len(branded_bytes),
        scan_status=VirusScanStatus.CLEAN,
        raw_bytes=branded_bytes,
        correlation_id=correlation_id,
        storage=storage,
    )
    return fin_res.asset.id


def _parse_cluster_tf_booleans(raw_answer: str | None) -> dict[str, bool] | None:
    if not raw_answer:
        return None
    cleaned = [ch.upper() for ch in raw_answer.strip() if ch.upper() in {"T", "F", "D", "Đ", "S", "1", "0"}]
    if len(cleaned) != 4:
        return None
    true_chars = {"T", "D", "Đ", "1"}
    return {key: (cleaned[idx] in true_chars) for idx, key in enumerate(("a", "b", "c", "d"))}


def _build_question_bank_args(
    *,
    import_id: uuid.UUID,
    section_idx: int,
    q: dict[str, Any],
) -> tuple[str, str, str, tuple[OptionInput, ...], dict[str, Any]]:
    q_no = int(q.get("question_no") or 1)
    raw_type = str(q.get("item_type") or "SINGLE_CHOICE").upper()
    stem_text = str(q.get("stem_md") or "").strip()
    if not stem_text:
        raise ExamImportValidationError(f"Câu {q_no} đang trống nội dung câu hỏi.")

    stable_code = f"IMP-{str(import_id)[:8].upper()}-S{section_idx + 1}-Q{q_no}"
    paired = str(q.get("paired_answer") or "").strip()

    if raw_type == "SINGLE_CHOICE":
        raw_opts = [str(o).strip() for o in (q.get("options_md") or []) if str(o).strip()]
        keys = ("A", "B", "C", "D", "E", "F", "G", "H")
        if len(raw_opts) < 2:
            raise ExamImportValidationError(
                f"Câu {q_no} (SINGLE_CHOICE) cần tối thiểu 2 lựa chọn trước khi lưu vào Ngân hàng câu hỏi."
            )
        options = tuple(OptionInput(key=keys[i], text=opt) for i, opt in enumerate(raw_opts[: len(keys)]))
        valid_keys = {opt.key for opt in options}
        ans = paired.upper()
        if ans not in valid_keys:
            raise ExamImportValidationError(
                f"Câu {q_no} (SINGLE_CHOICE) chưa có đáp án đúng hợp lệ ({', '.join(sorted(valid_keys))})."
            )
        return stable_code, "SINGLE_CHOICE", stem_text, options, {"correct_option": ans}

    if raw_type == "CLUSTER_TRUE_FALSE":
        raw_opts = [str(o).strip() for o in (q.get("options_md") or []) if str(o).strip()]
        if len(raw_opts) != 4:
            raise ExamImportValidationError(
                f"Câu {q_no} (CLUSTER_TRUE_FALSE) cần đúng 4 ý a, b, c, d."
            )
        tf_keys = ("a", "b", "c", "d")
        options = tuple(OptionInput(key=tf_keys[i], text=raw_opts[i]) for i in range(4))
        statements = _parse_cluster_tf_booleans(paired)
        if statements is None:
            raise ExamImportValidationError(
                f"Câu {q_no} (CLUSTER_TRUE_FALSE) cần chuỗi đáp án 4 ý dạng ĐSĐS hoặc TFTT."
            )
        return stable_code, "CLUSTER_TRUE_FALSE", stem_text, options, {"statements": statements}

    if raw_type == "SHORT_NUMERIC":
        normalized = paired.replace(",", ".")
        if not normalized:
            raise ExamImportValidationError(f"Câu {q_no} (SHORT_NUMERIC) chưa có đáp án số.")
        return stable_code, "SHORT_NUMERIC", stem_text, (), {"value": normalized}

    # ESSAY fallback
    points = float(q.get("points") or 1.0)
    rubric = paired or "Chấm theo hướng dẫn chấm của giáo viên."
    return stable_code, "ESSAY", stem_text, (), {"rubric": rubric, "max_points": max(0.25, points)}


async def approve_exam_import(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    import_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    require_answer_keys: bool = True,
    generate_branded_pdf: bool = False,
    banner_text: str = "H2Edu - Hoc thong minh - Thi but pha",
    promote_to_question_bank: bool = False,
    correlation_id: str = "exam-import-approve",
    storage: Storage | None = None,
) -> ExamImportStructureView:
    """Validate structure, enforce rights gate on BRANDED_PDF, optionally promote to Question Bank, and approve."""
    row = (
        await session.execute(
            select(ExamImportSessionModel).where(ExamImportSessionModel.id == import_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise ExamImportNotFoundError(f"exam import session {import_id} not found")
    if row.status != "PENDING_REVIEW":
        raise ExamImportInvalidStateError(f"exam import session {import_id} is already {row.status}")

    structure_json = dict(row.proposed_structure_json or {})
    rights = RightsStatus(str(structure_json.get("rights_status") or RightsStatus.PENDING_REVIEW.value))
    if generate_branded_pdf and not is_branded_pdf_allowed(rights):
        raise BrandedPdfRightsDeniedError(
            f"BRANDED_PDF is only allowed for OWNED or ALLOWED_DERIVATIVE, got {rights.value}"
        )

    core_session = _core_session_from_json(
        session_id=row.id,
        tenant_id=row.tenant_id,
        structure_json=structure_json,
        db_status=row.status,
    )
    try:
        approved_core, _report = approve_exam_import_session(
            core_session,
            require_answer_keys=require_answer_keys,
        )
    except ExamImportError as exc:
        raise ExamImportValidationError(str(exc)) from exc

    created_question_ids: list[str] = [str(qid) for qid in (structure_json.get("created_question_ids") or [])]
    if promote_to_question_bank:
        if actor_id is None:
            raise ExamImportValidationError("Cần định danh người duyệt để lưu câu hỏi vào Ngân hàng câu hỏi.")
        updated_sections: list[dict[str, Any]] = []
        for sec_idx, raw_sec in enumerate(structure_json.get("sections") or []):
            sec_copy = dict(raw_sec)
            sec_questions: list[dict[str, Any]] = []
            for raw_q in raw_sec.get("questions") or []:
                q_copy = dict(raw_q)
                if q_copy.get("question_id"):
                    existing_qid = str(q_copy["question_id"])
                    if existing_qid not in created_question_ids:
                        created_question_ids.append(existing_qid)
                    sec_questions.append(q_copy)
                    continue

                stable_code, item_type, stem_text, options, answer_schema = _build_question_bank_args(
                    import_id=row.id,
                    section_idx=sec_idx,
                    q=q_copy,
                )
                try:
                    created_q, _ = await create_question(
                        session,
                        tenant_id=tenant_id,
                        created_by=actor_id,
                        stable_code=stable_code,
                        item_type=item_type,
                        stem_text=stem_text,
                        answer_schema=answer_schema,
                        options=options,
                        rights_status=rights.value,
                    )
                except QuestionValidationFailedError as exc:
                    raise ExamImportValidationError(
                        f"Câu {q_copy.get('question_no')}: {exc}"
                    ) from exc

                q_copy["question_id"] = str(created_q.id)
                created_question_ids.append(str(created_q.id))
                sec_questions.append(q_copy)
            sec_copy["questions"] = sec_questions
            updated_sections.append(sec_copy)
        structure_json["sections"] = updated_sections
        structure_json["created_question_ids"] = created_question_ids

    banner_plan = _banner_plan_from_json(structure_json.get("banner_plan"))
    branded_asset_id: uuid.UUID | None = None
    if generate_branded_pdf:
        active_storage = storage or get_bronze_storage()
        branded_asset_id = await _build_and_store_branded_pdf(
            session,
            tenant_id=tenant_id,
            raw_asset_id=uuid.UUID(str(structure_json["raw_asset_id"])),
            banner_plan=banner_plan,
            banner_text=banner_text,
            correlation_id=correlation_id,
            storage=active_storage,
        )

    structure_json["branded_pdf_asset_id"] = str(branded_asset_id) if branded_asset_id else None
    structure_json["approved_at"] = datetime.now(UTC).isoformat()
    structure_json["approved_by"] = str(actor_id) if actor_id else None

    row.proposed_structure_json = structure_json
    row.status = approved_core.status.value
    await session.flush()

    await enqueue_event(
        session,
        EventEnvelopeContract(
            event_type="exam_import.approved",
            tenant_id=tenant_id,
            aggregate_type="exam_import_session",
            aggregate_id=row.id,
            correlation_id=correlation_id,
            payload={
                "exam_import_session_id": str(row.id),
                "extraction_run_id": str(row.extraction_run_id),
                "rights_status": rights.value,
                "branded_pdf_asset_id": str(branded_asset_id) if branded_asset_id else None,
            },
        ),
    )
    await session.flush()

    provenances = (
        await session.execute(
            select(ProvenanceRecord)
            .where(ProvenanceRecord.extraction_run_id == row.extraction_run_id)
            .order_by(ProvenanceRecord.page_no, ProvenanceRecord.created_at)
        )
    ).scalars().all()
    return _to_structure_view(row, provenances)


_LIST_EXAM_IMPORTS_ROUTE = "GET /api/v1/exam-imports"
EXAM_IMPORT_STATUSES = ("PENDING_REVIEW", "APPROVED", "REJECTED")


@dataclass(frozen=True, slots=True)
class ExamImportListItem:
    """One review session of a list: what to open at `/studio/imports/{id}/review`, and its state."""

    id: uuid.UUID
    extraction_run_id: uuid.UUID
    document_version_id: uuid.UUID | None
    title: str | None
    mime_type: str | None
    page_count: int | None
    status: str
    rights_status: str
    section_count: int
    question_count: int
    acquisition_job_id: uuid.UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ExamImportPage:
    items: tuple[ExamImportListItem, ...]
    next_cursor: str | None
    has_more: bool
    limit: int


_REVIEWABLE_JOB_STATUSES = frozenset({"QUEUED", "RUNNING", "COMPLETED"})


async def open_review_for_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    acquisition_job_id: uuid.UUID,
    expected_total_points: float | None = 10.0,
    correlation_id: str = "exam-import-from-job",
) -> tuple[ExamImportStructureView, bool]:
    """Bridge A01 -> A05 -> A04: the review session of an import job, created on the first call.

    A job of `POST /api/v1/imports` has a document version but no extraction run yet. This starts the run (parser
    profile chosen from the file) and opens an empty `PENDING_REVIEW` session on it, so the reviewer can draw the
    questions by hand on `/studio/imports/{id}/review`. Idempotent: the session already opened for the job (or for
    another job of the same document version) is returned with `created=False`.
    """
    try:
        job = await get_import_job(session, tenant_id=tenant_id, job_id=acquisition_job_id)
    except ImportJobNotFoundError as exc:
        raise ExamImportNotFoundError(str(exc)) from exc
    doc = job.document_version
    if doc is None:
        raise ExamImportValidationError(f"import job {acquisition_job_id} has no document yet")
    if job.job_status not in _REVIEWABLE_JOB_STATUSES:
        raise ExamImportInvalidStateError(f"import job {acquisition_job_id} is {job.job_status}: nothing to review")

    # Two reviewers opening the same job at once must end with one session.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"exam-import-job:{acquisition_job_id}"}
    )
    existing = (
        await session.execute(
            text(
                "SELECT es.id FROM exam_import_sessions es "
                "JOIN extraction_runs er ON er.id = es.extraction_run_id AND er.tenant_id = es.tenant_id "
                "WHERE es.tenant_id = :tid AND (er.acquisition_job_id = :jid OR er.document_version_id = :dvid) "
                "ORDER BY es.created_at DESC LIMIT 1"
            ),
            {"tid": tenant_id, "jid": acquisition_job_id, "dvid": doc.id},
        )
    ).scalar_one_or_none()
    if existing is not None:
        return await get_exam_import_structure(session, import_id=existing), False

    run_id = (
        await session.execute(
            text(
                "SELECT id FROM extraction_runs WHERE tenant_id = :tid AND document_version_id = :dvid "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"tid": tenant_id, "dvid": doc.id},
        )
    ).scalar_one_or_none()
    if run_id is None:
        try:
            run = await start_document_extraction(
                session,
                tenant_id=tenant_id,
                document_id=doc.source_document_id,
                document_version_id=doc.id,
                acquisition_job_id=acquisition_job_id,
                correlation_id=correlation_id,
            )
        except ParserValidationError as exc:
            raise ExamImportValidationError(str(exc)) from exc
        run_id = run.id

    view = await create_exam_import_session(
        session,
        tenant_id=tenant_id,
        extraction_run_id=run_id,
        sections=(),
        expected_total_points=expected_total_points,
        correlation_id=correlation_id,
    )
    return view, True


async def list_exam_imports(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    cursor_secret: str,
    status: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> ExamImportPage:
    """The tenant's exam-import review sessions, newest first (cursor on created_at, id); no structure is returned."""
    safe_limit = clamp_limit(limit)
    if status is not None and status not in EXAM_IMPORT_STATUSES:
        raise ExamImportValidationError(f"unsupported status: {status!r}")
    after_at: datetime | None = None
    after_id: uuid.UUID | None = None
    if cursor is not None:
        decoded = decode_cursor(
            cursor,
            secret=cursor_secret,
            allowed_sort_fields=frozenset({"created_at"}),
            expected_sort_field="created_at",
            expected_direction="desc",
            tenant_id=str(tenant_id),
            route=_LIST_EXAM_IMPORTS_ROUTE,
        )
        try:
            after_at = datetime.fromisoformat(str(decoded.sort_value))
            after_id = uuid.UUID(decoded.tie_id)
        except ValueError as exc:
            raise CursorError("malformed cursor payload") from exc
    rows = (
        (
            await session.execute(
                text(
                    """
                    SELECT s.id, s.extraction_run_id, s.status, s.created_at,
                           NULLIF(s.proposed_structure_json->>'document_version_id', '')::uuid AS document_version_id,
                           COALESCE(s.proposed_structure_json->>'rights_status', 'PENDING_REVIEW') AS rights_status,
                           sd.title, sd.mime_type, dv.page_count, er.acquisition_job_id,
                           jsonb_array_length(COALESCE(s.proposed_structure_json->'sections', '[]'::jsonb))
                               AS section_count,
                           (SELECT count(*)
                              FROM jsonb_array_elements(COALESCE(s.proposed_structure_json->'sections', '[]'::jsonb))
                                   AS sec(v),
                                   jsonb_array_elements(COALESCE(sec.v->'questions', '[]'::jsonb)) AS q(v)
                           ) AS question_count
                    FROM exam_import_sessions s
                    LEFT JOIN extraction_runs er ON er.id = s.extraction_run_id AND er.tenant_id = s.tenant_id
                    LEFT JOIN document_versions dv
                      ON dv.id = NULLIF(s.proposed_structure_json->>'document_version_id', '')::uuid
                     AND dv.tenant_id = s.tenant_id
                    LEFT JOIN source_documents sd
                      ON sd.id = dv.source_document_id AND sd.tenant_id = dv.tenant_id
                    WHERE s.tenant_id = :tid
                      AND (CAST(:status AS varchar) IS NULL OR s.status = :status)
                      AND (CAST(:after_at AS timestamptz) IS NULL
                           OR s.created_at < :after_at OR (s.created_at = :after_at AND s.id < :after_id))
                    ORDER BY s.created_at DESC, s.id DESC
                    LIMIT :n
                    """
                ).bindparams(
                    bindparam("after_at", type_=DateTime(timezone=True)),
                    bindparam("after_id", type_=Uuid()),
                ),
                {
                    "tid": tenant_id,
                    "status": status,
                    "after_at": after_at,
                    "after_id": after_id,
                    "n": safe_limit + 1,
                },
            )
        )
        .mappings()
        .all()
    )
    has_more = len(rows) > safe_limit
    items = tuple(
        ExamImportListItem(
            id=r["id"],
            extraction_run_id=r["extraction_run_id"],
            document_version_id=r["document_version_id"],
            title=r["title"],
            mime_type=r["mime_type"],
            page_count=r["page_count"],
            status=r["status"],
            rights_status=r["rights_status"],
            section_count=int(r["section_count"]),
            question_count=int(r["question_count"]),
            acquisition_job_id=r["acquisition_job_id"],
            created_at=r["created_at"],
        )
        for r in rows[:safe_limit]
    )
    next_cursor: str | None = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor(
            sort_field="created_at",
            sort_value=last.created_at.isoformat(),
            tie_id=str(last.id),
            direction="desc",
            secret=cursor_secret,
            tenant_id=str(tenant_id),
            route=_LIST_EXAM_IMPORTS_ROUTE,
        )
    return ExamImportPage(items=items, next_cursor=next_cursor, has_more=has_more, limit=safe_limit)
