"""W0.6 / TASK-23 Integration Tests for Idempotent Batched Legacy-to-V11.3 Backfill Runner.

Verifies:
1. Full 8-stage execution order (`assets -> sources -> taxonomy -> curriculum ->
   content -> questions -> dedup -> assessments`) with `batch_size=500`.
2. Pass-2 idempotency (`total_inserted_rows == 0`, unchanged table row counts).
3. Strict reconciliation (`source_count == migrated_count + skipped_count`) with
   explicit skip reasons (`EMPTY_STEM`, `OUT_OF_SCOPE_SUBJECT`, `OUT_OF_SCOPE_GRADE`).
4. 5-axis fail-closed gating, `HSG -> VDC` + tag preservation, and anti-answer-leak
   separation (`question_options` / `structure_snapshot_json` free of answer keys).
5. Unicode NFC `content_hash` & `structural_hash` parity with `app.modules.dedup.public`.
6. CLI `main()` execution against Alembic-migrated PostgreSQL tables in `public`.
"""

from __future__ import annotations

import asyncio
import json
import os
import unicodedata
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.modules.dedup.public import (
    content_hash as dedup_content_hash,
)
from app.modules.dedup.public import (
    structural_hash as dedup_structural_hash,
)
from app.ops.run_backfill import (
    DEFAULT_SYSTEM_TENANT_ID,
    STAGE_ORDER,
    compute_nfc_content_hash,
    compute_nfc_structural_hash,
    run_backfill,
)
from app.ops.run_backfill import (
    main as backfill_cli_main,
)


def _async_db_url() -> str:
    return os.environ.get(
        "TEST_DATABASE_URL",
        os.environ.get("DATABASE_URL", "postgresql+asyncpg://exam:exam@127.0.0.1:5432/exam_test?ssl=disable"),
    )


_ISOLATED_DDL_STATEMENTS: tuple[str, ...] = (
    # --- Legacy source tables ---
    """
    CREATE TABLE legacy_crawler_sites (
        site_id VARCHAR(64) PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        config JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE TABLE legacy_library_documents (
        id BIGINT PRIMARY KEY,
        title VARCHAR(500) NOT NULL,
        kind VARCHAR(64) NOT NULL,
        source VARCHAR(255),
        source_file_id VARCHAR(255),
        storage_key VARCHAR(512),
        preview_storage_key VARCHAR(512),
        checksum CHAR(64),
        mime VARCHAR(120),
        size_bytes BIGINT,
        markdown_content TEXT,
        access_tier VARCHAR(32) NOT NULL DEFAULT 'FREE'
    )
    """,
    """
    CREATE TABLE legacy_knowledge_articles (
        id BIGINT PRIMARY KEY,
        title VARCHAR(500) NOT NULL,
        summary_md TEXT,
        formula_md TEXT,
        method_md TEXT,
        tip_md TEXT,
        access_tier VARCHAR(32) NOT NULL DEFAULT 'FREE'
    )
    """,
    """
    CREATE TABLE legacy_categories (
        id BIGINT PRIMARY KEY,
        parent_id BIGINT,
        name VARCHAR(255) NOT NULL,
        depth INT NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE legacy_knowledge_units (
        id BIGINT PRIMARY KEY,
        parent_id BIGINT,
        subject VARCHAR(64),
        grade INT,
        unit_type VARCHAR(64) NOT NULL,
        code VARCHAR(120),
        name VARCHAR(255) NOT NULL,
        sort_order INT NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE legacy_questions (
        id BIGINT PRIMARY KEY,
        question_type VARCHAR(64) NOT NULL,
        difficulty VARCHAR(64) NOT NULL,
        content TEXT,
        choices JSONB NOT NULL DEFAULT '[]'::jsonb,
        accepted_answers JSONB NOT NULL DEFAULT '[]'::jsonb,
        explanation TEXT,
        image_path VARCHAR(512),
        origin_type VARCHAR(32) NOT NULL DEFAULT 'PUBLIC',
        audit_status VARCHAR(64),
        is_verified BOOLEAN NOT NULL DEFAULT FALSE,
        answer_source VARCHAR(64),
        target_tags VARCHAR(50)[],
        access_tier VARCHAR(32) NOT NULL DEFAULT 'FREE',
        knowledge_unit_id BIGINT REFERENCES legacy_knowledge_units(id),
        category_id BIGINT REFERENCES legacy_categories(id)
    )
    """,
    """
    CREATE TABLE legacy_crawled_questions (
        id BIGINT PRIMARY KEY,
        question_type VARCHAR(64) NOT NULL,
        content TEXT NOT NULL,
        choices JSONB NOT NULL DEFAULT '[]'::jsonb,
        images JSONB NOT NULL DEFAULT '[]'::jsonb,
        status VARCHAR(32) NOT NULL,
        duplicate_of BIGINT
    )
    """,
    """
    CREATE TABLE legacy_exams (
        id BIGINT PRIMARY KEY,
        title VARCHAR(500) NOT NULL,
        duration_seconds INT NOT NULL,
        access_tier VARCHAR(32) NOT NULL DEFAULT 'FREE',
        origin_type VARCHAR(32) NOT NULL DEFAULT 'PUBLIC'
    )
    """,
    """
    CREATE TABLE legacy_exam_questions (
        exam_id BIGINT NOT NULL REFERENCES legacy_exams(id),
        question_id BIGINT NOT NULL,
        position INT NOT NULL,
        PRIMARY KEY (exam_id, question_id)
    )
    """,
    # --- V11.3 target tables across all 8 stages ---
)

# The Chapter 4 targets are cloned from the migrated public tables (columns, defaults, generated columns, CHECK, UNIQUE
# and indexes), so this test cannot drift from the schema the migrations build.
_TARGET_CLONE_TABLES: tuple[str, ...] = (
    "assets",
    "sources",
    "source_documents",
    "document_versions",
    "taxonomy_nodes",
    "taxonomy_node_versions",
    "taxonomy_aliases",
    "curriculum_versions",
    "curriculum_nodes",
    "content_items",
    "content_versions",
    "questions",
    "question_versions",
    "question_options",
    "question_version_answers",
    "question_candidates",
    "duplicate_clusters",
    "duplicate_members",
    "scoring_scheme_versions",
    "assessments",
    "assessment_versions",
    "assessment_answer_keys",
)


async def _create_isolated_schema(conn: AsyncConnection, schema_name: str) -> None:
    await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
    await conn.execute(text(f"CREATE SCHEMA {schema_name}"))
    await conn.execute(text(f"SET LOCAL search_path TO {schema_name}, public"))
    for stmt in _ISOLATED_DDL_STATEMENTS:
        await conn.execute(text(stmt))
    for table in _TARGET_CLONE_TABLES:
        await conn.execute(text(f"CREATE TABLE {schema_name}.{table} (LIKE public.{table} INCLUDING ALL)"))


async def _count_target_tables(conn: AsyncConnection, schema_name: str) -> dict[str, int]:
    await conn.execute(text(f"SET LOCAL search_path TO {schema_name}, public"))
    counts: dict[str, int] = {}
    for t in _TARGET_CLONE_TABLES:
        res = await conn.execute(text(f"SELECT COUNT(*) FROM {t}"))
        counts[t] = int(res.scalar_one())
    return counts


def test_nfc_content_and_structural_hash_parity_with_dedup_module() -> None:
    nfd_stem = unicodedata.normalize("NFD", "Tính đồng biến của hàm số bậc 3 với tham số m = 25")
    nfc_stem = unicodedata.normalize("NFC", nfd_stem)
    opts_a = ["m > 2", "m < 1"]
    opts_b = ["m < 1", "m > 2"]

    assert compute_nfc_content_hash(nfd_stem, opts_a) == compute_nfc_content_hash(nfc_stem, opts_b)
    assert compute_nfc_content_hash(nfd_stem, opts_a) == dedup_content_hash(nfc_stem, opts_b)

    chem_1 = "Cho 25 gam dung dịch H2SO4 tác dụng với Fe"
    chem_2 = "Cho 40 gam dung dịch H₂SO₄ tác dụng với Fe"
    chem_diff = "Cho 40 gam dung dịch H2O tác dụng với Fe"

    assert compute_nfc_structural_hash(chem_1, []) == compute_nfc_structural_hash(chem_2, [])
    assert compute_nfc_structural_hash(chem_1, []) == dedup_structural_hash(chem_1, [])
    assert compute_nfc_structural_hash(chem_1, []) != compute_nfc_structural_hash(chem_diff, [])


def test_full_eight_stage_backfill_batched_and_idempotent_second_pass() -> None:
    schema_name = f"backfill_e2e_{uuid.uuid4().hex[:10]}"
    db_url = _async_db_url()

    async def _run() -> None:
        engine = create_async_engine(db_url, echo=False)
        try:
            async with engine.begin() as conn:
                await _create_isolated_schema(conn, schema_name)
                await conn.execute(text(f"SET LOCAL search_path TO {schema_name}, public"))

                # Seed sites & docs (including 1 doc missing storage_key to verify skip reconciliation)
                await conn.execute(
                    text(
                        """
                        INSERT INTO legacy_crawler_sites (site_id, name, config) VALUES
                            ('vted', 'VTED Bank', '{"rate_limit": 2}'::jsonb),
                            ('toanmath', 'ToanMath Archive', '{"rate_limit": 1}'::jsonb)
                        """
                    )
                )
                await conn.execute(
                    text(
                        """
                        INSERT INTO legacy_library_documents
                            (id, title, kind, source, source_file_id, storage_key,
                             preview_storage_key, checksum, mime, size_bytes, markdown_content, access_tier)
                        VALUES
                            (1, 'Chuyên đề Hàm số 12', 'TOPIC', 'drive:math-folder', 'gdrive-001',
                             'uploads/lib/ham-so-12.pdf', 'previews/lib/ham-so-12.pdf',
                             NULL, 'application/pdf', 2048, '# Tóm tắt cực trị hàm số', 'PRO'),
                            (2, 'Tài liệu lỗi thiếu file', 'PAPER', 'manual', NULL,
                             NULL, NULL, NULL, 'application/pdf', 0, 'Empty file ref', 'FREE')
                        """
                    )
                )
                await conn.execute(
                    text(
                        """
                        INSERT INTO legacy_knowledge_articles
                            (id, title, summary_md, formula_md, method_md, tip_md, access_tier)
                        VALUES (
                            10, 'Đạo hàm cơ bản', 'Định nghĩa đạo hàm',
                            '(x^n)'' = n x^{n-1}', 'Bước 1: tính giới hạn', 'Chú ý tập xác định', 'FREE'
                        )
                        """
                    )
                )
                await conn.execute(
                    text(
                        """
                        INSERT INTO legacy_categories (id, parent_id, name, depth) VALUES
                            (1, NULL, 'Toán THPT', 0),
                            (2, 1, 'Giải tích 12', 1),
                            (3, 2, 'Cực trị hàm số', 2)
                        """
                    )
                )
                await conn.execute(
                    text(
                        """
                        INSERT INTO legacy_knowledge_units
                            (id, parent_id, subject, grade, unit_type, code, name, sort_order)
                        VALUES
                            (100, NULL, 'Toán', 12, 'CHAPTER', 'C1_HAM_SO', 'Chương I: Ứng dụng đạo hàm', 1),
                            (101, 100, 'Toán', 12, 'LESSON', 'B1_DON_DIEU', 'Tính đơn điệu và cực trị', 2),
                            (102, 101, 'Toán', 12, 'TOPIC', 'D1_THAM_SO_M', 'Tìm tham số m để hàm đồng biến', 3),
                            (103, 102, 'Toán', 12, 'LEGACY_META', 'META_ONLY', 'Metadata bỏ qua', 4)
                        """
                    )
                )

                nfd_stem = unicodedata.normalize("NFD", "Tìm giá trị cực đại của hàm số y = x^3 - 3x + 2")
                choices_sc = json.dumps(
                    [
                        {"id": "A", "content": "y = 4", "is_correct": True},
                        {"id": "B", "content": "y = 0", "is_correct": False},
                    ],
                    ensure_ascii=False,
                )
                choices_tf = json.dumps(
                    [
                        {"id": "a", "content": "Hàm số có 2 điểm cực trị", "is_correct": True},
                        {"id": "b", "content": "Đạo hàm luôn dương", "is_correct": False},
                    ],
                    ensure_ascii=False,
                )

                await conn.execute(
                    text(
                        """
                        INSERT INTO legacy_questions
                            (id, question_type, difficulty, content, choices, accepted_answers, explanation,
                             image_path, origin_type, audit_status, is_verified, answer_source,
                             target_tags, access_tier, knowledge_unit_id, category_id)
                        VALUES
                            (1, 'MULTIPLE_CHOICE', 'HSG', :nfd_stem, CAST(:c_sc AS jsonb), '[]'::jsonb,
                             'Tính y'' = 3x^2 - 3 = 0', 'questions/img/q1.png', 'AUTHORED',
                             'VERIFIED_CONSENSUS', TRUE, 'AUTHOR', ARRAY['SGK_KNTT'], 'PRO', 102, 3),
                            (2, 'MULTIPLE_CHOICE', 'UNDERSTANDING', 'Giá trị nhỏ nhất của x^2 + 1 là bao nhiêu?',
                             CAST(:c_sc AS jsonb), '[]'::jsonb, 'x^2 >= 0', NULL, 'PUBLIC', 'PENDING',
                             TRUE, 'PDF_ANSWER_KEY', NULL, 'FREE', 101, 2),
                            (3, 'TRUE_FALSE', 'APPLICATION', 'Xét tính đúng sai cho hàm bậc ba y = x^3 - 3x',
                             CAST(:c_tf AS jsonb), '[]'::jsonb, 'y'' = 3x^2 - 3', NULL, 'AUTHORED',
                             'VERIFIED_CONSENSUS', TRUE, 'AUTHOR', ARRAY['THPTQG'], 'FREE', 102, 3),
                            (4, 'SHORT_ANSWER', 'RECOGNITION', 'Số nghiệm của phương trình 2x - 6 = 0 là:',
                             '[]'::jsonb, '["3"]'::jsonb, '2x = 6 => x = 3', NULL, 'AUTHORED',
                             'VERIFIED_CONSENSUS', TRUE, 'AUTHOR', NULL, 'FREE', 101, 2),
                            (5, 'MULTIPLE_CHOICE', 'RECOGNITION', '   ', '[]'::jsonb, '[]'::jsonb, '', NULL,
                             'PUBLIC', 'PENDING', FALSE, NULL, NULL, 'FREE', 101, 2)
                        """
                    ),
                    {"nfd_stem": nfd_stem, "c_sc": choices_sc, "c_tf": choices_tf},
                )

                # Add 500 more questions (id 6..505) so total=505 triggers 2 keyset batches at batch_size=500.
                batch_choices = json.dumps(
                    [{"id": "A", "content": "1", "is_correct": True}, {"id": "B", "content": "2", "is_correct": False}]
                )
                await conn.execute(
                    text(
                        """
                        INSERT INTO legacy_questions
                            (id, question_type, difficulty, content, choices, accepted_answers, explanation,
                             origin_type, audit_status, is_verified, answer_source, access_tier, knowledge_unit_id)
                        SELECT
                            g,
                            'MULTIPLE_CHOICE',
                            'UNDERSTANDING',
                            'Câu hỏi batch #' || g || ' tính giá trị 2x + ' || g,
                            CAST(:batch_choices AS jsonb),
                            '[]'::jsonb,
                            'Giải chi tiết ' || g,
                            'PUBLIC',
                            NULL,
                            TRUE,
                            'KEY',
                            'FREE',
                            101
                        FROM generate_series(6, 505) AS g
                        """
                    ),
                    {"batch_choices": batch_choices},
                )

                await conn.execute(
                    text(
                        """
                        INSERT INTO legacy_crawled_questions
                            (id, question_type, content, choices, images, status, duplicate_of)
                        VALUES
                            (9001, 'MULTIPLE_CHOICE', :nfd_stem, CAST(:c_sc AS jsonb),
                             '["crawled/c9001.png"]'::jsonb, 'DUPLICATE', 1),
                            (9002, 'MULTIPLE_CHOICE', 'Cho 15 gam H2SO4 tác dụng hết với Mg',
                             '[]'::jsonb, '[]'::jsonb, 'SIMILAR', 1)
                        """
                    ),
                    {"nfd_stem": nfd_stem, "c_sc": choices_sc},
                )

                await conn.execute(
                    text(
                        """
                        INSERT INTO legacy_exams (id, title, duration_seconds, access_tier, origin_type)
                        VALUES (50, 'Đề kiểm tra Giải tích 12 số 1', 2700, 'FREE', 'PUBLIC')
                        """
                    )
                )
                await conn.execute(
                    text(
                        """
                        INSERT INTO legacy_exam_questions (exam_id, question_id, position) VALUES
                            (50, 1, 1),
                            (50, 3, 2),
                            (50, 4, 3)
                        """
                    )
                )

            # --- PASS 1 ---
            async with engine.begin() as conn:
                await conn.execute(text(f"SET LOCAL search_path TO {schema_name}, public"))
                pass1 = await run_backfill(conn, tenant_id=DEFAULT_SYSTEM_TENANT_ID, batch_size=500)

            assert tuple(s.stage for s in pass1.stages) == STAGE_ORDER
            assert pass1.all_balanced is True
            assert pass1.total_inserted_rows > 500
            s_map1 = pass1.stage_map()
            assert all(s.executed is True for s in pass1.stages)

            # Batching & skip verification
            assert s_map1["questions"].source_count == 505
            assert s_map1["questions"].batches_executed == 2
            assert s_map1["questions"].inserted_count == 504
            assert s_map1["questions"].skipped_count == 1
            assert s_map1["questions"].skipped_reasons == {"5": "EMPTY_STEM"}

            assert s_map1["sources"].skipped_reasons == {"libdoc:2": "MISSING_STORAGE_KEY"}
            assert s_map1["curriculum"].skipped_reasons == {"ku:103": "UNSUPPORTED_NODE_LEVEL:LEGACY_META"}

            # Verify V11.3 invariants in DB
            async with engine.begin() as conn:
                await conn.execute(text(f"SET LOCAL search_path TO {schema_name}, public"))
                counts_after_pass1 = await _count_target_tables(conn, schema_name)
                assert counts_after_pass1["questions"] == 504
                assert counts_after_pass1["question_versions"] == 504
                assert counts_after_pass1["question_version_answers"] == 504
                assert counts_after_pass1["assessments"] == 1
                assert counts_after_pass1["assessment_answer_keys"] == 1
                assert counts_after_pass1["duplicate_clusters"] == 2
                assert counts_after_pass1["duplicate_members"] == 2

                # Check Q1 (AUTHORED + VERIFIED_CONSENSUS + HSG)
                q1 = (
                    (
                        await conn.execute(
                            text(
                                """
                            SELECT q.difficulty_level, q.exam_framework_tags, q.rights_status, q.verification_status,
                                   q.publication_status, q.access_tier, qv.stem_json, qv.options_json, qv.content_hash,
                                   qva.answer_schema
                            FROM questions q
                            JOIN question_versions qv ON qv.id = q.current_version_id
                            JOIN question_version_answers qva ON qva.question_version_id = qv.id
                            WHERE q.stable_code = 'LEG-1'
                            """
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                assert q1["difficulty_level"] == "VDC"
                assert "HSG" in q1["exam_framework_tags"]
                assert q1["rights_status"] == "OWNED"
                assert q1["verification_status"] == "VERIFIED"
                assert q1["publication_status"] == "PUBLISHED"
                assert q1["access_tier"] == "STUDENT_PRO"
                assert q1["content_hash"] == dedup_content_hash(
                    unicodedata.normalize("NFC", nfd_stem),
                    ["y = 4", "y = 0"],
                )
                # Anti-answer-leak: options_json never contains is_correct
                for opt in q1["options_json"]:
                    assert "is_correct" not in opt
                assert q1["answer_schema"]["correct_option_keys"] == ["A"]

                # Check Q2 (crawler PUBLIC + is_verified=True + PDF_ANSWER_KEY -> fail-closed DRAFT)
                q2 = (
                    (
                        await conn.execute(
                            text(
                                """
                            SELECT rights_status, verification_status, publication_status
                            FROM questions WHERE stable_code = 'LEG-2'
                            """
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                assert q2["rights_status"] == "PENDING_REVIEW"
                assert q2["verification_status"] == "AUTO_VERIFIED"
                assert q2["publication_status"] == "DRAFT"

                # assessment_versions snapshot must have NO answer keys; keys live in assessment_answer_keys
                av = (
                    (
                        await conn.execute(
                            text(
                                """
                            SELECT av.structure_snapshot_json, aak.answer_key_payload_json
                            FROM assessment_versions av
                            JOIN assessment_answer_keys aak ON aak.assessment_version_id = av.id
                            """
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                snap_str = json.dumps(av["structure_snapshot_json"])
                assert "correct_option_keys" not in snap_str
                assert "accepted_answers" not in snap_str
                assert len(av["answer_key_payload_json"]) == 3

            # --- PASS 2 (idempotency check) ---
            async with engine.begin() as conn:
                await conn.execute(text(f"SET LOCAL search_path TO {schema_name}, public"))
                pass2 = await run_backfill(conn, tenant_id=DEFAULT_SYSTEM_TENANT_ID, batch_size=500)
                counts_after_pass2 = await _count_target_tables(conn, schema_name)

            assert pass2.all_balanced is True
            assert pass2.total_inserted_rows == 0
            assert pass2.is_second_pass_zero_inserts is True
            assert all(s.inserted_count == 0 for s in pass2.stages)
            assert counts_after_pass2 == counts_after_pass1
        finally:
            async with engine.begin() as conn:
                await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
            await engine.dispose()

    asyncio.run(_run())


def test_scoped_subject_grade_filter_and_reconciliation() -> None:
    schema_name = f"backfill_scope_{uuid.uuid4().hex[:10]}"
    db_url = _async_db_url()

    async def _run() -> None:
        engine = create_async_engine(db_url, echo=False)
        try:
            async with engine.begin() as conn:
                await _create_isolated_schema(conn, schema_name)
                await conn.execute(text(f"SET LOCAL search_path TO {schema_name}, public"))
                await conn.execute(
                    text(
                        """
                        INSERT INTO legacy_knowledge_units
                            (id, parent_id, subject, grade, unit_type, code, name, sort_order)
                        VALUES
                            (1, NULL, 'Toán học', 12, 'LESSON', 'M12', 'Toán 12', 1),
                            (2, NULL, 'Vật lí', 10, 'LESSON', 'P10', 'Lý 10', 2),
                            (3, NULL, 'Sinh học', 12, 'LESSON', 'B12', 'Sinh 12', 3),
                            (4, NULL, 'Toán học', 9, 'LESSON', 'M9', 'Toán 9', 4)
                        """
                    )
                )
                await conn.execute(
                    text(
                        """
                        INSERT INTO legacy_questions (id, question_type, difficulty, content, knowledge_unit_id) VALUES
                            (10, 'MULTIPLE_CHOICE', 'RECOGNITION', 'Đạo hàm của x^2 là gì?', 1),
                            (11, 'MULTIPLE_CHOICE', 'UNDERSTANDING', 'Định luật II Newton F = ma', 2),
                            (12, 'MULTIPLE_CHOICE', 'RECOGNITION', 'Cấu trúc DNA gồm mấy mạch?', 3),
                            (13, 'MULTIPLE_CHOICE', 'RECOGNITION', 'Hệ phương trình lớp 9', 4),
                            (14, 'UNKNOWN_LEGACY_TYPE', 'RECOGNITION', 'Câu hỏi sai type', 1)
                        """
                    )
                )

                summary = await run_backfill(
                    conn,
                    tenant_id=DEFAULT_SYSTEM_TENANT_ID,
                    batch_size=2,
                    subject_filter=("MATH", "PHYSICS"),
                    grades_filter=(10, 11, 12),
                )

            q_stats = summary.stage_map()["questions"]
            assert summary.all_balanced is True
            assert q_stats.source_count == 5
            assert q_stats.inserted_count == 2
            assert q_stats.skipped_count == 3
            assert q_stats.skipped_reasons["12"] == "OUT_OF_SCOPE_SUBJECT:BIOLOGY"
            assert q_stats.skipped_reasons["13"] == "OUT_OF_SCOPE_GRADE:9"
            assert q_stats.skipped_reasons["14"].startswith("MAPPING_ERROR:")
            assert q_stats.reconciliation is not None and q_stats.reconciliation.balanced is True
        finally:
            async with engine.begin() as conn:
                await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
            await engine.dispose()

    asyncio.run(_run())


def test_cli_main_against_alembic_public_sprint1_tables(capsys) -> None:
    db_url = _async_db_url()

    async def _setup_legacy_public() -> None:
        engine = create_async_engine(db_url, echo=False)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        """
                        CREATE TABLE IF NOT EXISTS legacy_crawler_sites (
                            site_id VARCHAR(64) PRIMARY KEY,
                            name VARCHAR(255) NOT NULL,
                            config JSONB NOT NULL DEFAULT '{}'::jsonb
                        )
                        """
                    )
                )
                await conn.execute(
                    text(
                        """
                        CREATE TABLE IF NOT EXISTS legacy_library_documents (
                            id BIGINT PRIMARY KEY,
                            title VARCHAR(500) NOT NULL,
                            kind VARCHAR(64) NOT NULL,
                            source VARCHAR(255),
                            source_file_id VARCHAR(255),
                            storage_key VARCHAR(512),
                            preview_storage_key VARCHAR(512),
                            checksum CHAR(64),
                            mime VARCHAR(120),
                            size_bytes BIGINT,
                            markdown_content TEXT,
                            access_tier VARCHAR(32) NOT NULL DEFAULT 'FREE'
                        )
                        """
                    )
                )
                await conn.execute(
                    text(
                        """
                        CREATE TABLE IF NOT EXISTS legacy_categories (
                            id BIGINT PRIMARY KEY,
                            parent_id BIGINT,
                            name VARCHAR(255) NOT NULL,
                            depth INT NOT NULL DEFAULT 0
                        )
                        """
                    )
                )
                await conn.execute(
                    text(
                        """
                        CREATE TABLE IF NOT EXISTS legacy_knowledge_units (
                            id BIGINT PRIMARY KEY,
                            parent_id BIGINT,
                            subject VARCHAR(64),
                            grade INT,
                            unit_type VARCHAR(64) NOT NULL,
                            code VARCHAR(120),
                            name VARCHAR(255) NOT NULL,
                            sort_order INT NOT NULL DEFAULT 0
                        )
                        """
                    )
                )
                await conn.execute(
                    text(
                        "INSERT INTO legacy_crawler_sites (site_id, name, config) "
                        "VALUES ('cli_site', 'CLI Site', '{}'::jsonb)"
                    )
                )
                await conn.execute(
                    text(
                        """
                        INSERT INTO legacy_library_documents
                            (id, title, kind, source, source_file_id, storage_key, mime, size_bytes)
                        VALUES
                            (77, 'SGK Vật lí 12', 'TEXTBOOK', 'upload', 'cli-77',
                             'uploads/cli/vatli12.pdf', 'application/pdf', 1024)
                        """
                    )
                )
                await conn.execute(
                    text(
                        """
                        INSERT INTO legacy_categories (id, parent_id, name, depth) VALUES
                            (1, NULL, 'Vật lí', 0),
                            (2, 1, 'Dao động cơ', 1)
                        """
                    )
                )
                await conn.execute(
                    text(
                        """
                        INSERT INTO legacy_knowledge_units
                            (id, parent_id, subject, grade, unit_type, code, name, sort_order)
                        VALUES (501, NULL, 'Vật lí', 12, 'CHAPTER', 'VL12_C1', 'Dao động điều hòa', 1)
                        """
                    )
                )
        finally:
            await engine.dispose()

    async def _cleanup_legacy_public() -> None:
        engine = create_async_engine(db_url, echo=False)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        """
                        DROP TABLE IF EXISTS
                            legacy_knowledge_units,
                            legacy_categories,
                            legacy_library_documents,
                            legacy_crawler_sites
                        CASCADE
                        """
                    )
                )
        finally:
            await engine.dispose()

    asyncio.run(_setup_legacy_public())
    try:
        exit1 = backfill_cli_main(["--database-url", db_url, "--batch-size", "500"])
        assert exit1 == 0
        payload1 = json.loads(capsys.readouterr().out)
        assert payload1["all_balanced"] is True
        assert payload1["total_inserted_rows"] >= 4

        exit2 = backfill_cli_main(["--database-url", db_url, "--batch-size", "500"])
        assert exit2 == 0
        payload2 = json.loads(capsys.readouterr().out)
        assert payload2["all_balanced"] is True
        assert payload2["total_inserted_rows"] == 0

        async def _verify_ltree_query() -> int:
            engine = create_async_engine(db_url, echo=False)
            try:
                async with engine.begin() as conn:
                    res = await conn.execute(
                        text("SELECT COUNT(*) FROM taxonomy_nodes WHERE code_path <@ CAST('LEGACY' AS ltree)")
                    )
                    return int(res.scalar_one())
            finally:
                await engine.dispose()

        assert asyncio.run(_verify_ltree_query()) == 1
    finally:
        asyncio.run(_cleanup_legacy_public())
