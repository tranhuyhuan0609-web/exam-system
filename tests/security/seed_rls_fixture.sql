-- Seed for the DDL dry-run (tests/security/test_ddl_dry_run.py). Applied once, as the owner, after
-- docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql. Ids are fixed so tests can name rows.
--
-- Tenants:  A = aaaaaaaa-…-01, B = bbbbbbbb-…-02, system = 00000000-…-00 (seeded by the DDL).
-- The ids used by verify_rls_hardening.sql (1111…, 2222…) are added so that script runs unchanged.

INSERT INTO tenants (id, slug, name) VALUES
    ('aaaaaaaa-0000-0000-0000-000000000001', 'tenant-a', 'Tenant A'),
    ('bbbbbbbb-0000-0000-0000-000000000002', 'tenant-b', 'Tenant B'),
    ('11111111-1111-1111-1111-111111111111', 'verify-1', 'verify_rls_hardening tenant 1'),
    ('22222222-2222-2222-2222-222222222222', 'verify-2', 'verify_rls_hardening tenant 2');

-- Questions ------------------------------------------------------------------------------------
-- c1 system, published        c2 system, draft        c3 system, taken down after publication
-- c4 tenant A, private
INSERT INTO questions (id, tenant_id, stable_code, item_type, visibility_scope) VALUES
    ('c0000000-0000-0000-0000-000000000001', system_public_tenant_id(), 'SYS-PUBLISHED', 'SINGLE_CHOICE', 'SYSTEM_PUBLIC'),
    ('c0000000-0000-0000-0000-000000000002', system_public_tenant_id(), 'SYS-DRAFT', 'SINGLE_CHOICE', 'SYSTEM_PUBLIC'),
    ('c0000000-0000-0000-0000-000000000003', system_public_tenant_id(), 'SYS-TAKEN-DOWN', 'SINGLE_CHOICE', 'SYSTEM_PUBLIC'),
    ('c0000000-0000-0000-0000-000000000004', 'aaaaaaaa-0000-0000-0000-000000000001', 'A-PRIVATE', 'SINGLE_CHOICE', 'TENANT_PRIVATE');

INSERT INTO question_versions (id, tenant_id, question_id, version_no, stem_json, content_hash, structural_hash, visibility_scope) VALUES
    ('d0000000-0000-0000-0000-000000000001', system_public_tenant_id(), 'c0000000-0000-0000-0000-000000000001', 1, '{"t":"published"}', repeat('1', 64), repeat('1', 64), 'SYSTEM_PUBLIC'),
    ('d0000000-0000-0000-0000-000000000002', system_public_tenant_id(), 'c0000000-0000-0000-0000-000000000002', 1, '{"t":"draft"}', repeat('2', 64), repeat('2', 64), 'SYSTEM_PUBLIC'),
    ('d0000000-0000-0000-0000-000000000003', system_public_tenant_id(), 'c0000000-0000-0000-0000-000000000003', 1, '{"t":"taken down"}', repeat('3', 64), repeat('3', 64), 'SYSTEM_PUBLIC'),
    ('d0000000-0000-0000-0000-000000000004', 'aaaaaaaa-0000-0000-0000-000000000001', 'c0000000-0000-0000-0000-000000000004', 1, '{"t":"private"}', repeat('4', 64), repeat('4', 64), 'TENANT_PRIVATE');

INSERT INTO question_options (tenant_id, question_version_id, option_key, content_json, sequence_order)
SELECT tenant_id, id, 'A', '{"t":"option"}', 1 FROM question_versions;

INSERT INTO question_version_answers (question_version_id, tenant_id, answer_schema)
SELECT id, tenant_id, '{"correct":"A"}' FROM question_versions;

-- Publish c1 and c3 (the trigger freezes their versions), then take c3 down: its version stays
-- immutable and SYSTEM_PUBLIC, so only the parent's status can hide it.
UPDATE questions SET verification_status = 'VERIFIED', rights_status = 'OWNED', publication_status = 'PUBLISHED',
                     current_version_id = 'd0000000-0000-0000-0000-000000000001'
 WHERE id = 'c0000000-0000-0000-0000-000000000001';
UPDATE questions SET verification_status = 'VERIFIED', rights_status = 'OWNED', publication_status = 'PUBLISHED',
                     current_version_id = 'd0000000-0000-0000-0000-000000000003'
 WHERE id = 'c0000000-0000-0000-0000-000000000003';
UPDATE questions SET rights_status = 'TAKEDOWN', publication_status = 'TAKEN_DOWN'
 WHERE id = 'c0000000-0000-0000-0000-000000000003';

-- Assessments: one published system paper with an answer key, one for tenant A -------------------
INSERT INTO scoring_scheme_versions (id, code, rules_json) VALUES
    ('e0000000-0000-0000-0000-000000000001', 'DRYRUN-SCHEME', '{}');

INSERT INTO assessments (id, tenant_id, title, subject_code, grade_band, exam_type_code, visibility_scope) VALUES
    ('f0000000-0000-0000-0000-000000000001', system_public_tenant_id(), 'System paper', 'MATH', '12', 'THPTQG', 'SYSTEM_PUBLIC'),
    ('f0000000-0000-0000-0000-000000000002', 'aaaaaaaa-0000-0000-0000-000000000001', 'Tenant A paper', 'MATH', '12', 'THPTQG', 'TENANT_PRIVATE');

INSERT INTO assessment_versions (id, tenant_id, assessment_id, version_no, scoring_scheme_id, duration_seconds, structure_snapshot_json) VALUES
    ('f1000000-0000-0000-0000-000000000001', system_public_tenant_id(), 'f0000000-0000-0000-0000-000000000001', 1, 'e0000000-0000-0000-0000-000000000001', 5400, '{"items":[]}'),
    ('f1000000-0000-0000-0000-000000000002', 'aaaaaaaa-0000-0000-0000-000000000001', 'f0000000-0000-0000-0000-000000000002', 1, 'e0000000-0000-0000-0000-000000000001', 5400, '{"items":[]}');

INSERT INTO assessment_answer_keys (assessment_version_id, tenant_id, answer_key_payload_json, key_checksum_sha256)
SELECT id, tenant_id, '{"keys":{}}', repeat('a', 64) FROM assessment_versions;

-- Taxonomy and knowledge graph: n1, n2 active, n3 inactive; edges n1->n2 (both live) and n1->n3 ---
INSERT INTO taxonomy_nodes (id, tenant_id, node_type, code, code_path, is_active) VALUES
    ('a1000000-0000-0000-0000-000000000001', system_public_tenant_id(), 'TOPIC', 'N1', 'MATH.N1', TRUE),
    ('a1000000-0000-0000-0000-000000000002', system_public_tenant_id(), 'TOPIC', 'N2', 'MATH.N2', TRUE),
    ('a1000000-0000-0000-0000-000000000003', system_public_tenant_id(), 'TOPIC', 'N3', 'MATH.N3', FALSE);

INSERT INTO knowledge_edges (id, tenant_id, source_node_id, target_node_id, edge_type) VALUES
    ('a2000000-0000-0000-0000-000000000001', system_public_tenant_id(), 'a1000000-0000-0000-0000-000000000001', 'a1000000-0000-0000-0000-000000000002', 'PREREQUISITE'),
    ('a2000000-0000-0000-0000-000000000002', system_public_tenant_id(), 'a1000000-0000-0000-0000-000000000001', 'a1000000-0000-0000-0000-000000000003', 'PREREQUISITE');

-- Cross-tenant job material ---------------------------------------------------------------------
INSERT INTO outbox_events (tenant_id, event_type, aggregate_type, aggregate_id, payload_json) VALUES
    ('aaaaaaaa-0000-0000-0000-000000000001', 'dryrun.event', 'probe', gen_random_uuid(), '{}'),
    ('bbbbbbbb-0000-0000-0000-000000000002', 'dryrun.event', 'probe', gen_random_uuid(), '{}');

INSERT INTO analytics_events (tenant_id, event_name) VALUES
    ('aaaaaaaa-0000-0000-0000-000000000001', 'dryrun'),
    ('bbbbbbbb-0000-0000-0000-000000000002', 'dryrun');

INSERT INTO audit_events (tenant_id, action, target_type, target_id, occurred_at) VALUES
    ('aaaaaaaa-0000-0000-0000-000000000001', 'DRYRUN', 'probe', gen_random_uuid(), '2026-11-15T00:00:00Z');
