"""W0.6 / TASK-23 Idempotent Legacy-to-V11.3 Batched Backfill Runner.

Executes offline data migration from ``legacy_*`` (or pre-rename legacy) tables
into V11.3 modular monolith tables in the strict dependency order mandated by
``docs/architecture/BACKFILL_MAP.md`` section 6:

    assets -> sources -> taxonomy -> curriculum -> content -> questions -> dedup -> assessments

Boundary rule (`.importlinter` contract ``ops-is-offline-tooling``):
this module MUST NEVER import ``app.db``, ``app.models``, or ``app.modules.*``.
All database access goes strictly through caller-supplied or URL-created SQLAlchemy
``AsyncSession`` / ``AsyncConnection`` objects with parameterized SQL statements.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

from app.ops.backfill_mapper import (
    BackfillMappingError,
    BatchReconciliationReport,
    compute_batch_reconciliation_report,
    format_legacy_stable_code,
    map_legacy_difficulty,
    map_legacy_library_kind,
    map_legacy_question_type,
    resolve_legacy_five_axis_state,
)

DEFAULT_SYSTEM_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
DEFAULT_BATCH_SIZE = 500
STAGE_ORDER: tuple[str, ...] = (
    "assets",
    "sources",
    "taxonomy",
    "curriculum",
    "content",
    "questions",
    "dedup",
    "assessments",
)

_BACKFILL_UUID_NAMESPACE = uuid.UUID("7f3b9a10-6f2e-4c11-9b6a-11d8a8120001")
_WHITESPACE_RE = re.compile(r"\s+")
_SUBSCRIPT_TRANSLATION = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
_CHEM_OR_NUMERIC_TOKEN_RE = re.compile(
    r"(?P<chem>(?:[A-Z][a-z]?\d*|\([A-Za-z0-9]+\)\d+|\[[A-Za-z0-9\(\)]+\]\d*)"
    r"(?:[A-Z][a-z]?\d*|\([A-Za-z0-9]+\)\d*)+|[A-Z][a-z]?\d+)"
    r"|(?P<math_sub>[A-Za-z]_\{?\d+\}?)"
    r"|(?P<num>(?<![A-Za-z_\\0-9])\d+(?:[.,]\d+)?)"
)
_LTREE_CLEAN_RE = re.compile(r"[^A-Za-z0-9_]+")
_SLUG_CLEAN_RE = re.compile(r"[^a-z0-9-]+")


@dataclass(frozen=True)
class StageExecutionStats:
    stage: str
    source_count: int
    inserted_count: int
    existing_count: int
    skipped_count: int
    skipped_reasons: dict[str, str] = field(default_factory=dict)
    reconciliation: BatchReconciliationReport | None = None
    batches_executed: int = 0
    executed: bool = True


@dataclass(frozen=True)
class BackfillExecutionSummary:
    tenant_id: uuid.UUID
    batch_size: int
    stages: tuple[StageExecutionStats, ...]
    total_source_rows: int
    total_inserted_rows: int
    total_existing_rows: int
    total_skipped_rows: int
    all_balanced: bool
    is_second_pass_zero_inserts: bool

    def stage_map(self) -> dict[str, StageExecutionStats]:
        return {s.stage: s for s in self.stages}


def _det_uuid(scope: str, key: str | int) -> uuid.UUID:
    return uuid.uuid5(_BACKFILL_UUID_NAMESPACE, f"{scope}:{key}")


def _normalize_unicode_preserve_case(raw: str) -> str:
    nfc = unicodedata.normalize("NFC", raw or "")
    nfc = nfc.replace("\u2212", "-").replace("\u2013", "-")
    nfc = nfc.translate(_SUBSCRIPT_TRANSLATION)
    return _WHITESPACE_RE.sub(" ", nfc).strip()


def compute_nfc_content_hash(stem_md: str, options_md: Sequence[str] = ()) -> str:
    """Compute SHA-256 content hash after Unicode NFC normalization (A08 parity)."""
    norm_stem = _normalize_unicode_preserve_case(stem_md).lower()
    norm_opts = sorted(_normalize_unicode_preserve_case(opt).lower() for opt in options_md)
    canonical = f"{norm_stem}||{'|'.join(norm_opts)}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_nfc_structural_hash(stem_md: str, options_md: Sequence[str] = ()) -> str:
    """Compute SHA-256 structural hash replacing numbers with <num> while keeping chemistry."""

    def _struct_norm(text_val: str) -> str:
        cased = _normalize_unicode_preserve_case(text_val)

        def _repl(match: re.Match[str]) -> str:
            if match.group("chem") is not None:
                return match.group("chem")
            if match.group("math_sub") is not None:
                return match.group("math_sub")
            return "<NUM>"

        return _CHEM_OR_NUMERIC_TOKEN_RE.sub(_repl, cased).lower()

    struct_stem = _struct_norm(stem_md)
    struct_opts = sorted(_struct_norm(opt) for opt in options_md)
    canonical = f"{struct_stem}||{'|'.join(struct_opts)}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _to_ltree_label(raw: str, fallback: str) -> str:
    deacc = unicodedata.normalize("NFD", (raw or "").replace("đ", "d").replace("Đ", "D"))
    ascii_only = "".join(ch for ch in deacc if unicodedata.category(ch) != "Mn")
    cleaned = _LTREE_CLEAN_RE.sub("_", ascii_only).strip("_")
    if not cleaned:
        return fallback
    return cleaned[:48]


def _to_slug(raw: str, fallback: str) -> str:
    deacc = unicodedata.normalize("NFD", (raw or "").replace("đ", "d").replace("Đ", "D"))
    ascii_only = "".join(ch for ch in deacc if unicodedata.category(ch) != "Mn").lower()
    cleaned = _SLUG_CLEAN_RE.sub("-", ascii_only).strip("-")
    return (cleaned or fallback)[:200]


def _sha256_text(raw: str) -> str:
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def _quote_ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name


async def _set_tenant_rls(conn: AsyncSession | AsyncConnection, tenant_id: uuid.UUID) -> None:
    await conn.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true)"),
        {"tid": str(tenant_id)},
    )


async def _table_columns(conn: AsyncSession | AsyncConnection, table_name: str) -> set[str]:
    result = await conn.execute(
        text(
            """
            SELECT c.column_name
            FROM information_schema.columns c
            WHERE c.table_schema = current_schema()
              AND c.table_name = :tname
            """
        ),
        {"tname": table_name},
    )
    return {str(r[0]) for r in result.fetchall()}


async def _pick_legacy_table(
    conn: AsyncSession | AsyncConnection,
    candidates: Sequence[str],
    required_cols: set[str],
) -> str | None:
    for table_name in candidates:
        cols = await _table_columns(conn, table_name)
        if cols and required_cols.issubset(cols):
            return _quote_ident(table_name)
    return None


async def _target_ready(
    conn: AsyncSession | AsyncConnection,
    table_name: str,
    required_cols: set[str],
) -> bool:
    cols = await _table_columns(conn, table_name)
    return bool(cols) and required_cols.issubset(cols)


def _norm_subject_token(raw: str | None) -> str:
    if not raw:
        return ""
    deacc = unicodedata.normalize("NFD", raw.replace("đ", "d").replace("Đ", "D"))
    ascii_low = "".join(ch for ch in deacc if unicodedata.category(ch) != "Mn").lower().strip()
    if any(k in ascii_low for k in ("toan", "math")):
        return "MATH"
    if any(k in ascii_low for k in ("vat li", "vat ly", "phys")):
        return "PHYSICS"
    if any(k in ascii_low for k in ("hoa", "chem")):
        return "CHEMISTRY"
    if any(k in ascii_low for k in ("sinh", "bio")):
        return "BIOLOGY"
    return ascii_low.upper()


async def _run_assets_stage(
    conn: AsyncSession | AsyncConnection,
    tenant_id: uuid.UUID,
    batch_size: int,
) -> StageExecutionStats:
    if not await _target_ready(conn, "assets", {"id", "tenant_id", "asset_role", "object_key"}):
        return StageExecutionStats(
            stage="assets", source_count=0, inserted_count=0, existing_count=0, skipped_count=0, executed=False
        )

    records: list[tuple[str, str, str, str, int, dict[str, Any]]] = []
    # 1) Legacy questions image_path -> FIGURE_CROP
    q_table = await _pick_legacy_table(conn, ("legacy_questions", "questions"), {"id", "content", "image_path"})
    if q_table:
        res = await conn.execute(text(f"SELECT id, image_path FROM {q_table} ORDER BY id"))
        for qid, img_path in res.fetchall():
            if img_path and str(img_path).strip():
                key = str(img_path).strip()[:512]
                records.append(
                    (
                        f"qimg:{qid}",
                        "FIGURE_CROP",
                        key,
                        "image/png",
                        0,
                        {"legacy_id": f"qimg:{qid}", "legacy_question_id": str(qid)},
                    )
                )

    # 2) Legacy crawled_questions images JSONB -> FIGURE_CROP
    cq_table = await _pick_legacy_table(conn, ("legacy_crawled_questions", "crawled_questions"), {"id", "images"})
    if cq_table:
        res = await conn.execute(text(f"SELECT id, images FROM {cq_table} ORDER BY id"))
        for cqid, images in res.fetchall():
            if isinstance(images, list):
                for idx, entry in enumerate(images):
                    if isinstance(entry, dict) and entry.get("key"):
                        key = str(entry["key"]).strip()[:512]
                        if key:
                            records.append(
                                (
                                    f"cqimg:{cqid}:{idx}",
                                    "FIGURE_CROP",
                                    key,
                                    "image/png",
                                    0,
                                    {"legacy_id": f"cqimg:{cqid}:{idx}"},
                                )
                            )

    # 3) Legacy library_documents storage_key (RAW_SOURCE) and preview_storage_key (PREVIEW_PDF)
    lib_table = await _pick_legacy_table(conn, ("legacy_library_documents", "library_documents"), {"id", "storage_key"})
    if lib_table:
        cols = await _table_columns(conn, lib_table)
        prev_expr = "preview_storage_key" if "preview_storage_key" in cols else "NULL"
        chk_expr = "checksum" if "checksum" in cols else "NULL"
        mime_expr = "mime" if "mime" in cols else "'application/pdf'"
        size_expr = "size" if "size" in cols else "0"
        res = await conn.execute(
            text(
                f"SELECT id, storage_key, {prev_expr}, {chk_expr}, "
                f"{mime_expr}, {size_expr} FROM {lib_table} ORDER BY id"
            )
        )
        for doc_id, s_key, prev_key, chk, mime, size in res.fetchall():
            if s_key and str(s_key).strip():
                records.append(
                    (
                        f"libraw:{doc_id}",
                        "RAW_SOURCE",
                        str(s_key).strip()[:512],
                        str(mime or "application/pdf")[:120],
                        max(0, int(size or 0)),
                        {"legacy_id": f"libraw:{doc_id}", "legacy_checksum": chk},
                    )
                )
            if prev_key and str(prev_key).strip():
                records.append(
                    (
                        f"libprev:{doc_id}",
                        "PREVIEW_PDF",
                        str(prev_key).strip()[:512],
                        "application/pdf",
                        0,
                        {"legacy_id": f"libprev:{doc_id}"},
                    )
                )

    seen_keys: set[str] = set()
    source_ids: list[str] = []
    migrated_ids: list[str] = []
    skipped_reasons: dict[str, str] = {}
    inserted = 0
    existing = 0
    batches = 0

    for start in range(0, len(records), batch_size):
        chunk = records[start : start + batch_size]
        batches += 1
        await _set_tenant_rls(conn, tenant_id)
        for legacy_id, role, obj_key, mime, size_bytes, meta in chunk:
            source_ids.append(legacy_id)
            if obj_key in seen_keys:
                skipped_reasons[legacy_id] = "DUPLICATE_OBJECT_KEY_IN_SOURCE"
                continue
            seen_keys.add(obj_key)
            aid = _det_uuid(f"{tenant_id}:asset", obj_key)
            sha = str(meta.get("legacy_checksum") or "")
            if len(sha) != 64:
                sha = _sha256_text(obj_key)
            ins = await conn.execute(
                text(
                    """
                    INSERT INTO assets (
                        id, tenant_id, asset_role, object_key, mime_type, sha256, size_bytes, metadata_json
                    )
                    VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), :role, :okey, :mime, :sha, :sz, CAST(:meta AS jsonb))
                    ON CONFLICT (object_key) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "id": str(aid),
                    "tid": str(tenant_id),
                    "role": role,
                    "okey": obj_key,
                    "mime": mime,
                    "sha": sha,
                    "sz": size_bytes,
                    "meta": json.dumps(meta, ensure_ascii=False),
                },
            )
            if ins.fetchone() is not None:
                inserted += 1
            else:
                existing += 1
            migrated_ids.append(legacy_id)

    report = compute_batch_reconciliation_report(
        source_legacy_ids=source_ids,
        migrated_legacy_ids=migrated_ids,
        skipped_reasons=skipped_reasons,
    )
    return StageExecutionStats(
        stage="assets",
        source_count=len(source_ids),
        inserted_count=inserted,
        existing_count=existing,
        skipped_count=len(skipped_reasons),
        skipped_reasons=skipped_reasons,
        reconciliation=report,
        batches_executed=batches,
    )


async def _run_sources_stage(
    conn: AsyncSession | AsyncConnection,
    tenant_id: uuid.UUID,
    batch_size: int,
) -> StageExecutionStats:
    if not await _target_ready(conn, "sources", {"id", "tenant_id", "source_type", "name", "rights_status"}):
        return StageExecutionStats(
            stage="sources", source_count=0, inserted_count=0, existing_count=0, skipped_count=0, executed=False
        )

    source_ids: list[str] = []
    migrated_ids: list[str] = []
    skipped_reasons: dict[str, str] = {}
    inserted = 0
    existing = 0
    batches = 0

    site_rows: list[tuple[str, str, dict[str, Any]]] = []
    cs_table = await _pick_legacy_table(conn, ("legacy_crawler_sites", "crawler_sites"), {"site_id", "name", "config"})
    if cs_table:
        res = await conn.execute(text(f"SELECT site_id, name, config FROM {cs_table} ORDER BY site_id"))
        for sid, name, cfg in res.fetchall():
            site_rows.append((str(sid), str(name or sid), cfg if isinstance(cfg, dict) else {}))

    lib_rows: list[tuple[Any, ...]] = []
    lib_table = await _pick_legacy_table(
        conn, ("legacy_library_documents", "library_documents"), {"id", "title", "source", "storage_key"}
    )
    if lib_table:
        cols = await _table_columns(conn, lib_table)
        sf_expr = "source_file_id" if "source_file_id" in cols else "NULL"
        chk_expr = "checksum" if "checksum" in cols else "NULL"
        mime_expr = "mime" if "mime" in cols else "'application/pdf'"
        res = await conn.execute(
            text(
                f"SELECT id, title, source, {sf_expr}, storage_key, "
                f"{chk_expr}, {mime_expr} FROM {lib_table} ORDER BY id"
            )
        )
        lib_rows = list(res.fetchall())

    # Default upload/library source anchor for library_documents
    default_lib_src_id = _det_uuid(f"{tenant_id}:source", "legacy-library-default")
    if lib_rows:
        await _set_tenant_rls(conn, tenant_id)
        await conn.execute(
            text(
                """
                INSERT INTO sources (
                    id, tenant_id, source_type, name, adapter_key, config_json, rights_status, policy_status
                )
                VALUES (
                    CAST(:id AS uuid), CAST(:tid AS uuid), 'MANUAL_UPLOAD', :name,
                    'legacy_library', CAST(:cfg AS jsonb), 'PENDING_REVIEW', 'ACTIVE'
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": str(default_lib_src_id),
                "tid": str(tenant_id),
                "name": "Legacy Library Documents",
                "cfg": json.dumps({"legacy_id": "default_library"}),
            },
        )

    for start in range(0, len(site_rows), batch_size):
        chunk = site_rows[start : start + batch_size]
        batches += 1
        await _set_tenant_rls(conn, tenant_id)
        for sid, name, cfg in chunk:
            lid = f"site:{sid}"
            source_ids.append(lid)
            src_uuid = _det_uuid(f"{tenant_id}:source", sid)
            ins = await conn.execute(
                text(
                    """
                    INSERT INTO sources (
                        id, tenant_id, source_type, name, adapter_key, config_json, rights_status, policy_status
                    )
                    VALUES (
                        CAST(:id AS uuid), CAST(:tid AS uuid), 'WEB_CRAWLER', :name,
                        :akey, CAST(:cfg AS jsonb), 'PENDING_REVIEW', 'ACTIVE'
                    )
                    ON CONFLICT (id) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "id": str(src_uuid),
                    "tid": str(tenant_id),
                    "name": name[:255],
                    "akey": sid[:80],
                    "cfg": json.dumps({"legacy_site_id": sid, **cfg}, ensure_ascii=False),
                },
            )
            if ins.fetchone() is not None:
                inserted += 1
            else:
                existing += 1
            migrated_ids.append(lid)

    docs_ready = await _target_ready(
        conn, "source_documents", {"id", "tenant_id", "source_id", "external_id"}
    ) and await _target_ready(
        conn, "document_versions", {"id", "tenant_id", "source_document_id", "raw_asset_id", "checksum_sha256"}
    )
    if docs_ready:
        for start in range(0, len(lib_rows), batch_size):
            chunk = lib_rows[start : start + batch_size]
            batches += 1
            await _set_tenant_rls(conn, tenant_id)
            for doc_id, title, src_label, sf_id, s_key, chk, mime in chunk:
                lid = f"libdoc:{doc_id}"
                source_ids.append(lid)
                if not s_key or not str(s_key).strip():
                    skipped_reasons[lid] = "MISSING_STORAGE_KEY"
                    continue
                asset_id = _det_uuid(f"{tenant_id}:asset", str(s_key).strip()[:512])
                src_uuid = default_lib_src_id
                if src_label and str(src_label).startswith("drive:"):
                    src_uuid = _det_uuid(f"{tenant_id}:source", f"drive:{src_label}")
                    await conn.execute(
                        text(
                            """
                            INSERT INTO sources (
                                id, tenant_id, source_type, name, adapter_key, config_json, rights_status, policy_status
                            )
                            VALUES (
                                CAST(:id AS uuid), CAST(:tid AS uuid), 'GOOGLE_DRIVE', :name,
                                'google_drive', CAST(:cfg AS jsonb), 'PENDING_REVIEW', 'ACTIVE'
                            )
                            ON CONFLICT (id) DO NOTHING
                            """
                        ),
                        {
                            "id": str(src_uuid),
                            "tid": str(tenant_id),
                            "name": str(src_label)[:255],
                            "cfg": json.dumps({"legacy_source": str(src_label)}),
                        },
                    )

                sdoc_uuid = _det_uuid(f"{tenant_id}:source_doc", doc_id)
                ext_id = str(sf_id).strip()[:255] if sf_id else f"LEG-LIB-{doc_id}"
                ins_sd = await conn.execute(
                    text(
                        """
                        INSERT INTO source_documents (
                            id, tenant_id, source_id, external_id, title, mime_type,
                            acquisition_status, rights_status, inferred_metadata_json
                        )
                        VALUES (
                            CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:sid AS uuid), :ext, :title, :mime,
                            'NORMALIZED', 'PENDING_REVIEW', CAST(:meta AS jsonb)
                        )
                        ON CONFLICT (source_id, external_id) DO NOTHING
                        RETURNING id
                        """
                    ),
                    {
                        "id": str(sdoc_uuid),
                        "tid": str(tenant_id),
                        "sid": str(src_uuid),
                        "ext": ext_id,
                        "title": (title or f"Document {doc_id}")[:500],
                        "mime": str(mime or "application/pdf")[:120],
                        "meta": json.dumps({"legacy_id": int(doc_id)}),
                    },
                )
                row_sd = ins_sd.fetchone()
                actual_sd_id = str(row_sd[0]) if row_sd is not None else None
                if actual_sd_id is None:
                    lookup = await conn.execute(
                        text(
                            "SELECT id FROM source_documents "
                            "WHERE source_id = CAST(:sid AS uuid) AND external_id = :ext"
                        ),
                        {"sid": str(src_uuid), "ext": ext_id},
                    )
                    actual_sd_id = str(lookup.scalar_one())

                sha = str(chk or "")
                if len(sha) != 64:
                    sha = _sha256_text(str(s_key))
                dver_uuid = _det_uuid(f"{tenant_id}:doc_ver", f"{doc_id}:1")
                ins_dv = await conn.execute(
                    text(
                        """
                        INSERT INTO document_versions (
                            id, tenant_id, source_document_id, version_no, raw_asset_id, checksum_sha256
                        )
                        VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:sdid AS uuid), 1, CAST(:aid AS uuid), :sha)
                        ON CONFLICT (source_document_id, version_no) DO NOTHING
                        RETURNING id
                        """
                    ),
                    {
                        "id": str(dver_uuid),
                        "tid": str(tenant_id),
                        "sdid": actual_sd_id,
                        "aid": str(asset_id),
                        "sha": sha,
                    },
                )
                row_dv = ins_dv.fetchone()
                if row_sd is not None or row_dv is not None:
                    inserted += 1
                else:
                    existing += 1
                migrated_ids.append(lid)

    report = compute_batch_reconciliation_report(
        source_legacy_ids=source_ids,
        migrated_legacy_ids=migrated_ids,
        skipped_reasons=skipped_reasons,
    )
    return StageExecutionStats(
        stage="sources",
        source_count=len(source_ids),
        inserted_count=inserted,
        existing_count=existing,
        skipped_count=len(skipped_reasons),
        skipped_reasons=skipped_reasons,
        reconciliation=report,
        batches_executed=batches,
    )


async def _run_taxonomy_stage(
    conn: AsyncSession | AsyncConnection,
    tenant_id: uuid.UUID,
    batch_size: int,
) -> StageExecutionStats:
    if not await _target_ready(conn, "taxonomy_nodes", {"id", "tenant_id", "code", "code_path"}):
        return StageExecutionStats(
            stage="taxonomy", source_count=0, inserted_count=0, existing_count=0, skipped_count=0, executed=False
        )

    cat_table = await _pick_legacy_table(conn, ("legacy_categories", "categories"), {"id", "name", "depth"})
    ku_table = await _pick_legacy_table(
        conn, ("legacy_knowledge_units", "knowledge_units"), {"id", "name", "unit_type"}
    )

    items: list[tuple[str, str, str, str, str, str | None]] = []
    # (legacy_id, code, code_path, node_type, name, parent_legacy_id)
    cat_paths: dict[int, str] = {}
    if cat_table:
        res = await conn.execute(text(f"SELECT id, parent_id, name, depth FROM {cat_table} ORDER BY depth ASC, id ASC"))
        rows = res.fetchall()
        for cid, pid, name, depth in rows:
            lbl = _to_ltree_label(str(name or f"CAT_{cid}"), f"CAT_{cid}")
            parent_path = cat_paths.get(int(pid)) if pid is not None else None
            cpath = f"{parent_path}.{lbl}" if parent_path else f"LEGACY.{lbl}"
            cat_paths[int(cid)] = cpath
            if int(depth or 0) >= 1:
                ntype = "TOPIC" if int(depth) == 1 else ("CONCEPT" if int(depth) == 2 else "SKILL")
                items.append(
                    (
                        f"cat:{cid}",
                        f"LEG-CAT-{cid}",
                        cpath,
                        ntype,
                        str(name or f"Category {cid}"),
                        f"cat:{pid}" if pid else None,
                    )
                )

    if ku_table:
        res = await conn.execute(text(f"SELECT id, subject, grade, unit_type, code, name FROM {ku_table} ORDER BY id"))
        for kid, subj, grade, utype, kcode, name in res.fetchall():
            if str(utype or "").upper() in {"PROBLEM_TYPE", "TOPIC"}:
                subj_lbl = _to_ltree_label(_norm_subject_token(subj) or "GEN", "GEN")
                leaf_lbl = _to_ltree_label(str(kcode or name or f"KU_{kid}"), f"KU_{kid}")
                cpath = f"{subj_lbl}.G{int(grade or 12)}.{leaf_lbl}"
                code = str(kcode or f"LEG-KU-{kid}")[:120]
                items.append((f"ku:{kid}", code, cpath, "SKILL", str(name or code), None))

    source_ids: list[str] = []
    migrated_ids: list[str] = []
    skipped_reasons: dict[str, str] = {}
    inserted = 0
    existing = 0
    batches = 0

    for start in range(0, len(items), batch_size):
        chunk = items[start : start + batch_size]
        batches += 1
        await _set_tenant_rls(conn, tenant_id)
        for lid, code, cpath, ntype, name, _parent_lid in chunk:
            source_ids.append(lid)
            nid = _det_uuid(f"{tenant_id}:tax", code)
            ins = await conn.execute(
                text(
                    """
                    INSERT INTO taxonomy_nodes (
                        id, tenant_id, visibility_scope, node_type, code, code_path, current_version_no, is_active
                    )
                    VALUES (
                        CAST(:id AS uuid), CAST(:tid AS uuid), 'SYSTEM_PUBLIC', :ntype,
                        :code, CAST(:cpath AS ltree), 1, TRUE
                    )
                    ON CONFLICT (tenant_id, code) DO NOTHING
                    RETURNING id
                    """
                ),
                {"id": str(nid), "tid": str(tenant_id), "ntype": ntype, "code": code, "cpath": cpath},
            )
            row = ins.fetchone()
            actual_nid = (
                str(row[0])
                if row is not None
                else str(
                    (
                        await conn.execute(
                            text("SELECT id FROM taxonomy_nodes WHERE tenant_id = CAST(:tid AS uuid) AND code = :code"),
                            {"tid": str(tenant_id), "code": code},
                        )
                    ).scalar_one()
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO taxonomy_node_versions (id, tenant_id, taxonomy_node_id, version_no, name)
                    VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:nid AS uuid), 1, :name)
                    ON CONFLICT (taxonomy_node_id, version_no) DO NOTHING
                    """
                ),
                {
                    "id": str(_det_uuid(f"{tenant_id}:taxver", f"{code}:1")),
                    "tid": str(tenant_id),
                    "nid": actual_nid,
                    "name": name[:255],
                },
            )
            norm_alias = f"leg-{lid.replace(':', '-')}"
            await conn.execute(
                text(
                    """
                    INSERT INTO taxonomy_aliases (id, tenant_id, taxonomy_node_id, alias_name, normalized_alias)
                    VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:nid AS uuid), :aname, :nalias)
                    ON CONFLICT (tenant_id, normalized_alias) DO NOTHING
                    """
                ),
                {
                    "id": str(_det_uuid(f"{tenant_id}:taxalias", norm_alias)),
                    "tid": str(tenant_id),
                    "nid": actual_nid,
                    "aname": name[:255],
                    "nalias": norm_alias[:255],
                },
            )
            if row is not None:
                inserted += 1
            else:
                existing += 1
            migrated_ids.append(lid)

    report = compute_batch_reconciliation_report(
        source_legacy_ids=source_ids,
        migrated_legacy_ids=migrated_ids,
        skipped_reasons=skipped_reasons,
    )
    return StageExecutionStats(
        stage="taxonomy",
        source_count=len(source_ids),
        inserted_count=inserted,
        existing_count=existing,
        skipped_count=len(skipped_reasons),
        skipped_reasons=skipped_reasons,
        reconciliation=report,
        batches_executed=batches,
    )


async def _run_curriculum_stage(
    conn: AsyncSession | AsyncConnection,
    tenant_id: uuid.UUID,
    batch_size: int,
) -> StageExecutionStats:
    if not await _target_ready(conn, "curriculum_versions", {"id", "tenant_id", "code"}):
        return StageExecutionStats(
            stage="curriculum", source_count=0, inserted_count=0, existing_count=0, skipped_count=0, executed=False
        )

    ku_table = await _pick_legacy_table(
        conn, ("legacy_knowledge_units", "knowledge_units"), {"id", "subject", "grade", "unit_type", "name"}
    )
    rows: list[tuple[Any, ...]] = []
    if ku_table:
        res = await conn.execute(
            text(
                f"SELECT id, parent_id, subject, grade, unit_type, code, name, sort_order "
                f"FROM {ku_table} ORDER BY sort_order ASC, id ASC"
            )
        )
        rows = list(res.fetchall())

    source_ids: list[str] = []
    migrated_ids: list[str] = []
    skipped_reasons: dict[str, str] = {}
    inserted = 0
    existing = 0
    batches = 0

    valid_levels = {"CHAPTER", "LESSON", "SECTION", "PROBLEM_TYPE"}
    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        batches += 1
        await _set_tenant_rls(conn, tenant_id)
        for kid, _pid, subj, grade, utype, kcode, name, sort_order in chunk:
            lid = f"ku:{kid}"
            source_ids.append(lid)
            norm_level = str(utype or "").upper()
            if norm_level == "TOPIC":
                norm_level = "SECTION"
            if norm_level not in valid_levels:
                skipped_reasons[lid] = f"UNSUPPORTED_NODE_LEVEL:{norm_level}"
                continue

            subj_code = _norm_subject_token(str(subj or "MATH")) or "MATH"
            grade_band = f"GRADE_{int(grade or 12)}"
            cv_code = f"KNTT-{subj_code}-G{int(grade or 12)}"
            cv_id = _det_uuid(f"{tenant_id}:currver", cv_code)
            await conn.execute(
                text(
                    """
                    INSERT INTO curriculum_versions (
                        id, tenant_id, visibility_scope, code, name, subject_code, grade_band, version_label, status
                    )
                    VALUES (
                        CAST(:id AS uuid), CAST(:tid AS uuid), 'SYSTEM_PUBLIC', :code,
                        :name, :subj, :gband, 'SGK_KNTT_V1', 'PUBLISHED'
                    )
                    ON CONFLICT (tenant_id, code) DO NOTHING
                    """
                ),
                {
                    "id": str(cv_id),
                    "tid": str(tenant_id),
                    "code": cv_code,
                    "name": f"SGK KNTT {subj_code} Lớp {int(grade or 12)}",
                    "subj": subj_code[:50],
                    "gband": grade_band[:30],
                },
            )
            actual_cv_id = str(
                (
                    await conn.execute(
                        text(
                            "SELECT id FROM curriculum_versions WHERE tenant_id = CAST(:tid AS uuid) AND code = :code"
                        ),
                        {"tid": str(tenant_id), "code": cv_code},
                    )
                ).scalar_one()
            )
            cn_code = str(kcode or f"LEG-CN-{kid}")[:120]
            cn_id = _det_uuid(f"{tenant_id}:currnode", f"{cv_code}:{cn_code}")
            ins_cn = await conn.execute(
                text(
                    """
                    INSERT INTO curriculum_nodes (
                        id, tenant_id, curriculum_version_id, node_level, code, title, sequence_order
                    )
                    VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:cvid AS uuid), :lvl, :code, :title, :seq)
                    ON CONFLICT (curriculum_version_id, code) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "id": str(cn_id),
                    "tid": str(tenant_id),
                    "cvid": actual_cv_id,
                    "lvl": norm_level,
                    "code": cn_code,
                    "title": str(name or cn_code)[:255],
                    "seq": int(sort_order or 0),
                },
            )
            row_cn = ins_cn.fetchone()
            if row_cn is not None:
                inserted += 1
            else:
                existing += 1
            migrated_ids.append(lid)

    report = compute_batch_reconciliation_report(
        source_legacy_ids=source_ids,
        migrated_legacy_ids=migrated_ids,
        skipped_reasons=skipped_reasons,
    )
    return StageExecutionStats(
        stage="curriculum",
        source_count=len(source_ids),
        inserted_count=inserted,
        existing_count=existing,
        skipped_count=len(skipped_reasons),
        skipped_reasons=skipped_reasons,
        reconciliation=report,
        batches_executed=batches,
    )


async def _run_content_stage(
    conn: AsyncSession | AsyncConnection,
    tenant_id: uuid.UUID,
    batch_size: int,
) -> StageExecutionStats:
    if not (
        await _target_ready(conn, "content_items", {"id", "tenant_id", "content_type", "slug"})
        and await _target_ready(conn, "content_versions", {"id", "tenant_id", "content_item_id", "blocks_json"})
    ):
        return StageExecutionStats(
            stage="content", source_count=0, inserted_count=0, existing_count=0, skipped_count=0, executed=False
        )

    items: list[tuple[str, str, str, str, str, list[dict[str, Any]]]] = []
    lib_table = await _pick_legacy_table(
        conn, ("legacy_library_documents", "library_documents"), {"id", "title", "kind"}
    )
    if lib_table:
        cols = await _table_columns(conn, lib_table)
        md_expr = "content_markdown" if "content_markdown" in cols else "NULL"
        tier_expr = "access_tier" if "access_tier" in cols else "'FREE'"
        res = await conn.execute(text(f"SELECT id, title, kind, {md_expr}, {tier_expr} FROM {lib_table} ORDER BY id"))
        for doc_id, title, kind, md, tier in res.fetchall():
            try:
                mapped_type = map_legacy_library_kind(str(kind or "BOOK"))
            except BackfillMappingError:
                mapped_type = "DOCUMENT"
            slug = _to_slug(f"leg-doc-{doc_id}-{title or ''}", f"leg-doc-{doc_id}")
            blocks = [
                {
                    "block_type": "THEORY_SUMMARY",
                    "markdown": _normalize_unicode_preserve_case(str(md or title or "")),
                    "legacy_id": f"lib:{doc_id}",
                }
            ]
            norm_tier = "STUDENT_PRO" if str(tier or "").upper() == "PRO" else "FREE"
            items.append((f"lib:{doc_id}", mapped_type, slug, str(title or f"Document {doc_id}"), norm_tier, blocks))

    ka_table = await _pick_legacy_table(conn, ("legacy_knowledge_articles", "knowledge_articles"), {"id", "title"})
    if ka_table:
        cols = await _table_columns(conn, ka_table)
        sum_expr = "summary_formulas_md" if "summary_formulas_md" in cols else "NULL"
        mth_expr = "method_guide_md" if "method_guide_md" in cols else "NULL"
        tip_expr = "notes_and_tips_md" if "notes_and_tips_md" in cols else "NULL"
        tier_expr = "access_tier" if "access_tier" in cols else "'FREE'"
        res = await conn.execute(
            text(f"SELECT id, title, {sum_expr}, {mth_expr}, {tip_expr}, {tier_expr} FROM {ka_table} ORDER BY id")
        )
        for aid, title, sf, mg, nt, tier in res.fetchall():
            blocks: list[dict[str, Any]] = []
            if sf:
                blocks.append({"block_type": "FORMULA_BOX", "markdown": _normalize_unicode_preserve_case(str(sf))})
            if mg:
                blocks.append({"block_type": "METHOD_STEPS", "markdown": _normalize_unicode_preserve_case(str(mg))})
            if nt:
                blocks.append({"block_type": "TIP_WARNING", "markdown": _normalize_unicode_preserve_case(str(nt))})
            if not blocks:
                blocks.append(
                    {"block_type": "THEORY_SUMMARY", "markdown": _normalize_unicode_preserve_case(str(title or ""))}
                )
            slug = _to_slug(f"leg-art-{aid}", f"leg-art-{aid}")
            norm_tier = "STUDENT_PRO" if str(tier or "").upper() == "PRO" else "FREE"
            items.append((f"art:{aid}", "ARTICLE", slug, str(title or "Article"), norm_tier, blocks))

    source_ids: list[str] = []
    migrated_ids: list[str] = []
    skipped_reasons: dict[str, str] = {}
    inserted = 0
    existing = 0
    batches = 0

    for start in range(0, len(items), batch_size):
        chunk = items[start : start + batch_size]
        batches += 1
        await _set_tenant_rls(conn, tenant_id)
        for lid, ctype, slug, title, norm_tier, blocks in chunk:
            source_ids.append(lid)
            cid = _det_uuid(f"{tenant_id}:content", lid)
            cvid = _det_uuid(f"{tenant_id}:contentver", f"{lid}:1")
            # Fail-closed per BACKFILL_MAP section 3: legacy library starts PENDING_REVIEW + DRAFT.
            ins_ci = await conn.execute(
                text(
                    """
                    INSERT INTO content_items (
                        id, tenant_id, visibility_scope, content_type, slug,
                        acquisition_status, verification_status, rights_status, publication_status, access_tier
                    )
                    VALUES (
                        CAST(:id AS uuid), CAST(:tid AS uuid), 'SYSTEM_PUBLIC', :ctype, :slug,
                        'NORMALIZED', 'UNCHECKED', 'PENDING_REVIEW', 'DRAFT', :tier
                    )
                    ON CONFLICT (tenant_id, slug) DO NOTHING
                    RETURNING id
                    """
                ),
                {"id": str(cid), "tid": str(tenant_id), "ctype": ctype, "slug": slug, "tier": norm_tier},
            )
            row_ci = ins_ci.fetchone()
            actual_ci = (
                str(row_ci[0])
                if row_ci is not None
                else str(
                    (
                        await conn.execute(
                            text("SELECT id FROM content_items WHERE tenant_id = CAST(:tid AS uuid) AND slug = :slug"),
                            {"tid": str(tenant_id), "slug": slug},
                        )
                    ).scalar_one()
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO content_versions (
                        id, tenant_id, content_item_id, version_no, title, blocks_json, status, is_immutable
                    )
                    VALUES (
                        CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:cid AS uuid), 1,
                        :title, CAST(:blocks AS jsonb), 'DRAFT', FALSE
                    )
                    ON CONFLICT (content_item_id, version_no) DO NOTHING
                    """
                ),
                {
                    "id": str(cvid),
                    "tid": str(tenant_id),
                    "cid": actual_ci,
                    "title": title[:500],
                    "blocks": json.dumps(blocks, ensure_ascii=False),
                },
            )
            await conn.execute(
                text(
                    "UPDATE content_items SET current_version_id = CAST(:cvid AS uuid) "
                    "WHERE id = CAST(:cid AS uuid) AND current_version_id IS NULL"
                ),
                {"cvid": str(cvid), "cid": actual_ci},
            )
            if row_ci is not None:
                inserted += 1
            else:
                existing += 1
            migrated_ids.append(lid)

    report = compute_batch_reconciliation_report(
        source_legacy_ids=source_ids,
        migrated_legacy_ids=migrated_ids,
        skipped_reasons=skipped_reasons,
    )
    return StageExecutionStats(
        stage="content",
        source_count=len(source_ids),
        inserted_count=inserted,
        existing_count=existing,
        skipped_count=len(skipped_reasons),
        skipped_reasons=skipped_reasons,
        reconciliation=report,
        batches_executed=batches,
    )


async def _run_questions_stage(
    conn: AsyncSession | AsyncConnection,
    tenant_id: uuid.UUID,
    batch_size: int,
    *,
    subject_filter: Sequence[str] | None = None,
    grades_filter: Sequence[int] | None = None,
) -> StageExecutionStats:
    if not (
        await _target_ready(conn, "questions", {"id", "tenant_id", "stable_code", "item_type", "difficulty_level"})
        and await _target_ready(
            conn, "question_versions", {"id", "tenant_id", "question_id", "content_hash", "structural_hash"}
        )
        and await _target_ready(conn, "question_options", {"id", "tenant_id", "question_version_id", "option_key"})
        and await _target_ready(conn, "question_version_answers", {"question_version_id", "tenant_id", "answer_schema"})
    ):
        return StageExecutionStats(
            stage="questions", source_count=0, inserted_count=0, existing_count=0, skipped_count=0, executed=False
        )

    # Prefer renamed legacy_questions; fallback to pre-rename questions ONLY if it still has legacy column `content`.
    q_table = await _pick_legacy_table(
        conn, ("legacy_questions", "questions"), {"id", "content", "question_type", "difficulty"}
    )
    if not q_table:
        return StageExecutionStats(
            stage="questions", source_count=0, inserted_count=0, existing_count=0, skipped_count=0, executed=False
        )

    q_cols = await _table_columns(conn, q_table)
    has_ku = "knowledge_unit_id" in q_cols
    has_cat = "category_id" in q_cols
    ku_table = (
        await _pick_legacy_table(conn, ("legacy_knowledge_units", "knowledge_units"), {"id", "subject", "grade"})
        if has_ku
        else None
    )
    cat_table = await _pick_legacy_table(conn, ("legacy_categories", "categories"), {"id", "name"}) if has_cat else None

    join_sql = ""
    subj_expr = "NULL"
    grade_expr = "NULL"
    if ku_table:
        join_sql += f" LEFT JOIN {ku_table} ku ON q.knowledge_unit_id = ku.id"
        subj_expr = "ku.subject"
        grade_expr = "ku.grade"
    elif cat_table:
        join_sql += (
            f" LEFT JOIN {cat_table} c ON q.root_id = c.id"
            if "root_id" in q_cols
            else f" LEFT JOIN {cat_table} c ON q.category_id = c.id"
        )
        subj_expr = "c.name"

    acc_expr = "q.accepted_answers" if "accepted_answers" in q_cols else "NULL"
    exp_expr = "q.explanation" if "explanation" in q_cols else "NULL"
    orig_expr = "q.origin_type" if "origin_type" in q_cols else "'PUBLIC'"
    aud_expr = "q.audit_status" if "audit_status" in q_cols else "NULL"
    ver_expr = "q.is_verified" if "is_verified" in q_cols else "TRUE"
    ans_src_expr = "q.answer_source" if "answer_source" in q_cols else "NULL"
    tags_expr = "q.target_tags" if "target_tags" in q_cols else "NULL"
    tier_expr = "q.access_tier" if "access_tier" in q_cols else "'FREE'"
    choices_expr = "q.choices" if "choices" in q_cols else "'[]'::jsonb"

    allowed_subjs = {_norm_subject_token(s) for s in subject_filter} if subject_filter else None
    allowed_grades = {int(g) for g in grades_filter} if grades_filter else None

    source_ids: list[str] = []
    migrated_ids: list[str] = []
    skipped_reasons: dict[str, str] = {}
    inserted = 0
    existing = 0
    batches = 0

    last_id = -1
    while True:
        res = await conn.execute(
            text(
                f"""
                SELECT q.id, q.question_type, q.difficulty, q.content, {choices_expr}, {acc_expr}, {exp_expr},
                       {orig_expr}, {aud_expr}, {ver_expr}, {ans_src_expr}, {tags_expr}, {tier_expr},
                       {subj_expr}, {grade_expr}
                FROM {q_table} q
                {join_sql}
                WHERE q.id > :last_id
                ORDER BY q.id ASC
                LIMIT :batch_limit
                """
            ),
            {"last_id": last_id, "batch_limit": batch_size},
        )
        rows = res.fetchall()
        if not rows:
            break

        batches += 1
        await _set_tenant_rls(conn, tenant_id)
        for row in rows:
            (
                qid,
                raw_qtype,
                raw_diff,
                raw_content,
                raw_choices,
                raw_accepted,
                raw_explanation,
                raw_origin,
                raw_audit,
                raw_verified,
                raw_ans_source,
                raw_tags,
                raw_tier,
                raw_subj,
                raw_grade,
            ) = row
            last_id = int(qid)
            lid = str(qid)
            source_ids.append(lid)

            if allowed_subjs is not None:
                norm_s = _norm_subject_token(str(raw_subj or ""))
                if norm_s not in allowed_subjs:
                    skipped_reasons[lid] = f"OUT_OF_SCOPE_SUBJECT:{norm_s or 'UNKNOWN'}"
                    continue
            if allowed_grades is not None:
                g_val = int(raw_grade) if raw_grade is not None else None
                if g_val not in allowed_grades:
                    skipped_reasons[lid] = f"OUT_OF_SCOPE_GRADE:{g_val}"
                    continue

            stem_nfc = _normalize_unicode_preserve_case(str(raw_content or ""))
            if not stem_nfc:
                skipped_reasons[lid] = "EMPTY_STEM"
                continue

            try:
                mapped_type = map_legacy_question_type(str(raw_qtype))
                diff_map = map_legacy_difficulty(str(raw_diff))
                stable_code = format_legacy_stable_code(qid)
            except BackfillMappingError as exc:
                skipped_reasons[lid] = f"MAPPING_ERROR:{exc}"
                continue

            five_axis = resolve_legacy_five_axis_state(
                origin_type=raw_origin,
                audit_status=raw_audit,
                legacy_is_verified=bool(raw_verified),
                has_answer_source=bool(raw_ans_source and str(raw_ans_source).strip()),
                visibility_scope="SYSTEM_PUBLIC",
            )

            tags = list(raw_tags) if isinstance(raw_tags, (list, tuple)) and raw_tags else ["SGK_KNTT"]
            for extra_tag in diff_map.extra_framework_tags:
                if extra_tag not in tags:
                    tags.append(extra_tag)

            # Separate option text from correctness flags (ADR-005 / C01 anti-answer-leak)
            choices_list = raw_choices if isinstance(raw_choices, list) else []
            sanitized_options: list[dict[str, Any]] = []
            option_texts: list[str] = []
            correct_keys: list[str] = []
            true_false_map: dict[str, bool] = {}

            for idx, ch in enumerate(choices_list):
                if not isinstance(ch, dict):
                    continue
                opt_key = str(ch.get("id") or ch.get("key") or chr(65 + idx))[:10]
                opt_text = _normalize_unicode_preserve_case(str(ch.get("content") or ch.get("text") or ""))
                option_texts.append(opt_text)
                sanitized_options.append({"option_key": opt_key, "content_md": opt_text})
                if ch.get("is_correct") is True:
                    correct_keys.append(opt_key)
                if mapped_type == "CLUSTER_TRUE_FALSE":
                    true_false_map[opt_key] = bool(ch.get("is_correct", False))

            c_hash = compute_nfc_content_hash(stem_nfc, option_texts)
            s_hash = compute_nfc_structural_hash(stem_nfc, option_texts)
            norm_tier = "STUDENT_PRO" if str(raw_tier or "").upper() == "PRO" else "FREE"

            q_uuid = _det_uuid(f"{tenant_id}:question", lid)
            qv_uuid = _det_uuid(f"{tenant_id}:question_ver", f"{lid}:1")

            # Insert draft state first when PUBLISHED so deferred FK to current_version_id passes cleanly
            init_pub = (
                "DRAFT" if five_axis.publication_status.value == "PUBLISHED" else five_axis.publication_status.value
            )
            ins_q = await conn.execute(
                text(
                    """
                    INSERT INTO questions (
                        id, tenant_id, visibility_scope, stable_code, item_type,
                        exam_framework_tags, difficulty_level,
                        acquisition_status, verification_status, rights_status, publication_status, access_tier
                    )
                    VALUES (
                        CAST(:id AS uuid), CAST(:tid AS uuid), :vscope, :scode, :itype,
                        CAST(:tags AS varchar[]), :diff,
                        :acq, :ver, :rights, :pub, :tier
                    )
                    ON CONFLICT (tenant_id, stable_code) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "id": str(q_uuid),
                    "tid": str(tenant_id),
                    "vscope": five_axis.visibility_scope,
                    "scode": stable_code,
                    "itype": mapped_type.value,
                    "tags": tags,
                    "diff": diff_map.difficulty,
                    "acq": five_axis.acquisition_status.value,
                    "ver": five_axis.verification_status.value,
                    "rights": five_axis.rights_status.value,
                    "pub": init_pub,
                    "tier": norm_tier,
                },
            )
            row_q = ins_q.fetchone()
            actual_qid = (
                str(row_q[0])
                if row_q is not None
                else str(
                    (
                        await conn.execute(
                            text(
                                "SELECT id FROM questions WHERE tenant_id = CAST(:tid AS uuid) AND stable_code = :scode"
                            ),
                            {"tid": str(tenant_id), "scode": stable_code},
                        )
                    ).scalar_one()
                )
            )

            stem_json = {"markdown": stem_nfc, "legacy_id": int(qid)}
            ins_qv = await conn.execute(
                text(
                    """
                    INSERT INTO question_versions (
                        id, tenant_id, visibility_scope, question_id, version_no,
                        stem_json, options_json, content_hash, structural_hash, is_immutable, change_reason
                    )
                    VALUES (
                        CAST(:id AS uuid), CAST(:tid AS uuid), :vscope, CAST(:qid AS uuid), 1,
                        CAST(:stem AS jsonb), CAST(:opts AS jsonb), :chash, :shash, :imm, 'BACKFILL_V113'
                    )
                    ON CONFLICT (question_id, version_no) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "id": str(qv_uuid),
                    "tid": str(tenant_id),
                    "vscope": five_axis.visibility_scope,
                    "qid": actual_qid,
                    "stem": json.dumps(stem_json, ensure_ascii=False),
                    "opts": json.dumps(sanitized_options, ensure_ascii=False),
                    "chash": c_hash,
                    "shash": s_hash,
                    "imm": five_axis.publication_status.value == "PUBLISHED",
                },
            )
            row_qv = ins_qv.fetchone()
            actual_qvid = (
                str(row_qv[0])
                if row_qv is not None
                else str(
                    (
                        await conn.execute(
                            text(
                                "SELECT id FROM question_versions "
                                "WHERE question_id = CAST(:qid AS uuid) AND version_no = 1"
                            ),
                            {"qid": actual_qid},
                        )
                    ).scalar_one()
                )
            )

            for seq, opt in enumerate(sanitized_options):
                await conn.execute(
                    text(
                        """
                        INSERT INTO question_options (
                            id, tenant_id, question_version_id, option_key, content_json, sequence_order
                        )
                        VALUES (
                            CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:qvid AS uuid),
                            :okey, CAST(:cjson AS jsonb), :seq
                        )
                        ON CONFLICT (question_version_id, option_key) DO NOTHING
                        """
                    ),
                    {
                        "id": str(_det_uuid(f"{tenant_id}:qopt", f"{lid}:{opt['option_key']}")),
                        "tid": str(tenant_id),
                        "qvid": actual_qvid,
                        "okey": opt["option_key"],
                        "cjson": json.dumps({"markdown": opt["content_md"]}, ensure_ascii=False),
                        "seq": seq,
                    },
                )

            answer_schema = {
                "item_type": mapped_type.value,
                "correct_option_keys": correct_keys,
                "cluster_targets": true_false_map,
                "accepted_answers": raw_accepted if isinstance(raw_accepted, list) else [],
            }
            rationale_json = {"explanation_md": _normalize_unicode_preserve_case(str(raw_explanation or ""))}
            await conn.execute(
                text(
                    """
                    INSERT INTO question_version_answers (
                        question_version_id, tenant_id, answer_schema, rationale_json, is_immutable
                    )
                    VALUES (CAST(:qvid AS uuid), CAST(:tid AS uuid), CAST(:ans AS jsonb), CAST(:rat AS jsonb), :imm)
                    ON CONFLICT (question_version_id) DO NOTHING
                    """
                ),
                {
                    "qvid": actual_qvid,
                    "tid": str(tenant_id),
                    "ans": json.dumps(answer_schema, ensure_ascii=False),
                    "rat": json.dumps(rationale_json, ensure_ascii=False),
                    "imm": five_axis.publication_status.value == "PUBLISHED",
                },
            )

            await conn.execute(
                text(
                    """
                    UPDATE questions
                    SET current_version_id = COALESCE(current_version_id, CAST(:qvid AS uuid)),
                        publication_status = :pub
                    WHERE id = CAST(:qid AS uuid)
                    """
                ),
                {"qvid": actual_qvid, "pub": five_axis.publication_status.value, "qid": actual_qid},
            )

            if row_q is not None:
                inserted += 1
            else:
                existing += 1
            migrated_ids.append(lid)

    report = compute_batch_reconciliation_report(
        source_legacy_ids=source_ids,
        migrated_legacy_ids=migrated_ids,
        skipped_reasons=skipped_reasons,
    )
    return StageExecutionStats(
        stage="questions",
        source_count=len(source_ids),
        inserted_count=inserted,
        existing_count=existing,
        skipped_count=len(skipped_reasons),
        skipped_reasons=skipped_reasons,
        reconciliation=report,
        batches_executed=batches,
    )


async def _run_dedup_stage(
    conn: AsyncSession | AsyncConnection,
    tenant_id: uuid.UUID,
    batch_size: int,
) -> StageExecutionStats:
    if not (
        await _target_ready(conn, "duplicate_clusters", {"id", "tenant_id", "cluster_type", "status"})
        and await _target_ready(conn, "duplicate_members", {"cluster_id", "candidate_id", "tenant_id"})
        and await _target_ready(conn, "question_candidates", {"id", "tenant_id", "content_hash", "structural_hash"})
    ):
        return StageExecutionStats(
            stage="dedup", source_count=0, inserted_count=0, existing_count=0, skipped_count=0, executed=False
        )

    cq_table = await _pick_legacy_table(
        conn, ("legacy_crawled_questions", "crawled_questions"), {"id", "status", "content", "question_type"}
    )
    if not cq_table:
        return StageExecutionStats(
            stage="dedup", source_count=0, inserted_count=0, existing_count=0, skipped_count=0, executed=False
        )

    res = await conn.execute(
        text(f"SELECT id, question_type, content, choices, status, duplicate_of FROM {cq_table} ORDER BY id")
    )
    rows = res.fetchall()

    source_ids: list[str] = []
    migrated_ids: list[str] = []
    skipped_reasons: dict[str, str] = {}
    inserted = 0
    existing = 0
    batches = 0

    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        batches += 1
        await _set_tenant_rls(conn, tenant_id)
        for cqid, raw_qtype, raw_content, raw_choices, raw_status, dup_of in chunk:
            lid = f"cq:{cqid}"
            source_ids.append(lid)
            norm_status = str(raw_status or "").upper()
            if norm_status not in {"DUPLICATE", "SIMILAR", "NEEDS_REVIEW"}:
                skipped_reasons[lid] = f"SKIPPED_STATUS:{norm_status}"
                continue
            try:
                mapped_type = map_legacy_question_type(str(raw_qtype))
            except BackfillMappingError as exc:
                skipped_reasons[lid] = f"MAPPING_ERROR:{exc}"
                continue

            stem_nfc = _normalize_unicode_preserve_case(str(raw_content or ""))
            if not stem_nfc:
                skipped_reasons[lid] = "EMPTY_STEM"
                continue
            opts = [
                str(c.get("content") or "")
                for c in (raw_choices if isinstance(raw_choices, list) else [])
                if isinstance(c, dict)
            ]
            chash = compute_nfc_content_hash(stem_nfc, opts)
            shash = compute_nfc_structural_hash(stem_nfc, opts)

            cand_uuid = _det_uuid(f"{tenant_id}:qcand", lid)
            ins_c = await conn.execute(
                text(
                    """
                    INSERT INTO question_candidates (
                        id, tenant_id, acquisition_method, item_type, payload_json,
                        content_hash, structural_hash, acquisition_status, verification_status,
                        rights_status, candidate_status
                    )
                    VALUES (
                        CAST(:id AS uuid), CAST(:tid AS uuid), 'WEB_CRAWL', :itype, CAST(:payload AS jsonb),
                        :chash, :shash, 'EXTRACTED', 'UNCHECKED', 'PENDING_REVIEW', 'NEEDS_REVIEW'
                    )
                    ON CONFLICT (id) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "id": str(cand_uuid),
                    "tid": str(tenant_id),
                    "itype": mapped_type.value,
                    "payload": json.dumps({"stem_md": stem_nfc, "legacy_crawled_id": int(cqid)}, ensure_ascii=False),
                    "chash": chash,
                    "shash": shash,
                },
            )
            row_c = ins_c.fetchone()

            if norm_status in {"DUPLICATE", "SIMILAR"}:
                ctype = "EXACT" if norm_status == "DUPLICATE" else "STRUCTURAL_VARIANT"
                hval = chash if ctype == "EXACT" else shash
                cl_uuid = _det_uuid(f"{tenant_id}:dupcluster", f"{ctype}:{hval}")
                canon_q = _det_uuid(f"{tenant_id}:question", str(dup_of)) if dup_of is not None else None
                await conn.execute(
                    text(
                        """
                        INSERT INTO duplicate_clusters (id, tenant_id, cluster_type, canonical_question_id, status)
                        VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), :ctype, CAST(:cqid AS uuid), 'PENDING_REVIEW')
                        ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    {
                        "id": str(cl_uuid),
                        "tid": str(tenant_id),
                        "ctype": ctype,
                        "cqid": str(canon_q) if canon_q else None,
                    },
                )
                await conn.execute(
                    text(
                        """
                        INSERT INTO duplicate_members (cluster_id, candidate_id, tenant_id, similarity_score)
                        VALUES (CAST(:clid AS uuid), CAST(:cid AS uuid), CAST(:tid AS uuid), :sim)
                        ON CONFLICT (cluster_id, candidate_id) DO NOTHING
                        """
                    ),
                    {
                        "clid": str(cl_uuid),
                        "cid": str(cand_uuid),
                        "tid": str(tenant_id),
                        "sim": 1.0 if ctype == "EXACT" else 0.9,
                    },
                )

            if row_c is not None:
                inserted += 1
            else:
                existing += 1
            migrated_ids.append(lid)

    report = compute_batch_reconciliation_report(
        source_legacy_ids=source_ids,
        migrated_legacy_ids=migrated_ids,
        skipped_reasons=skipped_reasons,
    )
    return StageExecutionStats(
        stage="dedup",
        source_count=len(source_ids),
        inserted_count=inserted,
        existing_count=existing,
        skipped_count=len(skipped_reasons),
        skipped_reasons=skipped_reasons,
        reconciliation=report,
        batches_executed=batches,
    )


async def _run_assessments_stage(
    conn: AsyncSession | AsyncConnection,
    tenant_id: uuid.UUID,
    batch_size: int,
) -> StageExecutionStats:
    if not (
        await _target_ready(conn, "assessments", {"id", "tenant_id", "title", "subject_code"})
        and await _target_ready(
            conn, "assessment_versions", {"id", "tenant_id", "assessment_id", "structure_snapshot_json"}
        )
        and await _target_ready(
            conn, "assessment_answer_keys", {"assessment_version_id", "key_revision_no", "answer_key_payload_json"}
        )
        and await _target_ready(conn, "scoring_scheme_versions", {"id", "code", "rules_json"})
    ):
        return StageExecutionStats(
            stage="assessments", source_count=0, inserted_count=0, existing_count=0, skipped_count=0, executed=False
        )

    ex_table = await _pick_legacy_table(conn, ("legacy_exams", "exams"), {"id", "title", "duration_seconds"})
    if not ex_table:
        return StageExecutionStats(
            stage="assessments", source_count=0, inserted_count=0, existing_count=0, skipped_count=0, executed=False
        )

    eq_table = await _pick_legacy_table(
        conn, ("legacy_exam_questions", "exam_questions"), {"exam_id", "question_id", "position"}
    )
    scheme_id = _det_uuid("global:scoring_scheme", "THPTQG_2025_DEFAULT")
    await conn.execute(
        text(
            """
            INSERT INTO scoring_scheme_versions (id, code, rules_json)
            VALUES (CAST(:id AS uuid), 'THPTQG_2025_DEFAULT', CAST(:rules AS jsonb))
            ON CONFLICT (code) DO NOTHING
            """
        ),
        {"id": str(scheme_id), "rules": json.dumps({"part1": 0.25, "part2": [0.1, 0.25, 0.5, 1.0], "part3": 0.5})},
    )
    actual_scheme_id = str(
        (
            await conn.execute(text("SELECT id FROM scoring_scheme_versions WHERE code = 'THPTQG_2025_DEFAULT'"))
        ).scalar_one()
    )

    res = await conn.execute(
        text(f"SELECT id, title, duration_seconds, access_tier, origin_type FROM {ex_table} ORDER BY id")
    )
    rows = res.fetchall()

    source_ids: list[str] = []
    migrated_ids: list[str] = []
    skipped_reasons: dict[str, str] = {}
    inserted = 0
    existing = 0
    batches = 0

    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        batches += 1
        await _set_tenant_rls(conn, tenant_id)
        for ex_id, title, duration, access_tier, origin_type in chunk:
            lid = str(ex_id)
            source_ids.append(lid)
            if not title or not str(title).strip():
                skipped_reasons[lid] = "EMPTY_EXAM_TITLE"
                continue

            aid = _det_uuid(f"{tenant_id}:assessment", lid)
            avid = _det_uuid(f"{tenant_id}:assessment_ver", f"{lid}:1")

            snapshot_items: list[dict[str, Any]] = []
            answer_map: dict[str, Any] = {}
            if eq_table:
                q_res = await conn.execute(
                    text(
                        f"SELECT question_id, position FROM {eq_table} "
                        "WHERE exam_id = :eid ORDER BY position ASC, question_id ASC"
                    ),
                    {"eid": ex_id},
                )
                for qid, pos in q_res.fetchall():
                    qv_id = _det_uuid(f"{tenant_id}:question_ver", f"{qid}:1")
                    snapshot_items.append({"question_version_id": str(qv_id), "position": int(pos or 0)})
                    ans_row = await conn.execute(
                        text(
                            "SELECT answer_schema FROM question_version_answers "
                            "WHERE question_version_id = CAST(:qvid AS uuid)"
                        ),
                        {"qvid": str(qv_id)},
                    )
                    ans_val = ans_row.scalar_one_or_none()
                    if ans_val is not None:
                        answer_map[str(qv_id)] = ans_val

            norm_rights = "OWNED" if str(origin_type or "").upper() == "ORIGINAL" else "PENDING_REVIEW"
            norm_tier = "STUDENT_PRO" if str(access_tier or "").upper() == "PRO" else "FREE"
            ins_a = await conn.execute(
                text(
                    """
                    INSERT INTO assessments (
                        id, tenant_id, visibility_scope, title, subject_code, grade_band, exam_type_code,
                        access_tier, verification_status, rights_status, publication_status
                    )
                    VALUES (
                        CAST(:id AS uuid), CAST(:tid AS uuid), 'SYSTEM_PUBLIC', :title, 'MATH', 'GRADE_12', 'THPTQG',
                        :tier, 'UNCHECKED', :rights, 'DRAFT'
                    )
                    ON CONFLICT (id) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "id": str(aid),
                    "tid": str(tenant_id),
                    "title": str(title)[:500],
                    "tier": norm_tier,
                    "rights": norm_rights,
                },
            )
            row_a = ins_a.fetchone()
            snapshot_payload = {"legacy_exam_id": int(ex_id), "items": snapshot_items}
            await conn.execute(
                text(
                    """
                    INSERT INTO assessment_versions (
                        id, tenant_id, assessment_id, version_no, scoring_scheme_id,
                        duration_seconds, structure_snapshot_json, is_immutable
                    )
                    VALUES (
                        CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:aid AS uuid), 1,
                        CAST(:sid AS uuid), :dur, CAST(:snap AS jsonb), TRUE
                    )
                    ON CONFLICT (assessment_id, version_no) DO NOTHING
                    """
                ),
                {
                    "id": str(avid),
                    "tid": str(tenant_id),
                    "aid": str(aid),
                    "sid": actual_scheme_id,
                    "dur": max(60, int(duration or 5400)),
                    "snap": json.dumps(snapshot_payload, ensure_ascii=False),
                },
            )
            answer_json_str = json.dumps(answer_map, ensure_ascii=False, sort_keys=True)
            await conn.execute(
                text(
                    """
                    INSERT INTO assessment_answer_keys (
                        assessment_version_id, key_revision_no, tenant_id, is_current,
                        answer_key_payload_json, key_checksum_sha256, change_reason
                    )
                    VALUES (
                        CAST(:avid AS uuid), 1, CAST(:tid AS uuid), TRUE,
                        CAST(:payload AS jsonb), :sha, 'BACKFILL_INITIAL'
                    )
                    ON CONFLICT (assessment_version_id, key_revision_no) DO NOTHING
                    """
                ),
                {
                    "avid": str(avid),
                    "tid": str(tenant_id),
                    "payload": answer_json_str,
                    "sha": _sha256_text(answer_json_str),
                },
            )
            await conn.execute(
                text(
                    "UPDATE assessments SET current_version_id = COALESCE(current_version_id, CAST(:avid AS uuid)) "
                    "WHERE id = CAST(:aid AS uuid)"
                ),
                {"avid": str(avid), "aid": str(aid)},
            )
            if row_a is not None:
                inserted += 1
            else:
                existing += 1
            migrated_ids.append(lid)

    report = compute_batch_reconciliation_report(
        source_legacy_ids=source_ids,
        migrated_legacy_ids=migrated_ids,
        skipped_reasons=skipped_reasons,
    )
    return StageExecutionStats(
        stage="assessments",
        source_count=len(source_ids),
        inserted_count=inserted,
        existing_count=existing,
        skipped_count=len(skipped_reasons),
        skipped_reasons=skipped_reasons,
        reconciliation=report,
        batches_executed=batches,
    )


async def run_backfill(
    conn: AsyncSession | AsyncConnection,
    *,
    tenant_id: uuid.UUID = DEFAULT_SYSTEM_TENANT_ID,
    batch_size: int = DEFAULT_BATCH_SIZE,
    subject_filter: Sequence[str] | None = None,
    grades_filter: Sequence[int] | None = None,
    second_pass_created_count: int | None = None,
) -> BackfillExecutionSummary:
    """Execute idempotent batched backfill in strict dependency order per BACKFILL_MAP.md section 6."""
    if batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")

    stages = (
        await _run_assets_stage(conn, tenant_id, batch_size),
        await _run_sources_stage(conn, tenant_id, batch_size),
        await _run_taxonomy_stage(conn, tenant_id, batch_size),
        await _run_curriculum_stage(conn, tenant_id, batch_size),
        await _run_content_stage(conn, tenant_id, batch_size),
        await _run_questions_stage(
            conn,
            tenant_id,
            batch_size,
            subject_filter=subject_filter,
            grades_filter=grades_filter,
        ),
        await _run_dedup_stage(conn, tenant_id, batch_size),
        await _run_assessments_stage(conn, tenant_id, batch_size),
    )

    total_source = sum(s.source_count for s in stages)
    total_inserted = sum(s.inserted_count for s in stages)
    total_existing = sum(s.existing_count for s in stages)
    total_skipped = sum(s.skipped_count for s in stages)
    all_balanced = all(s.reconciliation is None or s.reconciliation.balanced for s in stages)

    return BackfillExecutionSummary(
        tenant_id=tenant_id,
        batch_size=batch_size,
        stages=stages,
        total_source_rows=total_source,
        total_inserted_rows=total_inserted,
        total_existing_rows=total_existing,
        total_skipped_rows=total_skipped,
        all_balanced=all_balanced,
        is_second_pass_zero_inserts=(
            second_pass_created_count == 0 if second_pass_created_count is not None else total_inserted == 0
        ),
    )


async def _run_cli_async(
    database_url: str,
    *,
    tenant_id: uuid.UUID,
    batch_size: int,
    subjects: Sequence[str] | None,
    grades: Sequence[int] | None,
) -> BackfillExecutionSummary:
    engine = create_async_engine(database_url, echo=False)
    try:
        async with engine.begin() as conn:
            return await run_backfill(
                conn,
                tenant_id=tenant_id,
                batch_size=batch_size,
                subject_filter=subjects,
                grades_filter=grades,
            )
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ULP V11.3 Idempotent Legacy Backfill Runner (TASK-23)")
    parser.add_argument("--database-url", required=True, help="Async SQLAlchemy PostgreSQL URL")
    parser.add_argument("--tenant-id", default=str(DEFAULT_SYSTEM_TENANT_ID), help="Target UUID tenant_id")
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Rows processed per batch (default: 500)"
    )
    parser.add_argument("--subjects", default="", help="Optional comma-separated subjects, e.g. MATH,PHYSICS")
    parser.add_argument("--grades", default="", help="Optional comma-separated grades, e.g. 10,11,12")
    args = parser.parse_args(argv)

    subjects = [s.strip() for s in args.subjects.split(",") if s.strip()] or None
    grades = [int(g.strip()) for g in args.grades.split(",") if g.strip()] or None
    summary = asyncio.run(
        _run_cli_async(
            args.database_url,
            tenant_id=uuid.UUID(args.tenant_id),
            batch_size=args.batch_size,
            subjects=subjects,
            grades=grades,
        )
    )
    print(
        json.dumps(
            {
                "tenant_id": str(summary.tenant_id),
                "batch_size": summary.batch_size,
                "total_source_rows": summary.total_source_rows,
                "total_inserted_rows": summary.total_inserted_rows,
                "total_existing_rows": summary.total_existing_rows,
                "total_skipped_rows": summary.total_skipped_rows,
                "all_balanced": summary.all_balanced,
                "stages": [
                    {
                        "stage": s.stage,
                        "source": s.source_count,
                        "inserted": s.inserted_count,
                        "existing": s.existing_count,
                        "skipped": s.skipped_count,
                        "balanced": s.reconciliation.balanced if s.reconciliation else True,
                    }
                    for s in summary.stages
                ],
            },
            indent=2,
        )
    )
    return 0 if summary.all_balanced else 1


if __name__ == "__main__":
    raise SystemExit(main())
