"""DB application service for A08 3-tier question deduplication & source pairing."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.events import EventEnvelopeContract
from app.modules.dedup.internal.hashing import compute_question_hashes, normalize_text
from app.modules.dedup.internal.models import CLUSTER_STATUSES, DuplicateCluster, DuplicateMember
from app.modules.outbox.public import enqueue_event

_SCORE_QUANT = Decimal("0.001")
_RESOLVED_DECISIONS = frozenset({"RESOLVED_MERGED", "RESOLVED_KEEP_VARIANTS"})


class DedupError(RuntimeError):
    """Base error for A08 dedup operations."""


class DuplicateClusterNotFoundError(DedupError):
    """Raised when duplicate_clusters row is missing or invisible under RLS (HTTP 404)."""


class CandidateNotFoundError(DedupError):
    """Raised when referenced candidate_id does not exist under tenant RLS (HTTP 404)."""


class DedupValidationError(ValueError, DedupError):
    """Raised when dedup request inputs or decisions are invalid (HTTP 422)."""


@dataclass(frozen=True, slots=True)
class DedupMatchCandidate:
    """Matched candidate summary from tier-1 exact, tier-2 structural, or tier-3 semantic search."""

    candidate_id: uuid.UUID
    match_tier: str
    similarity_score: Decimal
    existing_cluster_id: uuid.UUID | None
    stem_md: str


@dataclass(frozen=True, slots=True)
class DuplicateMemberView:
    """Member representation inside a duplicate cluster."""

    cluster_id: uuid.UUID
    candidate_id: uuid.UUID
    tenant_id: uuid.UUID
    similarity_score: Decimal


@dataclass(frozen=True, slots=True)
class DuplicateClusterView:
    """Read/write view of one DuplicateCluster and all members."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    cluster_type: str
    canonical_question_id: uuid.UUID | None
    status: str
    created_at: datetime
    members: tuple[DuplicateMemberView, ...]


@dataclass(frozen=True, slots=True)
class DedupMatchResult:
    """Result returned by POST /api/v1/dedup/match."""

    content_hash: str
    structural_hash: str
    cluster: DuplicateClusterView | None
    matches: tuple[DedupMatchCandidate, ...]


def _trigram_set(text_value: str) -> set[str]:
    norm = f"  {normalize_text(text_value)}  "
    if len(norm) < 3:
        return {norm}
    return {norm[i : i + 3] for i in range(len(norm) - 2)}


def _trigram_similarity(left: str, right: str) -> Decimal:
    tri_l = _trigram_set(left)
    tri_r = _trigram_set(right)
    union_len = len(tri_l | tri_r)
    if union_len == 0:
        return Decimal("0.000")
    score = Decimal(len(tri_l & tri_r)) / Decimal(union_len)
    return min(Decimal("0.999"), score.quantize(_SCORE_QUANT, rounding=ROUND_HALF_UP))


def _extract_stem_and_options(payload: dict[str, Any] | None) -> tuple[str, list[str]]:
    data = payload or {}
    stem = str(data.get("stem_md") or data.get("stem") or data.get("prompt") or "")
    raw_opts = data.get("options_md") or data.get("options") or ()
    options: list[str] = []
    if isinstance(raw_opts, list):
        for item in raw_opts:
            if isinstance(item, dict):
                options.append(str(item.get("content_md") or item.get("text") or item.get("label") or ""))
            else:
                options.append(str(item))
    return stem, options


def _cluster_to_view(
    cluster: DuplicateCluster,
    members: Sequence[DuplicateMember],
) -> DuplicateClusterView:
    return DuplicateClusterView(
        id=cluster.id,
        tenant_id=cluster.tenant_id,
        cluster_type=cluster.cluster_type,
        canonical_question_id=cluster.canonical_question_id,
        status=cluster.status,
        created_at=cluster.created_at,
        members=tuple(
            DuplicateMemberView(
                cluster_id=m.cluster_id,
                candidate_id=m.candidate_id,
                tenant_id=m.tenant_id,
                similarity_score=Decimal(m.similarity_score),
            )
            for m in sorted(members, key=lambda x: (-Decimal(x.similarity_score), str(x.candidate_id)))
        ),
    )


async def _fetch_cluster_view(
    session: AsyncSession,
    *,
    cluster_id: uuid.UUID,
) -> DuplicateClusterView:
    cluster = (
        await session.execute(select(DuplicateCluster).where(DuplicateCluster.id == cluster_id))
    ).scalar_one_or_none()
    if cluster is None:
        raise DuplicateClusterNotFoundError(f"duplicate cluster {cluster_id} not found")
    members = (
        await session.execute(
            select(DuplicateMember).where(DuplicateMember.cluster_id == cluster_id)
        )
    ).scalars().all()
    return _cluster_to_view(cluster, members)


async def get_duplicate_cluster(
    session: AsyncSession,
    *,
    cluster_id: uuid.UUID,
) -> DuplicateClusterView:
    """Fetch a single DuplicateCluster and its DuplicateMembers under current tenant RLS."""
    return await _fetch_cluster_view(session, cluster_id=cluster_id)


async def match_question_candidate(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    stem_md: str,
    options_md: Sequence[str] = (),
    candidate_id: uuid.UUID | None = None,
    semantic_threshold: Decimal = Decimal("0.550"),
    enable_semantic_tier: bool = True,
    correlation_id: str = "dedup-match",
) -> DedupMatchResult:
    """Run 3-tier deduplication (content_hash -> structural_hash -> trigram/semantic) under RLS.

    When matches are found and `candidate_id` exists in `question_candidates`, ensures a
    `duplicate_clusters` row and `duplicate_members` links are persisted and synced onto
    `question_candidates.duplicate_cluster_id`.
    """
    normalized_stem = normalize_text(stem_md)
    if not normalized_stem:
        raise DedupValidationError("stem_md must not be blank")
    if semantic_threshold <= Decimal("0") or semantic_threshold > Decimal("1"):
        raise DedupValidationError("semantic_threshold must be in (0, 1]")

    hashes = compute_question_hashes(stem_md, options_md)

    if candidate_id is not None:
        exists_row = (
            await session.execute(
                text("SELECT id FROM question_candidates WHERE id = :cid"),
                {"cid": candidate_id},
            )
        ).first()
        if exists_row is None:
            raise CandidateNotFoundError(f"question candidate {candidate_id} not found")

    rows = (
        await session.execute(
            text(
                """
                SELECT id, payload_json, content_hash, structural_hash, duplicate_cluster_id
                FROM question_candidates
                WHERE (:cid_is_null OR id <> :cid)
                ORDER BY created_at ASC, id ASC
                """
            ),
            {
                "cid_is_null": candidate_id is None,
                "cid": candidate_id or uuid.UUID(int=0),
            },
        )
    ).mappings().all()

    exact_matches: list[DedupMatchCandidate] = []
    structural_matches: list[DedupMatchCandidate] = []
    semantic_matches: list[DedupMatchCandidate] = []

    for row in rows:
        other_id = uuid.UUID(str(row["id"]))
        other_content = str(row["content_hash"])
        other_struct = str(row["structural_hash"])
        other_cluster = uuid.UUID(str(row["duplicate_cluster_id"])) if row["duplicate_cluster_id"] else None
        other_stem, _ = _extract_stem_and_options(row["payload_json"])

        if other_content == hashes.content_hash:
            exact_matches.append(
                DedupMatchCandidate(
                    candidate_id=other_id,
                    match_tier="EXACT",
                    similarity_score=Decimal("1.000"),
                    existing_cluster_id=other_cluster,
                    stem_md=other_stem,
                )
            )
        elif other_struct == hashes.structural_hash:
            structural_matches.append(
                DedupMatchCandidate(
                    candidate_id=other_id,
                    match_tier="STRUCTURAL_VARIANT",
                    similarity_score=Decimal("0.920"),
                    existing_cluster_id=other_cluster,
                    stem_md=other_stem,
                )
            )
        elif enable_semantic_tier and other_stem:
            score = _trigram_similarity(stem_md, other_stem)
            if score >= semantic_threshold:
                semantic_matches.append(
                    DedupMatchCandidate(
                        candidate_id=other_id,
                        match_tier="SEMANTIC_NEAR",
                        similarity_score=score,
                        existing_cluster_id=other_cluster,
                        stem_md=other_stem,
                    )
                )

    semantic_matches.sort(key=lambda m: (-m.similarity_score, str(m.candidate_id)))

    if exact_matches:
        chosen_type = "EXACT"
        all_matches = exact_matches + structural_matches
    elif structural_matches:
        chosen_type = "STRUCTURAL_VARIANT"
        all_matches = structural_matches
    elif semantic_matches:
        chosen_type = "SEMANTIC_NEAR"
        all_matches = semantic_matches
    else:
        return DedupMatchResult(
            content_hash=hashes.content_hash,
            structural_hash=hashes.structural_hash,
            cluster=None,
            matches=(),
        )

    cluster_view: DuplicateClusterView | None = None
    if candidate_id is not None:
        existing_cluster_id = next((m.existing_cluster_id for m in all_matches if m.existing_cluster_id), None)
        if existing_cluster_id is not None:
            cluster_row = (
                await session.execute(select(DuplicateCluster).where(DuplicateCluster.id == existing_cluster_id))
            ).scalar_one_or_none()
        else:
            cluster_row = None

        created_new_cluster = False
        if cluster_row is None:
            cluster_row = DuplicateCluster(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                cluster_type=chosen_type,
                canonical_question_id=None,
                status="PENDING_REVIEW",
            )
            session.add(cluster_row)
            await session.flush()
            created_new_cluster = True

        # Upsert source matched candidates + target candidate_id into duplicate_members
        member_payloads: list[dict[str, Any]] = [
            {
                "cluster_id": cluster_row.id,
                "candidate_id": m.candidate_id,
                "tenant_id": tenant_id,
                "similarity_score": m.similarity_score,
            }
            for m in all_matches
        ]
        top_score = all_matches[0].similarity_score
        member_payloads.append(
            {
                "cluster_id": cluster_row.id,
                "candidate_id": candidate_id,
                "tenant_id": tenant_id,
                "similarity_score": top_score,
            }
        )

        stmt = pg_insert(DuplicateMember).values(member_payloads).on_conflict_do_nothing(
            index_elements=["cluster_id", "candidate_id"]
        )
        await session.execute(stmt)

        target_ids = [candidate_id, *(m.candidate_id for m in all_matches)]
        for cid in target_ids:
            await session.execute(
                text(
                    "UPDATE question_candidates SET duplicate_cluster_id = :cluster_id WHERE id = :cid"
                ),
                {"cluster_id": cluster_row.id, "cid": cid},
            )

        if created_new_cluster:
            await enqueue_event(
                session,
                EventEnvelopeContract(
                    event_type="dedup.cluster_created",
                    tenant_id=tenant_id,
                    aggregate_type="duplicate_cluster",
                    aggregate_id=cluster_row.id,
                    correlation_id=correlation_id,
                    payload={
                        "cluster_id": str(cluster_row.id),
                        "cluster_type": cluster_row.cluster_type,
                        "candidate_id": str(candidate_id),
                        "member_count": len({m["candidate_id"] for m in member_payloads}),
                    },
                ),
            )

        await session.flush()
        cluster_view = await _fetch_cluster_view(session, cluster_id=cluster_row.id)

    return DedupMatchResult(
        content_hash=hashes.content_hash,
        structural_hash=hashes.structural_hash,
        cluster=cluster_view,
        matches=tuple(all_matches),
    )


async def decide_duplicate_cluster(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    cluster_id: uuid.UUID,
    decision: str,
    canonical_question_id: uuid.UUID | None = None,
    decided_by: uuid.UUID | None = None,
    note: str | None = None,
    correlation_id: str = "dedup-decision",
) -> DuplicateClusterView:
    """Resolve a DuplicateCluster as RESOLVED_MERGED or RESOLVED_KEEP_VARIANTS."""
    if decision not in _RESOLVED_DECISIONS or decision not in CLUSTER_STATUSES:
        raise DedupValidationError(
            f"decision must be one of {sorted(_RESOLVED_DECISIONS)}, got {decision!r}"
        )

    cluster = (
        await session.execute(select(DuplicateCluster).where(DuplicateCluster.id == cluster_id))
    ).scalar_one_or_none()
    if cluster is None:
        raise DuplicateClusterNotFoundError(f"duplicate cluster {cluster_id} not found")

    if decision == "RESOLVED_MERGED":
        cluster.canonical_question_id = canonical_question_id or cluster.canonical_question_id
    cluster.status = decision
    await session.flush()

    await enqueue_event(
        session,
        EventEnvelopeContract(
            event_type="dedup.cluster_resolved",
            tenant_id=tenant_id,
            aggregate_type="duplicate_cluster",
            aggregate_id=cluster.id,
            correlation_id=correlation_id,
            payload={
                "cluster_id": str(cluster.id),
                "status": cluster.status,
                "canonical_question_id": str(cluster.canonical_question_id)
                if cluster.canonical_question_id
                else None,
                "decided_by": str(decided_by) if decided_by else None,
                "note": note,
            },
        ),
    )
    await session.flush()
    return await _fetch_cluster_view(session, cluster_id=cluster.id)
