"""Database service layer for C03 Assessment Frameworks, Seeding, Blueprints & Shortage Report."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.blueprints import (
    BlueprintContract,
    BlueprintMatrixCell,
    BlueprintValidationError,
    FrameworkSpecContract,
    SectionSpecContract,
    ShortageReport,
)
from app.modules.frameworks.internal.blueprints import (
    THPTQG_2025_FRAMEWORKS,
    MatrixKey,
    get_thptqg_2025_framework,
    shortage_report,
    validate_blueprint,
)
from app.modules.frameworks.internal.models import (
    AssessmentFramework,
    Blueprint,
    FrameworkVersion,
    ScoringSchemeVersion,
)

THPTQG_2025_CODE = "THPTQG_2025"
THPTQG_2025_NAME = "Chuong trinh GDPT 2018 - Ky thi THPT Quoc gia 2025+"
THPTQG_2025_VERSION_CODE = "2025.1"
THPTQG_2025_SCORING_CODE = "THPTQG_2025_DEFAULT"

THPTQG_2025_SCORING_RULES: dict[str, Any] = {
    "framework_code": THPTQG_2025_CODE,
    "part_i_mcq": {"points_per_question": "0.25"},
    "part_ii_tf4": {
        "points_by_correct_subitems": {
            "0": "0.00",
            "1": "0.10",
            "2": "0.25",
            "3": "0.50",
            "4": "1.00",
        }
    },
    "part_iii_short": {
        "points_per_question_by_subject": {
            "MATH": "0.50",
            "PHYSICS": "0.25",
            "CHEMISTRY": "0.25",
            "BIOLOGY": "0.25",
        },
        "max_chars": 4,
        "allow_thousand_separator": False,
        "decimal_separators": [",", "."],
    },
}


class FrameworkValidationError(ValueError):
    """Raised when framework or blueprint payloads fail structural/type checks."""


class FrameworkNotFoundError(LookupError):
    """Raised when an assessment framework cannot be found."""


class BlueprintNotFoundError(LookupError):
    """Raised when a blueprint cannot be found or is invisible under RLS."""


@dataclass(frozen=True)
class FrameworkVersionView:
    id: uuid.UUID
    framework_id: uuid.UUID
    version_code: str
    subject_code: str
    total_questions: int
    duration_seconds: int
    sections: tuple[SectionSpecContract, ...]
    total_points: Decimal


@dataclass(frozen=True)
class AssessmentFrameworkView:
    id: uuid.UUID
    code: str
    name: str
    created_at: datetime
    versions: tuple[FrameworkVersionView, ...]


@dataclass(frozen=True)
class BlueprintView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    framework_id: uuid.UUID
    framework_code: str
    subject_code: str
    name: str
    duration_seconds: int
    total_points: Decimal
    sections: tuple[SectionSpecContract, ...]
    matrix_cells: tuple[BlueprintMatrixCell, ...]
    created_at: datetime


@dataclass(frozen=True)
class BlueprintValidationReportView:
    blueprint_id: uuid.UUID
    framework_code: str
    subject_code: str
    valid: bool
    errors: tuple[BlueprintValidationError, ...]
    shortage_report: ShortageReport


def _serialize_sections(sections: tuple[SectionSpecContract, ...]) -> list[dict[str, Any]]:
    return [
        {
            "section_code": sec.section_code,
            "item_type": sec.item_type.value,
            "question_count": sec.question_count,
            "points_per_question": str(sec.points_per_question),
        }
        for sec in sections
    ]


def _serialize_matrix(cells: tuple[BlueprintMatrixCell, ...]) -> list[dict[str, Any]]:
    return [
        {
            "taxonomy_node_code": cell.taxonomy_node_code,
            "cognitive_level": cell.cognitive_level,
            "item_type": cell.item_type.value,
            "required_count": cell.required_count,
        }
        for cell in cells
    ]


def _parse_section_specs_payload(raw_json: Any) -> tuple[tuple[SectionSpecContract, ...], Decimal]:
    total_points = Decimal("10.00")
    if isinstance(raw_json, dict):
        raw_sections = raw_json.get("sections", [])
        if raw_json.get("total_points") is not None:
            total_points = Decimal(str(raw_json["total_points"])).quantize(Decimal("0.01"))
    elif isinstance(raw_json, list):
        raw_sections = raw_json
    else:
        raise FrameworkValidationError("section_specs_json must be a dict or list")

    sections: list[SectionSpecContract] = []
    for item in raw_sections:
        sections.append(SectionSpecContract.model_validate(item))
    return tuple(sections), total_points


def _version_row_to_view(row: FrameworkVersion) -> FrameworkVersionView:
    sections, total_points = _parse_section_specs_payload(row.section_specs_json)
    return FrameworkVersionView(
        id=row.id,
        framework_id=row.framework_id,
        version_code=row.version_code,
        subject_code=row.subject_code,
        total_questions=row.total_questions,
        duration_seconds=row.duration_seconds,
        sections=sections,
        total_points=total_points,
    )


async def seed_thptqg_2025_frameworks_and_scoring(session: AsyncSession) -> AssessmentFrameworkView:
    """Idempotently seed standard THPTQG 2025+ framework versions (Math 22q/90m & NatSci 28q/50m) and scoring."""
    versions = tuple(THPTQG_2025_FRAMEWORKS.values())
    framework_view = await create_assessment_framework(
        session,
        code=THPTQG_2025_CODE,
        name=THPTQG_2025_NAME,
        version_code=THPTQG_2025_VERSION_CODE,
        versions=versions,
    )

    scoring_stmt = (
        pg_insert(ScoringSchemeVersion)
        .values(
            code=THPTQG_2025_SCORING_CODE,
            rules_json=THPTQG_2025_SCORING_RULES,
        )
        .on_conflict_do_update(
            index_elements=["code"],
            set_={"rules_json": THPTQG_2025_SCORING_RULES},
        )
    )
    await session.execute(scoring_stmt)
    return framework_view


async def create_assessment_framework(
    session: AsyncSession,
    *,
    code: str,
    name: str,
    version_code: str = THPTQG_2025_VERSION_CODE,
    versions: tuple[FrameworkSpecContract, ...] = (),
) -> AssessmentFrameworkView:
    """Create or update an assessment framework and its subject version specs."""
    normalized_code = code.strip().upper()
    cleaned_name = name.strip()
    cleaned_version = version_code.strip() or THPTQG_2025_VERSION_CODE
    if not normalized_code:
        raise FrameworkValidationError("framework code cannot be empty")
    if not cleaned_name:
        raise FrameworkValidationError("framework name cannot be empty")

    fw_stmt = (
        pg_insert(AssessmentFramework)
        .values(code=normalized_code, name=cleaned_name)
        .on_conflict_do_update(
            index_elements=["code"],
            set_={"name": cleaned_name},
        )
        .returning(AssessmentFramework.id)
    )
    fw_id = (await session.execute(fw_stmt)).scalar_one()

    specs_to_upsert = versions
    if not specs_to_upsert and normalized_code == THPTQG_2025_CODE:
        specs_to_upsert = tuple(THPTQG_2025_FRAMEWORKS.values())

    for spec in specs_to_upsert:
        subject_upper = spec.subject_code.strip().upper()
        total_q = sum(s.question_count for s in spec.sections)
        if spec.total_questions != total_q:
            raise FrameworkValidationError(
                f"total_questions ({spec.total_questions}) does not match section sum ({total_q}) for {subject_upper}"
            )
        payload = {
            "total_points": str(spec.total_points.quantize(Decimal("0.01"))),
            "sections": _serialize_sections(spec.sections),
        }
        ver_stmt = (
            pg_insert(FrameworkVersion)
            .values(
                framework_id=fw_id,
                version_code=cleaned_version,
                subject_code=subject_upper,
                total_questions=spec.total_questions,
                duration_seconds=spec.duration_seconds,
                section_specs_json=payload,
            )
            .on_conflict_do_update(
                index_elements=["framework_id", "version_code", "subject_code"],
                set_={
                    "total_questions": spec.total_questions,
                    "duration_seconds": spec.duration_seconds,
                    "section_specs_json": payload,
                },
            )
        )
        await session.execute(ver_stmt)

    fw_row = (
        await session.execute(select(AssessmentFramework).where(AssessmentFramework.id == fw_id))
    ).scalar_one()
    ver_rows = (
        await session.execute(
            select(FrameworkVersion)
            .where(FrameworkVersion.framework_id == fw_id)
            .order_by(FrameworkVersion.version_code.asc(), FrameworkVersion.subject_code.asc())
        )
    ).scalars().all()
    return AssessmentFrameworkView(
        id=fw_row.id,
        code=fw_row.code,
        name=fw_row.name,
        created_at=fw_row.created_at,
        versions=tuple(_version_row_to_view(v) for v in ver_rows),
    )


def _blueprint_row_to_view(row: Blueprint, *, framework_code: str) -> BlueprintView:
    rules = dict(row.rules_json or {})
    sections = tuple(SectionSpecContract.model_validate(s) for s in (rules.get("sections") or ()))
    matrix_cells = tuple(
        BlueprintMatrixCell.model_validate(c) for c in (rules.get("matrix_cells") or ())
    )
    return BlueprintView(
        id=row.id,
        tenant_id=row.tenant_id,
        framework_id=row.framework_id,
        framework_code=str(rules.get("framework_code") or framework_code),
        subject_code=str(rules.get("subject_code") or ""),
        name=row.name,
        duration_seconds=row.duration_seconds,
        total_points=Decimal(str(row.total_points)).quantize(Decimal("0.01")),
        sections=sections,
        matrix_cells=matrix_cells,
        created_at=row.created_at,
    )


async def create_blueprint(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    framework_code: str,
    name: str,
    subject_code: str,
    duration_seconds: int,
    total_points: Decimal = Decimal("10.00"),
    sections: tuple[SectionSpecContract, ...],
    matrix_cells: tuple[BlueprintMatrixCell, ...] = (),
    version_code: str = THPTQG_2025_VERSION_CODE,
) -> BlueprintView:
    """Persist a tenant-scoped blueprint linked to an assessment framework."""
    normalized_fw_code = framework_code.strip().upper()
    normalized_subject = subject_code.strip().upper()
    cleaned_name = name.strip()
    if not cleaned_name:
        raise FrameworkValidationError("Blueprint name cannot be empty")
    if not normalized_subject:
        raise FrameworkValidationError("subject_code cannot be empty")
    if duration_seconds <= 0:
        raise FrameworkValidationError("duration_seconds must be positive")
    if not sections:
        raise FrameworkValidationError("Blueprint must declare at least one section")

    fw_row = (
        await session.execute(
            select(AssessmentFramework).where(AssessmentFramework.code == normalized_fw_code)
        )
    ).scalar_one_or_none()
    if fw_row is None and normalized_fw_code == THPTQG_2025_CODE:
        await seed_thptqg_2025_frameworks_and_scoring(session)
        fw_row = (
            await session.execute(
                select(AssessmentFramework).where(AssessmentFramework.code == normalized_fw_code)
            )
        ).scalar_one_or_none()
    if fw_row is None:
        raise FrameworkNotFoundError(f"Assessment framework '{normalized_fw_code}' not found")

    quantized_total = Decimal(str(total_points)).quantize(Decimal("0.01"))
    rules_json = {
        "framework_code": fw_row.code,
        "version_code": version_code.strip() or THPTQG_2025_VERSION_CODE,
        "subject_code": normalized_subject,
        "sections": _serialize_sections(sections),
        "matrix_cells": _serialize_matrix(matrix_cells),
    }

    bp_row = Blueprint(
        tenant_id=tenant_id,
        framework_id=fw_row.id,
        name=cleaned_name,
        rules_json=rules_json,
        duration_seconds=duration_seconds,
        total_points=quantized_total,
    )
    session.add(bp_row)
    await session.flush()
    await session.refresh(bp_row)
    return _blueprint_row_to_view(bp_row, framework_code=fw_row.code)


async def _load_live_question_pool_counts(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
) -> dict[MatrixKey, int]:
    """Count eligible PUBLISHED + VERIFIED + rights-valid questions per (taxonomy, cognitive_level, item_type)."""
    reg_row = (
        await session.execute(
            text(
                """
                SELECT
                    EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'questions'
                          AND column_name = 'publication_status'
                    ) AS has_questions,
                    EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'taxonomy_nodes'
                          AND column_name = 'code_path'
                    ) AS has_taxonomy_nodes
                """
            )
        )
    ).mappings().one()
    if not reg_row["has_questions"] or not reg_row["has_taxonomy_nodes"]:
        return {}

    rows = (
        await session.execute(
            text(
                """
                SELECT
                    tn.code AS node_code,
                    tn.code_path::text AS node_path,
                    q.difficulty_level AS cognitive_level,
                    q.item_type AS item_type,
                    COUNT(*) AS cnt
                FROM questions q
                JOIN taxonomy_nodes tn ON tn.id = q.primary_taxonomy_node_id
                WHERE q.publication_status = 'PUBLISHED'
                  AND q.verification_status = 'VERIFIED'
                  AND q.rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE')
                  AND (
                      q.tenant_id = :tenant_id
                      OR (q.tenant_id = system_public_tenant_id() AND q.visibility_scope = 'SYSTEM_PUBLIC')
                  )
                GROUP BY tn.code, tn.code_path::text, q.difficulty_level, q.item_type
                """
            ),
            {"tenant_id": tenant_id},
        )
    ).mappings().all()

    counts: dict[MatrixKey, int] = {}
    for row in rows:
        cnt = int(row["cnt"] or 0)
        cog = str(row["cognitive_level"])
        try:
            contract = BlueprintMatrixCell.model_validate(
                {
                    "taxonomy_node_code": str(row["node_code"]),
                    "cognitive_level": cog,
                    "item_type": str(row["item_type"]),
                    "required_count": 0,
                }
            )
        except ValidationError:
            continue
        code_key: MatrixKey = (str(row["node_code"]), contract.cognitive_level, contract.item_type)
        counts[code_key] = counts.get(code_key, 0) + cnt
        node_path = row["node_path"]
        if node_path and str(node_path) != str(row["node_code"]):
            path_key: MatrixKey = (str(node_path), contract.cognitive_level, contract.item_type)
            counts[path_key] = counts.get(path_key, 0) + cnt
    return counts


async def validate_blueprint_and_shortage(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    blueprint_id: uuid.UUID,
    available_counts: dict[MatrixKey, int] | None = None,
) -> BlueprintValidationReportView:
    """Validate blueprint against stored framework specs and compute matrix ShortageReport."""
    row = (
        await session.execute(
            select(Blueprint).where(
                Blueprint.id == blueprint_id,
                Blueprint.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise BlueprintNotFoundError(f"Blueprint {blueprint_id} not found")

    fw_row = (
        await session.execute(
            select(AssessmentFramework).where(AssessmentFramework.id == row.framework_id)
        )
    ).scalar_one()

    view = _blueprint_row_to_view(row, framework_code=fw_row.code)
    rules = dict(row.rules_json or {})
    version_code = str(rules.get("version_code") or THPTQG_2025_VERSION_CODE)

    ver_row = (
        await session.execute(
            select(FrameworkVersion).where(
                FrameworkVersion.framework_id == fw_row.id,
                FrameworkVersion.version_code == version_code,
                FrameworkVersion.subject_code == view.subject_code,
            )
        )
    ).scalar_one_or_none()

    reference_spec: FrameworkSpecContract | None = None
    if ver_row is not None:
        ref_sections, ref_total_points = _parse_section_specs_payload(ver_row.section_specs_json)
        reference_spec = FrameworkSpecContract(
            framework_code=fw_row.code,
            subject_code=ver_row.subject_code,
            duration_seconds=ver_row.duration_seconds,
            total_questions=ver_row.total_questions,
            total_points=ref_total_points,
            sections=ref_sections,
        )
    elif fw_row.code == THPTQG_2025_CODE:
        reference_spec = get_thptqg_2025_framework(view.subject_code)

    bp_contract = BlueprintContract(
        framework_code=fw_row.code,
        subject_code=view.subject_code,
        duration_seconds=view.duration_seconds,
        total_points=view.total_points,
        sections=view.sections,
        matrix_cells=view.matrix_cells,
    )

    errors = validate_blueprint(bp_contract, reference=reference_spec)
    resolved_counts = (
        dict(available_counts)
        if available_counts is not None
        else await _load_live_question_pool_counts(session, tenant_id=tenant_id)
    )
    report = shortage_report(bp_contract, resolved_counts)
    return BlueprintValidationReportView(
        blueprint_id=view.id,
        framework_code=fw_row.code,
        subject_code=view.subject_code,
        valid=(len(errors) == 0),
        errors=errors,
        shortage_report=report,
    )
