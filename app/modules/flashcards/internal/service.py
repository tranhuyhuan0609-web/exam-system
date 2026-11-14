"""Database service layer for D04 Flashcards & Spaced Repetition (FSRS v5)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.flashcards.internal.fsrs import (
    SCHEDULER_VERSION,
    VN_TZ,
    FSRSRating,
    FSRSState,
    review_card,
)
from app.modules.flashcards.internal.models import (
    Flashcard,
    FlashcardDeck,
    RevisionSchedule,
)

_FOUR_DP = Decimal("0.0001")
_MAX_STABILITY = Decimal("9999.9999")
_MIN_STABILITY = Decimal("0.1000")


class FlashcardValidationError(ValueError):
    """Raised when deck payload, card content, or FSRS rating is invalid."""


class FlashcardNotFoundError(LookupError):
    """Raised when a deck or flashcard does not exist in the caller's tenant."""


@dataclass(frozen=True)
class NewFlashcardInput:
    front_md: str
    back_md: str
    source_question_version_id: uuid.UUID | None = None


@dataclass(frozen=True)
class FlashcardView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    deck_id: uuid.UUID
    front_md: str
    back_md: str
    source_question_version_id: uuid.UUID | None
    created_at: datetime


@dataclass(frozen=True)
class FlashcardDeckView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    title: str
    created_at: datetime
    cards: tuple[FlashcardView, ...]


@dataclass(frozen=True)
class FlashcardReviewView:
    schedule_id: uuid.UUID
    flashcard_id: uuid.UUID
    membership_id: uuid.UUID
    tenant_id: uuid.UUID
    rating: int
    scheduler_version: str
    stability: Decimal
    difficulty: Decimal
    retrievability: Decimal
    reps: int
    lapses: int
    interval_days: int
    last_review_at: datetime
    due_at: datetime
    next_review_date: date


@dataclass(frozen=True)
class RevisionScheduleItemView:
    schedule_id: uuid.UUID
    flashcard_id: uuid.UUID
    deck_id: uuid.UUID
    deck_title: str
    front_md: str
    back_md: str
    scheduler_version: str
    stability: Decimal
    difficulty: Decimal
    retrievability: Decimal
    reps: int
    lapses: int
    last_review_at: datetime | None
    due_at: datetime
    next_review_date: date
    is_due: bool


def _to_decimal_clamped(value: float, low: Decimal, high: Decimal) -> Decimal:
    quantized = Decimal(f"{value:.4f}").quantize(_FOUR_DP)
    return max(low, min(high, quantized))


def _ensure_aware_utc_or_vn(ts: datetime | None) -> datetime:
    if ts is None:
        return datetime.now(UTC).astimezone(VN_TZ)
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise FlashcardValidationError("timestamp must be timezone-aware")
    return ts.astimezone(VN_TZ)


def _card_view(card: Flashcard) -> FlashcardView:
    return FlashcardView(
        id=card.id,
        tenant_id=card.tenant_id,
        deck_id=card.deck_id,
        front_md=card.front_md,
        back_md=card.back_md,
        source_question_version_id=card.source_question_version_id,
        created_at=card.created_at,
    )


async def create_flashcard_deck(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    title: str,
    cards: Sequence[NewFlashcardInput] = (),
    now: datetime | None = None,
) -> FlashcardDeckView:
    """Create a flashcard deck along with optional cards and initial FSRS schedules."""
    clean_title = title.strip()
    if not clean_title:
        raise FlashcardValidationError("deck title must not be blank")

    now_vn = _ensure_aware_utc_or_vn(now)
    deck = FlashcardDeck(
        tenant_id=tenant_id,
        membership_id=membership_id,
        title=clean_title,
    )
    session.add(deck)
    await session.flush()

    created_cards: list[Flashcard] = []
    for item in cards:
        front = item.front_md.strip()
        back = item.back_md.strip()
        if not front or not back:
            raise FlashcardValidationError("flashcard front_md and back_md must not be blank")
        card = Flashcard(
            tenant_id=tenant_id,
            deck_id=deck.id,
            front_md=front,
            back_md=back,
            source_question_version_id=item.source_question_version_id,
        )
        session.add(card)
        await session.flush()
        created_cards.append(card)
        session.add(
            RevisionSchedule(
                tenant_id=tenant_id,
                membership_id=membership_id,
                flashcard_id=card.id,
                scheduler_version=SCHEDULER_VERSION,
                due_at=now_vn,
            )
        )

    await session.flush()
    return FlashcardDeckView(
        id=deck.id,
        tenant_id=deck.tenant_id,
        membership_id=deck.membership_id,
        title=deck.title,
        created_at=deck.created_at,
        cards=tuple(_card_view(c) for c in created_cards),
    )


async def review_flashcard(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    flashcard_id: uuid.UUID,
    rating: int,
    reviewed_at: datetime | None = None,
) -> FlashcardReviewView:
    """Apply an FSRS v5 rating (`1..4`) and persist the next schedule in `Asia/Ho_Chi_Minh`."""
    try:
        fsrs_rating = FSRSRating(rating)
    except ValueError as exc:
        raise FlashcardValidationError(f"invalid FSRS rating {rating!r}; expected 1..4") from exc

    reviewed_vn = _ensure_aware_utc_or_vn(reviewed_at)

    card_row = (
        await session.execute(
            select(Flashcard, FlashcardDeck)
            .join(FlashcardDeck, Flashcard.deck_id == FlashcardDeck.id)
            .where(Flashcard.id == flashcard_id, Flashcard.tenant_id == tenant_id)
        )
    ).first()
    if card_row is None:
        raise FlashcardNotFoundError(f"flashcard {flashcard_id} not found")
    _card, deck = card_row
    if deck.membership_id != membership_id:
        raise FlashcardNotFoundError(f"flashcard {flashcard_id} does not belong to caller membership")

    schedule = (
        await session.execute(
            select(RevisionSchedule)
            .where(
                RevisionSchedule.tenant_id == tenant_id,
                RevisionSchedule.membership_id == membership_id,
                RevisionSchedule.flashcard_id == flashcard_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    current_state: FSRSState | None = None
    if schedule is not None and schedule.reps > 0 and schedule.last_review_at is not None:
        current_state = FSRSState(
            stability=float(schedule.stability),
            difficulty=float(schedule.difficulty),
            reps=schedule.reps,
            lapses=schedule.lapses,
            last_review_at=schedule.last_review_at.astimezone(VN_TZ),
            scheduler_version=schedule.scheduler_version,
        )

    result = review_card(current_state, fsrs_rating, reviewed_vn)
    stability_dec = _to_decimal_clamped(result.state.stability, _MIN_STABILITY, _MAX_STABILITY)
    difficulty_dec = _to_decimal_clamped(result.state.difficulty, Decimal("1.0000"), Decimal("10.0000"))
    retrievability_dec = _to_decimal_clamped(result.retrievability, Decimal("0.0000"), Decimal("1.0000"))

    if schedule is None:
        schedule = RevisionSchedule(
            tenant_id=tenant_id,
            membership_id=membership_id,
            flashcard_id=flashcard_id,
            scheduler_version=result.state.scheduler_version,
            stability=stability_dec,
            difficulty=difficulty_dec,
            retrievability=retrievability_dec,
            reps=result.state.reps,
            lapses=result.state.lapses,
            last_review_at=result.state.last_review_at,
            due_at=result.next_review_at,
        )
        session.add(schedule)
    else:
        schedule.scheduler_version = result.state.scheduler_version
        schedule.stability = stability_dec
        schedule.difficulty = difficulty_dec
        schedule.retrievability = retrievability_dec
        schedule.reps = result.state.reps
        schedule.lapses = result.state.lapses
        schedule.last_review_at = result.state.last_review_at
        schedule.due_at = result.next_review_at

    await session.flush()
    return FlashcardReviewView(
        schedule_id=schedule.id,
        flashcard_id=flashcard_id,
        membership_id=membership_id,
        tenant_id=tenant_id,
        rating=int(fsrs_rating),
        scheduler_version=schedule.scheduler_version,
        stability=schedule.stability,
        difficulty=schedule.difficulty,
        retrievability=schedule.retrievability,
        reps=schedule.reps,
        lapses=schedule.lapses,
        interval_days=result.interval_days,
        last_review_at=result.state.last_review_at or reviewed_vn,
        due_at=result.next_review_at,
        next_review_date=result.next_review_date,
    )


async def get_revision_schedule(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    due_only: bool = True,
    as_of: datetime | None = None,
    limit: int = 50,
) -> tuple[RevisionScheduleItemView, ...]:
    """List upcoming or due flashcards for the caller's membership ordered by `due_at ASC`."""
    if limit < 1 or limit > 200:
        raise FlashcardValidationError("limit must be between 1 and 200")
    cutoff = _ensure_aware_utc_or_vn(as_of)

    stmt = (
        select(RevisionSchedule, Flashcard, FlashcardDeck)
        .join(Flashcard, RevisionSchedule.flashcard_id == Flashcard.id)
        .join(FlashcardDeck, Flashcard.deck_id == FlashcardDeck.id)
        .where(
            RevisionSchedule.tenant_id == tenant_id,
            RevisionSchedule.membership_id == membership_id,
        )
        .order_by(RevisionSchedule.due_at.asc(), Flashcard.created_at.asc())
        .limit(limit)
    )
    if due_only:
        stmt = stmt.where(RevisionSchedule.due_at <= cutoff)

    rows = (await session.execute(stmt)).all()
    items: list[RevisionScheduleItemView] = []
    for sched, card, deck in rows:
        due_vn = sched.due_at.astimezone(VN_TZ)
        last_vn = sched.last_review_at.astimezone(VN_TZ) if sched.last_review_at is not None else None
        items.append(
            RevisionScheduleItemView(
                schedule_id=sched.id,
                flashcard_id=card.id,
                deck_id=deck.id,
                deck_title=deck.title,
                front_md=card.front_md,
                back_md=card.back_md,
                scheduler_version=sched.scheduler_version,
                stability=sched.stability,
                difficulty=sched.difficulty,
                retrievability=sched.retrievability,
                reps=sched.reps,
                lapses=sched.lapses,
                last_review_at=last_vn,
                due_at=due_vn,
                next_review_date=due_vn.date(),
                is_due=due_vn <= cutoff,
            )
        )
    return tuple(items)
