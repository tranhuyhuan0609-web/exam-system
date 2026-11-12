BỘ TÀI LIỆU BÀN GIAO KỸ THUẬT CẤP TECH LEAD — PHIÊN BẢN V11.3 (ENGINEERING-HARDENING UNIFIED MASTER EDITION)
Dự án: Nền tảng Học tập, Luyện thi & Khảo thí Thích ứng (Universal Learning & Assessment Platform — exam-system Upgrade)
Phiên bản: V11.3 — Engineering-Hardening Unified Master (Bản Hợp Nhất Hoàn Chỉnh Giữa Kiến Trúc Master V11.0, Báo Cáo Review Kiến Trúc & Bản Vá Kỹ Thuật V11.1)
Ngày phát hành: 12/11/2026
Đối tượng sử dụng: Tech Lead, Solution Architect, Backend Engineers (BE), Data/AI Engineers (DE/AI), Frontend Engineers (FE), QA/SDET, DevOps/SRE & Pháp chế (Legal/Compliance)
Nguyên tắc Trung thực Kỹ thuật (Engineering Integrity Rule): Tài liệu này áp dụng nghiêm ngặt thang 5 cấp độ trưởng thành (SPECIFIED, IMPLEMENTED, TESTED, VERIFIED, OPERATED). Mọi thiết kế SQL/RLS, thuật toán, hợp đồng API và kịch bản kiểm thử trong tài liệu này đang ở mức ENGINEERING-REVIEWED SPECIFICATION (riêng các thuật toán Python và bộ lọc AST/Whitelist của SymPy đã chạy kiểm thử đơn vị trong Sandbox); hệ thống chỉ được gắn nhãn VERIFIED / OPERATED sau khi chạy alembic upgrade head, bộ test SQL/RLS và k6 trên hạ tầng PostgreSQL + Patroni thật trong giai đoạn M0 và các cổng GATE-01..GATE-10.
PHẦN MỞ ĐẦU: QUY TẮC QUẢN TRỊ KỸ THUẬT, CANONICAL MANIFEST & TỔNG HỢP HỢP NHẤT BẢN VÁ V11.1
1. Ma Trận Trạng Thái Kiểm Chứng Thực Tế & Bảng Tổng Hợp 9 Hạng Mục Đã Hợp Nhất Từ Bản Vá V11.1
Toàn bộ 8 lỗi P0 và nhóm lỗi P1 trong báo cáo Review Kiến Trúc V11.0 và Bản Vá V11.1 đã được tích hợp trực tiếp vào đúng các chương tương ứng của bản V11.3 này (xóa bỏ hoàn toàn việc phải đọc song song 2 file):
Mã Bản Vá	Vấn đề Phát hiện trong Review V11.0	Giải pháp Kỹ thuật Đã Hợp Nhất Trực Tiếp vào Bản V11.3	Vị trí trong V11.3 & Mức Trạng Thái
P0-1	Worker chấm điểm của Tenant A không đọc được khóa đáp án của đề hệ thống (SYSTEM_PUBLIC).	Bổ sung policy grading_read_question_answers trên question_version_answers và grading_read_answer_keys trên assessment_answer_keys cho role ulp_worker_grading.	Chương 4 (RLS)
SPECIFIED (Test ở M0)
P0-2	Các Job nền xuyên Tenant (outbox_relay, attempt_expiry, metric_rollup) trả về 0 dòng do FORCE RLS và không có BYPASSRLS.	Tạo role chuyên biệt ulp_system_scheduler (NOBYPASSRLS) kèm hàm app_system_job() và 4 policies hẹp theo từng loại job; sau khi quét attempt_expiry, mở Transaction riêng có SET LOCAL app.current_tenant_id để chốt AUTO_SUBMITTED.	Chương 4 & Mục 6.4
SPECIFIED (Test ở M0)
P0-3	Policy đọc học liệu công khai (hybrid_read_policy) chỉ kiểm tra tenant_id = system_public_tenant_id(), làm lộ bản DRAFT hoặc TAKEN_DOWN.	Siết chặt hybrid_read_policy trên toàn bộ 21 bảng công khai: bắt buộc visibility_scope = 'SYSTEM_PUBLIC' AND publication_status = 'PUBLISHED' AND is_immutable IS TRUE và kế thừa điều kiện từ bảng cha qua EXISTS, đồng thời tối ưu hiệu năng bằng (SELECT current_app_tenant_id()) (InitPlan).	Chương 4 (RLS)
SPECIFIED (Test ở M0)
P0-4	Mâu thuẫn giữa KEK theo Tenant và thu hồi KEK theo User trong ADR-015; guardian_email và các trường văn bản tự do (tutor_messages, appeals, comment_threads) chưa mã hóa/xóa khi Erasure.	Chuẩn hóa Phương án A (Khuyến nghị: Mỗi User 1 khóa ulp-user-{id} trên HashiCorp Vault Transit) kết hợp lớp bảo vệ thứ 2 là WORM Tombstone (reapply_tombstones_after_pitr_restore). Mã hóa guardian_email_ciphertext + guardian_email_blind_index và xóa sạch văn bản tự do trong tutor_messages, appeals, comment_threads khi thực thi Right to Erasure.	ADR-015, Chương 4 & Mục 6.8
TESTED (Python) / SPECIFIED (Vault)
P0-5	Gọi trực tiếp sp.sympify() trên chuỗi do LLM sinh ra gây nguy cơ thực thi mã tùy ý (RCE).	Thay thế hoàn toàn bằng kiến trúc 2 file: sympy_sandbox_worker.py chạy trong tiến trình cô lập python -I (whitelist ALLOWED_NAMES, chặn __, giới hạn RLIMIT_AS = 256MB, RLIMIT_CPU = 5s, tắt __builtins__) và sympy_runner.py.	Mục 6.3
TESTED (12 ca RCE bị chặn 100%)
P0-6	Client phòng thi ở Mục 6.9 chỉ dùng Map trong RAM thay vì IndexedDB thật, nếu F5 hoặc sập tab sẽ mất bài chưa gửi.	Viết lại hoàn toàn Mục 6.9 bằng thư viện Dexie (IndexedDB thật — exam-autosave-store.ts): lưu bền vững xuống ổ đĩa trình duyệt trước khi gửi, không xóa bản đã từng gửi (tries > 0), có exponential backoff + jitter và tự động xả hàng đợi khi có mạng (online / visibilitychange).	Mục 6.9
SPECIFIED (Chạy tsc ở M0)
P0-7	Client TS chưa gửi expected_response_version và payload_hash; kịch bản k6 dùng chung 1 attempt_id cho 5.000 VU và hash rỗng.	Cập nhật Client TS gửi đủ expected_response_version + payload_hash (SHA-256 của canonicalJson); Server kiểm tra đối chiếu payload_hash (422 PAYLOAD_HASH_MISMATCH); kịch bản k6 dùng SharedArray cấp cho mỗi VU 1 attempt_id riêng và tính SHA-256 thật.	Mục 6.4, 6.9 & 9.3C
TESTED (Python) / SPECIFIED (k6)
P0-8	ulp_worker_ingestion và ulp_worker_grading được cấp GRANT ALL TABLES, vi phạm nguyên tắc Least-Privilege.	Thu hồi GRANT ALL; cấp quyền chi tiết theo đúng danh sách bảng và cột cho từng Worker role và kiểm tra tự động bằng câu truy vấn đối chiếu information_schema.role_table_grants với grants_manifest.csv.	Chương 4 (GRANT) & Mục 9.3D
SPECIFIED (Test ở M0)
P1-All	E01 và C02 xếp ở Sprint 9 quá muộn; Pilot ở Sprint 11 không đủ mẫu IRT; thiếu Buffer; mã Gate lẫn lộn RG-xx và GATE-xx; thiếu DPIA, Anti-Cheat, Pentest, Chi phí LLM/GPU; mô tả FSRS lệch code.	Đưa E01 về Sprint 2, C02 về Sprint 3 (chuyển B07 sang Sprint 4); khởi động Pilot sớm từ Sprint 8 với Anchor Items; bổ sung Sprint 13–14 (4 tuần Buffer cho Pentest, WAF, DR Drill) kèm Phương án rút gọn 26 tuần; thống nhất duy nhất mã GATE-01..GATE-10 (tách GATE-06a và GATE-06b); bổ sung Mục 1.5 (DPIA, Anti-Cheat, Mô hình chi phí LLM/GPU) và khớp mô tả FSRS với code.	Mục 1.3, 1.5, Chương 3, 6.5, 8 & 9
VERIFIED_IN_MANIFEST

2. Nguồn Chuẩn Duy Nhất (`platform_manifest.yaml` — Phiên bản 11.2.0)
Khóa Chuẩn (manifest_key)	Giá trị Chuẩn Hóa Duy Nhất (V11.3)	Quy tắc Ràng buộc & Kiểm chứng Tự động trong CI (ci/check_gate_ids.sh)
module_count & release_tracks	47 Modules chia làm 3 Release Tracks tuần tự:
• Track 1 (Core MVP): 26 modules (Sprint 1..6 — đã bao gồm E01 ở Sprint 2 và C02 ở Sprint 3)
• Track 2 (Intelligent & LMS): 12 modules (Sprint 7..9 — khởi động Pilot từ Sprint 8)
• Track 3 (Expansion & IRT): 9 modules (Sprint 10..12)	Quy tắc tải Sprint: Tối đa 6 modules / Sprint, trong đó tối đa 2 modules rủi ro cao (RLS, Autosave, AI Gateway, Grading, Crawler, Drive). Mỗi module chỉ xuất hiện ở duy nhất 1 Sprint chính thức.
sprint_plan (2 Phương án)	• Phương án A (Đầy đủ 47 modules — Khuyến nghị): M0 (2 tuần) + Sprint 1..12 (24 tuần) + Sprint 13..14 Buffer/Pentest/DR (4 tuần) = 30 Tuần (225 Net PW).
• Phương án B (Giữ 26 tuần — 44 modules): Hoãn 3 modules mở rộng (A02, E03, G06) sang V1.1 để dành Sprint 11–12 làm Buffer/Pentest/DR = 26 Tuần (195 Net PW).	Tech Lead và PM chốt Phương án A hoặc B ngay cuối giai đoạn M0 dựa trên tốc độ thực tế của đội. Với đội 2 FE, hoãn tính năng kéo-thả cắt lại bbox trực tiếp trên Canvas sang V1.1 (hoặc bổ sung 1 FE từ Sprint 3).
release_gates	11 Cổng chuẩn dùng tiền tố duy nhất GATE-: GATE-01 đến GATE-05, GATE-06a (Toán/Lý — Sprint 6), GATE-06b (Hóa/Sinh — Sprint 8), GATE-07 đến GATE-10.	Script ci/check_gate_ids.sh chạy ở Cổng 1 của CI tự động đánh trượt bản build nếu phát hiện bất kỳ chuỗi RG-xx nào hoặc mã GATE-xx nằm ngoài platform_manifest.yaml.
irt_tier_registry	4 Bậc: RULE_WEIGHTED_L1, RASCH_1PL_L2, IRT_2PL_GPCM_L3, IRT_3PL_GPCM_L4.	Chấp nhận RULE_WEIGHTED_L1 là trạng thái hợp lệ tại thời điểm Go-Live cho những câu hỏi chưa tích lũy đủ 6 tiêu chí thống kê từ đợt Pilot (Sprint 8–11).

3. Sơ Đồ Kiến Trúc Tổng Thể 6 Lớp & Luồng Triển Khai 3 Release Tracks (V11.3)
Khối mã thực thi (TEXT):
+----------------------------------------------------------------------------------+
|             LỚP 1: PRESENTATION & 6 WEB WORKSPACES (Next.js + KaTeX)             |
|  [W1: Learner SSR] [W2: Exam SPA + Dexie IndexedDB] [W3: BBox Review] [W4-W6]    |
+----------------------------------------+-----------------------------------------+
                                         | HTTPS / OpenAPI 3.1 (JWT + W3C Trace)
                                         v
+----------------------------------------------------------------------------------+
|                  LỚP 2: API GATEWAY & APPLICATION ORCHESTRATION                  |
|  • Tenant Context: (SELECT current_app_tenant_id()) — InitPlan O(1) per query    |
|  • Single-Aggregate Transaction + Optimistic Concurrency + Payload Hash Check    |
+------------------------+-------------------------------+-------------------------+
                         |                               |
+------------------------v-------------------------+ +---v-------------------------+
| TRACK 1: CORE MVP (26 Modules — Sprint 1-6)      | | TRACK 2: AI & LMS (12)      |
| • A01, A04-A09 (Doc Intelligence / OCR / Review) | | • B05, D01-D03, D05 (Loop)  |
| • B01-B04, B06, B07 (SGK, Studio, Search FTS)    | | • F01-F03, F06 (Class LMS)  |
| • E01 (AI Gateway - S2), C02 (SymPy Sandbox - S3)| | • D04 (FSRS-5), E02 (Tutor) |
| • C01, C03-C07 (Question Bank, Exam, Autosave)   | | • E04 (Step Solutions)      |
| • F04, F05, G01-G03 (RBAC, Consent, Takedown)    | |   (Sprint 7-9 + Pilot S8)   |
+------------------------+-------------------------+ +---+-------------------------+
                         |                               |
                         +---------------+---------------+
                                         |
                                         v
+----------------------------------------------------------------------------------+
|    TRACK 3: EXPANSION & IRT (9 Modules — S10-12) + HARDENING BUFFER (S13-14)     |
|  • A02 (Crawler IP-Pin), A03 (Drive Sync), D06 (6-Criteria IRT), E03/E05/E06     |
|  • G04-G06 (Analytics, Billing, Verticals) + S13-14 (Pentest, WAF, DR, Go-Live)  |
+----------------------------------------+-----------------------------------------+
                                         |
                                         v
+----------------------------------------------------------------------------------+
|     LỚP 3 & 4: PATRONI QUORUM, VAULT TRANSIT & 7 LEAST-PRIVILEGE DB ROLES        |
|  • PostgreSQL 16/18 Patroni 3-Node (synchronous_mode_strict: true, 100% RLS)     |
|  • Roles: ulp_app_rw | ulp_exam_session_rw | ulp_worker_ingestion |              |
|           ulp_worker_grading | ulp_system_scheduler | ulp_analytics_ro           |
|  • Vault Transit Per-User Key (ulp-user-{id}) + WORM Tombstone Post-PITR Scrub   |
+----------------------------------------------------------------------------------+
PHẦN I (TẬP 1 / 3 — V11.3): KIẾN TRÚC HỆ THỐNG, 10 ADRs, MA TRẬN SỞ HỮU DỮ LIỆU & ĐẶC TẢ ĐẦY ĐỦ 47 MODULES (A01–G06)
CHƯƠNG 1. CHỈ ĐẠO KỸ THUẬT TỪ TECH LEAD & 6 QUY TẮC BẤT BIẾN (ARCHITECTURAL GUARDRAILS)
1.	KHÔNG ĐẬP ĐI XÂY LẠI TỪ ĐẦU (REFACTOR & EXTEND): Hệ thống hiện tại (tranhuyhuan0609-web/exam-system) đã có sẵn khung FastAPI, SQLAlchemy 2.0, Alembic, Key Rotator, Crawler (vietjack, tuyensinh247, loigiaihay, hoc247), Drive Sync, Exam PDF và Giao diện Admin/Student. Nhiệm vụ của phòng Dev là nâng cấp theo lộ trình Migration 5 bước không gián đoạn và bảng ánh xạ tại Chương 2.
2.	TÁCH BIỆT 4 CẤU TRÚC TRI THỨC & ĐỊNH DANH BẤT BIẾN: Tuyệt đối không gộp chung:
•	Academic Taxonomy (taxonomy_nodes giữ UUID định danh bất biến + taxonomy_node_versions giữ nội dung phiên bản).
•	Knowledge Graph (knowledge_edges nối các taxonomy_nodes.id cố định, không bị đứt khi cập nhật tên/mô tả khái niệm).
•	Curriculum (curriculum_versions, curriculum_nodes phản ánh mục lục SGK 4 cấp: Chương -> Bài -> Mục -> Dạng bài).
•	Assessment Framework (assessment_frameworks, framework_versions, blueprints phản ánh cấu trúc đề thi chuẩn THPTQG 2025+, ĐGNL, ĐGTD, HSG).
3.	TÁCH BIỆT 5 TRỤC TRẠNG THÁI CÓ KIỂM SOÁT CỨNG DƯỚI DATABASE: Mọi thực thể học liệu/câu hỏi phải quản lý độc lập 5 trục trạng thái:
•	acquisition_status (INGESTED, EXTRACTED, NORMALIZED, FAILED)
•	verification_status (UNCHECKED, AUTO_VERIFIED, VERIFIED, DISPUTED, REJECTED)
•	rights_status (PENDING_REVIEW, OWNED, LICENSED, ALLOWED_DERIVATIVE, RESTRICTED, TAKEDOWN) — Mặc định bắt buộc là PENDING_REVIEW (Fail-Closed)
•	publication_status (DRAFT, READY, PUBLISHED, ARCHIVED, TAKEN_DOWN)
•	job_status (QUEUED, RUNNING, COMPLETED, FAILED, DEAD_LETTER, CANCELLED)
•	Quy tắc Cổng Xuất Bản (Database CHECK & Trigger): Một bản ghi chỉ được phép chuyển sang publication_status = 'PUBLISHED' khi và chỉ khi verification_status = 'VERIFIED' VÀ rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE').
4.	BẤT BIẾN DỮ LIỆU GỐC & PHIÊN BẢN ĐÃ PHÁT HÀNH (DB-ENFORCED IMMUTABILITY): File gốc ở tầng Bronze (assets có asset_role = 'RAW_SOURCE'), content_versions đã Publish, question_versions đã Publish, assessment_versions đã khóa, response_revisions và audit_events được bảo vệ bằng Trigger PostgreSQL chặn tuyệt đối lệnh UPDATE/DELETE. Mọi chỉnh sửa nội dung phải sinh version_no + 1.
5.	POSTGRESQL LÀ NGUỒN SỰ THẬT DUY NHẤT (RPO = 0 CHO PHÒNG THI): Redis chỉ làm Cache đề thi công khai (đã lược bỏ đáp án) và Broker điều phối Worker. Thao tác Autosave bài thi bắt buộc COMMIT xuống PostgreSQL (kèm Synchronous Standby Replication cho cụm bảng thi cử) trước khi trả HTTP 200 ACK.
6.	CÔ LẬP MODULE & CẤM CHỒNG LẤN SỞ HỮU BẢNG (ZERO SHARED TABLE OWNERSHIP): Mỗi bảng trong PostgreSQL chỉ thuộc quyền sở hữu ghi (INSERT/UPDATE/DELETE) của duy nhất 1 Module. Các module khác chỉ được truy xuất qua Application Service công khai hoặc lắng nghe Domain Event qua Outbox/Inbox. Quy tắc này được kiểm tra tự động trong CI bằng import-linter.
1.1. Mười Bảy Quyết Định Kiến Trúc Cốt Lõi (ADR-001 đến ADR-017)
Mã ADR	Chủ đề quyết định	Quyết định chốt (Baseline Decision V11.3)	Hệ quả & Ràng buộc thực thi bắt buộc cho Dev
ADR-001	Kiến trúc Triển khai	Modular Monolith + 3 Background Worker Pools tách theo đặc tính tải: io-crawler-pool, cpu-doc-ocr-pool, gpu-ai-verify-pool.	Toàn bộ domain nằm trong 1 repo, kiểm soát biên giới import bằng .importlinter. Giao tiếp bất đồng bộ qua Transactional Outbox (outbox_events) + Idempotent Consumer Inbox (inbox_events).
ADR-002	Lưu trữ Dữ liệu & Khóa chính	PostgreSQL 16+ làm Transactional Source of Truth (kèm Synchronous Standby cho bảng phòng thi); MinIO/S3 lưu trữ toàn bộ đối tượng nhị phân.	Cấm lưu file nhị phân trong DB. Toàn bộ khóa chính dùng UUIDv7 (uuid_generate_v7()) sắp xếp theo thời gian để chống phân mảnh chỉ mục B-Tree. Mọi cột thời gian dùng TIMESTAMPTZ (UTC).
ADR-003	Cô lập Đa thuê bao & Học liệu Chung	Shared Database + tenant_id trên MỌI bảng (kể cả bảng con qua Composite FK) + PostgreSQL Row-Level Security (RLS) + visibility_scope.	Middleware API thiết lập SET LOCAL app.current_tenant_id = '<uuid>' trong mỗi transaction (tương thích PgBouncer transaction mode). Học liệu SGK dùng chung thuộc SYSTEM_TENANT_ID với visibility_scope = 'SYSTEM_PUBLIC'.
ADR-004	Phiên bản hóa Học liệu & Taxonomy	Tách bảng định danh (taxonomy_nodes, content_items, questions) khỏi bảng phiên bản bất biến (taxonomy_node_versions, content_versions, question_versions).	Khi sửa câu hỏi/bài học/nút phân loại đã Publish, hệ thống tự động tạo version_no + 1; các cạnh đồ thị knowledge_edges và đề thi lịch sử giữ nguyên tham chiếu không bao giờ bị gãy.
ADR-005	Đóng băng Đề thi, Tách Đáp án & Hỗ trợ Takedown	Khi Publish đề thi, đóng băng cấu trúc (structure_snapshot_json: danh sách question_version_id, điểm số, quy tắc trộn) tách biệt hoàn toàn với bảng đáp án (assessment_answer_keys).	API phát đề chỉ đọc structure_snapshot_json và nội dung câu hỏi (không bao giờ chạm tới assessment_answer_keys). Khi một câu hỏi bị Takedown bản quyền, đề thi tự động che nội dung câu đó kèm biên bản kiểm toán mà không làm hỏng lịch sử điểm.
ADR-006	Điều phối AI & Kiểm chứng Độc lập	AI Provider Gateway (E01) triển khai ngay từ Sprint 2 để phục vụ toàn bộ OCR/LaTeX, Healer (Job 1), SymPy + LLM Verifier (Job 2) và AI Tutor.	Mọi lời gọi LLM phải qua Gateway để xoay vòng key, áp trần chi phí theo Tenant và ghi ai_traces. AI không bao giờ được tự động chuyển trạng thái publication_status = 'PUBLISHED'.
ADR-007	Tách Bạch Lập Lịch Ghi Nhớ (FSRS v5) & Ước Lượng Năng Lực (Phased IRT)	Hai cơ chế độc lập: (1) FSRS v5 (19 trọng số) chuyên trách tính khoảng ngày ôn tập thẻ nhớ/công thức; (2) Mô hình Năng lực (Mastery/IRT) gồm 4 bậc: L1 (Rule-weighted decay) -> L2 (1PL Rasch khi N \≥ 150) -> L3 (2PL/GPCM khi N \≥ 300) -> L4 (3PL có Beta prior khi N \≥ 1000).	Không gộp lẫn FSRS vào bậc của IRT. Chỉ hiển thị \"Điểm ước lượng năng lực tham khảo (Khoảng tin cậy 95%)\" khi học sinh đã làm \≥ 20 câu, độ phủ chuyên đề \≥ 60\% và \SE(\θ) < 0.35.
ADR-008	Quản trị Bản quyền & Tái thương hiệu	Mặc định mọi tài liệu nạp vào có rights_status = 'PENDING_REVIEW'. Chỉ cho phép tạo bản PDF thay Logo/Banner khi rights_status = 'OWNED' hoặc 'ALLOWED_DERIVATIVE' có văn bản cấp phép.	Tuyệt đối không xóa thông tin tác giả/nguồn gốc của tài liệu bên thứ ba. Crawler bắt buộc kiểm tra robots.txt và tuân thủ Rate Limit ≤ 1 req/3s.
ADR-009	Phạm vi MVP Thực tế (Focused Wedge)	MVP (Sprint 1–6) tập trung sâu vào Toán & Vật lí THPT (Lớp 10–11–12 theo SGK Kết nối tri thức & Cấu trúc đề THPTQG 2025+ mới gồm 3 phần: 22–28 câu); mở rộng Hóa, Sinh ở Sprint 7–10.	Tách hoàn toàn cấu hình TOEIC/IELTS/HSK sang gói mở rộng VerticalPack (G06), không làm phức tạp hóa schema và giao diện trong giai đoạn MVP.
ADR-010	Tuân thủ Pháp lý, Giấy phép OSS & Bảo vệ Trẻ vị thành niên	Sử dụng pypdfium2 (Apache 2.0) + pdfplumber (MIT) làm engine PDF mặc định thay cho PyMuPDF (AGPLv3) trừ khi có giấy phép thương mại Artifex; tuân thủ Luật BVDLCN 91/2025/QH15 & NĐ 13/2023/NĐ-CP và dùng scope drive.file + Google Picker.	Học sinh dưới 16 tuổi phải có bản ghi xác nhận của phụ huynh/nhà trường (parental_consents). Bộ dữ liệu kiểm thử Golden Corpus phải xóa sạch PII và lưu trên S3 Private ngoài Git repo.
ADR-011	Ranh giới Giao dịch & Tính nhất quán Phân tán	Mỗi Use Case chỉ sửa đổi 1 Aggregate Root trong 1 Transaction PostgreSQL (READ COMMITTED + SELECT ... FOR UPDATE cho dòng cạnh tranh cao) và ghi outbox_events trong cùng Transaction.	Cấm gọi HTTP/RPC ra dịch vụ ngoài (MinIO, LLM, Google API) khi đang giữ DB Transaction mở. Phối hợp liên module thực hiện qua Saga / Outbox Event.
ADR-012	Vận chuyển Sự kiện, Retry, DLQ & Replay	Mọi sự kiện tuân thủ EventEnvelopeContract (có event_id, schema_version, correlation_id, causation_id). Consumer bắt buộc kiểm tra inbox_events trước khi xử lý.	Retry theo hàm mũ (exponential backoff + jitter, tối đa 5 lần); nếu vẫn lỗi thì chuyển vào dead_letter_items. Lệnh Replay từ DLQ yêu cầu quyền Admin và ghi audit_events.
ADR-013	Chiến lược Bộ nhớ đệm & Vô hiệu hóa Cache	Áp dụng Cache-Aside có khóa chống Stampede (Singleflight / Mutex) cho structure_snapshot_json, cây Taxonomy/Curriculum và bài viết đã Publish.	Key cache bắt buộc gắn tiền tố tenant_id và version_no (VD: ulp:{tenant_id}:qver:{id}:v{ver}). Khi Takedown bản quyền, phát sự kiện xóa tức thì key liên quan.
ADR-014	Kiến trúc Tìm kiếm & Quản lý Phiên bản Từ điển	Sử dụng PostgreSQL FTS (tsvector + pg_trgm) ở Track 1–2; ghim cố định mã băm rules_sha256 của từ điển unaccent.rules trong bảng fts_dictionary_versions.	Khi nâng cấp PostgreSQL hoặc sửa file từ điển unaccent.rules, migration bắt buộc tăng func_version và chạy REINDEX INDEX CONCURRENTLY cho search_documents.
ADR-015	Sao lưu, Khôi phục (PITR) & Vault Transit Per-User Crypto-Shredding	Phương án A (Khuyến nghị): Mỗi user_id có 1 khóa ulp-user-{id} trên HashiCorp Vault Transit bọc DEK của user đó; khi Erasure, xóa khóa trên Vault Transit làm vô hiệu hóa tức thì khả năng giải mã trên cả DB sống lẫn mọi bản Backup cũ.
Phương án B (Dự phòng): Dùng 1 KEK/Tenant/Năm + xóa DEK trong DB (cần Pháp chế xác nhận thời hạn lưu trữ PII tối đa 30 ngày trên bản Backup cũ).	Với cả 2 phương án, bắt buộc mã hóa guardian_email_ciphertext, xóa văn bản tự do (tutor_messages, appeals, comment_threads) và chạy reapply_tombstones_after_pitr_restore() từ WORM Storage sau mỗi lần PITR Restore.
ADR-016	Quan sát Hệ thống (Observability) & Quản lý Sự cố	Chuẩn hóa truyền ngữ cảnh theo W3C Trace Context kèm 8 trường định danh nghiệp vụ (correlation_id, causation_id, tenant_id, actor_id, document_version_id, acquisition_job_id, pipeline_stage_run_id, parser_or_model_version).	Toàn bộ log xuất định dạng JSON có cấu trúc và tuyệt đối không chứa PII hay Secret; cảnh báo đa cửa sổ (Multi-window Burn-rate Alerts) gắn trực tiếp với 14 Runbooks.
ADR-017	Đánh giá Chất lượng AI, Phiên bản hóa & Rollback	Tách biệt hoàn toàn tập Golden Corpus (11 đề) và Held-out Corpus (30 đề). Mọi lời gọi AI phải lưu model_name, prompt_version_id, temperature, input_sha256 và output_json.	Không nâng cấp Prompt hoặc Model trên Production nếu độ chính xác trên tập Held-out giảm hoặc tỷ lệ ảo giác toán học (SymPy mismatch) tăng; hỗ trợ Rollback tức thì qua FeatureFlag.

1.2. Định Vị Chiến Lược Sản Phẩm (Vượt Qua VietJack) & 4 Chỉ Thị Thực Chiến Khi Kick-Off Sprint 0
#### A. Chiến lược Sản phẩm & Lợi thế Cạnh tranh Cốt lõi (Tại sao kiến trúc này thắng trên thị trường)
Hệ thống không đi theo lối mòn làm web tĩnh tra cứu lời giải đại trà để đua SEO quảng cáo với VietJack, mà tập trung giải quyết 3 bài toán công nghệ có rào cản kỹ thuật cao (Deep-Tech Moat) theo mô hình B2B2C (Giáo viên/Trường học kéo Học sinh):
Trục Cạnh Tranh	Điểm nghẽn của các trang Giải bài tập Tĩnh (như VietJack)	Vũ khí Công nghệ Cốt lõi của Hệ thống ULP V11.3	Giá trị Chuyển đổi Thực tế
1. Công cụ Số hóa Đề thi cho Giáo viên (B2B2C Wedge)	Không có công cụ bóc tách đề phức hợp hay quản lý lớp học.	Module A04–A07 + C02 (SymPy): Giáo viên thả 1 file .docx (MathType OLE) hoặc .pdf (có sơ đồ Hóa học, hình vẽ Vector) -> tự động tách câu, cắt hình 300 DPI, loại trừ đường kẻ bảng (find_tables()), kiểm chứng đáp án sai bằng SymPy và mở phòng thi trong 1 phút.	Mỗi giáo viên sử dụng công cụ tạo đề/giao bài sẽ tự động kéo 100–300 học sinh của họ vào nền tảng mà không tốn chi phí quảng cáo (CAC ≈ 0).
2. Phòng Thi Chuẩn Cấu Trúc THPTQG 2025+	Chủ yếu là bài đọc tĩnh hoặc trắc nghiệm ABCD đơn giản, nhiều quảng cáo gây xao nhãng.	Module C03–C07: Hỗ trợ trọn vẹn đề THPTQG 2025+ (Phần I ABCD, Phần II Đúng/Sai 4 ý tính điểm lũy tiến 0.1 - 0.25 - 0.5 - 1.0, Phần III Trả lời ngắn 4 ký tự), trộn đề theo hạt giống và Autosave bất tử khi rớt mạng (IndexedDB + RPO = 0).	Đánh trúng nhu cầu cấp thiết nhất của học sinh THPT trước kỳ thi mới, nơi các kho trắc nghiệm ABCD cũ đã lỗi thời.
3. Vòng lặp Khắc phục Lỗ hổng Kiến thức	Học sinh vào chép lời giải xong rồi thoát (tỷ lệ giữ chân thấp).	Module B05, D02–D06, E02: Tự động gom câu sai vào Sổ tay, truy vết kiến thức tiên quyết (PREREQUISITE) đang hổng, giao 3 bài toán biến thể cùng dạng (structural_hash) yêu cầu làm đúng 2 câu liên tiếp mới xóa lỗi, kèm Gia sư AI chế độ gợi ý (Hint Mode).	Tạo tỷ lệ giữ chân hàng tuần (WLAU) cao và thúc đẩy chuyển đổi lên gói trả phí STUDENT_PRO / SCHOOL_ENTERPRISE.

#### B. Bốn Chỉ thị Kiểm chứng Thực địa Bắt buộc trong Tuần 1 của Sprint 0 (Pre-Flight Checklist cho Tech Lead)
Trước khi viết code tính năng ở Sprint 1, Tech Lead và các nhóm trưởng phải hoàn tất 4 việc đối chiếu thực tế sau ngay trong Tuần 1 của Sprint 0:
7.	Đối chiếu Schema với nhánh code exam-system thật: Lead BE chạy alembic revision --autogenerate đối chiếu giữa file app/models.py hiện hữu trên repo thật với kịch bản DDL ở Chương 4 để viết script chuyển đổi dữ liệu cũ (Dual-Write / Backfill theo 5 bước ở Mục 7.4) khớp 100% tên cột đang chạy.
8.	Đưa DDL & Bộ test Phân quyền vào CI Container PostgreSQL 16/18 thật: DevOps dựng pipeline GitHub Actions / GitLab CI chạy alembic upgrade head trên PostgreSQL thật, kiểm tra tự động độ phủ 100% RLS và xác nhận role phòng thi ulp_exam_session_rw ghi được attempts/responses nhưng bị từ chối SELECT trên question_version_answers và assessment_answer_keys.
9.	Chốt dứt điểm Thư viện PDF (pypdfium2 + pdfplumber): Nhóm DE/AI gỡ bỏ PyMuPDF (fitz — giấy phép AGPLv3) khỏi requirements.txt nếu không mua giấy phép thương mại Artifex, đồng thời chạy thử VietnameseExamDocumentProcessor (Mục 6.1) trên bộ 11 đề mẫu thật đã xóa PII trên bucket S3 riêng.
10.	Khởi động song song luồng Pháp chế & Google Cloud (Không chặn Dev code): Gửi Mục F04 + RB-14 cho bộ phận Pháp chế rà soát mẫu đồng ý của phụ huynh/nhà trường và thời hạn báo cáo theo Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15 & NĐ 13/2023/NĐ-CP; đồng thời tạo Google Cloud Project để kiểm thử thực tế Google Picker (drive.file chọn nhiều file/zip) và Service Account (cho thư mục trường học) trước Sprint 8.
1.3. Chiến Lược Phân Tầng 47 Modules Thành 3 Release Tracks + 2 Sprint Buffer (`M0 + Sprint 1–14`)
Theo đúng Bản vá V11.1, E01 (AI Provider Gateway) được đưa về Sprint 2 và C02 (Question Quality & SymPy Verification) được đưa về Sprint 3 để phục vụ ngay cho luồng bóc tách và kiểm chứng đề thi ở Track 1; đồng thời bổ sung Sprint 13–14 (4 tuần Buffer) dành riêng cho Pentest độc lập, WAF, sửa lỗi từ đợt Pilot (khởi động từ Sprint 8) và diễn tập DR:
Cấp Triển Khai (Release Track)	Mục tiêu Sản phẩm & Cổng Nghiệm thu (GATE-xx)	Danh sách Modules Chính Thức (Tổng = 47 Modules)
CẤP 1 — TRACK 1: Core Learning, Document Intelligence, SymPy Verification & Online Exam MVP
(Milestones M0 -> M2 — M0 + Sprint 1–6 = 14 tuần)	Mục tiêu: Chạy hoàn chỉnh vòng lặp lõi cho môn Toán & Vật lí THPT: Nạp đề PDF/DOCX -> Cắt hình & Loại bảng -> Kiểm chứng đáp án độc lập bằng Subprocess SymPy + LLM (E01, C02) -> Duyệt Side-by-Side -> Ngân hàng 6 loại câu hỏi -> Tạo đề ma trận THPTQG 2025+ -> Phòng thi trực tuyến (Dexie IndexedDB + Optimistic Autosave) -> Chấm điểm tất định -> Học chuyên đề VietJack.
Exit Gate: Đạt GATE-01, GATE-02, GATE-03, GATE-04, GATE-05, GATE-06a (Toán/Lý) và GATE-07.	26 Modules Lõi:
• Sprint 1 (6): A01, B03, F04, F05, G01, G03
• Sprint 2 (6): B01, B02, B04, B06, E01, G02
• Sprint 3 (5): A05, A06, A07, A09, C02
• Sprint 4 (5): A04, A08, B07, C01, C03
• Sprint 5 (3): C04, C05, C06
• Sprint 6 (1 + Gate Exit): C07
CẤP 2 — TRACK 2: Intelligent Learning Loop, FSRS-5, AI Tutor & Classroom LMS + Early Pilot
(Milestones M3 -> M4 — Sprint 7–9 = 6 tuần)	Mục tiêu: Mở rộng bóc tách đề Hóa học & Sinh học (GATE-06b), triển khai Đồ thị tri thức, Sổ tay câu sai, Quản lý lớp học / Giao bài / Bảng điểm (F01–F03) và khởi động Pilot thực tế tại trường THPT ngay từ Sprint 8 (cài cắm các câu hỏi mỏ neo Anchor Items để tích lũy ≥ 150–300 lượt làm cho hiệu chuẩn IRT ở Sprint 11). Bổ sung Gia sư AI (E02), Lời giải từng bước (E04) và thẻ nhớ FSRS-5 (D04).
Exit Gate: Đạt GATE-06b (đủ 41 đề Toán/Lý/Hóa/Sinh) và GATE-08.	12 Modules Thông minh & LMS:
• Sprint 7 (5): B05, D01, D02, D03, D05
• Sprint 8 (4 + Khởi động Pilot): F01, F02, F03, F06
• Sprint 9 (3): D04, E02, E04
CẤP 3 — TRACK 3: Platform Expansion, Multi-Criteria IRT, Commerce & Hardening Buffer
(Milestones M5 -> M6 — Sprint 10–14 = 10 tuần ở Phương án A, hoặc Sprint 10–12 = 6 tuần ở Phương án B)	Mục tiêu: Mở rộng Google Drive Sync (A03), Web Crawler (A02), Sinh câu hỏi AI (E03), hiệu chuẩn IRT 6 tiêu chí (D06) trên dữ liệu tích lũy từ Sprint 8–11 (chấp nhận RULE_WEIGHTED_L1 ở Go-Live cho câu chưa đủ mẫu), Teacher Copilot (E06), Analytics & Billing (G04–G06). Dành trọn Sprint 13–14 cho Pentest bên ngoài, WAF, DR Drill và đóng GATE-09, GATE-10.	9 Modules Mở rộng + 2 Sprints Hardening:
• Sprint 10 (3): A02, A03, E03
• Sprint 11 (3): D06, E05, E06
• Sprint 12 (3): G04, G05, G06
• Sprint 13–14: Hardening, Pentest, DR & Go-Live

1.4. Sáu Khung Quản Trị Pháp Lý, Bản Quyền, AI, Takedown, Right to Erasure & OSS SBOM
Để đảm bảo tính sẵn sàng về mặt pháp lý và tuân thủ (Legal & Compliance Readiness — AUD-015), hệ thống thực thi 6 cơ chế kiểm soát kỹ thuật sau (các định nghĩa pháp lý và ngưỡng thời gian cụ thể được đánh dấu để bộ phận Pháp chế rà soát và ký duyệt trước khi vận hành chính thức):
Trụ cột Quản trị	Cơ chế Kỹ thuật & Luồng Thực thi Bắt buộc trong Hệ thống	Bảng DB / Module Thực thi & Bằng chứng Kiểm toán
1. Quản lý Sự đồng ý (Consent Management & Minor Protection)	• Phân loại người học theo độ tuổi (v_users_status.is_minor). Với học sinh dưới ngưỡng tuổi quy định, tài khoản ở trạng thái PENDING_PARENTAL_CONSENT cho đến khi có bản ghi xác nhận hợp lệ từ phụ huynh (OTP/Email xác thực) hoặc hợp đồng triển khai B2B của nhà trường (SCHOOL_CONTRACT).
• Cho phép chọn lọc phạm vi đồng ý (consent_scope_json: lưu lịch sử học tập, sử dụng AI Tutor, nhận thông báo) và hỗ trợ thu hồi sự đồng ý (revoked_at) bất cứ lúc nào.	Module: F04
DB: users, parental_consents, audit_events
(Pháp chế xác nhận biểu mẫu đồng ý theo Luật 91/2025/QH15)
2. Tài liệu Bên thứ ba (Third-Party Content Rights & Provenance)	• Mọi tài liệu nạp vào từ Upload, Google Drive hay Web Crawler đều khởi tạo với rights_status = 'PENDING_REVIEW' (Fail-Closed).
• Bảng source_rights lưu tách biệt: nguồn gốc (source_id), loại giấy phép (license_type), quyền sử dụng nguyên trạng, quyền tạo bản phái sinh/thay thương hiệu (allow_rebranding) và quyền phân phối công khai (allow_public_redistribution) kèm file bằng chứng (evidence_asset_id).
• Database Trigger chặn cứng thao tác Publish nếu rights_status không thuộc ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE').	Module: A01, A09, B01, C01
DB: sources, source_rights, provenance_records
3. Quản trị AI (AI Transparency, Data Minimization & Guardrails)	• Zero-PII Outbound: E01 (AI Provider Gateway) tự động chạy bộ lọc PIIScrubber loại bỏ mọi thông tin định danh cá nhân (tên, email, trường lớp, membership_id) trước khi gửi prompt tới nhà cung cấp LLM bên ngoài; chỉ sử dụng kết nối API cam kết không dùng dữ liệu khách hàng để huấn luyện model.
• Minh bạch & Giới hạn xử lý: Mọi nội dung do AI sinh ra (E03, E04, E06) đều gắn nhãn AI_DRAFT, lưu rõ model_name, prompt_version_id, input_sha256 và bắt buộc qua bước duyệt của con người trước khi xuất bản.	Module: E01–E06, C02
DB: ai_jobs, ai_traces, prompt_versions
4. Quy trình Takedown Bản quyền 4 Bước (Notice -> Verify -> Execute -> Appeal)	• Bước 1 (Tiếp nhận): Ghi nhận yêu cầu qua POST /api/v1/takedown-cases kèm thông tin bên khiếu nại và bằng chứng bản quyền.
• Bước 2 (Xác minh): Quản trị viên pháp lý đối chiếu provenance_records và source_rights.
• Bước 3 (Thực thi < 30s): Phát sự kiện rights.takedown.executed chuyển ngay rights_status = 'TAKEDOWN', publication_status = 'TAKEN_DOWN', xóa khỏi search_documents và CDN/Redis, ẩn nội dung trên đề cũ nhưng bảo toàn điểm thi lịch sử.
• Bước 4 (Khiếu nại ngược / Counter-Notice): Lưu vết kháng nghị và chỉ khôi phục khi có phán quyết hợp lệ.	Module: G02, A09, B07
DB: takedown_cases, audit_events
5. Quy trình Right to Erasure (Xóa, Ẩn danh, Backup Expiry & Chứng nhận Hoàn tất)	• Thực thi trên DB sống: Xóa pii_ciphertext_bytea = NULL, đổi email_blind_index sang chuỗi ngẫu nhiên, chuyển users.status = 'ANONYMIZED'.
• KMS Envelope Crypto-Shredding (Xử lý Backup): Thu hồi/Xóa khóa chủ KEK (kms_kek_key_id) trên KMS/Vault nằm ngoài DB, khiến mọi bản sao lưu encrypted_dek_bytea trong các bản DB Backup lịch sử không thể giải mã được nữa.
• Chứng nhận hoàn tất: Xuất bản ghi erasure_certificates ghi nhận thời điểm xóa trên Primary/Replica, thời điểm hủy KEK và thời điểm bản Backup cuối cùng hết hạn lưu trữ (30 ngày).	Module: F04, G02
DB: users, user_pii_encryption_keys, erasure_certificates
6. Quản trị Mã nguồn mở (OSS SBOM, License Scanning & Dependency Policy)	• Mọi bản build trong CI (Cổng 1 & Cổng 6) tự động sinh danh mục thành phần phần mềm SBOM chuẩn CycloneDX (sbom.cdx.json) từ poetry.lock / pnpm-lock.yaml.
• Tự động quét giấy phép bằng pip-licenses / trivy: chặn đứng mọi thư viện nhiễm GPL/AGPLv3 (như PyMuPDF nếu không có giấy phép thương mại Artifex) và chỉ cho phép các giấy phép tương thích thương mại (MIT, Apache-2.0, BSD-3-Clause, ISC).	Module: CI/CD (GATE-01)
Artifact: sbom.cdx.json, license_audit_report.json

1.5. Năm Hạng Mục Quản Trị Bổ Sung: DPIA Xuyên Biên Giới, Chống Gian Lận Thi, Pentest/WAF, Mô Hình Chi Phí LLM/GPU & Giảm Tải Frontend
Hạng mục Bổ sung	Thời điểm Thực hiện & Owner	Quy tắc Kỹ thuật & Đầu ra Nghiệm thu Bắt buộc
1. Hồ sơ Đánh giá Tác động Xử lý & Chuyển Dữ liệu Ra Nước ngoài (DPIA & Cross-Border Transfer)	M0 -> Sprint 1
(Hoàn tất trước Pilot Sprint 8)
Owner: Pháp chế / DPO + Tech Lead	• Lập hồ sơ DPIA và Hồ sơ đánh giá tác động chuyển dữ liệu cá nhân xuyên biên giới đối với các luồng kết nối ra hạ tầng quốc tế (API nhà cung cấp LLM, Google Drive API, Cloud KMS/Monitoring) theo Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15 & Nghị định 13/2023/NĐ-CP.
• Ký kết Thỏa thuận Xử lý Dữ liệu (DPA / Zero-Data-Retention Addendum) với các nhà cung cấp trước khi mở Pilot.
2. Chống Gian Lận Trong Phòng Thi Trực Tuyến (Exam Anti-Cheating Telemetry)	Sprint 5 – Sprint 6
Owner: BE + FE + QA	• Trên Workspace 2 (Phòng thi): ghi nhận sự kiện mất tiêu điểm trình duyệt (visibilitychange / blur), thoát toàn màn hình (fullscreenchange), mở đồng thời từ 2 địa chỉ IP/thiết bị (409 Conflict), và hành vi chọn đáp án siêu tốc bất thường (< 2 giây/câu liên tiếp).
• Lưu vào activity_events gắn với attempt_id và hiển thị cờ cảnh báo mức độ trung thực (integrity_flags_json) trên bảng điểm giáo viên (F03), nhưng không tự động hủy bài thi của học sinh nếu chưa có quyết định của giáo viên.
3. Kiểm Thử Xâm Nhập Độc Lập (External Pentest), WAF & Rate Limiting	Sprint 13
Owner: DevOps + Security Partner	• Triển khai lớp bảo vệ Cloudflare / Nginx ModSecurity WAF (OWASP Core Rule Set) và Rate Limiting theo tenant_id / membership_id / ip ngay từ Track 1.
• Thuê đơn vị độc lập thực hiện Black-box & Grey-box Pentest ở Sprint 13 (tập trung vào IDOR chéo Tenant, lộ đáp án phòng thi, RCE qua file upload/LaTeX/SymPy, SSRF); bắt buộc vá 100% lỗi mức Critical/High trước khi đóng GATE-09.
4. Mô Hình Toán Học Chi Phí Đơn Vị LLM / GPU & Unit Economics Thực Tế	M0 (Khung công thức)
Sprint 9 (Số đo thật)
Owner: DE/AI + Tech Lead	• Thay giả định cảm tính bằng công thức hạch toán trực tiếp từ bảng ai_models và ai_traces:
Cost_{tenant, day} = \sum_{j \in Jobs} ≤ft( \frac{Tokens_{in, j}}{10^6} · P_{in} + \frac{Tokens_{out, j}}{10^6} · P_{out} \right) + T_{GPU\_hours} · C_{GPU\_hour}
• Thiết lập hạn ngạch cứng (daily_ai_budget_usd trên tenants và giới hạn số lượt gọi AI Tutor/ngày theo gói FREE vs STUDENT_PRO) để đảm bảo chi phí AI luôn ≤ 18% doanh thu gói cước.
5. Kế Hoạch Bảo Toàn Nguồn Lực Đội Frontend (2 FE cho 6 Workspaces)	Quyết định trước Sprint 3
Owner: Tech Lead + PM	• Với quy mô 2 FE Engineers, để tránh quá tải ở Sprint 3 và Sprint 4:
- Lựa chọn 1 (Mặc định không tăng người): Ở bản Go-Live đầu tiên, Workspace 3 (Side-by-Side Reviewer) chỉ hiển thị lớp phủ khung bbox tĩnh và cho phép chọn lại tọa độ/upload ảnh thay thế; hoãn tính năng vẽ kéo-thả cắt lại hình (Interactive Canvas Re-crop) sang bản V1.1; đồng thời dùng chung bộ Component Library (shadcn/ui) cho Workspace 5 và Workspace 6.
- Lựa chọn 2: Bổ sung thêm 1 FE Engineer chuyên trách Canvas/Editor từ đầu Sprint 3.

CHƯƠNG 2. BẢNG ÁNH XẠ NÂNG CẤP TỪ REPO `EXAM-SYSTEM` & MA TRẬN SỞ HỮU BẢNG ĐỘC QUYỀN
2.1. Bảng Ánh xạ Nâng cấp từ Repo `tranhuyhuan0609-web/exam-system`
File / Thư mục trong Repo exam-system hiện tại	Module đích (A01–G06)	Chỉ đạo Kỹ thuật V11.3 (Giữ nguyên / Nâng cấp / Tách mới)
app/security.py, app/deps.py, app/routers/auth.py, app/plans.py	F04, F05, G05	GIỮ NGUYÊN xác thực JWT & phân hạng Free/Pro; NÂNG CẤP middleware gắn SET LOCAL app.current_tenant_id, tách bảng memberships, roles, permissions và bổ sung bảng parental_consents (Luật BVDLCN 91/2025/QH15 & NĐ 13/2023/NĐ-CP).
app/curriculum_data.py, app/sections_data.py, app/application/categories.py	B04, B05, B06	NÂNG CẤP thành 3 nhóm bảng độc lập: taxonomy_nodes + taxonomy_node_versions (dùng ltree), knowledge_edges (đồ thị tiên quyết có chặn chu trình DAG) và curriculum_versions + curriculum_nodes (SGK 4 cấp).
app/application/books.py, app/application/library_publish.py, app/routers/library.py	B01, B02, B03, B07	NÂNG CẤP thành content_items + content_versions bất biến lưu blocks_json chuẩn VietJack Studio; bổ sung hàm bọc f_unaccent IMMUTABLE cho FTS tiếng Việt.
app/pipeline/crawl/*, app/pipeline/parsers/{vietjack,tuyensinh247,loigiaihay,hoc247}.py	A01, A02	GIỮ NGUYÊN logic bóc tách từng site; NÂNG CẤP bộ lọc chống SSRF (đủ IPv4/IPv6 + ghim IP chống DNS Rebinding), bộ kiểm tra robots.txt, chuẩn hóa đầu ra QuestionCandidateContract và Parser Drift Monitor.
app/pipeline/use_cases/drive_sync.py, configs/drive/thptqg2026.yaml	A03	NÂNG CẤP sang OAuth scope drive.file + Google Picker API (hoặc Domain-Wide Delegation cho trường học), lưu source_sync_cursors, bung nén an toàn chống Zip Bomb và ghép cặp 4 tín hiệu Đề + HDC.
app/application/exam_pdf.py, app/ai/figures.py, app/pipeline/domain/exam_split.py	A04, A05, A06, A07	NÂNG CẤP TOÀN DIỆN: Bọc qua PDFEngineAdapter (pypdfium2 + pdfplumber), sửa thuật toán _merge_rects bằng Union-Find, cô lập profile LibreOffice -env:UserInstallation + kill process group, phát hiện Header/Footer động và xử lý từng trang.
app/domain/question_hash.py, app/application/similar.py	A08	NÂNG CẤP chuẩn hóa Unicode NFC tiếng Việt trước khi băm; sửa Regex structural_hash bảo toàn chỉ số công thức Hóa học (H2O, Fe3O4, CH4) và gom cụm bằng pgvector.
app/models.py (Question), app/question_import.py, app/question_rules.py	C01, C02, A09	TÁCH BẢNG questions (định danh) và question_versions (nội dung bất biến), bổ sung đầy đủ 5 trục trạng thái (acquisition_status, verification_status, rights_status, publication_status, job_status).
app/pipeline/use_cases/rescue.py, app/ai/check.py, app/ai/frugal.py, app/ai/key_rotator.py	E01, E04, C02	ĐƯA LÊN SPRINT 2: Tách E01 (AI Provider Gateway) làm nền tảng chung; nâng cấp rescue.py thành Job 1 (Healer & Subject Lock) và Job 2 (Giải độc lập bằng SymPy + LLM).
app/application/custom_quiz.py, app/application/matrix_import.py, app/routers/maker.py	C03, C04	NÂNG CẤP hỗ trợ chuẩn đề THPTQG 2025+ (Toán 22 câu, Lý/Hóa/Sinh 28 câu đủ 3 phần), tách structure_snapshot_json và assessment_answer_keys, báo cáo ShortageReport.
app/attempts.py, app/static/js/attempt.js, app/application/essay_grading.py	C05, C06, C07	NÂNG CẤP lưu attempt_seed & option_permutations_json trên attempts; sửa luồng kiểm tra Idempotency-Key trả 200 OK trước khi xét client_sequence; cho phép nhiều grades revisions khi phúc khảo/chấm lại.
app/application/report.py, app/templates/result.html, app/routers/student.py	D01–D06, E02, E05	MỞ RỘNG tự động gom câu sai vào error_notes, tính lịch ôn tập FSRS v5, lộ trình đồ thị tiên quyết, ước lượng năng lực Rasch/2PL/GPCM và Gia sư AI 3 chế độ có khóa phòng thi.
app/application/teacher_tests.py, app/routers/teacher.py, app/routers/admin/*	F01–F03, G01–G04, G06	MỞ RỘNG quản lý Lớp học (classes), Giao bài (assessment_assignments), Bảng điểm (grades), Giám sát hàng đợi Outbox/DLQ, Takedown bản quyền và cấu hình VerticalPack.

2.2. Ma trận Sở hữu Bảng Độc quyền theo Module (Zero Shared Ownership Matrix)
Để khắc phục triệt để lỗi chồng lấn quyền sở hữu bảng được chỉ ra trong bản Review (takedown_cases, assessment_assignments, review_tasks, subscriptions), quy tắc bắt buộc trong V11.3 như sau:
Tên Bảng Dữ Liệu	Module Sở Hữu Ghi Duy Nhất (Write Owner)	Các Module Đọc Qua Service / Event (Read / Consumers)
review_tasks, review_decisions, source_rights	A09 (Review & Rights Governance)	C02, A07, B01 gọi ReviewGovernanceService.enqueue_task() để tạo yêu cầu duyệt; không ghi trực tiếp vào bảng của A09.
takedown_cases	G02 (Audit & Legal Takedown)	Khi G02 chốt lệnh Takedown, phát sự kiện rights.takedown.executed để A09, B01, C01, B07 tự động cập nhật rights_status = 'TAKEDOWN' và xóa khỏi chỉ mục tìm kiếm.
assessment_assignments	F02 (Classroom Assignments)	C05 (Exam Delivery) gọi AssignmentQueryService.verify_eligibility() để kiểm tra quyền làm bài và hạn nộp của học sinh.
grades, grading_details, appeals	C07 (Grading, Rubrics & Appeals)	F03 (Teacher Gradebook) và D01 (Learner Dashboard) đọc điểm qua Read Projection hoặc GradingQueryService.
subscriptions, usage_ledger	G05 (Commerce & Billing)	F05 (RBAC/ABAC & Entitlements) đọc hạn mức gói cước từ EntitlementProjection được đồng bộ qua sự kiện subscription.updated.
taxonomy_nodes, taxonomy_node_versions, taxonomy_aliases	B04 (Taxonomy Management)	B05 (Knowledge Graph) chỉ sở hữu bảng knowledge_edges và graph_validation_runs (tham chiếu tới taxonomy_nodes.id của B04, xóa bỏ tên bảng ảo knowledge_nodes).

CHƯƠNG 3. ĐẶC TẢ CHI TIẾT ĐẦY ĐỦ 47 MODULES ĐỘC LẬP (A01 – G06, KHÔNG GỘP DÒNG)
3.1. Nhóm A — Content Acquisition & Document Intelligence (A01 – A09)
Mã Module & Sprint	Bảng DB Sở Hữu (Write Owner) & API Chính	Quy tắc Nghiệp vụ, Thuật toán & Xử lý Lỗi Đặc thù (V11.3 Implementation Rules)	Tiêu chí Nghiệm thu Định lượng (DoD)
A01 — Unified Ingestion Backbone
[Track 1 (Core MVP) - M1 - Sprint 1]	DB: sources, source_documents, document_versions, acquisition_jobs, pipeline_stage_runs, dead_letter_items
API:
• POST /api/v1/imports
• GET /api/v1/imports/{id}
• POST /api/v1/imports/{id}/retry
• POST /api/v1/imports/{id}/cancel	• Khởi tạo acquisition_jobs bắt buộc có idempotency_key và đặt acquisition_status = 'INGESTED', rights_status = 'PENDING_REVIEW'.
• Kiểm tra chữ ký byte thật (libmagic), quét mã độc ClamAV, tính checksum_sha256 và lưu bản gốc bất biến lên MinIO tại bronze/{tenant_id}/{sha256}.
• Chia tiến trình thành các chặng độc lập trong pipeline_stage_runs; khi retry chỉ chạy lại từ chặng bị lỗi, không ghi đè file gốc.	• Upload file trùng sha256 trong cùng Tenant trả về document_version hiện có (200 OK), không nhân bản.
• Retry chặng lỗi đạt tính Idempotent 100% trên 50 kịch bản lỗi giả lập.
A02 — Permitted Web Crawler
[Track 3 (Expansion) - M5 - Sprint 10]	DB: crawl_urls, crawl_fetches
API:
• POST /api/v1/crawler/sources
• POST /api/v1/crawler/sources/{id}/validate
• POST /api/v1/crawler/sources/{id}/crawl-jobs
• POST /api/v1/crawler/sources/{id}/pause	• Chỉ chạy khi sources.rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE') và URL khớp robots.txt + allowed_paths, Rate Limit ≤ 1 req/3s.
• Chống SSRF & DNS Rebinding toàn diện: Phân giải DNS 1 lần duy nhất tại Custom HTTP Transport, chặn toàn bộ IPv4 nội bộ (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 100.64.0.0/10, 0.0.0.0/8) và IPv6 (::1/128, fc00::/7, fe80::/10, ::ffff:0:0/96), sau đó ghim trực tiếp IP đã duyệt vào socket.
• Tự động chuyển nguồn sang PAUSED_DRIFT nếu tỷ lệ bóc tách rỗng > 15% trên 30 trang liên tiếp.	• Vượt qua 100% bộ test tấn công SSRF (IPv4, IPv6-mapped, DNS Rebinding, HTTP 302 Redirect sang metadata 169.254.169.254).
• Tự động tạm dừng (pause) trong < 5s khi cấu trúc HTML mẫu thay đổi.
A03 — Google Drive Sync & Pairing
[Track 3 (Expansion) - M5 - Sprint 10]	DB: source_connections, source_sync_cursors, file_pairing_candidates
API:
• POST /api/v1/integrations/google-drive/connect
• POST /api/v1/drive/connections/{id}/sync
• GET /api/v1/drive/sync-jobs/{id}
• POST /api/v1/drive/pairings/{id}/confirm	• Sử dụng OAuth scope không nhạy cảm drive.file kết hợp Google Picker API (hoặc Service Account cho Enterprise B2B) để tránh rào cản kiểm toán CASA Tier 2; lưu tham chiếu khóa trên KMS/Vault (secret_vault_ref).
• Đồng bộ vi sai qua changes.list (startPageToken). Khi thu hồi quyền, đặt status = 'REVOKED' nhưng giữ nguyên đề đã nhập.
• Bung nén .zip trong sandbox (tối đa 4 cấp thư mục, ≤ 500 files, tổng dung lượng giải nén ≤ 200 MB, tỷ lệ nén < 50:1, chặn ../). Ghép cặp Đề + HDC bằng 4 tín hiệu (tên file, cùng thư mục, tiêu đề, số lượng câu).	• Đổi tên/di chuyển file trên Drive không kích hoạt OCR lại.
• Chặn đứng 100% file test Zip Bomb (42.zip) và Path Traversal.
• Ghép cặp tự động Đề + HDC đạt độ chính xác ≥ 92% trên tập test 50 cặp file.
A04 — Complete Exam Import
[Track 1 (Core MVP) - M2 - Sprint 4]	DB: exam_import_sessions, provenance_records
API:
• POST /api/v1/exam-imports
• GET /api/v1/exam-imports/{id}/structure
• PATCH /api/v1/exam-imports/{id}/structure
• POST /api/v1/exam-imports/{id}/approve	• Tái dựng cấu trúc đề nguyên bản (Phần thi, Thứ tự câu, Điểm số, Đoạn văn chung stimulus, Bảng đáp án/HDC) song song với việc tạo các QuestionCandidate.
• Chỉ tạo bản PDF gắn thương hiệu (BRANDED_PDF) khi rights_status = 'OWNED' hoặc 'ALLOWED_DERIVATIVE'.
• Phát hiện vùng Header động: nếu câu hỏi đầu tiên nằm sát mép trên (y_0 < 120pt), tự động mở rộng chiều cao đỉnh trang thêm 70pt trước khi chèn Banner để không bao giờ cắt vào nội dung Câu 1.	• Bóc tách thành công toàn bộ 11+ đề mẫu trong Golden Corpus, đạt Precision ≥ 98% và Recall ≥ 96% về ranh giới câu hỏi và hình vẽ.
A05 — Document Import & Parser Registry
[Track 1 (Core MVP) - M1 - Sprint 3]	DB: parser_definitions, extraction_runs
API:
• POST /api/v1/documents
• GET /api/v1/documents/{id}/versions
• POST /api/v1/documents/{id}/extract
• POST /api/v1/extraction-runs/{id}/reprocess	• Kiểm tra an toàn dung lượng word/document.xml (≤ 25 MB) trước khi đọc stream XML.
• Định tuyến tự động: file .doc cũ hoặc .docx có chứa <w:object> (MathType OLE), <v:shape>, <w:drawing> bắt buộc chuyển qua LibreOffice Headless với thư mục -env:UserInstallation cô lập theo từng job và quản lý bằng Process Group (os.killpg).
• Lưu parser_version trên từng extraction_runs để hỗ trợ chạy lại (reprocess) từng trang.	• Chạy đồng thời 20 tiến trình LibreOffice convert .doc/.docx không xảy ra xung đột khóa profile (0 lỗi exit code 77) và 0 tiến trình soffice.bin mồ côi khi timeout.
A06 — OCR & Layout Intelligence
[Track 1 (Core MVP) - M1 - Sprint 3]	DB: extracted_pages, layout_regions
API:
• POST /api/v1/ocr/jobs
• GET /api/v1/ocr/jobs/{id}
• POST /api/v1/ocr/jobs/{id}/pages/{page}/reprocess	• Tự động xoay thẳng ảnh nghiêng (Deskew) và khử bóng xám ảnh chụp điện thoại bằng OpenCV adaptiveThreshold.
• Thực thi Copy-on-Write theo từng trang (Page-level Clone) qua PDFEngineAdapter (pypdfium2 + pdfplumber mặc định): gom các khung hình bằng thuật toán Union-Find (_merge_rects) lặp đến khi hội tụ, cắt ảnh PNG 300 DPI từ trang gốc, sau đó che vùng hình trên trang tạm để đọc luồng văn bản sạch; lọc Header/Footer bằng thuật toán dò chuỗi lặp liên trang.	• Không làm rớt bất kỳ nhóm chức hóa học (-OH, -COOH) hay nhãn trục đồ thị (v(m/s)) nào; bộ nhớ RAM tiêu thụ khi xử lý file PDF 100 trang luôn < 250 MB.
A07 — Content & Question Extraction
[Track 1 (Core MVP) - M1 - Sprint 3]	DB: content_candidates, question_candidates
API:
• POST /api/v1/candidates/normalize
• GET /api/v1/candidates/{id}
• POST /api/v1/candidates/{id}/split
• POST /api/v1/candidates/{id}/merge	• Với tài liệu chuyên đề hỗn hợp (STUDY_GUIDE_MIXED như bài Công thức Bayes): tách phần Lý thuyết/Phương pháp/Công thức thành ContentCandidate; tách các phần bài tập thành QuestionCandidate (ESSAY, SINGLE_CHOICE, CLUSTER_TRUE_FALSE, SHORT_NUMERIC).
• Bảo toàn cấu trúc bảng phức hợp (colspan, rowspan) và khớp tự động bảng Hướng dẫn chấm 3 cột (Câu - Nội dung - Điểm (3 cột)).	• Tách chuẩn 1 ContentCandidate và 67 QuestionCandidate từ file chuyên đề Bayes kèm đầy đủ tọa độ bbox trong provenance_records.
A08 — Deduplication & Source Pairing
[Track 1 (Core MVP) - M2 - Sprint 4]	DB: duplicate_clusters, duplicate_members
API:
• POST /api/v1/dedup/match
• GET /api/v1/duplicate-clusters/{id}
• POST /api/v1/duplicate-clusters/{id}/decisions	• Chuẩn hóa chuỗi tiếng Việt về Unicode NFC trước khi băm.
• Khử trùng 3 tầng: (1) content_hash (bất biến thứ tự phương án A/B/C/D); (2) structural_hash (thay số liệu bài toán bằng <NUM> nhưng bảo toàn chỉ số hóa học như H2O, Fe3O4, C2H5OH); (3) tương đồng ngữ nghĩa bằng pg_trgm + pgvector.
• Gom các câu trùng tầng 2 & 3 vào duplicate_clusters để Reviewer quyết định giữ làm câu biến thể (VARIANT) hay gộp.	• Phân biệt chính xác 100% giữa công thức hóa học (H2SO4 vs H2SO3 có structural_hash khác nhau) và bài toán đổi số liệu (m=10g vs m=20g có cùng structural_hash).
A09 — Review, Approval & Rights Governance
[Track 1 (Core MVP) - M1 - Sprint 3]	DB: review_tasks, review_decisions, source_rights
API:
• POST /api/v1/review-tasks
• POST /api/v1/review-tasks/{id}/decisions
• POST /api/v1/rights-records	• Giao diện duyệt Side-by-Side hiển thị trang gốc có khoanh vùng bbox bên trái và trình soạn thảo câu hỏi bên phải.
• Quản lý độc lập quyết định duyệt chuyên môn (verification_status = 'VERIFIED') và quyết định duyệt bản quyền (rights_status).
• Cổng kiểm tra cứng ở cả Application Service và Database Trigger: chặn tuyệt đối thao tác Publish nếu rights_status là PENDING_REVIEW, RESTRICTED hoặc TAKEDOWN.	• Kiểm thử tự động xác nhận câu hỏi có verification_status = 'VERIFIED' nhưng rights_status = 'PENDING_REVIEW' hoặc 'RESTRICTED' bị DB từ chối lệnh Publish với lỗi CheckViolation.

3.2. Nhóm B — Content & Knowledge Management (B01 – B07)
Mã Module & Sprint	Bảng DB Sở Hữu (Write Owner) & API Chính	Quy tắc Nghiệp vụ, Thuật toán & Xử lý Lỗi Đặc thù (V11.3 Implementation Rules)	Tiêu chí Nghiệm thu Định lượng (DoD)
B01 — Content Library & Versioning
[Track 1 (Core MVP) - M1 - Sprint 2]	DB: content_items, content_versions, publications, access_grants
API:
• POST /api/v1/content
• POST /api/v1/content/{id}/versions
• POST /api/v1/content-versions/{id}/publish	• Hỗ trợ học liệu đa định dạng (ARTICLE, LESSON, VIDEO, DOCUMENT, FLASHCARD_SET, VOCABULARY_SET) và phân tách visibility_scope (SYSTEM_PUBLIC, TENANT_PRIVATE).
• content_items.current_version_id có khóa ngoại DEFERRABLE trỏ tới content_versions.id. Khi Publish bản mới, bản cũ giữ nguyên trạng thái lịch sử bất biến.	• Sửa bài viết đã Publish tự động sinh version_no + 1 ở trạng thái DRAFT; lệnh UPDATE trực tiếp lên dòng content_versions đã PUBLISHED bị Trigger DB chặn 100%.
B02 — Content Editor & Authoring Studio
[Track 1 (Core MVP) - M1 - Sprint 2]	DB: content_drafts, content_blocks, comment_threads
API:
• POST /api/v1/content/{id}/drafts
• PATCH /api/v1/drafts/{id}
• POST /api/v1/drafts/{id}/validate	• Trình soạn thảo dạng khối (Block Studio) chuẩn VietJack nâng cấp: THEORY_SUMMARY, FORMULA_BOX, METHOD_STEPS, TIP_WARNING, WORKED_EXAMPLE, INTERACTIVE_QUESTION_EMBED.
• Bộ kiểm tra validate phân tích cú pháp KaTeX và xác thực quyền truy cập của toàn bộ asset_id nhúng trong bài trước khi cho phép gửi duyệt.	• Phát hiện 100% lỗi cú pháp LaTeX/KaTeX không đóng ngoặc hoặc nhúng asset_id của Tenant khác trước khi lưu bản chính thức.
B03 — Media & Asset Management
[Track 1 (Core MVP) - M1 - Sprint 1]	DB: assets, content_assets, question_assets
API:
• POST /api/v1/assets/upload-sessions
• POST /api/v1/assets/{id}/finalize
• GET /api/v1/assets/{id}/signed-url	• Toàn bộ bucket MinIO/S3 đặt ở chế độ Private. Quy trình upload 2 bước: cấp Presigned PUT URL -> gọi finalize kiểm tra sha256, mime_type thực tế và quét ClamAV.
• Tự động cắt bản xem trước PREVIEW_PDF (2 trang đầu) cho tài liệu PRO và cấp Signed GET URL có thời hạn ngắn (≤ 15 phút).	• Tài khoản FREE chỉ lấy được Signed URL của PREVIEW_PDF; truy cập chéo asset_id giữa 2 Tenant bị chặn hoàn toàn bởi RLS (404 Not Found).
B04 — Taxonomy Management
[Track 1 (Core MVP) - M1 - Sprint 2]	DB: taxonomy_nodes, taxonomy_node_versions, taxonomy_aliases
API:
• GET /api/v1/taxonomy/nodes
• POST /api/v1/taxonomy/nodes
• POST /api/v1/taxonomy/nodes/{id}/versions	• Tách bảng định danh taxonomy_nodes (giữ id và code_path ltree ổn định) khỏi bảng taxonomy_node_versions để khi cập nhật tên/mô tả ở phiên bản mới, toàn bộ câu hỏi và cạnh đồ thị đang trỏ vào taxonomy_nodes.id không bao giờ bị đứt.
• Sử dụng kiểu dữ liệu PostgreSQL ltree (MATH.PROB.BAYES) kèm chỉ mục GIST để truy vấn cây phân cấp trong < 5ms.	• Nâng cấp phiên bản của một nút Taxonomy không làm mất bất kỳ liên kết câu hỏi hay cạnh đồ thị nào; truy vấn nhánh con bằng toán tử <@ của ltree đạt < 5ms.
B05 — Knowledge Graph
[Track 2 (Intelligent & LMS) - M3 - Sprint 7]	DB: knowledge_edges, graph_validation_runs
API:
• POST /api/v1/knowledge-graph/edges
• GET /api/v1/knowledge-graph/path
• POST /api/v1/knowledge-graph/validate	• Lưu trữ các cạnh quan hệ có hướng giữa các taxonomy_nodes.id: PREREQUISITE, RELATED_TO, EXTENDS, APPLIED_IN, COMMON_MISCONCEPTION.
• Chạy thuật toán kiểm tra đồ thị có hướng không chu trình (DAG Cycle Detection bằng DFS/Topological Sort) trên tập cạnh PREREQUISITE trước khi commit.	• Mọi thao tác thêm cạnh tạo chu trình A -> B -> C -> A bị chặn tức thì với mã lỗi 422 PREREQUISITE_CYCLE_DETECTED.
B06 — Curriculum Mapping & Planning
[Track 1 (Core MVP) - M1 - Sprint 2]	DB: curriculum_versions, curriculum_nodes, taxonomy_mappings
API:
• POST /api/v1/curricula
• POST /api/v1/curricula/{id}/versions
• POST /api/v1/curriculum-mappings
• GET /api/v1/curricula/{id}/coverage	• Quản lý cây mục lục chương trình học 4 cấp (CHAPTER -> LESSON -> SECTION -> PROBLEM_TYPE), khởi đầu với SGK Kết nối tri thức môn Toán & Vật lí lớp 10–11–12.
• Ánh xạ nhiều-nhiều giữa curriculum_nodes và taxonomy_nodes. Cung cấp CoverageReport thống kê số lượng câu hỏi theo từng mức độ nhận thức (NB, TH, VD, VDC) trên mỗi Dạng bài.	• Seed đầy đủ cây SGK Kết nối tri thức và xuất báo cáo CoverageReport chính xác trong < 200ms.
B07 — Search, Discovery & Collections
[Track 1 (Core MVP) - M2 - Sprint 4]	DB: search_documents, collections, collection_items
API:
• GET /api/v1/search
• POST /api/v1/collections
• POST /api/v1/collections/{id}/items	• Xây dựng hàm bọc f_unaccent(text) đánh dấu IMMUTABLE để tạo chỉ mục GIN kết hợp tsvector và pg_trgm, cho phép tìm kiếm tiếng Việt có dấu và không dấu siêu tốc.
• Là Read Projection có thể tái tạo hoàn toàn từ các bảng gốc; tự động loại bỏ tức thì các mục bị chuyển sang TAKEN_DOWN.	• Tìm kiếm từ khóa không dấu (cong thuc bayes, dao dong tat dan) trả kết quả đúng trong p95 < 120ms.

3.3. Nhóm C — Assessment, Question Bank, Fast Autosave & Grading (C01 – C07)
Mã Module & Sprint	Bảng DB Sở Hữu (Write Owner) & API Chính	Quy tắc Nghiệp vụ, Thuật toán & Xử lý Lỗi Đặc thù (V11.3 Implementation Rules)	Tiêu chí Nghiệm thu Định lượng (DoD)
C01 — Question Bank & Typed Item Authoring
[Track 1 (Core MVP) - M2 - Sprint 4]	DB: questions, question_versions, question_options, question_stimuli
API:
• POST /api/v1/questions
• POST /api/v1/questions/{id}/versions
• POST /api/v1/question-versions/{id}/validate	• Hỗ trợ 6 loại câu hỏi chuẩn: SINGLE_CHOICE (ABCD), MULTIPLE_SELECT, CLUSTER_TRUE_FALSE (Đúng/Sai 4 ý a,b,c,d), SHORT_NUMERIC (Trả lời ngắn), ESSAY (Tự luận), GROUP_STEM (Câu chùm dùng chung question_stimuli).
• Ràng buộc UNIQUE (tenant_id, stable_code) và GIN Index trên exam_framework_tags.	• Tạo và lưu phiên bản bất biến cho cả 6 loại câu hỏi; sửa câu hỏi đã Publish tự động tạo version_no + 1 và giữ nguyên bản cũ cho các đề đã phát hành.
C02 — Question Quality & Verification
[Track 1 (Core MVP) - M1 - Sprint 3]	DB: verification_runs
API:
• POST /api/v1/question-versions/{id}/verify
• GET /api/v1/verification-runs/{id}	• Gọi qua E01 (AI Provider Gateway) để chạy 2 Job kiểm định:
1) Job 1 (Healer & Subject Lock): Sửa lỗi OCR công thức, khóa môn học chống phân loại nhầm bài Toán xác suất (bối cảnh y sinh) sang Sinh học.
2) Job 2 (Heterogeneous SymPy + LLM Verifier): Giải độc lập không nhìn đáp án nguồn bằng SymPy sandbox + LLM, đối sánh qua canonicalize_answer. Nếu lệch, gọi A09 tạo review_task mức HIGH.	• Phát hiện 100% câu hỏi khuyết phương án hoặc câu bài toán có đáp án nguồn mâu thuẫn với nghiệm của SymPy.
C03 — Assessment Framework & Blueprint
[Track 1 (Core MVP) - M2 - Sprint 4]	DB: assessment_frameworks, framework_versions, blueprints, scoring_scheme_versions
API:
• POST /api/v1/assessment-frameworks
• POST /api/v1/blueprints
• POST /api/v1/blueprints/{id}/validate	• Định nghĩa chính xác cấu trúc Đề thi THPTQG 2025+ mới:
- Môn Toán (90 phút, 22 câu, 10 điểm): Phần I (12 câu ABCD × 0.25đ = 3.0đ), Phần II (4 câu Đúng/Sai 4 ý × 1.0đ = 4.0đ), Phần III (6 câu Trả lời ngắn × 0.5đ = 3.0đ).
- Môn Lý / Hóa / Sinh (50 phút, 28 câu, 10 điểm): Phần I (18 câu ABCD × 0.25đ = 4.5đ), Phần II (4 câu Đúng/Sai 4 ý × 1.0đ = 4.0đ), Phần III (6 câu Trả lời ngắn × 0.25đ = 1.5đ).
• Đối chiếu ma trận yêu cầu với kho câu hỏi đủ điều kiện để xuất ShortageReport.	• API validate kiểm tra đúng cấu trúc đề THPTQG 2025+ (22 câu Toán, 28 câu Lý/Hóa/Sinh) và báo cáo chính xác số câu còn thiếu theo từng ô chuyên đề × độ khó.
C04 — Exam Builder & Assembly Engine
[Track 1 (Core MVP) - M2 - Sprint 5]	DB: assessments, assessment_versions, assessment_items, assessment_answer_keys
API:
• POST /api/v1/assessments
• POST /api/v1/assessments/{id}/assemble
• POST /api/v1/assessments/{id}/publish	• Thuật toán bốc câu hỏi có ràng buộc: chỉ chọn câu có publication_status = 'PUBLISHED', verification_status = 'VERIFIED', rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE'), không chọn 2 câu cùng duplicate_cluster_id, giữ nguyên chùm stimulus_id.
• Khi Publish: lưu cấu trúc đề (không chứa đáp án) vào assessment_versions.structure_snapshot_json và lưu đáp án chấm điểm tách biệt hoàn toàn vào assessment_answer_keys.	• Bốc tự động đề thi chuẩn THPTQG 2025+ trong < 500ms; cùng random_seed cho ra cùng kết quả; kiểm tra bảo mật xác nhận structure_snapshot_json chứa 0% dữ liệu đáp án.
C05 — Exam Delivery & Session Control
[Track 1 (Core MVP) - M2 - Sprint 5]	DB: attempts
API:
• POST /api/v1/exam-sessions/start
• GET /api/v1/attempts/{id}/paper
• POST /api/v1/attempts/{id}/submit	• Kiểm tra tư cách dự thi, tính deadline_at = started_at + duration_seconds theo giờ Server.
• Sinh attempt_seed riêng cho từng lượt thi, hoán vị thứ tự câu hỏi trong từng phần (giữ nguyên nhóm stimulus_id) và hoán vị phương án A/B/C/D, sau đó lưu cố định question_order_ids và option_permutations_json vào bảng attempts để phục vụ chấm điểm và phúc khảo.	• Tải lại trang (F5) hoặc đổi thiết bị giữa giờ thi vẫn giữ nguyên 100% thứ tự câu hỏi và thứ tự phương án A/B/C/D đã chia cho thí sinh đó; JSON đề thi tuyệt đối không lộ đáp án.
C06 — Attempt Management & Fast Autosave
[Track 1 (Core MVP) - M2 - Sprint 5]	DB: responses, response_revisions
API:
• PUT /api/v1/attempts/{id}/responses/{item_id}
• GET /api/v1/attempts/{id}/sync	• Quy tắc Idempotency-First: Khi nhận request kèm Idempotency-Key và client_sequence:
1) Kiểm tra idempotency_key trong response_revisions: nếu đã tồn tại, lập tức trả về 200 OK kèm trạng thái đã lưu (hỗ trợ client retry an toàn khi rớt mạng).
2) Nếu idempotency_key mới, khóa dòng responses (FOR UPDATE), nếu client_sequence <= current_sequence thì trả 409 STALE_CLIENT_SEQUENCE.
3) Ghi vào responses + response_revisions và COMMIT đồng bộ trước khi trả 200 ACK (không ghi Outbox lẻ).	• Vượt qua bài kiểm thử Chaos/Failure Injection (ngắt mạng gửi lại cùng Idempotency-Key nhận 200 OK, gói tin cũ đến muộn nhận 409, sập Redis vẫn lưu bài bình thường); độ trễ Server p95 < 120ms.
C07 — Grading, Rubrics & Appeals
[Track 1 (Core MVP) - M2 - Sprint 6]	DB: grades, grading_details, rubric_versions, appeals
API:
• POST /api/v1/attempts/{id}/grade
• POST /api/v1/grades/{id}/release
• POST /api/v1/grades/{id}/appeals	• Chấm điểm tất định theo scoring_scheme_versions và assessment_answer_keys:
1) Phần I (ABCD): 0.25đ/câu.
2) Phần II (Đúng/Sai 4 ý): đúng 1 ý = 0.10đ, 2 ý = 0.25đ, 3 ý = 0.50đ, 4 ý = 1.00đ.
3) Phần III (Trả lời ngắn): áp dụng NumericParsePolicy chuẩn phiếu tô THPTQG (tối đa 4 ký tự, đổi dấu , thập phân thành ., từ chối hoặc cảnh báo ngay trên UI nếu nhập dấu phân cách hàng nghìn 1.000).
• Khi phúc khảo hoặc Regrade do đính chính đáp án, tạo dòng grades mới với revision_no + 1 và chuyển dòng cũ sang is_current = FALSE.	• Unit test đạt 100% trên toàn bộ tổ hợp điểm Phần I, II, III và kịch bản Regrade bảo toàn đầy đủ lịch sử các lần chấm (revision_no = 1, 2...).

3.4. Nhóm D — Learning Experience, Practice, FSRS & Adaptive IRT (D01 – D06)
Mã Module & Sprint	Bảng DB Sở Hữu (Write Owner) & API Chính	Quy tắc Nghiệp vụ, Thuật toán & Xử lý Lỗi Đặc thù (V11.3 Implementation Rules)	Tiêu chí Nghiệm thu Định lượng (DoD)
D01 — Learner Dashboard & Activity
[Track 2 (Intelligent & LMS) - M3 - Sprint 7]	DB: activity_events, learner_dashboard_projections
API:
• GET /api/v1/learners/me/dashboard
• GET /api/v1/learners/me/activity	• Tổng hợp tiến độ học tập cá nhân vào bảng learner_dashboard_projections: bài học đang đọc dở, bài tập giáo viên giao sắp đến hạn, số câu trong Sổ tay câu sai cần khắc phục, lịch ôn tập FSRS trong ngày và biểu đồ làm chủ kiến thức theo chương.	• API Dashboard phản hồi trong p95 < 200ms và cập nhật bất đồng bộ trong < 3s sau khi học sinh nộp bài thi hoặc hoàn thành phiên luyện tập.
D02 — Learning Path & Study Planner
[Track 2 (Intelligent & LMS) - M3 - Sprint 7]	DB: learning_plans, plan_tasks
API:
• POST /api/v1/learning-plans
• GET /api/v1/learning-plans/{id}
• POST /api/v1/learning-plans/{id}/recalculate	• Khi phát hiện học sinh có mastery_estimate < 0.50 ở một nút kiến thức u, thuật toán duyệt ngược các cạnh PREREQUISITE (v → u) trên knowledge_edges: nếu nút tiên quyết v cũng yếu (< 0.60), hệ thống tự động xếp nhiệm vụ ôn tập nút gốc v trước rồi mới đến nút u.	• 100% nhiệm vụ trong lộ trình cá nhân hóa có trường rationale_text giải thích rõ nguyên nhân gợi ý và không bao giờ ghi đè bài tập bắt buộc do giáo viên giao.
D03 — Practice Engine
[Track 2 (Intelligent & LMS) - M3 - Sprint 7]	DB: practice_sessions, practice_responses
API:
• POST /api/v1/practice-sessions
• POST /api/v1/practice-sessions/{id}/responses
• POST /api/v1/practice-sessions/{id}/complete	• Phục vụ luyện tập tương tác 4 Tab ngay dưới bài lý thuyết VietJack hoặc luyện theo chuyên đề/độ khó.
• Hỗ trợ chế độ IMMEDIATE_FEEDBACK (chấm và hiện lời giải chi tiết ngay sau từng câu) và chế độ mở gợi ý từng bước (Hint).	• Dữ liệu luyện tập được tách biệt hoàn toàn khỏi bảng attempts/grades của kỳ thi chính thức nhưng vẫn cung cấp tín hiệu đầu vào để cập nhật mastery_estimates.
D04 — Flashcards & Spaced Repetition (FSRS v5)
[Track 2 (Intelligent & LMS) - M4 - Sprint 9]	DB: flashcard_decks, flashcards, revision_schedules
API:
• POST /api/v1/flashcard-decks
• POST /api/v1/flashcards/{id}/review
• GET /api/v1/revision-schedule	• Hỗ trợ thẻ nhớ công thức Toán/Lý/Hóa/Sinh và thẻ câu hỏi từng làm sai.
• Cài đặt thuật toán FSRS v5 tính toán tất định Độ ổn định (S), Độ khó (D) và Khả năng nhớ (R) theo 4 mức đánh giá (1: Again, 2: Hard, 3: Good, 4: Easy) theo đúng múi giờ địa phương của người học (Asia/Ho_Chi_Minh).	• Kết quả tính khoảng cách ngày ôn tập (interval_days) khớp 100% với bộ test chuẩn của FSRS v5 và lưu rõ scheduler_version.
D05 — Error Notebook & Remediation
[Track 2 (Intelligent & LMS) - M3 - Sprint 7]	DB: error_notes, remediation_tasks
API:
• GET /api/v1/error-notes
• POST /api/v1/error-notes/{id}/remediate
• POST /api/v1/error-notes/{id}/resolve	• Tự động thu thập mọi câu trả lời sai từ cả attempts và practice_sessions vào error_notes.
• Truy vấn 3 câu hỏi biến thể cùng structural_hash hoặc gần nhất trên không gian pgvector trong cùng primary_taxonomy_node_id để tạo bài tập khắc phục. Chỉ chuyển sang RESOLVED khi học sinh làm đúng liên tiếp ≥ 2 câu tương tự.	• Học sinh làm đúng 1 câu chưa được đóng lỗi; chỉ khi chuỗi consecutive_correct_count >= 2 hệ thống mới chuyển trạng thái error_notes.status = 'RESOLVED'.
D06 — Progress, Mastery & Phased Adaptive IRT
[Track 3 (Expansion) - M5 - Sprint 11]	DB: mastery_estimates, question_irt_parameters
API:
• GET /api/v1/learners/me/mastery
• POST /api/v1/mastery/recompute	• Cổng Kiểm định Đa Tiêu Chí IRT (irt_tier_registry — Khắc phục AUD-003 & AUD-011): Không quyết định bậc mô hình chỉ bằng cỡ mẫu N; bắt buộc kiểm tra đồng thời số lượt trả lời (item_responses), số người học (learner_count), độ phân tán năng lực (ability_sd), hội tụ (converged), độ phù hợp (infit_msq), SE(b) và độ lệch DIF:
- RULE_WEIGHTED_L1: mặc định khi chưa đủ điều kiện hiệu chuẩn.
- RASCH_1PL_L2: khi N ≥ 150, learners >= 100, ability_sd >= 0.60, infit_msq \in [0.70, 1.30], SE(b) < 0.30.
- IRT_2PL_GPCM_L3: khi N ≥ 300, learners >= 250, ability_sd >= 0.65, infit_msq \in [0.75, 1.25], SE(b) < 0.25.
- IRT_3PL_GPCM_L4: khi N ≥ 1000, learners >= 800, ability_sd >= 0.70, infit_msq \in [0.80, 1.20], SE(b) < 0.20 kèm Beta(5, 17) prior.
• Tự động Fallback về bậc thấp hơn nếu bất kỳ chỉ số chẩn đoán nào trượt.	• Khi học sinh làm < 20 câu hoặc SE(θ) ≥ 0.35, API trả về status = 'INSUFFICIENT_DATA', tuyệt đối không hiển thị điểm dự đoán ảo gây hiểu lầm.

3.5. Nhóm E — AI Learning System & Governance (E01 – E06, Tách Rời Từng Module)
Mã Module & Sprint	Bảng DB Sở Hữu (Write Owner) & API Chính	Quy tắc Nghiệp vụ, Thuật toán & Xử lý Lỗi Đặc thù (V11.3 Implementation Rules)	Tiêu chí Nghiệm thu Định lượng (DoD)
E01 — AI Orchestration & Provider Gateway
[Track 1 (Core MVP) - M1 - Sprint 2]	DB: ai_providers, ai_models, prompt_versions, ai_jobs, ai_traces
API:
• POST /api/v1/ai/jobs
• GET /api/v1/ai/jobs/{id}
• GET /api/v1/ai/usage	• Triển khai ngay tại Sprint 2 để làm cổng điều phối duy nhất cho OCR/LaTeX, C02 Verifier và AI Tutor.
• Tự động xoay vòng API Key (key_rotator), kiểm tra hạn mức ngân sách token theo Tenant/Ngày (daily_budget_usd), kích hoạt Circuit Breaker và chuyển sang model dự phòng khi gặp lỗi 429/5xx hoặc timeout > 15s.	• Khi 1 API Key trả về 429 Too Many Requests, Gateway tự động xoay sang Key tiếp theo trong < 50ms; chặn đứng 100% lời gọi khi Tenant chạm trần ngân sách ngày.
E02 — AI Tutor & Grounded Q&A
[Track 2 (Intelligent & LMS) - M4 - Sprint 9]	DB: tutor_sessions, tutor_messages, retrieval_traces
API:
• POST /api/v1/tutor/sessions
• POST /api/v1/tutor/sessions/{id}/messages	• Chỉ truy xuất ngữ cảnh (RAG) từ các content_versions và question_versions đã PUBLISHED mà học sinh có quyền xem.
• Hỗ trợ 3 chế độ: HINT_MODE (chỉ gợi ý công thức/bước tiếp theo, bộ lọc hậu kiểm chặn lộ đáp số), EXPLAIN_MODE (giải thích chi tiết sau khi đã nộp bài), CHALLENGE_MODE (đưa bài tương tự).
• Exam Lock: Kiểm tra bảng attempts: nếu học sinh đang có phiên thi status = 'IN_PROGRESS', lập tức từ chối phục vụ với mã 403 EXAM_SESSION_ACTIVE.	• Chặn 100% yêu cầu hỏi AI Tutor khi học sinh đang trong phòng thi IN_PROGRESS và chặn 100% các kịch bản Prompt Injection gạ lộ đáp án ở HINT_MODE.
E03 — AI Question Generation (Matrix Gap Filler)
[Track 3 (Expansion) - M5 - Sprint 10]	DB: generation_requests
API:
• POST /api/v1/ai/generations
• GET /api/v1/ai/generations/{id}	• Khi ShortageReport của C03 báo thiếu câu hỏi ở một ô ma trận (chuyên đề × độ khó), giáo viên có thể yêu cầu E03 sinh câu hỏi biến thể bám sát đoạn tài liệu gốc (source_anchors).
• Mọi câu hỏi sinh ra bắt buộc gắn nhãn acquisition_method = 'AI_GENERATED', đi qua C02 (SymPy + LLM Verifier) và đẩy vào A09 (Review Queue) để con người duyệt.	• 0% câu hỏi do AI sinh ra được phép đi thẳng vào kho PUBLISHED mà không qua bước phê duyệt của Reviewer trong review_decisions.
E04 — AI Step-by-Step Solution & SymPy Cross-Check
[Track 2 (Intelligent & LMS) - M4 - Sprint 9]	DB: explanation_runs
API:
• POST /api/v1/ai/explanations
• POST /api/v1/ai/answer-checks	• Sinh lời giải chi tiết từng bước (Phương pháp giải -> Biến đổi công thức -> Thay số -> Kết luận) cho các câu hỏi mới nhập chỉ có đáp án A/B/C/D mà thiếu lời giải.
• Tự động trích xuất biểu thức đại số/giải tích sang mã Python SymPy chạy trong sandbox cô lập để xác minh kết quả tính toán trước khi lưu bản nháp lời giải.	• Lời giải sinh ra có đầy đủ cú pháp KaTeX hợp lệ và đạt tỷ lệ khớp nghiệm với SymPy ≥ 95% trên tập câu hỏi Toán/Lý tính toán.
E05 — Explainable Next-Step Recommendation Engine
[Track 3 (Expansion) - M5 - Sprint 11]	DB: recommendations
API:
• GET /api/v1/recommendations
• POST /api/v1/recommendations/{id}/feedback	• Kết hợp tín hiệu từ error_notes (D05), mastery_estimates (D06) và knowledge_edges (B05) để xếp hạng Top 3 hành động học tập tiếp theo có lợi ích kỳ vọng cao nhất.
• Mỗi gợi ý phải kèm theo cấu trúc giải thích tường minh (evidence_json: sai những câu nào, lỗ hổng ở khái niệm tiên quyết nào).	• Mọi bản ghi recommendations trả về trên UI đều hiển thị rõ lý do gợi ý dựa trên dữ liệu thực tế của học sinh và cho phép ẩn/báo không phù hợp.
E06 — Teacher AI Copilot
[Track 3 (Expansion) - M5 - Sprint 11]	DB: teacher_assist_jobs
API:
• POST /api/v1/teacher/ai-assist
• GET /api/v1/teacher/ai-assist/{id}	• Hỗ trợ giáo viên sau mỗi bài kiểm tra của lớp: tự động phân cụm các lỗi sai phổ biến nhất của học sinh trong lớp, tóm tắt các lỗ hổng kiến thức trọng tâm và soạn sẵn đề xuất phiếu bài tập khắc phục (Draft Assignment) cho từng nhóm học lực.	• Báo cáo tóm tắt lớp học chỉ sử dụng dữ liệu thuộc đúng class_id của giáo viên đó (tuân thủ RLS) và yêu cầu giáo viên bấm xác nhận trước khi giao phiếu bài tập khắc phục.

3.6. Nhóm F — Teacher LMS, Organization, RBAC & Notifications (F01 – F06, Tách Rời Từng Module)
Mã Module & Sprint	Bảng DB Sở Hữu (Write Owner) & API Chính	Quy tắc Nghiệp vụ, Thuật toán & Xử lý Lỗi Đặc thù (V11.3 Implementation Rules)	Tiêu chí Nghiệm thu Định lượng (DoD)
F01 — Class Roster & Group Management
[Track 2 (Intelligent & LMS) - M3 - Sprint 8]	DB: groups, classes, class_members
API:
• POST /api/v1/classes
• POST /api/v1/classes/{id}/members
• DELETE /api/v1/classes/{id}/members/{member_id}	• Giáo viên tạo lớp học, thêm học sinh qua mã mời (invite_code) hoặc import danh sách CSV.
• Khi học sinh rời lớp hoặc chuyển lớp, thực hiện Soft-Delete (class_members.status = 'INACTIVE', left_at = NOW()), tuyệt đối không xóa cứng bản ghi memberships hay attempts để bảo toàn lịch sử điểm.	• Học sinh rời lớp không còn nhận bài tập mới của lớp đó, nhưng toàn bộ bài thi và điểm số cũ trong quá khứ vẫn giữ nguyên 100%.
F02 — Classroom Assignments & Scheduling
[Track 2 (Intelligent & LMS) - M3 - Sprint 8]	DB: assessment_assignments
API:
• POST /api/v1/assignments
• GET /api/v1/assignments/{id}
• PATCH /api/v1/assignments/{id}	• Giáo viên giao bài học hoặc đề thi (assessment_version_id cố định) cho cả lớp (class_id) hoặc nhóm học sinh (group_id) kèm khung giờ mở đề (available_from), hạn nộp (due_at), số lần làm tối đa (max_attempts) và chính sách công bố đáp án (reveal_policy).	• Thí sinh không thể bắt đầu làm bài trước available_from hoặc vượt quá max_attempts; đáp án chỉ hiển thị khi thỏa mãn reveal_policy (VD: sau khi hết hạn nộp của cả lớp).
F03 — Teacher Gradebook & Class Analytics
[Track 2 (Intelligent & LMS) - M3 - Sprint 8]	DB: gradebook_projections
API:
• GET /api/v1/classes/{id}/gradebook
• GET /api/v1/classes/{id}/item-analysis
• GET /api/v1/classes/{id}/gradebook/export	• Tổng hợp bảng điểm toàn lớp, tỷ lệ nộp bài đúng hạn, phân phối phổ điểm và bảng phân tích độ khó thực tế của từng câu hỏi trong bài kiểm tra (chỉ ra Top 5 câu cả lớp sai nhiều nhất và phương án nhiễu nào học sinh hay chọn nhầm nhất).	• Xuất bảng điểm lớp ra CSV/Excel chính xác 100% và tải trang phân tích câu hỏi của lớp 50 học sinh trong < 300ms.
F04 — Tenant Admin, Organization & Parental Consent
[Track 1 (Core MVP) - M1 - Sprint 1]	DB: tenants, users, memberships, parental_consents
API:
• POST /api/v1/tenants
• POST /api/v1/memberships
• POST /api/v1/privacy/parental-consents
• POST /api/v1/privacy/data-erasure-requests	• Quản lý hồ sơ trường học/trung tâm, cấu hình hạn mức tài nguyên.
• Tuân thủ Luật BVDLCN 91/2025/QH15 & NĐ 13/2023/NĐ-CP: Với người học K-12 dưới 16 tuổi, bắt buộc ghi nhận xác nhận đồng ý xử lý dữ liệu cá nhân của phụ huynh hoặc đại diện nhà trường vào bảng parental_consents. Hỗ trợ quy trình xóa/ẩn danh hóa thông tin nhận dạng cá nhân (PII Anonymization) khi có yêu cầu hợp lệ.	• Tài khoản học sinh dưới 16 tuổi chưa có xác nhận parental_consents (qua phụ huynh hoặc hợp đồng B2B nhà trường) không thể kích hoạt tính năng lưu trữ hồ sơ cá nhân nhạy cảm.
F05 — RBAC/ABAC Access Control & Entitlements
[Track 1 (Core MVP) - M1 - Sprint 1]	DB: roles, permissions, role_permissions
API:
• GET /api/v1/permissions/me
• POST /api/v1/roles
• PUT /api/v1/memberships/{id}/roles	• Tách biệt rạch ròi giữa RBAC/ABAC (Role/Permission: quyền thực hiện thao tác nghiệp vụ như question:approve, exam:publish) và Entitlement (FREE, STUDENT_PRO, TEACHER_PRO, SCHOOL_ENTERPRISE: quyền lợi gói cước).
• Mọi API lấy tenant_id và membership_id từ JWT đã ký, kết hợp thiết lập biến phiên PostgreSQL app.current_tenant_id cho RLS.	• Vượt qua bộ kiểm thử tự động Cross-Tenant IDOR trên toàn bộ các nhóm endpoint: 0 trường hợp User thuộc Tenant A đọc hoặc sửa được bản ghi của Tenant B.
F06 — Omnichannel Notifications (Idempotent Delivery)
[Track 2 (Intelligent & LMS) - M3 - Sprint 8]	DB: notifications, notification_deliveries
API:
• GET /api/v1/inbox
• POST /api/v1/inbox/{id}/read
• PATCH /api/v1/notifications/preferences	• Gửi thông báo In-App và Email cho các sự kiện: bài tập mới được giao, sắp đến hạn nộp bài, có kết quả chấm điểm/phúc khảo, và nhắc lịch ôn tập FSRS hàng ngày.
• Chống gửi trùng thông báo bằng khóa dedup_key UNIQUE trên bảng notifications.	• Khi sự kiện attempt.graded bị phát lại 3 lần trên bus, học sinh chỉ nhận đúng 1 thông báo duy nhất.

3.7. Nhóm G — Platform Admin, Operations, Analytics, Commerce & Verticals (G01 – G06, Tách Rời Từng Module)
Mã Module & Sprint	Bảng DB Sở Hữu (Write Owner) & API Chính	Quy tắc Nghiệp vụ, Thuật toán & Xử lý Lỗi Đặc thù (V11.3 Implementation Rules)	Tiêu chí Nghiệm thu Định lượng (DoD)
G01 — Platform Configuration & Feature Flags
[Track 1 (Core MVP) - M1 - Sprint 1]	DB: platform_settings, feature_flags
API:
• GET /api/v1/admin/feature-flags
• PATCH /api/v1/admin/feature-flags/{key}
• PATCH /api/v1/admin/config/{key}	• Quản lý cờ tính năng (feature_flags) hỗ trợ bật/tắt tức thì theo phần trăm người dùng, theo tenant_id hoặc ghim cố định phiên bản parser_version / ai_model khi phát hiện lỗi hồi quy mà không cần redeploy code.	• Đổi trạng thái feature_flag có hiệu lực trên toàn bộ API nodes trong < 5s và tự động ghi nhật ký vào audit_events.
G02 — Immutable Audit Trail & Legal Takedown
[Track 1 (Core MVP) - M1 - Sprint 2]	DB: audit_events, takedown_cases
API:
• GET /api/v1/audit-logs
• POST /api/v1/takedown-cases
• POST /api/v1/takedown-cases/{id}/execute	• Bảng audit_events được phân vùng theo tháng, áp dụng REVOKE UPDATE, DELETE và Trigger chặn sửa xóa.
• Khi thực thi takedown_cases, hệ thống phát sự kiện rights.takedown.executed để gỡ tài liệu/câu hỏi khỏi tìm kiếm, CDN và kho bốc đề mới trong < 30s, đồng thời lưu biên bản pháp lý.	• Mọi thao tác nhạy cảm (Publish, sửa đáp án, đổi quyền, Takedown) đều có bản ghi audit_events kèm before_json, after_json, reason và correlation_id.
G03 — Transactional Outbox, Inbox & Job Operations
[Track 1 (Core MVP) - M1 - Sprint 1]	DB: outbox_events, inbox_events
API:
• GET /api/v1/admin/outbox/stats
• GET /api/v1/admin/dlq
• POST /api/v1/admin/dlq/{id}/replay	• Cung cấp cơ chế Transactional Outbox (FOR UPDATE SKIP LOCKED) để phát sự kiện tin cậy và bảng Idempotent Consumer Inbox (inbox_events với UNIQUE(consumer_name, event_id)) để đảm bảo mọi Worker xử lý sự kiện đúng 1 lần (Exactly-Once Side Effect).
• Cung cấp giao diện quản lý và Replay có kiểm soát các mục trong Dead-Letter Queue.	• Gửi lặp cùng một event_id vào Worker 5 lần chỉ thực thi tác dụng phụ đúng 1 lần nhờ chốt chặn inbox_events.
G04 — Learning & Operational Analytics Warehouse
[Track 3 (Expansion) - M5 - Sprint 12]	DB: analytics_events, daily_metric_rollups
API:
• GET /api/v1/analytics/platform-kpis
• GET /api/v1/analytics/pipeline-yield	• Thu thập sự kiện chuẩn hóa để tính toán các chỉ số vận hành và học tập: WLAU (Weekly Learning Active Users), tỷ lệ bóc tách đề tự động thành công (Pipeline Yield), tỷ lệ câu hỏi phải sửa tay và mức tăng trưởng năng lực học sinh (Mastery Gain).	• Báo cáo KPI chạy trên bảng tổng hợp daily_metric_rollups, không gây tải nặng lên các bảng giao dịch phòng thi.
G05 — Subscriptions, Billing & Usage Ledger
[Track 3 (Expansion) - M5 - Sprint 12]	DB: subscription_plans, subscriptions, usage_ledger, payment_webhooks
API:
• GET /api/v1/plans
• POST /api/v1/subscriptions
• POST /api/v1/webhooks/payment	• Quản lý gói cước (FREE, STUDENT_PRO, TEACHER_PRO, SCHOOL_ENTERPRISE), xử lý webhook thanh toán bất biến có kiểm tra chữ ký HMAC và ghi sổ cái tiêu thụ (usage_ledger) cho lượt tải PDF thương hiệu và hạn mức AI.	• Webhook thanh toán gửi trùng nhiều lần chỉ cộng hạn mức đúng 1 lần; phát sự kiện subscription.updated đồng bộ tức thì sang F05.
G06 — Vertical Packs, Localization & Accessibility
[Track 3 (Expansion) - M5 - Sprint 12]	DB: vertical_packs, locale_bundles
API:
• GET /api/v1/vertical-packs
• POST /api/v1/vertical-packs/{code}/activate	• Đóng gói cấu hình mở rộng cho các mảng ngoài K-12 (như IELTS, TOEIC, HSK 3.0, Đại học) dưới dạng vertical_packs (chứa Taxonomy mẫu, Assessment Framework và Scoring Rules riêng) mà không làm phình to hay thay đổi bảng lõi của K-12.	• Bật/tắt một VerticalPack mới không yêu cầu sửa đổi bất kỳ dòng code hay bảng DDL cốt lõi nào của hệ thống.

3.8. Kiến trúc Giao diện Web (Frontend Architecture), Ánh xạ Nâng cấp & Đặc tả Chi tiết 6 Workspaces
#### A. Định hướng Kiến trúc Frontend & Lộ trình Nâng cấp từ app/templates/*, app/static/js/*
Trong repo exam-system hiện tại, giao diện đang dùng kết hợp các trang HTML/Jinja2 (app/templates/result.html, admin/*, student/*) và JavaScript thuần (app/static/js/attempt.js). Để vừa đảm bảo tốc độ tải trang và chuẩn SEO < 1 giây cho hàng trăm nghìn trang bài học lý thuyết, vừa đáp ứng độ mượt, khả năng chịu lỗi mất mạng và tương tác phức tạp ở Phòng thi & Công cụ Cắt/Duyệt đề, kiến trúc Web V11.3 áp dụng mô hình Next.js (App Router, TypeScript) + TailwindCSS + TanStack Query + Zustand + Dexie.js (IndexedDB):
Thành phần Giao diện trong exam-system cũ	Kiến trúc Web đích (V11.3 Frontend)	Chiến lược Render & Công nghệ Cốt lõi
Trang đọc bài học chuyên đề & Thư viện đề (app/routers/library.py)	Workspace 1: Learner Study & Practice Hub	SSR / ISR (Incremental Static Regeneration): Render sẵn HTML + công thức KaTeX trên Server giúp trang tải tức thì (LCP < 1.2s), chuẩn SEO Google, sau đó Hydrate 4 Tab bài tập tương tác ở phía Client.
Phòng làm bài thi (app/static/js/attempt.js)	Workspace 2: High-Resilience Online Exam Room	SPA (Client-Side State Machine + IndexedDB): Đồng hồ đồng bộ Server (ServerClockSync), lưu nháp tức thì vào IndexedDB (< 5ms) trước khi đẩy ngầm qua AutosaveQueue có Idempotency-Key & client_sequence.
Màn hình nhập đề & Cắt hình (app/routers/admin/*)	Workspace 3: Side-by-Side Bounding-Box Reviewer	Interactive Canvas Split-Pane: Khung trái vẽ trang PDF gốc + lớp phủ SVG bbox cho phép kéo-thả cắt lại hình (Re-crop); khung phải là form chỉnh sửa câu hỏi và xem cảnh báo nghiệm từ SymPy.
Trình tạo bài viết & Câu hỏi (app/routers/maker.py)	Workspace 4: VietJack-Style Block Studio & Item Authoring	Block-Based Editor (TipTap / Lexical tùy biến): Hỗ trợ gõ lệnh nhanh /congthuc, /phuongphap, /chuy, /vidu kèm khung Live KaTeX Preview kiểm tra lỗi cú pháp toán/hóa học tức thì.
Giao diện Giáo viên & Tạo đề ma trận (app/routers/teacher.py)	Workspace 5: Matrix Exam Builder & Teacher LMS Hub	Reactive Matrix Grid & Data Charts: Bảng chọn số câu theo Chuyên đề × Độ khó báo thiếu câu (ShortageReport) theo thời gian thực; biểu đồ phổ điểm lớp và phân tích phương án nhiễu.
Quản trị hệ thống, Crawler, Drive & Takedown	Workspace 6: Admin Ops, Drive Picker & Takedown Console	Operational Dashboard: Tích hợp Google Picker SDK (drive.file), biểu đồ ParserDriftMonitor, quản lý hàng đợi DLQ và nút khẩn cấp Execute Legal Takedown.

#### B. Đặc tả Chi tiết 6 Workspaces & Quy tắc Xử lý 6 Trạng thái UI Bắt buộc
Mọi màn hình trong 6 Workspaces bắt buộc phải cài đặt đầy đủ 6 trạng thái UI chuẩn (Loading Skeleton, Empty State, Error Boundary, Locked Pro Preview, Offline Retry Banner, Sequence Conflict Modal):
Workspace & Đường dẫn Route	Bố cục Màn hình (Layout) & Luồng Tương tác Trọng tâm	Xử lý Đặc thù về Toán/Hóa (KaTeX), Ngoại tuyến & Bảo mật
1. Learner Study & Practice Hub
Route: /hoc-lieu/[slug], /lo-trinh, /so-tay-cau-sai	• Cột trái (260px, Collapsible): Cây mục lục SGK 4 cấp (Chương -> Bài -> Mục -> Dạng bài) gắn chấm màu theo mức độ làm chủ (Mastery: Xanh ≥ 80%, Vàng 50–79%, Đỏ < 50%).
• Cột giữa (760px, Tối ưu đọc): Hiển thị các khối bài học chuẩn VietJack nâng cấp: Tóm tắt lý thuyết, Hộp công thức đóng khung nổi bật, Các bước giải bài toán, Chú ý / Bẫy hay sai, Ví dụ mẫu có nút Xem/Ẩn lời giải. Ngay bên dưới bài học là 4 Tab bài tập tương tác (Tự luận, ABCD, Đúng/Sai 4 ý, Trả lời ngắn).
• Cột phải (320px, Sticky Drawer): Khung chat Gia sư AI (3 nút chuyển chế độ: Gợi ý bước tiếp theo / Giải thích chi tiết / Thử thách câu tương tự) và danh sách câu hỏi cần khắc phục trong Sổ tay câu sai.	• Công thức KaTeX được cấu hình throwOnError: false, trust: false (chống XSS qua mã LaTeX độc hại).
• Với tài liệu thuộc gói STUDENT_PRO mà học sinh đang ở gói FREE, hiển thị trạng thái Locked Preview: cho xem trọn vẹn 2 trang đầu (PREVIEW_PDF) kèm nút mở khóa, tuyệt đối không tải URL của file gốc xuống DOM.
2. High-Resilience Online Exam Room
Route: /phong-thi/[attempt_id]	• Thanh Header cố định (Sticky Topbar): Hiển thị tên kỳ thi, đồng hồ đếm ngược tính theo server_time_offset_ms, huy hiệu trạng thái lưu bài (Đã lưu trên máy chủ màu xanh / Đang lưu tạm ngoại tuyến (N câu) màu cam) và nút Nộp bài.
• Khu vực làm bài 3 Phần chuẩn THPTQG 2025+:
- Phần I (ABCD): Các nút chọn phương án lớn, hỗ trợ phím tắt A, B, C, D và nút gạch bỏ phương án nhiễu (Eliminate Option).
- Phần II (Đúng/Sai 4 ý): Bảng 4 dòng a), b), c), d) với 2 cột radio Đúng / Sai rõ ràng; tự động đánh dấu cảnh báo màu vàng trên bảng câu hỏi nếu thí sinh mới chọn 1–3 ý mà quên ý còn lại.
- Phần III (Trả lời ngắn 4 ký tự): Ô nhập liệu gồm 4 ô ký tự trực quan mô phỏng phiếu tô trắc nghiệm THPTQG (chỉ cho phép nhập chữ số 0–9, dấu - ở ô đầu, và dấu , thập phân); cảnh báo đỏ ngay dưới ô nếu thí sinh gõ dấu chấm phân cách hàng nghìn (1.000).
• Bảng điều hướng câu hỏi (Question Palette): Hiển thị trạng thái từng câu và nút gắn cờ (Flag).	• Chống mất bài tuyệt đối: Mỗi khi thí sinh bấm chọn đáp án, UI lưu ngay vào bảng pending_autosaves trong IndexedDB (< 5ms) rồi mới gửi PUT /attempts/{id}/responses/{item_id} kèm Idempotency-Key và client_sequence.
• Nếu mất mạng, hiển thị thanh Offline Retry Banner màu cam nhưng không khóa màn hình — thí sinh vẫn tiếp tục làm các câu khác bình thường; khi có mạng trở lại, Worker ngầm trên trình duyệt tự động đồng bộ lại toàn bộ.
• Nếu thí sinh mở 2 tab cùng lúc gây lỗi 409 STALE_CLIENT_SEQUENCE, hiển thị hộp thoại Conflict Modal và gọi GET /attempts/{id}/sync để lấy trạng thái mới nhất từ Server.
3. Side-by-Side Bounding-Box Reviewer
Route: /studio/imports/[run_id]/review	• Khung trái (50% màn hình — Interactive PDF/Image Canvas): Hiển thị trang tài liệu gốc với các khung chữ nhật màu xanh (câu hỏi), màu cam (hình vẽ cắt FIGURE_CROP), màu tím (bảng biểu HTML_TABLE). Khi click vào Câu k ở khung phải, khung trái tự động cuộn và phóng to đúng vùng bbox của Câu k. Hỗ trợ kéo chuột vẽ lại khung bbox để cắt lại hình ảnh ngay trên trình duyệt.
• Khung phải (50% màn hình — Structured Question Inspector): Form chỉnh sửa nội dung câu hỏi, bảng đối chiếu giữa Đáp án nguồn (HDC) và Đáp án giải độc lập bởi SymPy + LLM. Nếu 2 bên lệch nhau, hiển thị nhãn đỏ DISPUTED_ANSWER yêu cầu Reviewer kiểm tra lại trước khi bấm Approve.	• Cổng khóa nút Publish ngay trên UI: Nút Publish lên Kho bị vô hiệu hóa (Disabled) kèm giải thích rõ nếu rights_status đang là PENDING_REVIEW / RESTRICTED hoặc verification_status != 'VERIFIED'.
4. VietJack-Style Content Studio & Authoring
Route: /studio/content/[id], /studio/questions/new	• Trình soạn thảo chia cột: bên trái là các khối nội dung (THEORY_SUMMARY, FORMULA_BOX, METHOD_STEPS, TIP_WARNING, WORKED_EXAMPLE), bên phải là bản xem trước giao diện Học sinh theo thời gian thực (Desktop / Mobile).
• Hỗ trợ dán trực tiếp công thức từ Word/MathType hoặc gõ LaTeX có gợi ý tự động các ký hiệu Toán, Lý, Hóa (\vec{F}, \Delta \varphi, \xrightarrow{t°}).	• Bộ kiểm tra Pre-Submit Validator quét toàn bộ các cặp dấu ..., \begin{aligned}...\end{aligned} và kiểm tra quyền sở hữu của các asset_id hình ảnh trước khi cho phép lưu bản nháp.
5. Matrix Exam Builder & Teacher LMS Hub
Route: /teacher/exam-builder, /teacher/classes/[id]	• Exam Builder: Bảng ma trận hai chiều (Hàng = Chương/Bài/Chuyên đề trong SGK; Cột = 4 mức độ NB, TH, VD, VDC × 3 loại câu hỏi). Giáo viên nhập số lượng câu mong muốn -> hệ thống hiển thị ngay số câu khả dụng trong kho và báo đỏ các ô thiếu câu (ShortageReport).
• Teacher Gradebook: Bảng điểm toàn lớp, biểu đồ phân phối điểm, danh sách học sinh chưa nộp bài và bảng Item Analysis chỉ ra 5 câu cả lớp sai nhiều nhất kèm tỷ lệ chọn từng phương án nhiễu A/B/C/D.	• Tích hợp bảng trợ lý Teacher AI Copilot (E06): gợi ý nhóm học sinh cần phụ đạo cùng chuyên đề và tạo sẵn nút "Giao phiếu bài tập khắc phục 10 câu cho nhóm học sinh này".
6. Admin Ops, Crawler, Drive & Takedown Console
Route: /admin/ops, /admin/sources, /admin/takedowns	• Quản lý Nguồn & Google Drive: Nút mở Google Picker (chế độ chọn nhiều file đề/HDC hoặc file .zip) và hướng dẫn chia sẻ thư mục trường học cho địa chỉ email Service Account. Hiển thị danh sách các cặp File Đề + File HDC được ghép tự động kèm điểm tin cậy 4 tín hiệu để Admin bấm Confirm.
• Giám sát Hàng đợi & Takedown: Biểu đồ độ trễ Outbox, danh sách dead_letter_items có nút Replay, và biểu mẫu thực thi lệnh gỡ bỏ bản quyền khẩn cấp (Takedown).	• Mọi thao tác bấm nút nhạy cảm trên trang Admin (Execute Takedown, Override Grade, Change Rights Status, Replay DLQ) đều bật hộp thoại bắt buộc nhập reason (tối thiểu 15 ký tự) để lưu vào audit_events.

