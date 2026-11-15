"""C01 questions service: typed questions, immutable versions and their answers, under the caller's tenant RLS.

Rules enforced here and again by the database (so a bug here cannot do what the DDL forbids):
* every version is checked by the typed authoring core (`internal.authoring`, all 6 item types) before it is
  stored; the answer goes to `question_version_answers`, never into `question_versions` or `question_options`;
* a version is frozen when its question is PUBLISHED with it as current version (`trg_lock_question_version_on_
  publish`); a frozen version or answer can no longer change (`IMMUTABLE_VIOLATION`), so a change to a
  published question is a new version `version_no + 1` (`create_next_version`), and the old one stays as it was
  for the papers that already use it;
* a question reaches PUBLISHED only when VERIFIED, with cleared rights and a current version
  (`chk_question_publish_gate`); `publish_question_version` reports the failing reasons before the database would.

`current_version_id` names the published (served) version; a newer draft is the highest `version_no`.
Hashes are the A08 ones (`dedup.compute_question_hashes`), so bank questions and candidates compare directly.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, insert, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.events import EventEnvelopeContract
from app.modules.dedup.public import compute_question_hashes
from app.modules.outbox.public import enqueue
from app.modules.questions.internal.authoring import (
    DifficultyLevel,
    QuestionAuthoringError,
    QuestionDraft,
    QuestionItemType,
    QuestionOptionDraft,
    QuestionVersionState,
    fork_next_version,
    validate_question_draft,
)
from app.modules.questions.internal.models import (
    DIFFICULTY_LEVELS,
    ITEM_TYPES,
    PUBLICATION_STATUSES,
    Question,
    QuestionAsset,
    QuestionOption,
    QuestionStimulus,
    QuestionVersion,
    QuestionVersionAnswer,
)
from app.modules.review.public import PublicationStatus, RightsStatus, VerificationStatus, evaluate_publish_gate
from app.platform.pagination import CursorError, clamp_limit, decode_cursor, encode_cursor

QUESTION_PUBLISHED_EVENT = "question.version.published"
DEFAULT_MAX_NUMERIC_CHARS = 4


class QuestionNotFoundError(LookupError):
    """The question, version or a referenced row does not exist or is not visible to the tenant (same answer)."""


class QuestionConflictError(ValueError):
    """A state conflict; `code` is one of STABLE_CODE_TAKEN, DRAFT_EXISTS, QUESTION_TAKEN_DOWN,
    VERSION_NOT_LATEST, VERSION_ALREADY_PUBLISHED."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class QuestionValidationFailedError(ValueError):
    """The typed authoring core rejected the version; `code` is its error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PublishGateBlockedError(ValueError):
    """The publish gate is not met; `codes` and `reasons` say why."""

    def __init__(self, codes: Sequence[str], reasons: Sequence[str]) -> None:
        self.codes = tuple(codes)
        self.reasons = tuple(reasons)
        super().__init__("; ".join(self.reasons))


@dataclass(frozen=True, slots=True)
class OptionInput:
    """An option (SINGLE_CHOICE / MULTIPLE_SELECT: A..D) or statement (CLUSTER_TRUE_FALSE: a..d)."""

    key: str
    text: str


@dataclass(frozen=True, slots=True)
class StimulusInput:
    """A new shared context for a GROUP_STEM question, created with it."""

    stimulus_type: str
    body: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class QuestionRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    visibility_scope: str
    stable_code: str
    item_type: str
    primary_taxonomy_node_id: uuid.UUID | None
    curriculum_node_id: uuid.UUID | None
    exam_framework_tags: tuple[str, ...]
    difficulty_level: str
    current_version_id: uuid.UUID | None
    acquisition_status: str
    verification_status: str
    rights_status: str
    publication_status: str
    access_tier: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class QuestionVersionRecord:
    """A version without its answer: safe to show to anyone who may see the question."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    question_id: uuid.UUID
    version_no: int
    visibility_scope: str
    stimulus_id: uuid.UUID | None
    stem: dict[str, Any]
    options: tuple[dict[str, Any], ...]
    numeric_parse_policy: dict[str, Any]
    content_hash: str
    structural_hash: str
    is_immutable: bool
    change_reason: str | None
    created_by: uuid.UUID | None
    approved_by: uuid.UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ValidationReport:
    version_id: uuid.UUID
    valid: bool
    code: str | None
    message: str | None


@dataclass(frozen=True, slots=True)
class PublishResult:
    question: QuestionRecord
    version: QuestionVersionRecord
    publication_id: uuid.UUID
    event_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class VerificationTarget:
    """What C02 needs to check a version independently (the API role reads answers; the exam room never does)."""

    version_id: uuid.UUID
    question_id: uuid.UUID
    item_type: str
    answer_schema: dict[str, Any]


def _question(row: Question) -> QuestionRecord:
    values = {f: getattr(row, f) for f in QuestionRecord.__dataclass_fields__ if f != "exam_framework_tags"}
    return QuestionRecord(exam_framework_tags=tuple(row.exam_framework_tags or ()), **values)


def _version(row: QuestionVersion) -> QuestionVersionRecord:
    skip = {"stem", "options", "numeric_parse_policy"}
    values = {f: getattr(row, f) for f in QuestionVersionRecord.__dataclass_fields__ if f not in skip}
    return QuestionVersionRecord(
        stem=dict(row.stem_json),
        options=tuple(row.options_json or ()),
        numeric_parse_policy=dict(row.numeric_parse_policy_json or {}),
        **values,
    )


def _draft(
    *,
    item_type: str,
    difficulty_level: str,
    stem_text: str,
    options: Sequence[OptionInput],
    answer_schema: Mapping[str, Any],
    stimulus_id: uuid.UUID | None,
    max_numeric_chars: int = DEFAULT_MAX_NUMERIC_CHARS,
) -> QuestionDraft:
    return QuestionDraft(
        item_type=QuestionItemType(item_type),
        difficulty_level=DifficultyLevel(difficulty_level),
        stem_text=stem_text,
        options=tuple(QuestionOptionDraft(label=o.key, content_text=o.text) for o in options),
        answer_schema=dict(answer_schema),
        stimulus_id=None if stimulus_id is None else str(stimulus_id),
        max_short_numeric_chars=max_numeric_chars,
    )


def _validate(draft: QuestionDraft) -> None:
    try:
        validate_question_draft(draft)
    except QuestionAuthoringError as exc:
        raise QuestionValidationFailedError(exc.code, str(exc)) from exc


def _options_from_json(raw: Sequence[Mapping[str, Any]]) -> tuple[OptionInput, ...]:
    return tuple(OptionInput(key=str(o["key"]), text=str(o["text"])) for o in raw)


def _max_numeric_chars(version: QuestionVersion) -> int:
    value = (version.numeric_parse_policy_json or {}).get("max_chars", DEFAULT_MAX_NUMERIC_CHARS)
    return value if isinstance(value, int) and not isinstance(value, bool) else DEFAULT_MAX_NUMERIC_CHARS


async def _require_visible(session: AsyncSession, table: str, row_id: uuid.UUID | None, label: str) -> None:
    """The row must be visible to the tenant: a foreign key alone would also accept another tenant's private
    row, because referential checks bypass row-level security."""
    if row_id is None:
        return
    # table is a literal chosen by the caller, never input
    if await session.scalar(text(f"SELECT 1 FROM {table} WHERE id = :id"), {"id": row_id}) is None:
        raise QuestionNotFoundError(f"{label} not found")


async def _require_own_stimulus(session: AsyncSession, tenant_id: uuid.UUID, stimulus_id: uuid.UUID) -> None:
    found = await session.scalar(
        select(QuestionStimulus.id).where(QuestionStimulus.id == stimulus_id, QuestionStimulus.tenant_id == tenant_id)
    )
    if found is None:
        raise QuestionNotFoundError("stimulus not found")


async def _insert_version(
    session: AsyncSession,
    *,
    question: Question,
    version_no: int,
    stem_text: str,
    options: Sequence[OptionInput],
    answer_schema: Mapping[str, Any],
    rationale: Mapping[str, Any] | None,
    stimulus_id: uuid.UUID | None,
    created_by: uuid.UUID,
    change_reason: str | None,
    asset_ids: Sequence[uuid.UUID],
    numeric_parse_policy: Mapping[str, Any] | None = None,
) -> QuestionVersion:
    """Store a validated version: the version row, its options, its answer (separate table) and its assets."""
    hashes = compute_question_hashes(stem_text, [o.text for o in options])
    values: dict[str, Any] = {
        "tenant_id": question.tenant_id,
        "visibility_scope": question.visibility_scope,
        "question_id": question.id,
        "version_no": version_no,
        "stimulus_id": stimulus_id,
        "stem_json": {"text": stem_text},
        "options_json": [{"key": o.key, "text": o.text} for o in options],
        "content_hash": hashes.content_hash,
        "structural_hash": hashes.structural_hash,
        "change_reason": change_reason,
        "created_by": created_by,
    }
    if numeric_parse_policy is not None:
        values["numeric_parse_policy_json"] = dict(numeric_parse_policy)
    version = (await session.execute(insert(QuestionVersion).values(**values).returning(QuestionVersion))).scalar_one()
    for order, option in enumerate(options, start=1):
        await session.execute(
            insert(QuestionOption).values(
                tenant_id=question.tenant_id,
                question_version_id=version.id,
                option_key=option.key,
                content_json={"text": option.text},
                sequence_order=order,
            )
        )
    await session.execute(
        insert(QuestionVersionAnswer).values(
            question_version_id=version.id,
            tenant_id=question.tenant_id,
            answer_schema=dict(answer_schema),
            rationale_json=dict(rationale or {}),
        )
    )
    for asset_id in dict.fromkeys(asset_ids):
        await _require_visible(session, "assets", asset_id, "asset")
        await session.execute(
            insert(QuestionAsset).values(
                question_version_id=version.id, asset_id=asset_id, tenant_id=question.tenant_id
            )
        )
    return version


async def create_question(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    created_by: uuid.UUID,
    stable_code: str,
    item_type: str,
    stem_text: str,
    answer_schema: Mapping[str, Any],
    options: Sequence[OptionInput] = (),
    difficulty_level: str = "TH",
    rationale: Mapping[str, Any] | None = None,
    stimulus_id: uuid.UUID | None = None,
    stimulus: StimulusInput | None = None,
    primary_taxonomy_node_id: uuid.UUID | None = None,
    curriculum_node_id: uuid.UUID | None = None,
    exam_framework_tags: Sequence[str] = ("SGK_KNTT",),
    visibility_scope: str = "TENANT_PRIVATE",
    access_tier: str = "FREE",
    rights_status: str = "PENDING_REVIEW",
    asset_ids: Sequence[uuid.UUID] = (),
) -> tuple[QuestionRecord, QuestionVersionRecord]:
    """Create a question with its first version (`version_no = 1`, a draft). Status axes start fail-closed.

    A GROUP_STEM question needs a stimulus: an existing one of the tenant (`stimulus_id`) or a new one
    (`stimulus`), never both.
    """
    if stimulus_id is not None and stimulus is not None:
        raise ValueError("give stimulus_id or stimulus, not both")
    await _require_visible(session, "taxonomy_nodes", primary_taxonomy_node_id, "taxonomy node")
    await _require_visible(session, "curriculum_nodes", curriculum_node_id, "curriculum node")
    if stimulus_id is not None:
        await _require_own_stimulus(session, tenant_id, stimulus_id)
    if stimulus is not None:
        stimulus_id = (
            await session.execute(
                insert(QuestionStimulus)
                .values(tenant_id=tenant_id, stimulus_type=stimulus.stimulus_type, body_json=dict(stimulus.body))
                .returning(QuestionStimulus.id)
            )
        ).scalar_one()
    _validate(
        _draft(
            item_type=item_type,
            difficulty_level=difficulty_level,
            stem_text=stem_text,
            options=options,
            answer_schema=answer_schema,
            stimulus_id=stimulus_id,
        )
    )
    try:
        async with session.begin_nested():
            question = (
                await session.execute(
                    insert(Question)
                    .values(
                        tenant_id=tenant_id,
                        visibility_scope=visibility_scope,
                        stable_code=stable_code,
                        item_type=item_type,
                        primary_taxonomy_node_id=primary_taxonomy_node_id,
                        curriculum_node_id=curriculum_node_id,
                        exam_framework_tags=list(dict.fromkeys(exam_framework_tags)),
                        difficulty_level=difficulty_level,
                        access_tier=access_tier,
                        rights_status=rights_status,
                    )
                    .returning(Question)
                )
            ).scalar_one()
    except IntegrityError as exc:
        if "uq_questions_tenant_id_stable_code" in str(exc.orig):
            raise QuestionConflictError(
                "STABLE_CODE_TAKEN", f"stable_code {stable_code!r} is already used in this tenant"
            ) from exc
        raise
    version = await _insert_version(
        session,
        question=question,
        version_no=1,
        stem_text=stem_text,
        options=options,
        answer_schema=answer_schema,
        rationale=rationale,
        stimulus_id=stimulus_id,
        created_by=created_by,
        change_reason=None,
        asset_ids=asset_ids,
    )
    return _question(question), _version(version)


async def get_question(session: AsyncSession, question_id: uuid.UUID) -> QuestionRecord | None:
    row = await session.scalar(
        select(Question).where(Question.id == question_id).execution_options(populate_existing=True)
    )
    return None if row is None else _question(row)


async def get_question_version(session: AsyncSession, version_id: uuid.UUID) -> QuestionVersionRecord | None:
    row = await session.scalar(
        select(QuestionVersion).where(QuestionVersion.id == version_id).execution_options(populate_existing=True)
    )
    return None if row is None else _version(row)


async def list_question_versions(session: AsyncSession, question_id: uuid.UUID) -> list[QuestionVersionRecord]:
    rows = await session.scalars(
        select(QuestionVersion)
        .where(QuestionVersion.question_id == question_id)
        .order_by(QuestionVersion.version_no)
        .execution_options(populate_existing=True)
    )
    return [_version(r) for r in rows]


async def _answer(session: AsyncSession, version_id: uuid.UUID) -> QuestionVersionAnswer:
    row = await session.scalar(
        select(QuestionVersionAnswer)
        .where(QuestionVersionAnswer.question_version_id == version_id)
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise QuestionNotFoundError("answer of the question version not found")
    return row


async def create_next_version(
    session: AsyncSession,
    question_id: uuid.UUID,
    *,
    created_by: uuid.UUID,
    change_reason: str,
    stem_text: str | None = None,
    options: Sequence[OptionInput] | None = None,
    answer_schema: Mapping[str, Any] | None = None,
    rationale: Mapping[str, Any] | None = None,
    asset_ids: Sequence[uuid.UUID] = (),
) -> QuestionVersionRecord:
    """Fork the newest (frozen) version into `version_no + 1`, a draft; fields not given are copied from it.

    The frozen version and its answer stay untouched for the papers that use them. While the newest version is
    still a draft it is the one to edit, so a second draft is a conflict.
    """
    question = await session.scalar(
        select(Question).where(Question.id == question_id).with_for_update().execution_options(populate_existing=True)
    )
    if question is None:
        raise QuestionNotFoundError("question not found")
    if question.publication_status == "TAKEN_DOWN" or question.rights_status == "TAKEDOWN":
        raise QuestionConflictError("QUESTION_TAKEN_DOWN", "the question was taken down")
    latest = await session.scalar(
        select(QuestionVersion)
        .where(QuestionVersion.question_id == question_id)
        .order_by(QuestionVersion.version_no.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )
    if latest is None:
        raise QuestionNotFoundError("question has no version")
    if not latest.is_immutable:
        raise QuestionConflictError("DRAFT_EXISTS", f"version {latest.version_no} is still a draft: edit it")
    answer = await _answer(session, latest.id)
    new_options = _options_from_json(latest.options_json or ()) if options is None else tuple(options)
    new_answer = dict(answer.answer_schema) if answer_schema is None else dict(answer_schema)
    new_stem = str(latest.stem_json.get("text", "")) if stem_text is None else stem_text
    max_chars = _max_numeric_chars(latest)

    def draft_of(stem: str, opts: Sequence[OptionInput], ans: Mapping[str, Any]) -> QuestionDraft:
        return _draft(
            item_type=question.item_type,
            difficulty_level=question.difficulty_level,
            stem_text=stem,
            options=opts,
            answer_schema=ans,
            stimulus_id=latest.stimulus_id,
            max_numeric_chars=max_chars,
        )

    current = QuestionVersionState(
        question_id=str(question.id),
        tenant_id=str(question.tenant_id),
        version_no=latest.version_no,
        draft=draft_of(str(latest.stem_json.get("text", "")), _options_from_json(latest.options_json or ()),
                       answer.answer_schema),
        content_hash=latest.content_hash,
        is_immutable=True,
    )  # fmt: skip
    try:
        forked = fork_next_version(
            current,
            new_draft=draft_of(new_stem, new_options, new_answer),
            change_reason=change_reason,
            created_by=str(created_by),
        )
    except QuestionAuthoringError as exc:
        raise QuestionValidationFailedError(exc.code, str(exc)) from exc
    version = await _insert_version(
        session,
        question=question,
        version_no=forked.version_no,
        stem_text=new_stem,
        options=new_options,
        answer_schema=new_answer,
        rationale=dict(answer.rationale_json) if rationale is None else rationale,
        stimulus_id=latest.stimulus_id,
        created_by=created_by,
        change_reason=forked.change_reason,
        asset_ids=asset_ids,
        numeric_parse_policy=latest.numeric_parse_policy_json,
    )
    return _version(version)


async def validate_question_version(session: AsyncSession, version_id: uuid.UUID) -> ValidationReport:
    """Run the typed authoring checks on a stored version (with its answer); nothing is changed."""
    version = await session.scalar(
        select(QuestionVersion).where(QuestionVersion.id == version_id).execution_options(populate_existing=True)
    )
    if version is None:
        raise QuestionNotFoundError("question version not found")
    question = await session.scalar(select(Question).where(Question.id == version.question_id))
    if question is None:
        raise QuestionNotFoundError("question not found")
    answer = await _answer(session, version.id)
    try:
        _validate(
            _draft(
                item_type=question.item_type,
                difficulty_level=question.difficulty_level,
                stem_text=str(version.stem_json.get("text", "")),
                options=_options_from_json(version.options_json or ()),
                answer_schema=answer.answer_schema,
                stimulus_id=version.stimulus_id,
                max_numeric_chars=_max_numeric_chars(version),
            )
        )
    except QuestionValidationFailedError as exc:
        return ValidationReport(version_id=version.id, valid=False, code=exc.code, message=str(exc))
    return ValidationReport(version_id=version.id, valid=True, code=None, message=None)


async def publish_question_version(
    session: AsyncSession,
    version_id: uuid.UUID,
    *,
    approved_by: uuid.UUID,
    correlation_id: str | None = None,
) -> PublishResult:
    """Make the newest draft version the question's current version and publish the question.

    The database freezes the version and its answer in the same statement (trigger). Writes the
    `publications` log row and the `question.version.published` outbox event in the caller's transaction.
    No route yet: A09 calls this once a reviewer approves (spec C01 lists no publish endpoint).
    """
    version = await session.scalar(select(QuestionVersion).where(QuestionVersion.id == version_id).with_for_update())
    if version is None:
        raise QuestionNotFoundError("question version not found")
    question = await session.scalar(
        select(Question)
        .where(Question.id == version.question_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if question is None:
        raise QuestionNotFoundError("question not found")
    if version.is_immutable:
        raise QuestionConflictError("VERSION_ALREADY_PUBLISHED", f"version {version.version_no} is already frozen")
    newest = await session.scalar(
        select(QuestionVersion.version_no)
        .where(QuestionVersion.question_id == question.id)
        .order_by(QuestionVersion.version_no.desc())
        .limit(1)
    )
    if newest != version.version_no:
        raise QuestionConflictError("VERSION_NOT_LATEST", f"version {version.version_no} is not the newest one")
    report = await validate_question_version(session, version.id)
    if not report.valid:
        raise QuestionValidationFailedError(report.code or "INVALID", report.message or "validation failed")
    decision = evaluate_publish_gate(
        verification_status=VerificationStatus(question.verification_status),
        rights_status=RightsStatus(question.rights_status),
        publication_status=PublicationStatus(question.publication_status),
        current_version_id=str(version.id),
    )
    if not decision.allowed_publish:
        raise PublishGateBlockedError(decision.blocking_codes, decision.reasons)

    await session.execute(
        update(QuestionVersion).where(QuestionVersion.id == version.id).values(approved_by=approved_by)
    )
    await session.execute(
        update(Question)
        .where(Question.id == question.id)
        .values(publication_status="PUBLISHED", current_version_id=version.id)
    )
    # `publications` is the B01 publication log; entity_type QUESTION is part of its CHECK.
    publication_id = (
        await session.execute(
            text(
                "INSERT INTO publications (tenant_id, entity_type, entity_id, version_id, published_by) "
                "VALUES (:t, 'QUESTION', :e, :v, :p) RETURNING id"
            ),
            {"t": question.tenant_id, "e": question.id, "v": version.id, "p": approved_by},
        )
    ).scalar_one()
    envelope = EventEnvelopeContract(
        event_type=QUESTION_PUBLISHED_EVENT,
        tenant_id=question.tenant_id,
        aggregate_type="question",
        aggregate_id=question.id,
        correlation_id=correlation_id,
        payload={
            "question_id": str(question.id),
            "version_id": str(version.id),
            "version_no": version.version_no,
            "taxonomy_node_id": None
            if question.primary_taxonomy_node_id is None
            else str(question.primary_taxonomy_node_id),
            "exam_tags": list(question.exam_framework_tags or ()),
        },
    )
    await enqueue(session, envelope)
    fresh_question = await get_question(session, question.id)
    fresh_version = await get_question_version(session, version.id)
    assert fresh_question is not None and fresh_version is not None
    return PublishResult(fresh_question, fresh_version, publication_id, envelope.event_id)


# --- C02 quality hooks ----------------------------------------------------------------------------


async def get_verification_target(session: AsyncSession, version_id: uuid.UUID) -> VerificationTarget | None:
    """The version's item type and answer, for an independent check (None: not visible to the tenant)."""
    row = (
        await session.execute(
            select(
                QuestionVersion.id, QuestionVersion.question_id, Question.item_type, QuestionVersionAnswer.answer_schema
            )
            .join(Question, Question.id == QuestionVersion.question_id)
            .join(QuestionVersionAnswer, QuestionVersionAnswer.question_version_id == QuestionVersion.id)
            .where(QuestionVersion.id == version_id)
        )
    ).one_or_none()
    if row is None:
        return None
    return VerificationTarget(
        version_id=row.id, question_id=row.question_id, item_type=row.item_type, answer_schema=dict(row.answer_schema)
    )


AUTOMATIC_VERIFICATION_OPEN = ("UNCHECKED", "AUTO_VERIFIED", "DISPUTED")


async def record_automatic_verification(session: AsyncSession, version_id: uuid.UUID, matches: bool | None) -> str:
    """Move the question to AUTO_VERIFIED (match) or DISPUTED (mismatch) after an automatic check of its newest
    version; returns the question's verification status afterwards.

    Left as it is when the check could not run (`matches is None`), when an older version was checked, when a
    human already decided (VERIFIED / REJECTED: only A09 changes those) or when the question is published.
    """
    version = await session.scalar(select(QuestionVersion).where(QuestionVersion.id == version_id))
    if version is None:
        raise QuestionNotFoundError("question version not found")
    question = await session.scalar(
        select(Question)
        .where(Question.id == version.question_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if question is None:
        raise QuestionNotFoundError("question not found")
    newest = await session.scalar(
        select(QuestionVersion.version_no)
        .where(QuestionVersion.question_id == question.id)
        .order_by(QuestionVersion.version_no.desc())
        .limit(1)
    )
    if (
        matches is None
        or newest != version.version_no
        or question.verification_status not in AUTOMATIC_VERIFICATION_OPEN
        or question.publication_status == "PUBLISHED"
    ):
        return question.verification_status
    status = "AUTO_VERIFIED" if matches else "DISPUTED"
    if status != question.verification_status:
        await session.execute(update(Question).where(Question.id == question.id).values(verification_status=status))
    return status


# --- reading the bank (no answer is ever read here) ----------------------------------------------------------------

_LIST_QUESTIONS_ROUTE = "GET /api/v1/questions"
_Q_MAX_LENGTH = 200


@dataclass(frozen=True, slots=True)
class QuestionListItem:
    """A question with the version a list shows: the published (served) one, else the newest draft. No answer."""

    question: QuestionRecord
    version_id: uuid.UUID
    version_no: int
    is_immutable: bool
    stimulus_id: uuid.UUID | None
    stem_text: str
    options: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class QuestionListPage:
    items: tuple[QuestionListItem, ...]
    next_cursor: str | None
    has_more: bool
    limit: int


@dataclass(frozen=True, slots=True)
class VersionSummary:
    id: uuid.UUID
    version_no: int
    is_immutable: bool
    is_current: bool
    change_reason: str | None
    created_by: uuid.UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class QuestionDetail:
    question: QuestionRecord
    shown_version: QuestionVersionRecord | None
    versions: tuple[VersionSummary, ...]


def _like_pattern(raw: str) -> str:
    """A case-insensitive "contains" pattern with the LIKE wildcards of the input made literal."""
    escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def _taxonomy_subtree(session: AsyncSession, node_id: uuid.UUID) -> list[uuid.UUID]:
    """The node and everything under it (ltree), as far as the tenant can see them."""
    rows = await session.execute(
        text(
            "SELECT t.id FROM taxonomy_nodes t "
            "WHERE t.code_path <@ (SELECT code_path FROM taxonomy_nodes WHERE id = :n)"
        ),
        {"n": node_id},
    )
    return [r.id for r in rows]


async def _curriculum_subtree(session: AsyncSession, node_id: uuid.UUID) -> list[uuid.UUID]:
    rows = await session.execute(
        text(
            "WITH RECURSIVE tree AS ("
            "  SELECT id FROM curriculum_nodes WHERE id = :n"
            "  UNION"
            "  SELECT c.id FROM curriculum_nodes c JOIN tree ON c.parent_id = tree.id"
            ") SELECT id FROM tree"
        ),
        {"n": node_id},
    )
    return [r.id for r in rows]


async def list_questions(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    cursor_secret: str,
    q: str | None = None,
    item_type: str | None = None,
    difficulty_level: str | None = None,
    curriculum_node_id: uuid.UUID | None = None,
    taxonomy_node_id: uuid.UUID | None = None,
    publication_status: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> QuestionListPage:
    """The bank of the tenant plus the SYSTEM_PUBLIC questions, newest first (cursor pagination on created_at, id).

    Row-level security already shows only the tenant's own rows and the published, frozen SYSTEM_PUBLIC ones; the
    WHERE says it again so a policy change cannot widen this list. `question_version_answers` is not touched:
    a list item carries the stem and the options, never an answer or a rationale. The taxonomy and curriculum
    filters include the whole subtree under the node.
    """
    safe_limit = clamp_limit(limit)
    query = (q or "").strip()
    if len(query) > _Q_MAX_LENGTH:
        raise ValueError(f"q is longer than {_Q_MAX_LENGTH} characters")
    for value, allowed, label in (
        (item_type, ITEM_TYPES, "item_type"),
        (difficulty_level, DIFFICULTY_LEVELS, "difficulty_level"),
        (publication_status, PUBLICATION_STATUSES, "publication_status"),
    ):
        if value is not None and value not in allowed:
            raise ValueError(f"unsupported {label}: {value!r}")

    latest_version_id = (
        select(QuestionVersion.id)
        .where(QuestionVersion.question_id == Question.id)
        .order_by(QuestionVersion.version_no.desc())
        .limit(1)
        .correlate(Question)
        .scalar_subquery()
    )
    stmt = (
        select(Question, QuestionVersion)
        .join(
            QuestionVersion,
            and_(
                QuestionVersion.id == func.coalesce(Question.current_version_id, latest_version_id),
                QuestionVersion.tenant_id == Question.tenant_id,
            ),
        )
        .where(or_(Question.tenant_id == tenant_id, Question.visibility_scope == "SYSTEM_PUBLIC"))
    )
    if query:
        pattern = _like_pattern(query)
        stmt = stmt.where(
            or_(
                Question.stable_code.ilike(pattern, escape="\\"),
                QuestionVersion.stem_json["text"].astext.ilike(pattern, escape="\\"),
            )
        )
    if item_type is not None:
        stmt = stmt.where(Question.item_type == item_type)
    if difficulty_level is not None:
        stmt = stmt.where(Question.difficulty_level == difficulty_level)
    if publication_status is not None:
        stmt = stmt.where(Question.publication_status == publication_status)
    if taxonomy_node_id is not None:
        stmt = stmt.where(Question.primary_taxonomy_node_id.in_(await _taxonomy_subtree(session, taxonomy_node_id)))
    if curriculum_node_id is not None:
        stmt = stmt.where(Question.curriculum_node_id.in_(await _curriculum_subtree(session, curriculum_node_id)))

    if cursor is not None:
        decoded = decode_cursor(
            cursor,
            secret=cursor_secret,
            allowed_sort_fields=frozenset({"created_at"}),
            expected_sort_field="created_at",
            expected_direction="desc",
            tenant_id=str(tenant_id),
            route=_LIST_QUESTIONS_ROUTE,
        )
        try:
            after_at = datetime.fromisoformat(str(decoded.sort_value))
            after_id = uuid.UUID(decoded.tie_id)
        except ValueError as exc:
            raise CursorError("malformed cursor payload") from exc
        stmt = stmt.where(
            or_(Question.created_at < after_at, and_(Question.created_at == after_at, Question.id < after_id))
        )

    rows = (
        await session.execute(
            stmt.order_by(Question.created_at.desc(), Question.id.desc())
            .limit(safe_limit + 1)
            .execution_options(populate_existing=True)
        )
    ).all()
    has_more = len(rows) > safe_limit
    items = tuple(
        QuestionListItem(
            question=_question(question),
            version_id=version.id,
            version_no=version.version_no,
            is_immutable=version.is_immutable,
            stimulus_id=version.stimulus_id,
            stem_text=str((version.stem_json or {}).get("text", "")),
            options=tuple(version.options_json or ()),
        )
        for question, version in rows[:safe_limit]
    )
    next_cursor: str | None = None
    if has_more and items:
        last = items[-1].question
        next_cursor = encode_cursor(
            sort_field="created_at",
            sort_value=last.created_at.isoformat(),
            tie_id=str(last.id),
            direction="desc",
            secret=cursor_secret,
            tenant_id=str(tenant_id),
            route=_LIST_QUESTIONS_ROUTE,
        )
    return QuestionListPage(items=items, next_cursor=next_cursor, has_more=has_more, limit=safe_limit)


async def get_question_detail(session: AsyncSession, question_id: uuid.UUID) -> QuestionDetail:
    """One question with the version the bank shows and the history of its versions (answers are not read)."""
    row = await session.scalar(
        select(Question).where(Question.id == question_id).execution_options(populate_existing=True)
    )
    if row is None:
        raise QuestionNotFoundError("question not found")
    versions = (
        await session.scalars(
            select(QuestionVersion)
            .where(QuestionVersion.question_id == question_id)
            .order_by(QuestionVersion.version_no.desc())
            .execution_options(populate_existing=True)
        )
    ).all()
    shown = next((v for v in versions if v.id == row.current_version_id), versions[0] if versions else None)
    return QuestionDetail(
        question=_question(row),
        shown_version=None if shown is None else _version(shown),
        versions=tuple(
            VersionSummary(
                id=v.id,
                version_no=v.version_no,
                is_immutable=v.is_immutable,
                is_current=v.id == row.current_version_id,
                change_reason=v.change_reason,
                created_by=v.created_by,
                created_at=v.created_at,
            )
            for v in versions
        ),
    )
