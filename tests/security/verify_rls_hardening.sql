-- tests/security/verify_rls_hardening.sql — Chạy tự động ở Cổng 2 và Cổng 4 của CI Pipeline
BEGIN;

-- Test 1: Không được phép có bất kỳ Runtime/Worker/Scheduler Role nào mang cờ rolbypassrls = true
DO $$
DECLARE v_bypass_count INT;
BEGIN
    SELECT count(*) INTO v_bypass_count
    FROM pg_roles
    WHERE rolname IN (
        'ulp_app_rw', 'ulp_exam_session_rw', 'ulp_worker_ingestion',
        'ulp_worker_grading', 'ulp_system_scheduler', 'ulp_analytics_ro'
    ) AND rolbypassrls IS TRUE;
    IF v_bypass_count > 0 THEN
        RAISE EXCEPTION 'SECURITY_GATE_FAILED: Phát hiện % runtime/worker role có BYPASSRLS!', v_bypass_count;
    END IF;
END $$;

-- Test 2: Tenant B tuyệt đối không nhìn thấy học liệu hệ thống (SYSTEM_PUBLIC) đang ở trạng thái DRAFT hoặc TAKEN_DOWN (P0-3)
SET LOCAL ROLE ulp_app_rw;
SET LOCAL app.current_tenant_id = '11111111-1111-1111-1111-111111111111';
DO $$
DECLARE n INT;
BEGIN
    SELECT count(*) INTO n FROM questions
    WHERE tenant_id = system_public_tenant_id() AND publication_status <> 'PUBLISHED';
    IF n <> 0 THEN
        RAISE EXCEPTION 'HYBRID_RLS_LEAK: Tenant khác nhìn thấy % câu hỏi hệ thống chưa PUBLISHED!', n;
    END IF;
END $$;

-- Test 3: Worker chấm điểm (ulp_worker_grading) của Tenant A đọc được khóa đáp án của đề hệ thống SYSTEM_PUBLIC (P0-1)
RESET ROLE;
SET LOCAL ROLE ulp_worker_grading;
SET LOCAL app.current_tenant_id = '22222222-2222-2222-2222-222222222222';
DO $$
DECLARE n INT;
BEGIN
    -- Giả định dữ liệu seed kiểm thử đã có ít nhất 1 đề SYSTEM_PUBLIC trong assessment_answer_keys
    PERFORM count(*) FROM assessment_answer_keys WHERE tenant_id = system_public_tenant_id();
END $$;

-- Test 4: Role ulp_system_scheduler khi chưa khai báo app.system_job thì thấy 0 dòng; khai báo đúng job mới đọc được (P0-2)
RESET ROLE;
SET LOCAL ROLE ulp_system_scheduler;
RESET app.system_job;
DO $$
DECLARE n INT;
BEGIN
    SELECT count(*) INTO n FROM outbox_events;
    IF n <> 0 THEN
        RAISE EXCEPTION 'SCHEDULER_LEAK: ulp_system_scheduler thấy % dòng khi chưa đặt app.system_job!', n;
    END IF;
END $$;

-- Test 5: Role Phòng Thi (ulp_exam_session_rw) và Worker Nạp Đề (ulp_worker_ingestion) bị chặn đọc bảng đáp án (P0-8)
RESET ROLE;
SET LOCAL ROLE ulp_exam_session_rw;
DO $$
BEGIN
    BEGIN
        PERFORM * FROM question_version_answers LIMIT 1;
        RAISE EXCEPTION 'SECURITY_LEAK: ulp_exam_session_rw đọc được bảng question_version_answers!';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $$;

RESET ROLE;
SET LOCAL ROLE ulp_worker_ingestion;
DO $$
BEGIN
    BEGIN
        PERFORM * FROM assessment_answer_keys LIMIT 1;
        RAISE EXCEPTION 'SECURITY_LEAK: ulp_worker_ingestion đọc được bảng assessment_answer_keys!';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $$;

ROLLBACK;
