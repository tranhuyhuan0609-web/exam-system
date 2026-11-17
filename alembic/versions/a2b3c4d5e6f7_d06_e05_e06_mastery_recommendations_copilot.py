"""D06 mastery, E05 recommendations, E06 teacher copilot: mastery_estimates, question_irt_parameters, recommendations,
teacher_assist_jobs

Tables as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql:
* mastery_estimates, recommendations and teacher_assist_jobs are strict tenant tables (section 2).
* question_irt_parameters has no tenant column and no RLS, as in the DDL and Decision #7 ("keep as it is"): the
  calibration of a question version is shared by every tenant that uses the version. Its CHECK constraints are the
  calibration gate (at least 150 answers from 100 learners, ability spread >= 0.6, converged, infit 0.7..1.3, no
  DIF above 0.5, standard error of the difficulty below 0.3), so a row that fails them cannot exist.

Privileges, section 5 (grants_manifest.csv has no row for these tables): the API role (5a) runs the learner and teacher
loops. It upserts mastery estimates and calibrations, updates recommendations and copilot jobs, and may delete a
learner's stale recommendations when they are recomputed.

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-11-15
"""

from alembic import op

from app.platform.rls import strict_tenant_rls

revision = "a2b3c4d5e6f7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None

TABLES = ("mastery_estimates", "question_irt_parameters", "recommendations", "teacher_assist_jobs")
STRICT_TABLES = ("mastery_estimates", "recommendations", "teacher_assist_jobs")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE mastery_estimates (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            membership_id UUID NOT NULL,
            taxonomy_node_id UUID NOT NULL REFERENCES taxonomy_nodes(id) ON DELETE CASCADE,
            model_code VARCHAR(40) NOT NULL DEFAULT 'RULE_WEIGHTED_L1',
            theta_estimate NUMERIC(6,3) NOT NULL DEFAULT 0.000,
            mastery_probability NUMERIC(5,3) NOT NULL,
            standard_error NUMERIC(5,3) NOT NULL DEFAULT 0.500,
            evidence_count INT NOT NULL DEFAULT 0,
            status VARCHAR(30) NOT NULL DEFAULT 'INSUFFICIENT_DATA',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_mastery_estimates_model_code CHECK (
                model_code IN ('RULE_WEIGHTED_L1', 'RASCH_1PL_L2', 'IRT_2PL_GPCM_L3', 'IRT_3PL_GPCM_L4')
            ),
            CONSTRAINT ck_mastery_estimates_prob CHECK (mastery_probability BETWEEN 0 AND 1),
            CONSTRAINT ck_mastery_estimates_status CHECK (status IN ('INSUFFICIENT_DATA', 'CALIBRATED')),
            CONSTRAINT uq_mastery_estimates_member_node_model UNIQUE (membership_id, taxonomy_node_id, model_code),
            CONSTRAINT fk_mastery_estimates_membership_tenant
                FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE question_irt_parameters (
            question_version_id UUID PRIMARY KEY REFERENCES question_versions(id) ON DELETE CASCADE,
            calibration_run_id UUID NOT NULL DEFAULT uuid_generate_v7(),
            model_type VARCHAR(30) NOT NULL,
            sample_size INT NOT NULL,
            learner_count INT NOT NULL,
            ability_spread_sd NUMERIC(5,3) NOT NULL,
            converged BOOLEAN NOT NULL DEFAULT TRUE,
            infit_msq NUMERIC(5,3) NOT NULL,
            outfit_msq NUMERIC(5,3) NOT NULL,
            max_abs_dif NUMERIC(5,3) NOT NULL DEFAULT 0.000,
            discrimination_a NUMERIC(6,4) NOT NULL DEFAULT 1.0000,
            difficulty_b NUMERIC(6,4) NOT NULL,
            guessing_c NUMERIC(6,4) NOT NULL DEFAULT 0.0000,
            step_thresholds_json JSONB NOT NULL DEFAULT '[]',
            se_difficulty NUMERIC(6,4) NOT NULL,
            diagnostics_json JSONB NOT NULL DEFAULT '{}',
            approved_by UUID REFERENCES users(id),
            calibrated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_question_irt_parameters_model_type
                CHECK (model_type IN ('RASCH_1PL_L2', 'IRT_2PL_GPCM_L3', 'IRT_3PL_GPCM_L4')),
            CONSTRAINT ck_question_irt_parameters_sample_size CHECK (sample_size >= 150),
            CONSTRAINT ck_question_irt_parameters_learner_count CHECK (learner_count >= 100),
            CONSTRAINT ck_question_irt_parameters_ability_sd CHECK (ability_spread_sd >= 0.600),
            CONSTRAINT ck_question_irt_parameters_converged CHECK (converged IS TRUE),
            CONSTRAINT ck_question_irt_parameters_infit CHECK (infit_msq BETWEEN 0.700 AND 1.300),
            CONSTRAINT ck_question_irt_parameters_max_dif CHECK (max_abs_dif <= 0.500),
            CONSTRAINT ck_question_irt_parameters_se_b CHECK (se_difficulty < 0.3000)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE recommendations (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            membership_id UUID NOT NULL,
            recommended_action_type VARCHAR(40) NOT NULL,
            target_entity_id UUID NOT NULL,
            evidence_json JSONB NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_recommendations_status CHECK (status IN ('ACTIVE', 'DISMISSED', 'COMPLETED')),
            CONSTRAINT fk_recommendations_membership_tenant
                FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE teacher_assist_jobs (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            class_id UUID NOT NULL,
            assignment_id UUID NOT NULL,
            summary_report_json JSONB NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'READY_FOR_TEACHER',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_teacher_assist_jobs_class_tenant
                FOREIGN KEY (class_id, tenant_id) REFERENCES classes(id, tenant_id) ON DELETE CASCADE
        )
        """
    )

    for table in STRICT_TABLES:
        for stmt in strict_tenant_rls(table):
            op.execute(stmt)

    # --- privileges (section 5a) ----------------------------------------------------------------
    op.execute(f"REVOKE ALL ON {', '.join(TABLES)} FROM PUBLIC")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON mastery_estimates, question_irt_parameters, teacher_assist_jobs TO ulp_app_rw"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON recommendations TO ulp_app_rw")  # recomputing replaces them


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
