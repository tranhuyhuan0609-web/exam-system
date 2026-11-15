"""Database service layer for E03 AI Question Generation (grounded anchors, C02 -> A09, 0% direct publish)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.enums import (
    AcquisitionMethod,
    PublicationStatus,
    QuestionItemType,
    RightsStatus,
    VerificationStatus,
)
from app.contracts.event_catalog import (
    QuestionCandidateCreatedPayload,
    QuestionReviewRequiredPayload,
    build_catalog_envelope,
)
from app.modules.dedup.public import content_hash, structural_hash
from app.modules.generation.internal.gap_filler import (
    ALLOWED_ANCHOR_RIGHTS,
    GeneratedCandidate,
    GenerationPolicyError,
    GenerationRequest,
    GenerationStatus,
    MatrixGapCell,
    SourceAnchor,
    approve_and_publish_ai_candidate,
    create_gap_fill_request,
    record_sympy_verification_and_queue_review,
    register_generated_candidate,
)
from app.modules.generation.internal.models import GenerationRequestRecord
from app.modules.outbox.public import enqueue
from app.modules.quality.public import AnswerCheck, verify_numeric_answer

ALLOWED_DIFFICULTIES: frozenset[str] = frozenset({"NB", "TH", "VD", "VDC"})
NUMERIC_VERIFIER = Callable[[str, str, Decimal], AnswerCheck]


class GenerationValidationError(ValueError):
    """Raised when E03 generation request inputs fail validation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class GenerationNotFoundError(LookupError):
    """Raised when a generation request or candidate is missing in tenant scope."""


class GenerationPolicyViolationError(PermissionError):
    """Raised when anchor grounding, C02 verification, or 0% direct-publish gates reject an action."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AnchorInput:
    """Request-level grounded anchor reference mapped onto a verified DB source or question."""

    anchor_id: str
    document_version_id: uuid.UUID
    excerpt: str
    publication_status: PublicationStatus = PublicationStatus.PUBLISHED
    rights_status: RightsStatus = RightsStatus.OWNED
    owner_tenant_id: uuid.UUID | None = None
    source_question_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class GenerationRequestView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    requested_by: uuid.UUID
    taxonomy_node_id: uuid.UUID
    target_difficulty: str
    status: str
    generated_candidate_id: uuid.UUID | None
    acquisition_method: str | None
    verification_status: str | None
    candidate_status: str | None
    publication_status: str
    review_task_id: uuid.UUID | None
    source_anchors_json: list[dict[str, Any]]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GenerationPublishView:
    generation_request_id: uuid.UUID
    candidate_id: uuid.UUID
    review_task_id: uuid.UUID
    review_decision_id: uuid.UUID
    question_id: uuid.UUID
    question_version_id: uuid.UUID
    acquisition_method: str
    verification_status: str
    publication_status: str
    status: str


def _resolve_rights_status(anchors: Sequence[SourceAnchor]) -> RightsStatus:
    rights = [a.rights_status for a in anchors]
    for candidate_right in (RightsStatus.OWNED, RightsStatus.LICENSED, RightsStatus.ALLOWED_DERIVATIVE):
        if all(r == candidate_right for r in rights):
            return candidate_right
    return RightsStatus.ALLOWED_DERIVATIVE


async def _verify_anchors_in_db(
    session: AsyncSession,
    *,
    anchors: Sequence[AnchorInput],
) -> None:
    """Confirm cited document_version/question anchors exist inside visible tenant/SYSTEM_PUBLIC RLS."""
    for anchor in anchors:
        if anchor.source_question_id is not None:
            q_row = (
                await session.execute(
                    text(
                        "SELECT id, publication_status, rights_status "
                        "FROM questions WHERE id = :qid"
                    ),
                    {"qid": anchor.source_question_id},
                )
            ).mappings().one_or_none()
            if q_row is None:
                raise GenerationPolicyViolationError(
                    "UNGROUNDED_ANCHOR",
                    f"Anchor {anchor.anchor_id!r} references missing or cross-tenant question",
                )
            if q_row["publication_status"] != "PUBLISHED":
                raise GenerationPolicyViolationError(
                    "ANCHOR_NOT_PUBLISHED",
                    f"Anchor {anchor.anchor_id!r} source question is not PUBLISHED",
                )
            if RightsStatus(q_row["rights_status"]) not in ALLOWED_ANCHOR_RIGHTS:
                raise GenerationPolicyViolationError(
                    "ANCHOR_RIGHTS_RESTRICTED",
                    f"Anchor {anchor.anchor_id!r} rights_status={q_row['rights_status']} is not cleared",
                )
            continue

        doc_row = (
            await session.execute(
                text(
                    "SELECT dv.id, sd.rights_status "
                    "FROM document_versions dv "
                    "JOIN source_documents sd "
                    "  ON sd.id = dv.source_document_id AND sd.tenant_id = dv.tenant_id "
                    "WHERE dv.id = :dv_id"
                ),
                {"dv_id": anchor.document_version_id},
            )
        ).mappings().one_or_none()
        if doc_row is None:
            raise GenerationPolicyViolationError(
                "UNGROUNDED_ANCHOR",
                f"Anchor {anchor.anchor_id!r} references missing or cross-tenant document_version",
            )
        if RightsStatus(doc_row["rights_status"]) not in ALLOWED_ANCHOR_RIGHTS:
            raise GenerationPolicyViolationError(
                "ANCHOR_RIGHTS_RESTRICTED",
                f"Anchor {anchor.anchor_id!r} rights_status={doc_row['rights_status']} is not cleared",
            )


async def _load_generation_view(
    session: AsyncSession,
    record: GenerationRequestRecord,
) -> GenerationRequestView:
    acquisition_method: str | None = None
    verification_status: str | None = None
    candidate_status: str | None = None
    publication_status = PublicationStatus.DRAFT.value
    review_task_id: uuid.UUID | None = None

    if record.generated_candidate_id is not None:
        cand_row = (
            await session.execute(
                text(
                    "SELECT acquisition_method, verification_status, candidate_status, "
                    "promoted_question_version_id "
                    "FROM question_candidates WHERE id = :cid AND tenant_id = :tenant_id"
                ),
                {"cid": record.generated_candidate_id, "tenant_id": record.tenant_id},
            )
        ).mappings().one_or_none()
        if cand_row is not None:
            acquisition_method = str(cand_row["acquisition_method"])
            verification_status = str(cand_row["verification_status"])
            candidate_status = str(cand_row["candidate_status"])
            if cand_row["promoted_question_version_id"] is not None:
                q_row = (
                    await session.execute(
                        text(
                            "SELECT q.publication_status FROM questions q "
                            "JOIN question_versions qv ON qv.question_id = q.id AND qv.tenant_id = q.tenant_id "
                            "WHERE qv.id = :qvid AND q.tenant_id = :tenant_id"
                        ),
                        {"qvid": cand_row["promoted_question_version_id"], "tenant_id": record.tenant_id},
                    )
                ).mappings().one_or_none()
                if q_row is not None:
                    publication_status = str(q_row["publication_status"])

        rt_row = (
            await session.execute(
                text(
                    "SELECT id FROM review_tasks "
                    "WHERE entity_type = 'QUESTION_CANDIDATE' AND entity_id = :cid AND tenant_id = :tenant_id "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"cid": record.generated_candidate_id, "tenant_id": record.tenant_id},
            )
        ).mappings().one_or_none()
        if rt_row is not None:
            review_task_id = rt_row["id"]

    anchors_list = list(record.source_anchors_json) if isinstance(record.source_anchors_json, list) else []
    return GenerationRequestView(
        id=record.id,
        tenant_id=record.tenant_id,
        requested_by=record.requested_by,
        taxonomy_node_id=record.taxonomy_node_id,
        target_difficulty=record.target_difficulty,
        status=record.status,
        generated_candidate_id=record.generated_candidate_id,
        acquisition_method=acquisition_method,
        verification_status=verification_status,
        candidate_status=candidate_status,
        publication_status=publication_status,
        review_task_id=review_task_id,
        source_anchors_json=anchors_list,
        created_at=record.created_at,
    )


def _core_request_and_candidate(
    *,
    record: GenerationRequestRecord,
    item_type: QuestionItemType,
    stem: str,
    candidate_id: uuid.UUID,
    verification_status: VerificationStatus,
) -> GeneratedCandidate:
    anchors_raw = record.source_anchors_json if isinstance(record.source_anchors_json, list) else []
    anchors = tuple(
        SourceAnchor(
            anchor_id=str(item["anchor_id"]),
            document_version_id=str(item["document_version_id"]),
            excerpt=str(item["excerpt"]),
            publication_status=PublicationStatus(item["publication_status"]),
            rights_status=RightsStatus(item["rights_status"]),
            owner_tenant_id=str(item["owner_tenant_id"]) if item.get("owner_tenant_id") else None,
        )
        for item in anchors_raw
    )
    req = create_gap_fill_request(
        request_id=str(record.id),
        tenant_id=str(record.tenant_id),
        cell=MatrixGapCell(
            taxonomy_node_id=str(record.taxonomy_node_id),
            difficulty=record.target_difficulty,
            question_type=item_type,
            missing_count=1,
        ),
        anchors=anchors,
        prompt_version="e03_persisted",
    )
    cand = register_generated_candidate(
        req,
        candidate_id=str(candidate_id),
        stem=stem,
        used_anchor_ids=tuple(a.anchor_id for a in anchors),
    )
    if record.status == GenerationStatus.QUEUED_FOR_REVIEW.value:
        cand = record_sympy_verification_and_queue_review(
            cand,
            verification_status=verification_status,
        )
    return cand


async def create_ai_generation(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    requested_by: uuid.UUID,
    taxonomy_node_id: uuid.UUID,
    target_difficulty: str,
    question_type: QuestionItemType,
    missing_count: int,
    prompt_version: str,
    anchors: Sequence[AnchorInput],
    draft_stem: str,
    computed_expression: str | None = None,
    source_answer: str | None = None,
    verify_with_c02: bool = True,
    require_db_anchors: bool = True,
    verifier: NUMERIC_VERIFIER | None = None,
    requested_publication_status: str | None = None,
    skip_review: bool = False,
) -> GenerationRequestView:
    """Create a grounded E03 generation request, force DRAFT+AI_GENERATED, and queue C02/A09 review."""
    if skip_review or (
        requested_publication_status is not None
        and requested_publication_status.strip().upper() == PublicationStatus.PUBLISHED.value
    ):
        raise GenerationPolicyViolationError(
            "AI_DIRECT_PUBLISH_FORBIDDEN",
            "AI-generated questions cannot bypass C02 verification and A09 human review",
        )
    normalized_difficulty = target_difficulty.strip().upper()
    if normalized_difficulty not in ALLOWED_DIFFICULTIES:
        raise GenerationValidationError(
            "INVALID_DIFFICULTY",
            f"target_difficulty must be one of {sorted(ALLOWED_DIFFICULTIES)}",
        )
    if not draft_stem.strip():
        raise GenerationValidationError("INVALID_STEM", "draft_stem must not be empty")

    tax_row = (
        await session.execute(
            text("SELECT id FROM taxonomy_nodes WHERE id = :tid"),
            {"tid": taxonomy_node_id},
        )
    ).one_or_none()
    if tax_row is None:
        raise GenerationNotFoundError(f"Taxonomy node {taxonomy_node_id} not found or not visible")

    request_id = uuid.uuid4()
    core_anchors = tuple(
        SourceAnchor(
            anchor_id=a.anchor_id,
            document_version_id=str(a.document_version_id),
            excerpt=a.excerpt,
            publication_status=a.publication_status,
            rights_status=a.rights_status,
            owner_tenant_id=str(a.owner_tenant_id) if a.owner_tenant_id is not None else None,
        )
        for a in anchors
    )
    cell = MatrixGapCell(
        taxonomy_node_id=str(taxonomy_node_id),
        difficulty=normalized_difficulty,
        question_type=question_type,
        missing_count=missing_count,
    )
    try:
        core_req: GenerationRequest = create_gap_fill_request(
            request_id=str(request_id),
            tenant_id=str(tenant_id),
            cell=cell,
            anchors=core_anchors,
            prompt_version=prompt_version,
        )
    except GenerationPolicyError as exc:
        raise GenerationPolicyViolationError("GENERATION_GROUNDING_FAILED", str(exc)) from exc

    if require_db_anchors:
        await _verify_anchors_in_db(session, anchors=anchors)

    candidate_id = uuid.uuid4()
    try:
        core_cand = register_generated_candidate(
            core_req,
            candidate_id=str(candidate_id),
            stem=draft_stem,
            used_anchor_ids=tuple(a.anchor_id for a in core_anchors),
        )
    except GenerationPolicyError as exc:
        raise GenerationPolicyViolationError("CANDIDATE_POLICY_FAILED", str(exc)) from exc

    c_hash = content_hash(core_cand.stem)
    s_hash = structural_hash(core_cand.stem)
    cand_rights = _resolve_rights_status(core_anchors)
    anchors_json = [
        {
            "anchor_id": a.anchor_id,
            "document_version_id": str(a.document_version_id),
            "excerpt": a.excerpt,
            "publication_status": a.publication_status.value,
            "rights_status": a.rights_status.value,
            "owner_tenant_id": str(a.owner_tenant_id) if a.owner_tenant_id is not None else None,
            "source_question_id": str(a.source_question_id) if a.source_question_id is not None else None,
        }
        for a in anchors
    ]
    payload_json = {
        "stem": core_cand.stem,
        "target_difficulty": normalized_difficulty,
        "taxonomy_node_id": str(taxonomy_node_id),
        "prompt_version": prompt_version.strip(),
        "anchor_ids": list(core_cand.anchor_ids),
        "publication_status": PublicationStatus.DRAFT.value,
    }

    await session.execute(
        text(
            """
            INSERT INTO question_candidates (
                id, tenant_id, acquisition_method, item_type, payload_json,
                content_hash, structural_hash, acquisition_status,
                verification_status, rights_status, candidate_status
            ) VALUES (
                :id, :tenant_id, 'AI_GENERATED', :item_type, CAST(:payload_json AS jsonb),
                :content_hash, :structural_hash, 'EXTRACTED',
                'UNCHECKED', :rights_status, 'NEEDS_REVIEW'
            )
            """
        ),
        {
            "id": candidate_id,
            "tenant_id": tenant_id,
            "item_type": question_type.value,
            "payload_json": json.dumps(payload_json),
            "content_hash": c_hash,
            "structural_hash": s_hash,
            "rights_status": cand_rights.value,
        },
    )

    record = GenerationRequestRecord(
        id=request_id,
        tenant_id=tenant_id,
        requested_by=requested_by,
        taxonomy_node_id=taxonomy_node_id,
        target_difficulty=normalized_difficulty,
        source_anchors_json=anchors_json,
        generated_candidate_id=candidate_id,
        status="DRAFT_CREATED",
    )
    session.add(record)
    await session.flush()

    await enqueue(
        session,
        build_catalog_envelope(
            event_type="question.candidate.created",
            aggregate_type="question_candidate",
            aggregate_id=candidate_id,
            payload=QuestionCandidateCreatedPayload(
                tenant_id=tenant_id,
                candidate_id=candidate_id,
                acquisition_method=AcquisitionMethod.AI_GENERATED,
                content_hash=c_hash,
                structural_hash=s_hash,
            ),
        ),
    )

    if verify_with_c02:
        await run_c02_verification_and_queue_review(
            session,
            tenant_id=tenant_id,
            request_id=request_id,
            computed_expression=computed_expression,
            source_answer=source_answer,
            verifier=verifier,
        )

    await session.refresh(record)
    return await _load_generation_view(session, record)


async def run_c02_verification_and_queue_review(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    computed_expression: str | None = None,
    source_answer: str | None = None,
    verifier: NUMERIC_VERIFIER | None = None,
) -> GenerationRequestView:
    """Execute C02 verification against the AI candidate and place it into A09 review queue."""
    record = (
        await session.execute(
            select(GenerationRequestRecord).where(
                GenerationRequestRecord.id == request_id,
                GenerationRequestRecord.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if record is None:
        raise GenerationNotFoundError(f"Generation request {request_id} not found")
    if record.generated_candidate_id is None:
        raise GenerationValidationError("MISSING_CANDIDATE", "Generation request has no candidate yet")

    cand_row = (
        await session.execute(
            text(
                "SELECT id, item_type, payload_json, verification_status "
                "FROM question_candidates WHERE id = :cid AND tenant_id = :tenant_id"
            ),
            {"cid": record.generated_candidate_id, "tenant_id": tenant_id},
        )
    ).mappings().one()
    payload = dict(cand_row["payload_json"] or {})

    is_match = False
    computed_str: str | None = None
    findings: dict[str, Any] = {"mode": "c02_sympy_verify"}
    if computed_expression is not None and source_answer is not None:
        verifier_fn = verifier or verify_numeric_answer
        check = verifier_fn(computed_expression, source_answer, Decimal("0.01"))
        computed_str = check.computed
        is_match = bool(check.matches and check.error is None)
        findings["error"] = check.error
        findings["abs_diff"] = check.abs_diff
        next_verif = VerificationStatus.AUTO_VERIFIED if is_match else VerificationStatus.DISPUTED
    else:
        findings["note"] = "Missing expression/answer pair; escalated as DISPUTED for human review"
        next_verif = VerificationStatus.DISPUTED

    core_cand = _core_request_and_candidate(
        record=GenerationRequestRecord(
            id=record.id,
            tenant_id=record.tenant_id,
            requested_by=record.requested_by,
            taxonomy_node_id=record.taxonomy_node_id,
            target_difficulty=record.target_difficulty,
            source_anchors_json=record.source_anchors_json,
            generated_candidate_id=record.generated_candidate_id,
            status=GenerationStatus.GENERATED.value,
        ),
        item_type=QuestionItemType(str(cand_row["item_type"])),
        stem=str(payload.get("stem", "")),
        candidate_id=record.generated_candidate_id,
        verification_status=VerificationStatus(str(cand_row["verification_status"])),
    )
    queued_cand = record_sympy_verification_and_queue_review(core_cand, verification_status=next_verif)

    await session.execute(
        text(
            """
            INSERT INTO verification_runs (
                id, tenant_id, candidate_id, verifier_type, model_or_tool_version,
                computed_answer, is_match_with_source, findings_json
            ) VALUES (
                :id, :tenant_id, :candidate_id, 'SYMPY_SOLVER', 'sympy_sandbox_v1',
                :computed_answer, :is_match_with_source, CAST(:findings_json AS jsonb)
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "candidate_id": record.generated_candidate_id,
            "computed_answer": computed_str,
            "is_match_with_source": is_match,
            "findings_json": json.dumps(findings),
        },
    )
    await session.execute(
        text(
            "UPDATE question_candidates SET verification_status = :vs "
            "WHERE id = :cid AND tenant_id = :tenant_id"
        ),
        {"vs": queued_cand.verification_status.value, "cid": record.generated_candidate_id, "tenant_id": tenant_id},
    )

    severity = "NORMAL" if queued_cand.verification_status == VerificationStatus.AUTO_VERIFIED else "HIGH"
    review_task_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO review_tasks (
                id, tenant_id, entity_type, entity_id, queue_name, severity, status
            ) VALUES (
                :id, :tenant_id, 'QUESTION_CANDIDATE', :entity_id,
                'ai_generation_review', :severity, 'OPEN'
            )
            """
        ),
        {
            "id": review_task_id,
            "tenant_id": tenant_id,
            "entity_id": record.generated_candidate_id,
            "severity": severity,
        },
    )

    await enqueue(
        session,
        build_catalog_envelope(
            event_type="question.review.required",
            aggregate_type="review_task",
            aggregate_id=review_task_id,
            payload=QuestionReviewRequiredPayload(
                tenant_id=tenant_id,
                entity_id=record.generated_candidate_id,
                reason_codes=("AI_GENERATED_REQUIRES_HUMAN_REVIEW",),
                severity=severity,
                queue_name="ai_generation_review",
            ),
        ),
    )

    record.status = GenerationStatus.QUEUED_FOR_REVIEW.value
    await session.flush()
    return await _load_generation_view(session, record)


async def get_generation_request(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
) -> GenerationRequestView:
    """Fetch a generation request plus its candidate and A09 review status inside tenant scope."""
    record = (
        await session.execute(
            select(GenerationRequestRecord).where(
                GenerationRequestRecord.id == request_id,
                GenerationRequestRecord.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if record is None:
        raise GenerationNotFoundError(f"Generation request {request_id} not found")
    return await _load_generation_view(session, record)


async def approve_generation_and_publish(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    reason: str = "Human reviewer approved C02-verified AI question",
) -> GenerationPublishView:
    """Promote an AI-generated candidate to PUBLISHED strictly after C02 pass and human A09 approval."""
    record = (
        await session.execute(
            select(GenerationRequestRecord).where(
                GenerationRequestRecord.id == request_id,
                GenerationRequestRecord.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if record is None:
        raise GenerationNotFoundError(f"Generation request {request_id} not found")
    if record.generated_candidate_id is None:
        raise GenerationPolicyViolationError("MISSING_CANDIDATE", "No generated candidate exists")

    cand_row = (
        await session.execute(
            text(
                "SELECT id, acquisition_method, item_type, payload_json, content_hash, "
                "structural_hash, verification_status, rights_status "
                "FROM question_candidates WHERE id = :cid AND tenant_id = :tenant_id"
            ),
            {"cid": record.generated_candidate_id, "tenant_id": tenant_id},
        )
    ).mappings().one()

    review_task = (
        await session.execute(
            text(
                "SELECT id, status FROM review_tasks "
                "WHERE entity_type = 'QUESTION_CANDIDATE' AND entity_id = :cid AND tenant_id = :tenant_id "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"cid": record.generated_candidate_id, "tenant_id": tenant_id},
        )
    ).mappings().one_or_none()
    if review_task is None:
        raise GenerationPolicyViolationError(
            "MISSING_A09_REVIEW_TASK",
            "AI candidate cannot be published without passing through A09 review_tasks",
        )

    decision_id = uuid.uuid4()
    payload = dict(cand_row["payload_json"] or {})
    core_cand = _core_request_and_candidate(
        record=record,
        item_type=QuestionItemType(str(cand_row["item_type"])),
        stem=str(payload.get("stem", "")),
        candidate_id=record.generated_candidate_id,
        verification_status=VerificationStatus(str(cand_row["verification_status"])),
    )
    try:
        published_core = approve_and_publish_ai_candidate(
            core_cand,
            review_decision_id=str(decision_id),
            human_reviewer_id=str(reviewer_id),
        )
    except GenerationPolicyError as exc:
        raise GenerationPolicyViolationError("AI_PUBLISH_GATE_BLOCKED", str(exc)) from exc

    await session.execute(
        text(
            """
            INSERT INTO review_decisions (
                id, tenant_id, review_task_id, reviewer_id, decision, reason
            ) VALUES (
                :id, :tenant_id, :review_task_id, :reviewer_id, 'APPROVE', :reason
            )
            """
        ),
        {
            "id": decision_id,
            "tenant_id": tenant_id,
            "review_task_id": review_task["id"],
            "reviewer_id": reviewer_id,
            "reason": reason.strip() or "Approved",
        },
    )
    await session.execute(
        text("UPDATE review_tasks SET status = 'RESOLVED' WHERE id = :tid AND tenant_id = :tenant_id"),
        {"tid": review_task["id"], "tenant_id": tenant_id},
    )

    question_id = uuid.uuid4()
    question_version_id = uuid.uuid4()
    stable_code = f"AI-{question_id.hex[:12].upper()}"
    await session.execute(
        text(
            """
            INSERT INTO questions (
                id, tenant_id, visibility_scope, stable_code, item_type,
                primary_taxonomy_node_id, difficulty_level, acquisition_status,
                verification_status, rights_status, publication_status, access_tier
            ) VALUES (
                :id, :tenant_id, 'TENANT_PRIVATE', :stable_code, :item_type,
                :taxonomy_node_id, :difficulty_level, 'NORMALIZED',
                'VERIFIED', :rights_status, 'DRAFT', 'FREE'
            )
            """
        ),
        {
            "id": question_id,
            "tenant_id": tenant_id,
            "stable_code": stable_code,
            "item_type": cand_row["item_type"],
            "taxonomy_node_id": record.taxonomy_node_id,
            "difficulty_level": record.target_difficulty,
            "rights_status": cand_row["rights_status"],
        },
    )
    await session.execute(
        text(
            """
            INSERT INTO question_versions (
                id, tenant_id, visibility_scope, question_id, version_no,
                stem_json, options_json, numeric_parse_policy_json,
                content_hash, structural_hash, is_immutable,
                change_reason, created_by, approved_by
            ) VALUES (
                :id, :tenant_id, 'TENANT_PRIVATE', :question_id, 1,
                CAST(:stem_json AS jsonb), '[]'::jsonb, '{}'::jsonb,
                :content_hash, :structural_hash, TRUE,
                'E03 human-approved AI generation', :created_by, :approved_by
            )
            """
        ),
        {
            "id": question_version_id,
            "tenant_id": tenant_id,
            "question_id": question_id,
            "stem_json": json.dumps({"text": published_core.stem}),
            "content_hash": cand_row["content_hash"],
            "structural_hash": cand_row["structural_hash"],
            "created_by": record.requested_by,
            "approved_by": reviewer_id,
        },
    )
    await session.execute(
        text(
            "UPDATE questions SET current_version_id = :qvid, publication_status = 'PUBLISHED' "
            "WHERE id = :qid AND tenant_id = :tenant_id"
        ),
        {"qvid": question_version_id, "qid": question_id, "tenant_id": tenant_id},
    )
    await session.execute(
        text(
            "UPDATE question_candidates SET candidate_status = 'APPROVED', verification_status = 'VERIFIED', "
            "promoted_question_version_id = :qvid "
            "WHERE id = :cid AND tenant_id = :tenant_id"
        ),
        {"qvid": question_version_id, "cid": record.generated_candidate_id, "tenant_id": tenant_id},
    )
    record.status = GenerationStatus.APPROVED.value
    await session.flush()

    return GenerationPublishView(
        generation_request_id=record.id,
        candidate_id=record.generated_candidate_id,
        review_task_id=review_task["id"],
        review_decision_id=decision_id,
        question_id=question_id,
        question_version_id=question_version_id,
        acquisition_method=AcquisitionMethod.AI_GENERATED.value,
        verification_status="VERIFIED",
        publication_status=PublicationStatus.PUBLISHED.value,
        status=record.status,
    )
