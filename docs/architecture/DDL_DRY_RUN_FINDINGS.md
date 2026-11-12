# Kết quả dry-run DDL Chương 4 (W0.1 — H-01, H-03)

Ngày chạy: 12/11/2026, PostgreSQL 17 (`pgvector/pgvector:pg17`). Bộ test: `tests/security/test_ddl_dry_run.py`.

File `chapters/04_postgresql_ddl_rls_grants.sql` được áp nguyên văn lên một database tạm, nạp dữ liệu
`tests/security/seed_rls_fixture.sql` (2 tenant + tenant hệ thống), rồi kiểm tra bằng các role runtime.

## Đã kiểm chứng đúng như đặc tả

- DDL chạy sạch, 0 lỗi: 123 bảng logic + 14 partition, 2 view.
- 7 role đều `NOBYPASSRLS`, không role runtime nào có `CREATE` trên schema.
- 103 bảng có `tenant_id` đều bật `ENABLE` + `FORCE ROW LEVEL SECURITY` và có policy.
- 130 policy đều đọc tenant qua `(SELECT current_app_tenant_id())` (InitPlan).
- 21 bảng `hybrid_public_tables` có đủ `hybrid_read_policy` và `hybrid_write_policy`.
- Tenant khác chỉ thấy câu hỏi hệ thống đã `PUBLISHED`; bản `DRAFT`, `TAKEN_DOWN` và dữ liệu riêng của tenant A bị ẩn.
- Bảng con (`question_versions`, `question_options`) bị ẩn khi câu hỏi cha bị gỡ, dù phiên bản vẫn `is_immutable` và `SYSTEM_PUBLIC`.
- `knowledge_edges` chỉ hiện khi cả hai đầu mút `is_active`.
- Tenant không ghi được vào tenant hệ thống hay tenant khác.
- `ulp_exam_session_rw`, `ulp_worker_ingestion`, `ulp_system_scheduler`, `ulp_analytics_ro` nhận `42501` trên hai bảng đáp án.
- `ulp_worker_grading` của tenant B đọc được khóa đáp án đề hệ thống, không đọc được của tenant A.
- `ulp_system_scheduler` thấy 0 dòng khi chưa khai `app.system_job`, thấy đúng dữ liệu của job đã khai.
- Quyền của 5 role worker khớp 141/141 dòng với `tests/security/grants_manifest.csv`.
- Cổng xuất bản: `PUBLISHED` bị `CheckViolation` khi `rights_status` là `PENDING_REVIEW`, `RESTRICTED`, `TAKEDOWN` hoặc chưa `VERIFIED`.
- `audit_events` chặn `UPDATE`/`DELETE`; `verify_rls_hardening.sql` chạy sạch.

## Lỗi của đặc tả (đang là xfail trong bộ test, chờ quyết định)

| Mã | Mức | Hiện tượng đã tái hiện | Nguyên nhân | Cách sửa đề xuất |
|---|---|---|---|---|
| F-01 | Cao | `ulp_app_rw` của tenant B đọc được dòng `audit_events` của tenant A bằng `SELECT ... FROM audit_events_2026_11` | Partition không bật RLS riêng (RLS của bảng cha chỉ áp khi truy vấn qua bảng cha), trong khi `GRANT ... ON ALL TABLES` cấp quyền cho `ulp_app_rw` trên cả 14 partition của `audit_events` và `response_revisions` | `REVOKE ALL` trên mọi partition khỏi các role runtime (kể cả trong `ensure_monthly_partitions`), và bật `ENABLE`/`FORCE RLS` + policy trên từng partition |
| F-02 | Cao | Qua view `v_public_question_versions`, tenant B và role phòng thi thấy cả bản `draft`, `taken down` và câu hỏi riêng của tenant A | View không đặt `security_invoker`, nên đọc bảng bằng quyền của chủ view; khi chủ là superuser (như role `exam` đang chạy migration của repo này) thì RLS bị bỏ qua | `CREATE VIEW ... WITH (security_invoker = true)` cho cả hai view; chạy migration bằng role không phải superuser |
| F-03 | Thấp | `UPDATE`/`DELETE` trên `question_versions`, `question_version_answers`, `assessment_versions` đã khóa bị từ chối bằng lỗi `record "new" has no field "status"` thay vì `IMMUTABLE_VIOLATION` | `prevent_version_immutability_mutation()` đọc `NEW.status` trong cùng biểu thức `IF`, cột này chỉ có ở `content_versions` | Tách nhánh `content_versions` thành `IF` lồng riêng. Thao tác vẫn bị chặn, chỉ sai thông báo lỗi |

## Điểm thiết kế cần bạn xác nhận (không phải lỗi chạy)

1. **20 bảng không có `tenant_id`, không RLS.** Phần lớn là danh mục toàn cục (hợp lý). Năm bảng đáng cân nhắc: `users`, `user_pii_encryption_keys` (PII và khóa mã hóa — `ulp_app_rw` đọc/ghi toàn bộ), `notification_deliveries`, `question_irt_parameters`, `payment_webhooks`. Đặc tả ghi "RLS 100% bảng nghiệp vụ".
2. **Không có ngữ cảnh tenant vẫn thấy học liệu hệ thống đã xuất bản.** Đúng với policy đã viết (phục vụ trang công khai), nhưng nghĩa là một request quên đặt tenant không bị chặn hoàn toàn.
3. **`ulp_worker_grading` đọc được đáp án của mọi câu hỏi hệ thống**, kể cả bản `DRAFT` và đã gỡ (policy `grading_read_question_answers` không lọc trạng thái).
4. **Số bảng:** đặc tả nói 126 bảng; thực tế là 123 bảng logic cộng 3 partition khai báo tay.
