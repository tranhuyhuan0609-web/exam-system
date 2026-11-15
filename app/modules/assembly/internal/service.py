"""C04 assembly service: papers, draft assembly and publishing, under the caller's tenant RLS.

* `assemble_assessment` picks the questions for a framework and subject (`internal.selection`: PUBLISHED, VERIFIED,
  rights-cleared, no two of one duplicate cluster, stimulus clusters whole, same seed -> same paper) and stores them as
  a DRAFT version: `is_immutable = false`, `published_at` null. Assembling again replaces that draft. The snapshot
  (`structure_snapshot_json`) holds ids, sections, points and hashes, never an answer.
* `publish_assessment` rechecks every question, writes the grading key to `assessment_answer_keys` (a separate table
  with its own privileges), freezes the version (trigger `IMMUTABLE_VIOLATION` from then on), makes it the paper's
  current version, logs the publication and announces `assessment.published`, all in the caller's transaction.
  A paper is VERIFIED when every question in it is (checked again at publish time); rights are the paper's own claim.

The key payload is self-contained for C07: per item the type, section, position, points and numeric parse policy, plus
the version's scoring scheme. Answers: the key of a question of the tenant is copied (`answer_source = SNAPSHOT`).
A question of the system tenant cannot be read by the API role (strict RLS on `question_version_answers`); its entry
only names the question version (`answer_source = SYSTEM_QUESTION_VERSION`) and the grading worker resolves it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.core_schemas import AssessmentSnapshotPublicContract
from app.contracts.events import EventEnvelopeContract
from app.modules.assembly.internal.models import Assessment, AssessmentAnswerKey, AssessmentItem, AssessmentVersion
from app.modules.assembly.internal.selection import (
    AssemblyShortageError,
    PoolQuestion,
    SectionRequirement,
    select_items,
)
from app.modules.outbox.public import enqueue
from app.modules.review.public import PublicationStatus, RightsStatus, VerificationStatus, evaluate_publish_gate

ASSESSMENT_PUBLISHED_EVENT = "assessment.published"
CLEARED = ("OWNED", "LICENSED", "ALLOWED_DERIVATIVE")


class AssessmentNotFoundError(LookupError):
    """The paper or a referenced catalogue row does not exist or is not visible to the tenant (same answer)."""


class AssessmentConflictError(ValueError):
    """A state conflict; `code` is one of ASSESSMENT_TAKEN_DOWN, NO_DRAFT_VERSION, NOT_ASSEMBLED."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AssemblyShortageFailed(ValueError):
    """The question pool cannot fill the framework under the rules; the numbers are in `shortage`."""

    def __init__(self, shortage: AssemblyShortageError) -> None:
        self.shortage = shortage
        super().__init__(str(shortage))


class PublishGateBlockedError(ValueError):
    """Publishing is blocked; `codes` and `reasons` say why."""

    def __init__(self, codes: Sequence[str], reasons: Sequence[str]) -> None:
        self.codes = tuple(codes)
        self.reasons = tuple(reasons)
        super().__init__("; ".join(self.reasons))


@dataclass(frozen=True, slots=True)
class AssessmentRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    visibility_scope: str
    title: str
    subject_code: str
    grade_band: str
    exam_type_code: str
    access_tier: str
    current_version_id: uuid.UUID | None
    verification_status: str
    rights_status: str
    publication_status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AssessmentItemRecord:
    question_version_id: uuid.UUID
    section_code: str
    sequence_order: int
    points: Decimal


@dataclass(frozen=True, slots=True)
class AssessmentVersionRecord:
    """A version with its answer-free snapshot and items."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    assessment_id: uuid.UUID
    version_no: int
    scoring_scheme_id: uuid.UUID
    duration_seconds: int
    total_points: Decimal
    base_seed: int
    is_immutable: bool
    published_at: datetime | None
    structure_snapshot: dict[str, Any]
    items: tuple[AssessmentItemRecord, ...]


@dataclass(frozen=True, slots=True)
class PublishResult:
    assessment: AssessmentRecord
    version: AssessmentVersionRecord
    answer_key_checksum: str
    publication_id: uuid.UUID
    event_id: uuid.UUID


def _assessment(row: Assessment) -> AssessmentRecord:
    return AssessmentRecord(**{f: getattr(row, f) for f in AssessmentRecord.__dataclass_fields__})


def _version(row: AssessmentVersion, items: Sequence[AssessmentItem]) -> AssessmentVersionRecord:
    values = {
        f: getattr(row, f)
        for f in AssessmentVersionRecord.__dataclass_fields__
        if f not in {"structure_snapshot", "items"}
    }
    return AssessmentVersionRecord(
        structure_snapshot=dict(row.structure_snapshot_json),
        items=tuple(
            AssessmentItemRecord(i.question_version_id, i.section_code, i.sequence_order, i.points)
            for i in sorted(items, key=lambda i: i.sequence_order)
        ),
        **values,
    )


async def create_assessment(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    title: str,
    subject_code: str,
    grade_band: str,
    exam_type_code: str,
    visibility_scope: str = "TENANT_PRIVATE",
    access_tier: str = "FREE",
    rights_status: str = "PENDING_REVIEW",
) -> AssessmentRecord:
    """Create a paper with no version yet. Status axes start fail-closed (DRAFT, UNCHECKED)."""
    row = (
        await session.execute(
            insert(Assessment)
            .values(
                tenant_id=tenant_id,
                visibility_scope=visibility_scope,
                title=title,
                subject_code=subject_code.strip().upper(),
                grade_band=grade_band,
                exam_type_code=exam_type_code,
                access_tier=access_tier,
                rights_status=rights_status,
            )
            .returning(Assessment)
        )
    ).scalar_one()
    return _assessment(row)


async def get_assessment(session: AsyncSession, assessment_id: uuid.UUID) -> AssessmentRecord | None:
    row = await session.scalar(
        select(Assessment).where(Assessment.id == assessment_id).execution_options(populate_existing=True)
    )
    return None if row is None else _assessment(row)


async def _locked_assessment(session: AsyncSession, assessment_id: uuid.UUID) -> Assessment:
    row = await session.scalar(
        select(Assessment)
        .where(Assessment.id == assessment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise AssessmentNotFoundError("assessment not found")
    if row.publication_status == "TAKEN_DOWN" or row.rights_status == "TAKEDOWN":
        raise AssessmentConflictError("ASSESSMENT_TAKEN_DOWN", "the assessment was taken down")
    return row


# A SINGLE_CHOICE question is delivered with its options shuffled among A..D, so it needs exactly those four; a
# CLUSTER_TRUE_FALSE needs the statements a..d. The authoring core accepts looser shapes, the paper cannot use them.
_POOL_SQL = """
SELECT q.id AS question_id, v.id AS question_version_id, q.item_type, v.content_hash, v.stimulus_id,
       q.duplicate_cluster_id
FROM questions q
JOIN question_versions v ON v.id = q.current_version_id
WHERE q.publication_status = 'PUBLISHED'
  AND q.verification_status = 'VERIFIED'
  AND q.rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE')
  AND v.is_immutable IS TRUE
  AND q.item_type = ANY(:item_types)
  AND (
        q.item_type NOT IN ('SINGLE_CHOICE', 'CLUSTER_TRUE_FALSE')
        OR (SELECT array_agg(o.option_key::text ORDER BY o.option_key) FROM question_options o
            WHERE o.question_version_id = v.id)
           = CASE WHEN q.item_type = 'SINGLE_CHOICE' THEN ARRAY['A', 'B', 'C', 'D'] ELSE ARRAY['a', 'b', 'c', 'd'] END
      )
  AND EXISTS (
        SELECT 1 FROM taxonomy_nodes qn
        JOIN taxonomy_nodes sn
          ON sn.node_type = 'SUBJECT' AND upper(sn.code) = :subject AND qn.code_path <@ sn.code_path
        WHERE qn.id = q.primary_taxonomy_node_id
  )
  AND (
        cardinality(CAST(:taxonomy_ids AS uuid[])) = 0
        OR EXISTS (
            SELECT 1 FROM taxonomy_nodes qn
            JOIN taxonomy_nodes an ON an.id = ANY(CAST(:taxonomy_ids AS uuid[])) AND qn.code_path <@ an.code_path
            WHERE qn.id = q.primary_taxonomy_node_id
        )
  )
"""


async def _framework_version(session: AsyncSession, framework_code: str, subject_code: str) -> Any:
    row = (
        await session.execute(
            text(
                "SELECT f.code AS framework_code, fv.subject_code, fv.duration_seconds, fv.section_specs_json "
                "FROM framework_versions fv JOIN assessment_frameworks f ON f.id = fv.framework_id "
                "WHERE f.code = :f AND fv.subject_code = :s ORDER BY fv.version_code DESC LIMIT 1"
            ),
            {"f": framework_code.strip().upper(), "s": subject_code},
        )
    ).one_or_none()
    if row is None:
        raise AssessmentNotFoundError(f"framework {framework_code} has no specification for subject {subject_code}")
    return row


async def _scoring_scheme_id(session: AsyncSession, framework_code: str) -> uuid.UUID:
    scheme_id = await session.scalar(
        text("SELECT id FROM scoring_scheme_versions WHERE rules_json->>'framework_code' = :f ORDER BY code LIMIT 1"),
        {"f": framework_code.strip().upper()},
    )
    if scheme_id is None:
        raise AssessmentNotFoundError(f"framework {framework_code} has no scoring scheme")
    return scheme_id


async def _load_version(session: AsyncSession, version_id: uuid.UUID) -> AssessmentVersionRecord:
    row = await session.scalar(
        select(AssessmentVersion).where(AssessmentVersion.id == version_id).execution_options(populate_existing=True)
    )
    if row is None:
        raise AssessmentNotFoundError("assessment version not found")
    items = (
        await session.scalars(
            select(AssessmentItem)
            .where(AssessmentItem.assessment_version_id == version_id)
            .execution_options(populate_existing=True)
        )
    ).all()
    return _version(row, items)


async def _latest_version(session: AsyncSession, assessment_id: uuid.UUID) -> AssessmentVersion | None:
    return await session.scalar(
        select(AssessmentVersion)
        .where(AssessmentVersion.assessment_id == assessment_id)
        .order_by(AssessmentVersion.version_no.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )


async def assemble_assessment(
    session: AsyncSession,
    assessment_id: uuid.UUID,
    *,
    framework_code: str,
    random_seed: int,
    subject_code: str | None = None,
    taxonomy_node_ids: Sequence[uuid.UUID] = (),
) -> AssessmentVersionRecord:
    """Assemble the paper into a DRAFT version (replacing the previous draft); same seed, same paper."""
    assessment = await _locked_assessment(session, assessment_id)
    subject = (subject_code or assessment.subject_code).strip().upper()
    framework = await _framework_version(session, framework_code, subject)
    scheme_id = await _scoring_scheme_id(session, framework.framework_code)
    spec = framework.section_specs_json
    sections = [
        SectionRequirement(
            section_code=s["section_code"],
            item_type=s["item_type"],
            question_count=int(s["question_count"]),
            points_per_question=Decimal(s["points_per_question"]),
        )
        for s in spec["sections"]
    ]
    pool_rows = (
        await session.execute(
            text(_POOL_SQL),
            {
                "item_types": sorted({s.item_type for s in sections}),
                "subject": subject,
                "taxonomy_ids": [str(t) for t in taxonomy_node_ids],
            },
        )
    ).all()
    pool = [
        PoolQuestion(
            question_id=r.question_id,
            question_version_id=r.question_version_id,
            item_type=r.item_type,
            content_hash=r.content_hash,
            stimulus_id=r.stimulus_id,
            duplicate_cluster_id=r.duplicate_cluster_id,
        )
        for r in pool_rows
    ]
    try:
        chosen = select_items(pool, sections, random_seed)
    except AssemblyShortageError as exc:
        raise AssemblyShortageFailed(exc) from exc

    total_points = sum((i.points for i in chosen), Decimal("0")).quantize(Decimal("0.01"))
    expected_points = Decimal(spec["total_points"]).quantize(Decimal("0.01"))
    if total_points != expected_points:
        raise AssessmentConflictError(
            "FRAMEWORK_POINTS_MISMATCH",
            f"sections add up to {total_points} points, the framework says {expected_points}",
        )

    latest = await _latest_version(session, assessment_id)
    if latest is not None and not latest.is_immutable:
        version_id, version_no = latest.id, latest.version_no
        await session.execute(delete(AssessmentItem).where(AssessmentItem.assessment_version_id == version_id))
    else:
        version_id = (await session.execute(text("SELECT uuid_generate_v7()"))).scalar_one()
        version_no = 1 if latest is None else latest.version_no + 1

    snapshot = AssessmentSnapshotPublicContract.model_validate(
        {
            "assessment_version_id": version_id,
            "framework_code": framework.framework_code,
            "subject_code": subject,
            "duration_seconds": framework.duration_seconds,
            "total_points": total_points,
            "items": [
                {
                    "question_version_id": i.question_version_id,
                    "stimulus_id": i.stimulus_id,
                    "section_code": i.section_code,
                    "sequence_order": i.sequence_order,
                    "points": i.points,
                    "content_hash": i.content_hash,
                }
                for i in chosen
            ],
        }
    ).model_dump(mode="json")

    if latest is not None and not latest.is_immutable:
        await session.execute(
            update(AssessmentVersion)
            .where(AssessmentVersion.id == version_id)
            .values(
                scoring_scheme_id=scheme_id,
                duration_seconds=framework.duration_seconds,
                total_points=total_points,
                base_seed=random_seed,
                structure_snapshot_json=snapshot,
            )
        )
    else:
        await session.execute(
            insert(AssessmentVersion).values(
                id=version_id,
                tenant_id=assessment.tenant_id,
                assessment_id=assessment.id,
                version_no=version_no,
                scoring_scheme_id=scheme_id,
                duration_seconds=framework.duration_seconds,
                total_points=total_points,
                base_seed=random_seed,
                structure_snapshot_json=snapshot,
                is_immutable=False,
                published_at=None,
            )
        )
    for item in chosen:
        await session.execute(
            insert(AssessmentItem).values(
                tenant_id=assessment.tenant_id,
                assessment_version_id=version_id,
                question_version_id=item.question_version_id,
                section_code=item.section_code,
                sequence_order=item.sequence_order,
                points=item.points,
            )
        )
    return await _load_version(session, version_id)


def _checksum(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def publish_assessment(
    session: AsyncSession,
    assessment_id: uuid.UUID,
    *,
    published_by: uuid.UUID,
    correlation_id: str | None = None,
) -> PublishResult:
    """Freeze the draft version, store its answer key apart and publish the paper (see the module docstring)."""
    assessment = await _locked_assessment(session, assessment_id)
    draft = await _latest_version(session, assessment_id)
    if draft is None or draft.is_immutable:
        raise AssessmentConflictError(
            "NO_DRAFT_VERSION" if draft is None else "NOT_ASSEMBLED",
            "assemble the paper first: there is no draft version to publish",
        )
    items = (
        await session.execute(
            text(
                "SELECT i.question_version_id, i.section_code, i.sequence_order, i.points, "
                "       v.tenant_id AS question_tenant_id, v.question_id, q.item_type, q.current_version_id, "
                "       q.publication_status, q.verification_status, q.rights_status, v.numeric_parse_policy_json "
                "FROM assessment_items i "
                "JOIN question_versions v ON v.id = i.question_version_id "
                "JOIN questions q ON q.id = v.question_id "
                "WHERE i.assessment_version_id = :v ORDER BY i.sequence_order"
            ),
            {"v": draft.id},
        )
    ).all()
    stored_count = await session.scalar(
        text("SELECT count(*) FROM assessment_items WHERE assessment_version_id = :v"), {"v": draft.id}
    )
    if len(items) != stored_count:
        raise PublishGateBlockedError(
            ["PUBLISH_QUESTION_UNAVAILABLE"], ["a question of the paper is no longer visible (taken down?)"]
        )

    codes: list[str] = []
    reasons: list[str] = []
    for item in items:
        if item.publication_status != "PUBLISHED" or item.rights_status not in CLEARED:
            codes.append("PUBLISH_QUESTION_NOT_PUBLISHED")
            reasons.append(f"question {item.question_id} is {item.publication_status}/{item.rights_status}")
        elif item.verification_status != "VERIFIED":
            codes.append("PUBLISH_NOT_VERIFIED")
            reasons.append(f"question {item.question_id} is {item.verification_status}, VERIFIED is required")
        elif item.current_version_id != item.question_version_id:
            codes.append("QUESTION_VERSION_SUPERSEDED")
            reasons.append(f"question {item.question_id} has a newer version: assemble the paper again")
    if codes:
        raise PublishGateBlockedError(tuple(dict.fromkeys(codes)), reasons)

    decision = evaluate_publish_gate(
        verification_status=VerificationStatus.VERIFIED,  # every question was just checked VERIFIED
        rights_status=RightsStatus(assessment.rights_status),
        publication_status=PublicationStatus(assessment.publication_status),
        current_version_id=str(draft.id),
    )
    if not decision.allowed_publish:
        raise PublishGateBlockedError(decision.blocking_codes, decision.reasons)

    own_ids = [i.question_version_id for i in items if i.question_tenant_id == assessment.tenant_id]
    answers = {
        r.question_version_id: r.answer_schema
        for r in (
            await session.execute(
                text(
                    "SELECT question_version_id, answer_schema FROM question_version_answers "
                    "WHERE question_version_id = ANY(:ids)"
                ),
                {"ids": own_ids},
            )
        ).all()
    }
    key_items: dict[str, dict[str, Any]] = {}
    for item in items:
        entry: dict[str, Any] = {
            "item_type": item.item_type,
            "section_code": item.section_code,
            "sequence_order": item.sequence_order,
            "points": str(item.points),
            "numeric_parse_policy": item.numeric_parse_policy_json,
        }
        if item.question_tenant_id == assessment.tenant_id:
            if item.question_version_id not in answers:
                raise PublishGateBlockedError(
                    ["PUBLISH_ANSWER_MISSING"], [f"question version {item.question_version_id} has no answer"]
                )
            entry["answer_source"] = "SNAPSHOT"
            entry["answer_schema"] = answers[item.question_version_id]
        else:
            entry["answer_source"] = "SYSTEM_QUESTION_VERSION"
        key_items[str(item.question_version_id)] = entry
    # Self-contained: C07 grades from this key alone (plus the answers of system questions), so a regrade years
    # later sees exactly what was published, whatever happened to the versions and items meanwhile.
    payload = {
        "assessment_version_id": str(draft.id),
        "scoring_scheme_id": str(draft.scoring_scheme_id),
        "total_points": str(draft.total_points),
        "items": key_items,
    }
    checksum = _checksum(payload)

    await session.execute(
        insert(AssessmentAnswerKey).values(
            assessment_version_id=draft.id,
            key_revision_no=1,
            tenant_id=assessment.tenant_id,
            is_current=True,
            answer_key_payload_json=payload,
            key_checksum_sha256=checksum,
            change_reason="INITIAL_PUBLISH",
            created_by=published_by,
        )
    )
    now = datetime.now(UTC)
    await session.execute(
        update(AssessmentVersion).where(AssessmentVersion.id == draft.id).values(is_immutable=True, published_at=now)
    )
    await session.execute(
        update(Assessment)
        .where(Assessment.id == assessment.id)
        .values(verification_status="VERIFIED", publication_status="PUBLISHED", current_version_id=draft.id)
    )
    publication_id = (
        await session.execute(
            text(
                "INSERT INTO publications (tenant_id, entity_type, entity_id, version_id, published_by) "
                "VALUES (:t, 'ASSESSMENT', :e, :v, :p) RETURNING id"
            ),
            {"t": assessment.tenant_id, "e": assessment.id, "v": draft.id, "p": published_by},
        )
    ).scalar_one()
    envelope = EventEnvelopeContract(
        event_type=ASSESSMENT_PUBLISHED_EVENT,
        tenant_id=assessment.tenant_id,
        aggregate_type="assessment",
        aggregate_id=assessment.id,
        correlation_id=correlation_id,
        payload={
            "tenant_id": str(assessment.tenant_id),
            "assessment_id": str(assessment.id),
            "assessment_version_id": str(draft.id),
            "framework_code": draft.structure_snapshot_json["framework_code"],
        },
    )
    await enqueue(session, envelope)

    fresh = await get_assessment(session, assessment.id)
    version = await _load_version(session, draft.id)
    assert fresh is not None
    return PublishResult(fresh, version, checksum, publication_id, envelope.event_id)
