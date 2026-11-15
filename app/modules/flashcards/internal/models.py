"""SQLAlchemy ORM models for D04 Flashcards & Spaced Repetition (spec chapter 4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class FlashcardDeck(Base):
    """A learner's flashcard deck scoped to a membership within a tenant."""

    __tablename__ = "flashcard_decks"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_flashcard_decks_id_tenant_id"),
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_flashcard_decks_membership_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class Flashcard(Base):
    """An individual formula or error-review flashcard inside a deck."""

    __tablename__ = "flashcards"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_flashcards_id_tenant_id"),
        ForeignKeyConstraint(
            ["deck_id", "tenant_id"],
            ["flashcard_decks.id", "flashcard_decks.tenant_id"],
            ondelete="CASCADE",
            name="fk_flashcards_deck_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    deck_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    front_md: Mapped[str] = mapped_column(Text, nullable=False)
    back_md: Mapped[str] = mapped_column(Text, nullable=False)
    source_question_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("question_versions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class RevisionSchedule(Base):
    """FSRS v5 spaced-repetition memory state & next due timestamp per learner-card pair."""

    __tablename__ = "revision_schedules"
    __table_args__ = (
        UniqueConstraint("membership_id", "flashcard_id", name="uq_revision_schedules_membership_flashcard"),
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_revision_schedules_membership_tenant",
        ),
        ForeignKeyConstraint(
            ["flashcard_id", "tenant_id"],
            ["flashcards.id", "flashcards.tenant_id"],
            ondelete="CASCADE",
            name="fk_revision_schedules_flashcard_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    flashcard_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scheduler_version: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text("'FSRS_V5'")
    )
    stability: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, server_default=text("1.0000")
    )
    difficulty: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, server_default=text("5.0000")
    )
    retrievability: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, server_default=text("1.0000")
    )
    reps: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    lapses: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
