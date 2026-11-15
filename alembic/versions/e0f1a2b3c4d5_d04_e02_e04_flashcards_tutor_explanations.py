"""D04 flashcards, E02 tutor, E04 explanations: flashcard_decks, flashcards, revision_schedules, tutor_sessions,
tutor_messages, retrieval_traces, explanation_runs

Tables as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql, all strict tenant tables (section 2).
* flashcards.source_question_version_id, tutor_sessions.context_question_version_id and
  explanation_runs.question_version_id reference question_versions (single column, NO ACTION as in the DDL: they may
  point at the system tenant's versions); tutor_sessions.context_content_version_id references content_versions alike.
* revision_schedules holds one FSRS-5 state per (membership, flashcard); retrieval_traces keeps what the tutor retrieved
  and its scores, so an answer can be audited.

Privileges, section 5 (grants_manifest.csv has no row for these tables): the API role (5a) runs the study loop. Decks,
cards, tutor sessions, messages, traces and explanation drafts are append-only for it; only the review schedule is
updated (each review moves due_at, stability and the rest). Nothing here is deleted.

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-11-15
"""

from alembic import op
from app.platform.rls import strict_tenant_rls

revision = "e0f1a2b3c4d5"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None

TABLES = (
    "flashcard_decks",
    "flashcards",
    "revision_schedules",
    "tutor_sessions",
    "tutor_messages",
    "retrieval_traces",
    "explanation_runs",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE flashcard_decks (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            membership_id UUID NOT NULL,
            title VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_flashcard_decks_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT fk_flashcard_decks_membership_tenant
                FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE flashcards (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            deck_id UUID NOT NULL,
            front_md TEXT NOT NULL,
            back_md TEXT NOT NULL,
            source_question_version_id UUID REFERENCES question_versions(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_flashcards_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT fk_flashcards_deck_tenant
                FOREIGN KEY (deck_id, tenant_id) REFERENCES flashcard_decks(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE revision_schedules (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            membership_id UUID NOT NULL,
            flashcard_id UUID NOT NULL,
            scheduler_version VARCHAR(30) NOT NULL DEFAULT 'FSRS_V5',
            stability NUMERIC(8,4) NOT NULL DEFAULT 1.0000,
            difficulty NUMERIC(6,4) NOT NULL DEFAULT 5.0000,
            retrievability NUMERIC(5,4) NOT NULL DEFAULT 1.0000,
            reps INT NOT NULL DEFAULT 0,
            lapses INT NOT NULL DEFAULT 0,
            last_review_at TIMESTAMPTZ,
            due_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_revision_schedules_membership_flashcard UNIQUE (membership_id, flashcard_id),
            CONSTRAINT fk_revision_schedules_membership_tenant
                FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_revision_schedules_flashcard_tenant
                FOREIGN KEY (flashcard_id, tenant_id) REFERENCES flashcards(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE tutor_sessions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            membership_id UUID NOT NULL,
            context_question_version_id UUID REFERENCES question_versions(id),
            context_content_version_id UUID REFERENCES content_versions(id),
            tutor_mode VARCHAR(30) NOT NULL DEFAULT 'HINT_MODE',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_tutor_sessions_tutor_mode
                CHECK (tutor_mode IN ('HINT_MODE', 'EXPLAIN_MODE', 'CHALLENGE_MODE')),
            CONSTRAINT uq_tutor_sessions_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT fk_tutor_sessions_membership_tenant
                FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE tutor_messages (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            tutor_session_id UUID NOT NULL,
            sender_role VARCHAR(20) NOT NULL,
            message_md TEXT NOT NULL,
            citations_json JSONB NOT NULL DEFAULT '[]',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_tutor_messages_sender_role CHECK (sender_role IN ('USER', 'ASSISTANT', 'SYSTEM_GUARD')),
            CONSTRAINT fk_tutor_messages_session_tenant
                FOREIGN KEY (tutor_session_id, tenant_id)
                REFERENCES tutor_sessions(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE retrieval_traces (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            tutor_session_id UUID NOT NULL,
            retrieved_ids UUID[] NOT NULL,
            scores NUMERIC(5,4)[] NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_retrieval_traces_session_tenant
                FOREIGN KEY (tutor_session_id, tenant_id)
                REFERENCES tutor_sessions(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE explanation_runs (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            question_version_id UUID NOT NULL REFERENCES question_versions(id),
            draft_rationale_json JSONB NOT NULL,
            sympy_verified BOOLEAN NOT NULL DEFAULT FALSE,
            status VARCHAR(30) NOT NULL DEFAULT 'PENDING_APPROVAL',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    for table in TABLES:
        for stmt in strict_tenant_rls(table):
            op.execute(stmt)

    # --- privileges (section 5a) ----------------------------------------------------------------
    op.execute(f"REVOKE ALL ON {', '.join(TABLES)} FROM PUBLIC")
    op.execute(
        "GRANT SELECT, INSERT ON flashcard_decks, flashcards, tutor_sessions, tutor_messages, retrieval_traces, "
        "explanation_runs TO ulp_app_rw"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON revision_schedules TO ulp_app_rw")  # each review moves the schedule


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
