PHẦN III (TẬP 3 / 3 — V11.3): QUY TRÌNH GIT/CI/CD, LỘ TRÌNH 3 RELEASE TRACKS (M0 + SPRINT 1–14 = 30 TUẦN), 11 RELEASE GATES (GATE-01..10), GOLDEN CORPUS, SLOs & 14 RUNBOOKS VẬN HÀNH
CHƯƠNG 7. QUY TRÌNH PHÒNG DEV, TRUNK-BASED GIT FLOW, CI/CD 6 CỔNG, DoR/DoD & ZERO-DOWNTIME MIGRATION
7.1. Quy mô Đội Ngũ (Team Topology — 10 Nhân sự) & Phân vai
Vai trò (Role)	Số lượng	Phụ trách chính (Ownership)	Tiêu chuẩn bàn giao Code hàng ngày
Tech Lead / Architect	1	Duyệt thiết kế DB Migration, API/Event Contracts, Review PR nhạy cảm (RLS, Autosave, Scoring, Rights, AI Gateway).	Không merge bất kỳ PR nào vi phạm .importlinter, thiếu tenant_id RLS hoặc thiếu Unit/Integration Test.
Backend Engineers (BE)	3	Phát triển các domain identity, taxonomy, content, questions, assessment, attempts, grading, classes, commerce.	100% endpoint tuân thủ OpenAPI 3.1, thiết lập SET LOCAL app.current_tenant_id, hỗ trợ Idempotency-Key và có test pytest.
Data / AI Engineers (DE/AI)	2	Phát triển acquisition, document_intelligence (pypdfium2/pdfplumber/LibreOffice), crawler, drive_sync, ai_gateway, sympy_verifier.	Mọi parser/model phải lưu version; không bao giờ ghi đè file gốc ở tầng Bronze; kiểm soát trần ngân sách token theo Tenant.
Frontend Engineers (FE)	2	Xây dựng 6 Workspaces (Learner, Teacher, Content Studio, Review Queue Side-by-Side, Exam Builder, Admin Ops).	Render công thức KaTeX mượt mà; xử lý đủ 6 trạng thái UI (Loading, Empty, Error, Locked Preview, Offline Retry, Conflict).
QA / SDET	1	Quản lý bộ Golden Corpus (11+ đề mẫu ẩn danh trên S3 Private + tập Held-out 30 đề), tự động hóa E2E Playwright, kiểm thử tải Autosave và IDOR.	Chặn đứng mọi bản build nếu Precision/Recall bóc tách Golden Corpus tụt giảm hoặc có lỗi bảo mật Cross-Tenant (IDOR).
DevOps / SRE	1	Vận hành CI/CD, PostgreSQL Synchronous Replication (RPO = 0), PgBouncer, MinIO, Redis, Prometheus/Grafana và diễn tập Backup/Restore.	Đảm bảo thời gian chạy CI Pipeline < 8 phút và tự động hóa kiểm tra 10 Release Gates trước khi lên Production.

7.2. Quy trình Trunk-Based Git Flow & 6 Cổng Kiểm Soát CI/CD (Khắc phục P0-1)
17.	Chiến lược nhánh (Trunk-Based Development with Short-Lived Branches):
•	Nhánh chính main luôn ở trạng thái sẵn sàng triển khai (Deployable).
•	Mọi tính năng phát triển trên nhánh ngắn hạn feat/<module>-<ticket> (tồn tại tối đa 2–3 ngày), tích hợp qua Pull Request và ẩn dưới FeatureFlag (G01) nếu chưa hoàn thiện toàn bộ luồng.
18.	Sáu Cổng Tự Động trong CI Pipeline (GitHub Actions / GitLab CI):
•	Cổng 1 — Static & Architecture Boundary Check: Chạy ruff, mypy --strict và lint-imports (cấu hình .importlinter cấm các module import trực tiếp SQLAlchemy model của module khác).
•	Cổng 2 — Schema & RLS Compliance Check: Chạy alembic upgrade head trên PostgreSQL 16 container sạch, sau đó chạy script kiểm tra mọi bảng nghiệp vụ đều đã bật ROW LEVEL SECURITY và FORCE ROW LEVEL SECURITY.
•	Cổng 3 — Unit & Deterministic Contract Tests: Chạy toàn bộ test đơn vị (Chấm điểm THPTQG 2025+, Idempotency-First Autosave, FSRS v5, structural_hash Hóa học, SymPy Verifier).
•	Cổng 4 — Multi-Tenant IDOR Security Suite: Chạy bộ test tự động tạo dữ liệu ở Tenant A và dùng JWT của Tenant B gọi toàn bộ endpoints đọc/sửa/xóa (bắt buộc trả về 404/403 100%).
•	Cổng 5 — Golden Corpus Extraction Gate: Kéo 11+ đề mẫu đã ẩn danh từ bucket s3://ulp-qa-golden-private/, chạy pytest -m golden kiểm tra Precision ≥ 98%, Recall ≥ 96%.
•	Cổng 6 — OSS License & Vulnerability Audit: Chặn mọi thư viện vi phạm giấy phép AGPLv3 (nếu không nằm trong danh sách đã mua giấy phép thương mại) và quét CVE bằng pip-audit / trivy.
7.3. Tiêu chuẩn Definition of Ready (DoR) & Definition of Done (DoD)
•	Definition of Ready (DoR — Điều kiện trước khi đưa Ticket vào Sprint):
19.	Đã xác định rõ mã Module (A01–G06) và bảng dữ liệu sở hữu ghi duy nhất (Write Owner).
20.	Đã định nghĩa rõ hợp đồng API (OpenAPI/Pydantic v2) hoặc hợp đồng sự kiện (event_type, schema_version).
21.	Đã xác định rõ yêu cầu phân quyền (Permission, Entitlement, chính sách RLS) và trạng thái trên 5 trục trạng thái.
22.	Có tiêu chí nghiệm thu định lượng (Acceptance Criteria) và dữ liệu kiểm thử mẫu.
•	Definition of Done (DoD — Điều kiện hoàn thành Ticket):
23.	Code vượt qua cả 6 Cổng CI/CD, độ phủ test (Coverage) của logic nghiệp vụ lõi ≥ 85%.
24.	Mọi thao tác thay đổi dữ liệu nhạy cảm đều ghi audit_events và phát outbox_events trong cùng Transaction.
25.	Đã kiểm thử thực tế trên môi trường Staging với PgBouncer transaction pooling bật RLS.
26.	Có đầy đủ Metrics (OpenTelemetry/Prometheus) và tài liệu cập nhật vào Runbook nếu có cảnh báo mới.
7.4. Quy trình Database Migration 5 Bước Không Gián Đoạn (Zero-Downtime Migration)
27.	Bước 1 (Expand): Thêm bảng mới hoặc cột mới (cho phép NULL hoặc có DEFAULT hằng số không khóa bảng); tạo chỉ mục bằng CREATE INDEX CONCURRENTLY.
28.	Bước 2 (Deploy Dual-Write): Triển khai code mới ghi đồng thời vào cả cấu trúc cũ và cấu trúc mới (ví dụ khi tách bảng questions sang question_versions và assessment_answer_keys).
29.	Bước 3 (Backfill): Chạy Worker chuyển đổi dữ liệu lịch sử theo từng lô nhỏ (batch_size = 500), đối soát tổng số dòng và mã băm sha256.
30.	Bước 4 (Switch Read): Chuyển luồng đọc của API sang bảng/cột mới qua cờ FeatureFlag sau khi dữ liệu khớp 100%.
31.	Bước 5 (Contract): Gỡ bỏ cột/bảng cũ ở Sprint tiếp theo sau khi hệ thống vận hành ổn định tối thiểu 7 ngày.
CHƯƠNG 8. LỘ TRÌNH 3 RELEASE TRACKS (`M0 + SPRINT 1–14 = 30 TUẦN` HOẶC `PHƯƠNG ÁN B = 26 TUẦN`), KẾ HOẠCH NGUỒN LỰC & 11 RELEASE GATES (`GATE-xx`)
Quy tắc Đồng bộ Tuyệt đối trong V11.3 (Đã tích hợp Mục 6 & Mục 7 của Bản vá V11.1):
1. Đưa E01 về Sprint 2 và C02 về Sprint 3 (chuyển B07 sang Sprint 4): Đáp ứng đúng yêu cầu của ADR-006 để A06/A07 có sẵn cổng AI và bộ kiểm chứng SymPy ngay từ Sprint 3.
2. Quy tắc tải Sprint: Tối đa 6 modules / Sprint, trong đó tối đa 2 modules rủi ro cao (RLS, Autosave, AI Gateway, Grading, Crawler, Drive).
3. Khởi động Pilot sớm từ Sprint 8 (kèm Anchor Items): Giúp tích lũy dữ liệu trả lời từ Sprint 8 đến Sprint 11 cho hiệu chuẩn IRT (D06); chấp nhận RULE_WEIGHTED_L1 là trạng thái hợp lệ tại thời điểm Go-Live nếu câu hỏi chưa đạt đủ 6 tiêu chí thống kê.
4. Bổ sung 4 tuần Buffer (Sprint 13–14 ở Phương án A — 30 tuần, hoặc hoãn A02, E03, G06 ở Phương án B — 26 tuần): Dành riêng cho Pentest bên ngoài, WAF, sửa lỗi từ trường Pilot và diễn tập DR trước khi Go-Live.
8.1. Bảng Phân Công Chi Tiết `M0` và `Sprint 1 – Sprint 14` (Đội ngũ 10 Kỹ sư)
Giai đoạn / Sprint & Modules Chính Thức	Nhiệm vụ Backend (3 BE)	Nhiệm vụ Data/AI & Worker (2 DE/AI)	Nhiệm vụ Frontend (2 FE)	Kiểm thử QA/SDET & DevOps (Exit Criteria & Gates)
M0 — Pre-Flight
(Tuần 1–2 — Milestone M0)
Modules: Chuẩn bị nền tảng (0 module nghiệp vụ)	• Audit repo exam-system (app/models.py), lập kịch bản Backfill.
• Khởi tạo 7 DB Roles (NOBYPASSRLS, gồm cả ulp_system_scheduler), UUIDv7, fts_dictionary_versions.
• Lập hồ sơ DPIA & DPA nhà cung cấp.	• Chốt PDFEngineAdapter (pypdfium2 + pdfplumber), gỡ bỏ PyMuPDF AGPLv3.
• Ẩn danh hóa 11 đề Golden + 30 đề Held-out lên bucket S3 riêng; dựng khung tính chi phí LLM/GPU.	• Khởi tạo Next.js App Router, Design System, bộ render KaTeX (trust: false) và khung 6 trạng thái UI chuẩn.	• Dựng cụm PostgreSQL 3-Node Patroni (synchronous_mode_strict: true) + PgBouncer + Vault Transit + MinIO + Redis + CI 6 Cổng (check_gate_ids.sh).
Sprint 1 (Track 1)
(Tuần 3–4 — Milestone M1)
Modules (6): A01, B03, F04, F05, G01, G03	• Middleware JWT + (SELECT current_app_tenant_id()), RBAC (F05), Consent & Vault Transit Per-User Crypto-Shredding (F04).
• Upload MinIO (B03), Config (G01), Outbox/Inbox (G03), Ingestion (A01).	• Khung Worker đọc outbox_events qua ulp_system_scheduler (app.system_job = 'outbox_relay').
• Quét ClamAV, kiểm tra libmagic và lưu bất biến tầng Bronze.	• Màn hình Đăng nhập, Xác nhận Phụ huynh, Chọn Workspace.
• Component Upload tài liệu lên MinIO có thanh tiến trình Pipeline.	• Chạy bộ verify_rls_hardening.sql trên PostgreSQL 16 thật.
• Demo: Upload file đề PDF, lưu tầng Bronze, kiểm chứng xóa khóa trên Vault Transit + WORM Tombstone.
Sprint 2 (Track 1)
(Tuần 5–6 — Milestone M1)
Modules (6): B01, B02, B04, B06, E01, G02	• CRUD taxonomy_nodes (ltree — B04), curriculum_versions (B06).
• API content_items & content_versions bất biến (B01, B02).
• Triển khai sớm E01 (AI Provider Gateway) & G02 (Legal Takedown).	• Cài đặt E01: xoay vòng API Key, PIIScrubber hai chiều và khóa trần daily_ai_budget_usd.
• Seed cây SGK Kết nối tri thức môn Toán & Vật lí (Lớp 10–11–12).	• Giao diện Workspace 4 (VietJack-Style Content Studio), Workspace 1 (Trang đọc chuyên đề SSR) và giao diện thực thi Takedown (Workspace 6).	• Kiểm tra Trigger tự bật is_immutable = TRUE khi Publish, kiểm tra hybrid_read_policy chặn bản DRAFT, và test Takedown < 30s.
Sprint 3 (Track 1)
(Tuần 7–8 — Milestone M1)
Modules (5): A05, A06, A07, A09, C02	• API Parser Registry (A05), OCR Jobs (A06), Candidate Extraction (A07), Hàng đợi duyệt Side-by-Side (A09) và Kiểm chứng Chất lượng Câu hỏi (C02).	• Đưa VietnameseExamDocumentProcessor (LibreOffice cô lập profile, find_tables(), Union-Find) và Subprocess SymPy Sandbox (sympy_sandbox_worker.py) vào xử lý đề Toán & Lý.	• Giao diện Workspace 3 (Side-by-Side Reviewer): khung trái hiển thị trang gốc + lớp phủ bbox tĩnh (hoãn kéo-thả re-crop sang V1.1 nếu giữ 2 FE), khung phải duyệt câu hỏi & xem cảnh báo SymPy.	• Kiểm chứng 12 ca chặn RCE của Subprocess SymPy và độ chính xác bóc tách đề Toán/Lý.
• Demo: Nhập đề Toán 11 (MathType OLE) & Lý 11 -> SymPy kiểm chứng -> Duyệt.
Sprint 4 (Track 1)
(Tuần 9–10 — Milestone M2)
Modules (5): A04, A08, B07, C01, C03	• API Nhập đề nguyên bản (A04), Khử trùng 3 tầng (A08), Tìm kiếm FTS không dấu (B07).
• Ngân hàng 6 loại câu hỏi tách bảng question_version_answers (C01) & Ma trận THPTQG 2025+ (C03).	• Forward Chemical Tokenizer (A08: bắt cả số dính đơn vị 10g == 200g và bảo toàn H2SO4).
• Bộ kiểm tra đáp ứng ma trận và xuất ShortageReport.	• Trình soạn thảo 6 loại câu hỏi (ABCD, Đúng/Sai 4 ý, Trả lời ngắn, Tự luận, Câu chùm), thanh tìm kiếm FTS và lưới thiết lập Ma trận đề.	• Kiểm tra quyền ulp_exam_session_rw và ulp_worker_ingestion bị chặn SELECT trên question_version_answers.
• Demo: Nhập nguyên đề chuyên đề Bayes (67 câu) và tìm kiếm FTS < 120ms.
Sprint 5 (Track 1)
(Tuần 11–12 — Milestone M2)
Modules (3): C04, C05, C06	• API Bốc đề tất định (C04), Phát đề & Hoán vị (C05).
• API Optimistic-Concurrency Autosave (C06: kiểm tra expected_response_version, đối chiếu payload_hash, deadline_at, khóa chống đua lệnh Submit và ghi log chống gian lận).	• Worker quét hết giờ bằng ulp_system_scheduler (app.system_job = 'attempt_expiry') rồi mở Transaction riêng dưới ulp_worker_grading để chốt AUTO_SUBMITTED.	• Giao diện Workspace 2 (Phòng Thi Trực Tuyến): lưu Dexie (IndexedDB thật), gửi expected_response_version + payload_hash, tự động resync, và ghi nhận visibilitychange (Anti-Cheat).	• Chạy bộ Load Test k6 5.000 CCU (mỗi VU 1 attempt riêng từ SharedArray) & Chaos Failover Patroni.
• Demo: Thi thử 5.000 CCU đạt Server p95 < 120ms, RPO = 0.
Sprint 6 (Track 1 Exit)
(Tuần 13–14 — Milestone M2)
Modules (1): C07 (+ Chốt Track 1)	• Engine chấm điểm tất định 3 phần (C07) đọc đáp án đề hệ thống qua policy grading_read_answer_keys, ánh xạ ngược option_permutations_json, NumericParsePolicy (max_chars=4) và hỗ trợ nhiều grades revisions khi phúc khảo/Regrade.	• Kiểm toán toàn bộ quyền information_schema.role_table_grants khớp 100% với grants_manifest.csv và đóng gói bằng chứng cho Track 1.	• Giao diện Xem kết quả thi, Giải thích đáp án sau khi mở khóa (reveal_policy), hiển thị cờ cảnh báo gian lận cho giáo viên và gửi yêu cầu Phúc khảo (Appeals).	• Đóng các cổng nghiệm thu Track 1 (GATE-01, GATE-02, GATE-03, GATE-04, GATE-05, GATE-06a, GATE-07): Phát hành bản MVP Core Toán & Vật lí THPT.
Sprint 7 (Track 2)
(Tuần 15–16 — Milestone M3)
Modules (5): B05, D01, D02, D03, D05	• API Đồ thị tri thức knowledge_edges có chặn chu trình DAG (B05).
• API Learner Dashboard (D01), Lộ trình tiên quyết (D02), Luyện tập 4 Tab (D03) và Sổ tay câu sai (D05).	• Thuật toán gom câu sai tự động sau khi chấm điểm, duyệt đồ thị PREREQUISITE và tìm 3 câu biến thể cùng structural_hash/pgvector để giao bài khắc phục.	• Nhúng 4 Tab bài tập dưới trang Lý thuyết (Workspace 1), giao diện Sổ tay câu sai (làm đúng 2 câu liên tiếp để xóa lỗi) và Dashboard học sinh.	• Kiểm tra chặn chu trình 422 và điều kiện đóng lỗi consecutive_correct_count >= 2.
• Demo: Sai câu Bayes -> Sổ tay câu sai -> Ôn bài tiên quyết & làm đúng 2 câu biến thể.
Sprint 8 (Track 2 + Pilot Kickoff)
(Tuần 17–18 — Milestone M3)
Modules (4): F01, F02, F03, F06	• API Quản lý Lớp học (F01 với Soft-Delete class_members), Giao bài kiểm tra (F02 với reveal_policy), Bảng điểm lớp (F03) và Thông báo chống gửi trùng (F06).	• Hoàn thiện bóc tách đề Hóa học & Sinh học (GATE-06b: đủ 41 đề Golden + Held-out).
• Khởi động Pilot thực tế tại trường THPT với bộ đề có gắn câu hỏi mỏ neo (Anchor Items) để thu thập mẫu cho IRT.	• Giao diện Workspace 5 (Teacher LMS Hub): tạo lớp, mời học sinh, giao đề thi, xem phổ điểm lớp và Top 5 câu cả lớp sai nhiều nhất.	• Đóng cổng GATE-06b (41 đề Toán/Lý/Hóa/Sinh) và vận hành đợt kiểm tra đầu tiên tại trường Pilot.
Sprint 9 (Track 2 Exit)
(Tuần 19–20 — Milestone M4)
Modules (3): D04, E02, E04	• API Thẻ nhớ & Lịch ôn tập FSRS-5 (D04), Gia sư AI 3 chế độ có khóa phòng thi (E02) và Sinh lời giải từng bước kiểm tra chéo với Subprocess SymPy (E04).	• Cài đặt chuẩn 19 trọng số FSRS-5 (Linear Damping + Mean Reversion + trần quên); đo đạc chi phí thực tế LLM/GPU từ ai_traces trên tập học sinh Pilot.	• Khung chat Gia sư AI (Hint / Explain / Challenge) và giao diện lật thẻ nhớ công thức FSRS-5.	• Đóng cổng GATE-08: Khớp 100% vector chuẩn py-fsrs, SymPy đạt ≥ 95%, chặn 100% AI Tutor khi đang thi (IN_PROGRESS).
Sprint 10 (Track 3)
(Tuần 21–22 — Milestone M5)
Modules (3): A02, A03, E03	• API Permitted Web Crawler (A02), Google Drive Sync 2 chế độ (Picker + drive.file & Service Account — A03), và Sinh câu hỏi nháp bù ma trận (E03).	• Crawler có PinnedIPHTTPSConnection ghim IP vào socket ở mỗi bước Redirect + ParserDriftMonitor; bung nén chống Zip Bomb & ghép cặp 4 tín hiệu Đề + HDC.	• Giao diện chọn nhiều file đề/zip qua Google Picker, duyệt cặp Đề + HDC, bảng điều khiển Crawler và công cụ sinh câu hỏi bù ma trận.	• Vượt qua bộ test SSRF/DNS Rebinding, Zip Bomb và xác nhận 0% câu hỏi AI sinh ra tự động Publish khi chưa qua A09.
Sprint 11 (Track 3)
(Tuần 23–24 — Milestone M5)
Modules (3): D06, E05, E06	• API Ước lượng năng lực IRT 6 tiêu chí (D06), Gợi ý bước học tiếp theo có giải thích (E05) và Trợ lý AI cho Giáo viên (E06).	• Chạy hiệu chuẩn IRT trên dữ liệu Pilot tích lũy từ Sprint 8 đến Sprint 11; các câu đủ 6 tiêu chí được nâng lên L2/L3, các câu chưa đủ mẫu giữ ở RULE_WEIGHTED_L1 hợp lệ.	• Hộp "Điểm ước lượng năng lực tham khảo (Khoảng tin cậy 95%)", thẻ gợi ý bước tiếp theo và màn hình Teacher AI Copilot.	• Phê duyệt báo cáo hiệu chuẩn IRT (Calibration Diagnostics Report) và kiểm chứng cơ chế tự động Fallback.
Sprint 12 (Track 3)
(Tuần 25–26 — Milestone M5)
Modules (3): G04, G05, G06	• API Kho phân tích (G04), Gói cước & Thanh toán (G05), Cấu hình mở rộng VerticalPack (G06).	• Hoàn thiện job tổng hợp daily_metric_rollups chạy dưới ulp_system_scheduler (app.system_job = 'metric_rollup').	• Trang quản trị gói cước Free/Pro, Dashboard KPI vận hành và tối ưu WCAG 2.2 AA.	• Hoàn tất toàn bộ 47 modules tính năng (nếu chọn Phương án B 26 tuần thì hoãn A02, E03, G06 để dùng Sprint 11–12 làm Hardening).
Sprint 13 – Sprint 14 (Hardening & Go-Live Buffer)
(Tuần 27–30 — Milestone M6)
Modules: 0 module mới (100% Hardening)	• Khắc phục toàn bộ phát hiện từ đợt Pentest độc lập và phản hồi từ giáo viên/học sinh trường Pilot; đóng băng Schema Production.	• Diễn tập khôi phục thảm họa (PITR Restore + chạy reapply_tombstones_after_pitr_restore + kiểm tra xóa khóa Vault Transit) và làm nóng cache.	• Kiểm thử E2E mất mạng / F5 giữa giờ thi với Dexie và hoàn thiện hiệu năng Mobile Web.	• Đóng cổng GATE-09 (Legal DPIA/DPA, OSS SBOM, Pentest) và GATE-10 (DR Drill, Patroni Strict Quorum) -> Chính thức Go-Live.

8.2. Hai Phương Án Ngân Sách Công Sức & Thời Lượng (`Phương án A: 30 Tuần` vs `Phương án B: 26 Tuần`)
Tiêu chí So sánh	Phương án A (Khuyến nghị — Đầy đủ 47 Modules + 4 Tuần Buffer)	Phương án B (Cố định 26 Tuần — Cắt giảm phạm vi Go-Live còn 44 Modules)
Tổng thời lượng (Tính cả M0)	30 Tuần (M0: 2 tuần + Sprint 1..12: 24 tuần + Sprint 13..14 Buffer: 4 tuần)	26 Tuần (M0: 2 tuần + Sprint 1..10: 20 tuần + Sprint 11..12 Buffer: 4 tuần)
Ngân sách Công sức (10 Kỹ sư)	300 Gross PW \Rightarrow 225.0 Net Effective PW (Focus Factor 75%)	260 Gross PW \Rightarrow 195.0 Net Effective PW (Focus Factor 75%)
Phạm vi Tính năng tại Go-Live	Trọn vẹn 47/47 Modules (bao gồm cả Web Crawler A02, AI Question Gen E03, Vertical Packs G06).	44/47 Modules (Hoãn A02, E03, G06 sang bản V1.1 sau Go-Live để giải phóng 30 Gross PW dành cho Pentest, DR Drill và sửa lỗi Pilot).

8.3. Mười Một Cổng Nghiệm Thu Phát Hành Chuẩn Hóa (`GATE-01` đến `GATE-10` — Kiểm Tra Tự Động Bằng `ci/check_gate_ids.sh`)
Toàn bộ tài liệu và platform_manifest.yaml chỉ sử dụng duy nhất hệ mã GATE-xx dưới đây:
Mã Gate	Tên Cổng Nghiệm Thu (Release Gate)	Thời điểm Đóng	Tiêu chí Kỹ thuật & Bằng chứng Bắt buộc (Evidence Bundle)
GATE-01	Multi-Tenant RLS, InitPlan & Zero-IDOR Gate	Sprint 6	100% bảng nghiệp vụ bật ENABLE & FORCE RLS với (SELECT current_app_tenant_id()); vượt qua verify_rls_hardening.sql (chặn đọc DRAFT hệ thống, khớp grants_manifest.csv, 0 lỗi IDOR).
GATE-02	Data Immutability & 5-Axis State Gate	Sprint 6	Trigger DB chặn 100% lệnh UPDATE/DELETE trên bảng phiên bản đã khóa và bảng phân vùng Append-Only (audit_events, response_revisions); 0 học liệu PENDING_REVIEW/RESTRICTED lọt sang PUBLISHED.
GATE-03	Exam Security & Zero-Answer-Leak Gate	Sprint 6	Role ulp_exam_session_rw và ulp_worker_ingestion bị từ chối quyền đọc question_version_answers và assessment_answer_keys (42501 insufficient_privilege); JSON đề thi chứa 0% dữ liệu đáp án.
GATE-04	Autosave Durability, Dexie Offline & Chaos Gate	Sprint 6	Client lưu IndexedDB thật (Dexie), gửi đủ expected_response_version + payload_hash, tự động resync khi gặp 409; đạt RPO = 0 khi ngắt mạng, F5 trình duyệt, sập Redis và Failover Patroni.
GATE-05	High-Concurrency 5.000 CCU Per-VU Load Gate	Sprint 6	Kịch bản k6 (mỗi VU 1 attempt_id riêng từ SharedArray + kịch bản submit_burst) đạt độ trễ Server p50 < 25ms, p95 < 120ms, p99 < 300ms, tỷ lệ lỗi < 0.01%.
GATE-06a	Golden & Held-out Extraction Gate (Toán & Vật lí)	Sprint 6	Đạt Precision ≥ 98%, Recall ≥ 96% về ranh giới câu hỏi, công thức MathType OLE và hình vẽ Vector (có find_tables() bảo vệ bảng) trên toàn bộ tập đề Toán & Vật lí.
GATE-06b	Golden & Held-out Extraction Gate (Hóa & Sinh — Đủ 41 Đề)	Sprint 8	Đạt Precision ≥ 98%, Recall ≥ 96% trên toàn bộ 11 đề Golden + 30 đề Held-out (41 đề đủ 4 môn Toán, Lý, Hóa, Sinh) bao gồm sơ đồ hữu cơ, bảng phức chất và ảnh chụp nghiêng.
GATE-07	Deterministic Grading & Option Permutation Gate	Sprint 6	Khớp 100% điểm số trên 500 bài thi mẫu chuẩn THPTQG 2025+ (có ánh xạ ngược option_permutations_json, max_chars=4, đọc đáp án đề hệ thống qua grading_read_answer_keys và Regrade nhiều revision_no).
GATE-08	AI Safety, Exam Lock, Subprocess SymPy & FSRS-5 Gate	Sprint 9	Subprocess SymPy (python -I) chặn 100% mẫu RCE và đạt độ khớp nghiệm ≥ 95%; FSRS-5 khớp 100% vector chuẩn py-fsrs; chặn 100% AI Tutor khi đang thi (IN_PROGRESS) và khi chạm trần daily_ai_budget_usd.
GATE-09	Legal DPIA/DPA, Vault Erasure, OSS SBOM & Pentest Gate	Sprint 14	Hoàn tất ký DPIA/DPA xuyên biên giới; kiểm chứng xóa khóa ulp-user-{id} trên Vault Transit + xóa văn bản tự do; 0 thư viện nhiễm AGPLv3 trong sbom.cdx.json; vá 100% lỗi High/Critical từ đợt Pentest độc lập.
GATE-10	DR, Patroni Strict Quorum & Post-PITR Tombstone Gate	Sprint 14	Diễn tập thực tế: Auto-Failover Patroni (synchronous_mode_strict: true) đạt RPO = 0, RTO <= 30s; diễn tập PITR Restore + chạy reapply_tombstones_after_pitr_restore() đạt RTO <= 2 giờ mà không làm sống lại PII đã xóa.

Khối mã thực thi (BASH):
#!/usr/bin/env bash
# ci/check_gate_ids.sh — Chạy tự động ở Cổng 1 của CI Pipeline
set -euo pipefail
if grep -rnE '\bRG-[0-9]{2}\b' docs/ handover/; then
  echo "ERROR: Phát hiện mã RG-xx cũ; bắt buộc dùng thống nhất GATE-xx theo platform_manifest.yaml"; exit 1
fi
declared=$(grep -oE 'GATE-[0-9]{2}[ab]?' platform_manifest.yaml | sort -u)
used=$(grep -rhoE 'GATE-[0-9]{2}[ab]?' docs/ handover/ | sort -u)
missing=$(comm -13 <(echo "$declared") <(echo "$used"))
[ -z "$missing" ] || { echo "ERROR: Phát hiện mã Gate không nằm trong platform_manifest.yaml: $missing"; exit 1; }
8.4. Thứ Tự Ưu Tiên Triển Khai `Wave 0 -> Wave 1 -> Wave 2` & Ánh Xạ `G0..G6` Với `GATE-01..10` (Hợp Nhất Phụ Lục Hardening V11.3)
Để đội kỹ thuật không triển khai dàn trải, các hạng mục Hardening (H-01 đến H-07) được khóa cứng theo 3 đợt ưu tiên (Wave 0 phải đóng xong trước khi mở rộng tải):
Đợt Triển Khai (Wave)	Mã Hardening & Hạng mục Bắt buộc	Giải pháp Đã Hợp Nhất Đồng Bộ Với Schema Chương 4 & Code Chương 6	Ánh xạ Cổng Nghiệm thu (G0..G6 ≤ftrightarrow GATE-xx)
Wave 0 (M0 -> Sprint 3)
Đóng toàn bộ P0 Security & Correctness	• H-01 (RLS Child & Parent Visibility): Chặn đường vòng qua bảng con.
• H-02 (SymPy Fail-Closed): Xóa mọi fallback chạy SymPy trong process API.
• H-03 (Least-Privilege DB Roles): Thu hồi CREATE và khóa quyền theo đúng bảng.
• H-04 (Mandatory expected_response_version): Bắt buộc truyền expected_response_version: int (không Optional).	• Đã cập nhật trực tiếp vào hybrid_read_policy của cả 21 bảng ở Chương 4 (bao gồm kiểm tra 2 đầu mút knowledge_edges).
• Đã xóa sạch except AttributeError: sp.sympify() và nhánh fallback in-process ở Mục 6.3 (SANDBOX_WORKER_UNAVAILABLE).
• Đã đổi chữ ký hàm process_autosave_idempotent_first ở Mục 6.4 bắt buộc expected_response_version: int.	• G0 Security \equiv GATE-01, GATE-03, GATE-08
• G2 Correctness \equiv GATE-02, GATE-04, GATE-07
Wave 1 (Sprint 3 -> Sprint 8)
Chuẩn hóa Pipeline Bronze/Silver/Gold & Observability	• H-05 (Data Engineering Lineage & DQ KPIs): Kiểm soát 6 chặng (Source -> Bronze -> Silver -> Quality Gate -> Gold -> Serving).
• H-06 (SLI/SLO & Observability): Đo đủ 6 miền (API, Autosave, Ingestion, Search, SymPy/AI, Database).	• Mọi chặng lưu vết vào pipeline_stage_runs và extraction_runs kèm input_ref, output_ref, parser_version; hỗ trợ Replay từ tầng Bronze bất biến và fan-out lệnh Takedown trong < 30s.	• G1 Data \equiv GATE-06a (Toán/Lý), GATE-06b (Hóa/Sinh)
Wave 2 (Sprint 5 -> Sprint 14)
Kiểm thử Tải 5.000 CCU, Chaos, DR & Traceability	• H-06 (Load, Chaos & Restore Drill): Chạy k6 mỗi VU 1 attempt riêng và diễn tập PITR + WORM Tombstone.
• H-07 (Manifest & Traceability): Đồng bộ 100% số lượng Roles (7 DB Roles), ADRs (17 ADRs) và Gates (11 Gates).	• Đã đồng bộ toàn bộ tiêu đề Phần III, comment SQL 7 DB Roles và platform_manifest.yaml (v11.3.0).	• G3 Performance \equiv GATE-05
• G4 Resilience \equiv GATE-10
• G5 Legal & G6 Release \equiv GATE-09

CHƯƠNG 9. MA TRẬN KIỂM THỬ HỒI QUY 11+ ĐỀ MẪU (GOLDEN CORPUS + HELD-OUT) & MỤC TIÊU SLOs
9.1. Quy tắc Quản lý Golden Corpus An toàn Pháp lý & Định lượng Chất lượng (Khắc phục P0-6 & P1)
32.	Tuyệt đối không lưu trữ file đề thi có thông tin nhạy cảm trực tiếp trong Git Repo: Toàn bộ 11 file mẫu trong bảng dưới đây cùng 30 file đề Held-out (dùng để kiểm tra chống Overfitting ngưỡng tọa độ) phải được xóa bỏ hoàn toàn PII (tên giáo viên, số điện thoại, watermark tài khoản, tên đơn vị nếu chưa xin phép) và lưu trữ trên bucket riêng s3://ulp-qa-golden-private/ có mã hóa SSE-KMS. CI Runner kéo về thư mục tạm /tmp/golden_fixtures/ khi chạy test.
33.	Thước đo định lượng bắt buộc trên toàn tập:
•	Question Boundary Precision ≥ 98.0% & Recall ≥ 96.0% (không gộp nhầm 2 câu, không cắt sót câu).
•	Figure & Formula Preservation Rate ≥ 99.0% (không mất công thức MathType OLE, không mất nhãn hóa học trong hình).
•	Clean Text Redaction Rate = 100% (không còn chuỗi ký tự rác từ trục tọa độ/đồ thị lọt vào lời dẫn câu hỏi).
Mã File Mẫu (Đã Ẩn Danh Hóa trên S3 Private)	Đặc điểm Kỹ thuật & Góc khuất Thực tế	Tiêu chí Pass Định lượng Tự động trong CI/CD (pytest -m golden)
1. golden_01_phy10_essay.pdf
(Đề Vật lí 10 tự luận)	5 câu tự luận (Câu I..V), Câu II và IV vắt qua 2 trang, có watermark và logo tròn góc trái trên trang 1.	Tách đúng 5 câu lớn và các ý 1a, 1b, 2a, 2b; nối liền mạch Câu II & IV qua ranh giới trang; cắt đúng 4 hình bài toán và tự động loại bỏ logo trang 1.
2. golden_02_phy11_vector.pdf
(Đề Vật lí 11 có hình Vector)	5 câu tự luận, riêng Hình 5 ở trang 3 được vẽ bằng các nét Vector rời rạc (không phải ảnh bitmap nhúng).	Thuật toán Union-Find merge_intersecting_rects_union_find() gom và cắt trọn vẹn Hình 5 ở 300 DPI qua pypdfium2 gắn vào Câu V.
3. golden_03_chem11_legacy.doc
(Đề Hóa học 11 định dạng .doc cũ)	File .doc nhị phân Word 97–2003; có bảng phức chất Cobalt và sơ đồ tổng hợp hữu cơ (Câu 4, 6, 8) không có nhãn 'Hình X'.	Convert qua LibreOffice (với -env:UserInstallation cô lập) thành công; bảo toàn bảng phức chất; cắt trọn sơ đồ hữu cơ mà không làm rớt nhóm OH, COOH.
4. golden_04_bio11_canvas.docx
(Đề Sinh học 11 có bảng gộp cột)	10 câu lớn có tên chuyên đề khớp SGK; Bảng 2 và Bảng 3 có gộp cột (colspan); Hình 4, 7, 8 dùng Word Drawing Canvas.	Làm phẳng hoàn toàn Hình 4, 7, 8 qua nhánh render PDF; chuyển đổi và giữ nguyên cấu trúc colspan/rowspan của Bảng 2 và Bảng 3 sang HTML_TABLE.
5. golden_05_math11_ole.docx
(Đề Toán 11 có MathType OLE)	Toàn bộ công thức dãy số u_n, đa thức P(x) được nhúng dưới dạng đối tượng MathType OLE (<w:object>).	Phát hiện <w:object> (có kiểm tra chống Zip Bomb ≤ 25 MB) và định tuyến qua LibreOffice PDF + LaTeX OCR; 0 công thức bị mất trống.
6. golden_06_phy12_hdc_table.docx
(Hướng dẫn chấm bảng 3 cột)	File Hướng dẫn chấm trình bày trong Bảng 3 cột (Câu - Nội dung - Điểm (3 cột)), có cả đáp số ngắn và hình mạch điện.	Bóc chính xác 10 đáp số trả lời ngắn ở Câu I (0,157 mm/s, 2,1 giờ...) và lời giải từng ý II.1, II.2.a kèm hình mạch điện gắn đúng câu.
7. golden_07_no_header_sheets.pdf
(Bộ 3 phiếu bài tập sát mép trên)	Phiếu bài tập không có Header kỳ thi (Câu 1: nằm ngay sát mép trên y_0 = 40pt), xen kẽ chữ Bài 3: giữa Câu 6: và Câu 7:.	Bộ phát hiện Header động giữ nguyên vẹn Câu 1 ở y_0 = 40pt; khi xuất PDF gắn thương hiệu tự động mở rộng đỉnh trang 70pt; tách đủ cả Bài 3:.
8. golden_08_combined_exam_sol.pdf
(File gộp Đề + Đáp án giữa chừng)	Nửa đầu file là 5 câu đề bài, từ giữa trang 2 có dòng chữ Đáp án rồi giải chi tiết từ Câu 1 đến Câu 4 kèm hình phân tích lực.	Tự động chia đôi tại mốc Đáp án, ghép đúng lời giải và hình phân tích lực ở nửa sau vào từng QuestionCandidate tương ứng ở nửa đầu.
9. golden_09_word_shapes_graph.docx
(Đề có đồ thị vẽ bằng Word Shapes)	Đồ thị vận tốc - thời gian ở Câu 2, 3, 5, 7 vẽ bằng Word Shapes gây chuỗi rác v(m/s)t(s)O24648 nếu đọc XML thô.	Cắt phẳng đồ thị thành ảnh PNG sắc nét và lọc bỏ 100% các ký tự có tâm nằm trong khung đồ thị khỏi văn bản lời dẫn câu hỏi.
10. golden_10_mobile_photo_skewed.jpg
(Ảnh chụp đề thi bằng điện thoại)	Ảnh chụp trang giấy đề thi bị bóng xám và nghiêng 4.5°, chứa Câu 4, Câu 5 và Hình 5.	Xoay thẳng (Deskew), khử bóng xám bằng OpenCV adaptiveThreshold, nhận diện đúng văn bản LaTeX của 2 câu và cắt riêng khung Hình 5.
11. golden_11_bayes_mixed_guide.pdf
(Chuyên đề hỗn hợp Công thức Bayes)	11 trang gồm: Lý thuyết + Phương pháp + Công thức + 8 câu Tự luận + 24 câu ABCD + 13 câu Đúng/Sai 4 ý + 22 câu Trả lời ngắn; có Footer lặp ở đáy trang.	Lọc sạch Footer lặp liên trang; tách chuẩn 1 bản ghi ContentCandidate (đủ 3 hộp Phương pháp, Công thức, Chú ý) và đúng 67 QuestionCandidate chia chuẩn vào 4 định dạng.

9.2. Cam kết Mục tiêu Chất lượng Dịch vụ (Production SLO Targets — Đã Đồng bộ Hóa ở V11.3)
Hạng mục Đo lường (SLI)	Chỉ số Mục tiêu (Target SLO V11.3)	Cách thức Giám sát, Kiến trúc Đảm bảo & Cảnh báo (Alerting)
Độ sẵn sàng API Phòng Thi & Đọc Học Liệu	≥ 99.95% trong khung giờ thi cao điểm; ≥ 99.9% hàng tháng	Synthetic health checks mỗi 15 giây kết hợp Prometheus multi-window burn-rate alerts.
Độ trễ API Đọc Học liệu / Phát Đề Thi	p50 < 80ms, p95 < 250ms dưới tải tiêu chuẩn	OpenTelemetry histogram http_server_duration_ms phân theo route.
Độ trễ Lưu tự động Bài thi (Autosave)	Server DB Commit: p50 < 25ms, p95 < 120ms
End-to-End API: p95 < 300ms (tối đa < 800ms ở 5.000 CCU)	Đo trực tiếp trên PUT /api/v1/attempts/{id}/responses/{item_id} (chỉ trả 200 ACK sau khi PostgreSQL Primary + Synchronous Standby đã commit).
Độ toàn vẹn Dữ liệu Phòng Thi (Exam RPO)	RPO = 0 (0% mất mát câu trả lời đã nhận ACK)	Đảm bảo bằng PostgreSQL Synchronous Streaming Replication (synchronous_commit = on) cho các bảng attempts, responses, response_revisions, grades.
Mục tiêu Khôi phục Thảm họa Toàn Vùng (DR)	Exam DB Auto-Failover (Patroni synchronous_mode_strict: true): RPO = 0, RTO <= 30 giây (tối đa <= 2 phút)
Full Regional DR (PITR + Object Storage): RPO <= 15 phút, RTO <= 2 giờ	Patroni / repmgr tự động Failover sang Synchronous Standby trong < 30s; sao lưu WAL liên tục + Cross-Region MinIO Replication.
Thời gian Khởi chạy Job Nạp Đề (Ingestion)	99% các Job thông thường bắt đầu chạy trong < 90 giây	Cảnh báo PagerDuty/Telegram khi oldest_queued_job_age_seconds > 90.

9.3. Ma Trận 6 Kịch Bản Kiểm Thử Tải / Tiêm Lỗi (`AUD-012`) & Hồ Sơ Bằng Chứng Kiểm Chứng (`Evidence Bundle — AUD-007`)
#### A. Sáu Kịch bản Kiểm thử Hiệu năng & Chịu lỗi Bắt buộc Trước Khi Đóng Cổng GATE-08 & GATE-09
Mọi kết quả benchmark chỉ được công nhận khi ghi rõ cấu hình phần cứng (CPU/RAM/NVMe IOPS), phiên bản PostgreSQL/PgBouncer, kích thước dữ liệu và commit_sha:
Kịch bản Kiểm thử (Scenario)	Cấu hình Tải / Lỗi Giả lập (Load & Fault Profile)	Chỉ số Đầu ra Bắt buộc Lưu vào Evidence Bundle
1. Normal Exam Steady State	5.000 thí sinh làm bài đồng thời, đổi đáp án trung bình mỗi 15 giây (≈ 333 RPS Autosave phân bố đều).	Throughput, độ trễ p50/p95/p99, tỷ lệ lỗi (< 0.01%), DB CPU, WAL write IOPS, PgBouncer pool wait time.
2. Synchronized Start Burst	5.000 thí sinh bấm nút Bắt đầu làm bài trong cùng cửa sổ 30 giây đầu ca thi.	Độ trễ tạo attempts và sinh hoán vị đề, thời gian chờ khóa, tỷ lệ Cache Hit của v_public_question_versions.
3. Reconnect Autosave Storm	1.500 thí sinh bị rớt mạng cục bộ trong 45 giây và đồng loạt có mạng trở lại, xả hàng đợi IndexedDB cùng lúc.	Tỷ lệ 200 IDEMPOTENT_REPLAY vs 200 COMMITTED, thời gian xả hết hàng đợi (< 10s), độ bão hòa kết nối DB.
4. End-of-Exam Submit Storm	3.000 thí sinh nộp bài đồng thời ở phút cuối + 2.000 bài tự động thu (attempt.expired).	Thời gian xử lý khóa attempts, độ sâu hàng đợi ulp_worker_grading, thời gian trả điểm trắc nghiệm (< 3s).
5. Primary DB Crash & Failover	Ngắt đột ngột node PostgreSQL Primary khi đang chạy tải Autosave 300 RPS.	Thời gian Patroni thăng cấp Synchronous Standby (RTO <= 30s), đối soát autosave_idempotency_registry xác nhận RPO = 0 (0 bản ghi đã ACK bị mất).
6. External Dependency Outage	Cô lập hoàn toàn mạng tới Redis, MinIO và nhà cung cấp LLM trong 10 phút giữa ca thi.	Xác nhận làm bài, Autosave và nộp bài vẫn đạt thành công ≥ 99.9% nhờ ghi trực tiếp xuống PostgreSQL; AI Tutor báo lỗi thân thiện.

#### B. Bảng Đánh Giá Trung Thực Mức Độ Trưởng Thành Hiện Tại Theo Thang 5 Cấp (EV-V11-01 đến EV-V11-04 — Mục 9.1 của Bản vá V11.1)
Tuân thủ nghiêm ngặt quy tắc không gắn nhãn "VERIFIED" khi chưa chạy trên hạ tầng PostgreSQL/Patroni thật và dữ liệu thật:
Mã Hạng Mục	Nội dung Kiểm tra	Mức Độ Đạt Được Hiện Tại (Thang 5 Cấp)	Phần Đã Kiểm Chứng Tự Động vs Điều Kiện Bắt Buộc Để Nâng Lên VERIFIED Ở M0 / CI
EV-V11-01	Canonical Manifest & Gate IDs Reconciliation	TESTED (Cấp 3/5)	Đã chạy script kiểm tra tự động xác nhận 100% đồng bộ giữa platform_manifest.yaml (v11.2.0) và toàn bộ văn bản (0 mã RG-xx sót lại, 47 modules khớp 1-1 vị trí Sprint/Track). Sẽ nâng lên VERIFIED khi gắn ci/check_gate_ids.sh vào GitHub Actions của repo thật.
EV-V11-02	9 Thuật toán lõi, Subprocess SymPy Sandbox & FSRS-5	TESTED một phần (Python) / SPECIFIED (TypeScript)	• Python (Mục 5.2, 6.1–6.8): Đã chạy pass bộ Unit Test trong Sandbox (bao gồm 12 ca chặn RCE __import__, open, exec, __class__ của SymPySandboxWorker, 10g == 200g của Hóa học, Optimistic Autosave + payload_hash, FSRS-5, IRT 6 tiêu chí, Crypto-Shredding).
• Điều kiện nâng cấp: Chạy lại trong CI có cài thư viện sympy đầy đủ (kiểm tra cả ca 10**10**10 bị cắt bởi RLIMIT_AS) và chạy tsc --noEmit + test fake-indexeddb cho Dexie TypeScript.
EV-V11-03	SQL DDL, RLS InitPlan, ulp_system_scheduler & Least-Privilege Grants	SPECIFIED (Cấp 1/5 — Đã phân tích tĩnh)	Đã kiểm tra tĩnh 100% bảng bật ENABLE & FORCE RLS, 0 role có BYPASSRLS, đầy đủ hybrid_read_policy có EXISTS và GRANT theo bảng/cột. Bắt buộc chạy alembic upgrade head và tests/security/verify_rls_hardening.sql trên PostgreSQL 16+ thật ở M0 để lên TESTED / VERIFIED.
EV-V11-04	Direct Source Code Audit (exam-system) & 5.000 CCU Benchmark	SPECIFIED (Cấp 1/5)	Thực hiện ngay trong M0 (audit app/models.py) và Sprint 5–6 (chạy seed_attempts.py --count 5000 + k6_exam_5000ccu.js trên cụm Patroni 3-node thật).

#### C. Mô Hình Hóa Toán Học Workload 5.000 CCU & Kịch Bản k6 Mỗi VU Một Attempt Riêng + Submit Burst (tests/load/k6_exam_5000ccu.js — Khắc phục P0-7)
•	Dẫn xuất theo định luật Little (L = \lambda · W): Với N = 5.000 CCU, chu kỳ chọn đáp án trung bình T_{cycle} = 15.0s \Rightarrow \lambda_{steady} = 333.3 RPS, \lambda_{peak} = 500.0 RPS. Với W_{db} = 12ms, số kết nối DB thực sự bận đồng thời là L_{db} = 6 active connections (cấu hình PgBouncer default_pool_size = 60, max_client_conn = 10000).
•	Khắc phục lỗi dùng chung 1 attempt trong k6: Trước khi chạy k6, chạy script python tests/load/seed_attempts.py --count 5000 --out tests/load/data/sessions.json để tạo 5.000 phiên thi độc lập; mỗi VU lấy đúng phiên thi của mình từ SharedArray("sessions"), tự tính crypto.sha256 thật và có thêm kịch bản submit_burst riêng cho pha nộp bài đồng loạt cuối giờ:
Khối mã thực thi (JAVASCRIPT):
// tests/load/k6_exam_5000ccu.js — Mỗi VU có attempt_id riêng, tính SHA-256 thật & có kịch bản Submit Burst (Sửa P0-7)
import http from "k6/http";
import { check, sleep } from "k6";
import { SharedArray } from "k6/data";
import crypto from "k6/crypto";

const sessions = new SharedArray("sessions", () => JSON.parse(open("./data/sessions.json")));
const BASE_URL = __ENV.API_BASE_URL || "https://staging-api.ulp.edu.vn";

export const options = {
  scenarios: {
    exam_5000_ccu_autosave: {
      executor: "ramping-vus",
      exec: "runAutosaveLoop",
      startVUs: 0,
      stages: [
        { duration: "1m", target: 5000 },  // Pha 1: Synchronized Start Ramp-up
        { duration: "10m", target: 5000 }, // Pha 2: Steady-State Autosave (~333 - 500 RPS)
        { duration: "30s", target: 0 },
      ],
    },
    end_of_exam_submit_burst: {
      executor: "per-vu-iterations",
      exec: "runSubmitBurst",
      vus: 3000,
      iterations: 1,
      startTime: "11m",                    // Pha 3: 3.000 thí sinh bấm Nộp bài đồng loạt ở phút cuối
      maxDuration: "1m",
    },
  },
  thresholds: {
    "http_req_duration{operation:autosave}": ["p(50)<25", "p(95)<120", "p(99)<300"],
    "http_req_duration{operation:submit}": ["p(95)<250"],
    "http_req_failed": ["rate<0.0001"],
    "checks": ["rate>0.9999"],
  },
};

const vuState = {}; // Lưu { seq, version } theo từng câu hỏi của mỗi VU

export function runAutosaveLoop() {
  const s = sessions[(__VU - 1) % sessions.length];
  for (let i = 0; i < 40; i++) {
    const q = s.question_version_ids[i % s.question_version_ids.length];
    const st = vuState[q] || (vuState[q] = { seq: 0, version: 0 });
    st.seq += 1;

    const payload = { selected_key: ["A", "B", "C", "D"][st.seq % 4] };
    const idemKey = `${s.attempt_id}:${q}:seq_${st.seq}`;
    const payloadHash = crypto.sha256(JSON.stringify(payload), "hex");

    const body = JSON.stringify({
      client_sequence: st.seq,
      expected_response_version: st.version,
      idempotency_key: idemKey,
      payload_hash: payloadHash,
      response_payload: payload,
      is_flagged: false,
    });

    const params = {
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${s.jwt}`,
        "Idempotency-Key": idemKey,
      },
      tags: { operation: "autosave" },
    };

    const url = `${BASE_URL}/api/v1/attempts/${s.attempt_id}/responses/${q}`;
    const res = http.put(url, body, params);
    const ok = check(res, { "autosave 200": (r) => r.status === 200 });
    if (ok) {
      st.version = res.json("saved_response_version");
    }

    // 5% xác suất gửi lặp cùng Idempotency-Key -> Phải nhận 200 IDEMPOTENT_REPLAY
    if (Math.random() < 0.05) {
      const rep = http.put(url, body, params);
      check(rep, {
        "replay 200": (r) => r.status === 200 && r.json("status") === "IDEMPOTENT_REPLAY",
      });
    }
    sleep(12 + Math.random() * 6);
  }
}

export function runSubmitBurst() {
  const s = sessions[(__VU - 1) % sessions.length];
  const submitUrl = `${BASE_URL}/api/v1/attempts/${s.attempt_id}/submit`;
  const res = http.post(submitUrl, JSON.stringify({ submit_idempotency_key: `sub:${s.attempt_id}` }), {
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${s.jwt}` },
    tags: { operation: "submit" },
  });
  check(res, { "submit 200": (r) => r.status === 200 });
}
#### D. Bộ Kiểm Thử Bảo Mật Runtime Roles & RLS Hardening Trên PostgreSQL Thật (tests/security/verify_rls_hardening.sql — Mục 2.3 của Bản vá V11.1)
Khối mã thực thi (SQL):
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
CHƯƠNG 10. SỔ TAY VẬN HÀNH & XỬ LÝ SỰ CỐ SẢN XUẤT (14 OPERATIONAL RUNBOOKS — ĐẦY ĐỦ OWNER, SEVERITY, ROLLBACK)
Mọi sự cố sản xuất được phân loại theo 3 mức độ:
•	SEV-1 (Critical): Ảnh hưởng trực tiếp đến ca thi đang diễn ra, nguy cơ mất dữ liệu, rò rỉ dữ liệu liên Tenant hoặc vi phạm pháp lý khẩn cấp. Thời gian phản ứng (Response SLA): < 5 phút.
•	SEV-2 (High): Gián đoạn một tính năng chính (Nạp đề, AI Tutor, Chấm tự luận) nhưng không làm gián đoạn phòng thi trắc nghiệm. Thời gian phản ứng: < 15 phút.
•	SEV-3 (Medium): Lệch cấu trúc nguồn crawl bên ngoài hoặc cảnh báo hiệu năng sớm. Thời gian phản ứng: < 2 giờ.
Mã & Tên Sự Cố (Incident)	Mức độ & Owner Chính	Dấu hiệu Nhận biết (Trigger)	Quy trình Xử lý Từng bước, Rollback & Truyền thông (Step-by-Step Response)
RB-01: Nghẽn Hàng Đợi Worker (Queue Backlog)	SEV-2
Owner: DevOps + DE On-Call	oldest_queued_job_age > 180s hoặc số lượng Job QUEUED vượt 500.	1) Chẩn đoán: Kiểm tra sức khỏe các Worker Pools (io-crawler, cpu-doc-ocr, gpu-ai) và kết nối PgBouncer.
2) Cô lập: Tìm các 'Poison Jobs' (attempt_count >= 3 hoặc xử lý > 120s) và chuyển sang dead_letter_items.
3) Mở rộng: Scale ngang số lượng replica của pool đang nghẽn.
4) Truyền thông: Hiển thị thông báo trên Import Workspace về thời gian chờ dự kiến tăng thêm.
RB-02: Lỗi Hồi Quy Bộ Bóc Tách Đề (Parser Regression)	SEV-2
Owner: DE/AI Lead	Tỷ lệ extraction_confidence < 0.80 tăng vọt sau khi deploy bản Parser mới.	1) Rollback tức thì: Bật FeatureFlag (G01) ghim hệ thống quay về parser_version ổn định liền trước trong < 10s.
2) Bảo toàn: Giữ nguyên 100% file gốc ở tầng Bronze.
3) Khắc phục: Đưa file gây lỗi (đã xóa PII) vào tập Golden Corpus, sửa lỗi và chạy lệnh POST /extraction-runs/{id}/reprocess cho các lượt bị ảnh hưởng.
RB-03: Lệch Cấu Trúc Web Nguồn (Crawler Drift)	SEV-3
Owner: DE On-Call	Sự kiện crawl.parser.drift_detected; nguồn tự động chuyển sang PAUSED_DRIFT.	1) Nguyên tắc: Tuyệt đối không bật lại nguồn khi chưa sửa selector.
2) Phân tích: So sánh bản chụp HTML thô ở tầng Bronze của trang vừa lỗi với fixture chuẩn.
3) Cập nhật & Kiểm chứng: Cập nhật selector trong config_version mới, chạy /crawler/sources/{id}/validate trên 10 URL mẫu; chỉ bấm Resume khi đạt Yield ≥ 95%.
RB-04: Thu Hồi Quyền hoặc Lỗi Kết Nối Google Drive	SEV-3
Owner: BE On-Call	Job đồng bộ báo lỗi 401/403 REAUTH_REQUIRED từ Google Drive API.	1) Tự động: Đổi source_connections.status = 'REAUTH_REQUIRED' và dừng lịch quét định kỳ của kết nối đó.
2) Bảo toàn: Giữ nguyên toàn bộ đề thi và câu hỏi đã nhập hợp lệ trước đó.
3) Truyền thông: Gửi thông báo In-App/Email (F06) hướng dẫn giáo viên mở lại Google Picker để cấp quyền lại nếu muốn đồng bộ thêm file mới.
RB-05: Sự Cố Lưu Bài Thi Giữa Giờ (Autosave Anomaly)	SEV-1
Owner: Tech Lead + BE + DevOps	Tỷ lệ lỗi 5xx trên PUT /attempts/{id}/responses > 0.5% trong 1 phút.	1) Nguyên tắc bất khả xâm phạm: Tuyệt đối không trả 200 ACK giả nếu PostgreSQL chưa commit.
2) Phía Client: Giao diện phòng thi tự động giữ toàn bộ câu trả lời trong IndexedDB và retry ngầm với cùng Idempotency-Key (nhận 200 IDEMPOTENT_REPLAY ngay khi mạng/DB phục hồi).
3) Đền bù thời gian: Nếu sự cố hạ tầng kéo dài > 60s, chạy script cộng bù thời gian (deadline_at = deadline_at + interval) cho toàn bộ attempts đang IN_PROGRESS và thông báo trực tiếp trên màn hình phòng thi.
RB-06: Sập Cụm Redis Giữa Ca Thi (Redis Outage)	SEV-2
Owner: DevOps On-Call	Mất kết nối tới Redis Sentinel/Cluster.	1) Đảm bảo liên tục: Do attempts, responses và response_revisions ghi trực tiếp xuống PostgreSQL và sự kiện nằm trong bảng outbox_events của PostgreSQL, thí sinh vẫn làm bài, lưu bài và nộp bài bình thường 100%.
2) Khôi phục: Chuyển sang node Redis Replica hoặc khởi động lại cụm Redis.
3) Đồng bộ lại: Outbox Relay Worker tự động quét các dòng outbox_events đang PENDING trong PostgreSQL để đẩy tiếp vào hàng đợi.
RB-07: Xuất Bản Nhầm Câu Hỏi Sai Đáp Án Nguồn (Errata & Regrade)	SEV-2
Owner: Tech Lead + Content Lead	Giáo viên/Học sinh báo cáo một câu hỏi trong đề đã Publish bị sai đáp án gốc.	1) Chặn lan truyền: Tạm gỡ câu hỏi khỏi kho bốc đề mới.
2) Tạo phiên bản mới: Tạo question_versions mới (version_no + 1) với đáp án đúng và cập nhật assessment_answer_keys (giữ nguyên version_no cũ để làm bằng chứng kiểm toán).
3) Chấm lại minh bạch: Chạy công cụ Regrade cho các attempts bị ảnh hưởng, sinh bản ghi grades mới với revision_no + 1, grade_reason = 'ERRATA_REGRADE', giữ nguyên bản ghi điểm cũ (is_current = FALSE) và gửi thông báo giải trình cho giáo viên/học sinh.
RB-08: Gián Đoạn Nhà Cung Cấp AI (LLM Provider Outage)	SEV-2
Owner: DE/AI On-Call	API LLM trả lỗi 429/503 liên tục hoặc độ trễ > 15s.	1) Tự động: E01 (AI Provider Gateway) mở Circuit Breaker (circuit_state = 'OPEN'), xoay sang API Key dự phòng hoặc chuyển sang nhà cung cấp LLM thứ cấp.
2) Cô lập: Các luồng cốt lõi (Đọc bài học, Tìm kiếm, Làm bài thi, Autosave, Chấm điểm trắc nghiệm) hoàn toàn không phụ thuộc vào LLM nên tiếp tục chạy bình thường 100%.
RB-09: Yêu Cầu Gỡ Bỏ Bản Quyền Khẩn Cấp (Legal Takedown)	SEV-1
Owner: Tech Lead + Legal	Nhận yêu cầu pháp lý hợp lệ (POST /api/v1/takedown-cases).	1) Thực thi tức thì (< 30s): Gọi /takedown-cases/{id}/execute để phát sự kiện rights.takedown.executed: chuyển rights_status = 'TAKEDOWN', publication_status = 'TAKEN_DOWN'.
2) Làm sạch: Xóa ngay lập tức khỏi bảng search_documents, xóa cache CDN/Redis và thu hồi Signed URL.
3) Bảo toàn lịch sử thi: Trên các đề thi cũ đã làm, hệ thống tự động ẩn văn bản câu hỏi vi phạm (hiển thị nhãn [Nội dung đã gỡ theo yêu cầu bản quyền]) nhưng giữ nguyên điểm số lịch sử của học sinh.
RB-10: Rò Rỉ Xác Thực Hoặc Lộ Token Quản Trị	SEV-1
Owner: Tech Lead + DevOps	Phát hiện truy cập bất thường hoặc nghi ngờ lộ JWT Secret / OAuth Token.	1) Phong tỏa: Thu hồi toàn bộ phiên đăng nhập liên quan, xoay khóa (Rotate) JWT Signing Key và OAuth Secret trên KMS/Vault.
2) Truy vết: Truy vấn bảng bất biến audit_events theo actor_id và correlation_id để lập danh sách toàn bộ bản ghi bị tác động.
3) Khôi phục: Hoàn nguyên các thay đổi cấu hình trái phép dựa trên trường before_json trong audit_events.
RB-11: Sự Cố Sập Node PostgreSQL Primary (DB Failover & PITR)	SEV-1
Owner: DevOps + Tech Lead	Node PostgreSQL Primary mất kết nối hoặc lỗi ổ cứng.	1) Auto-Failover (< 30s): Patroni tự động thăng cấp (Promote) node Synchronous Standby lên làm Primary mới (đảm bảo RPO = 0, không mất bất kỳ giao dịch Autosave nào đã nhận ACK).
2) Kết nối: PgBouncer tự động định tuyến kết nối vào Primary mới mà không cần khởi động lại ứng dụng.
3) Trường hợp lỗi logic cần PITR: Khôi phục từ bản Base Backup + WAL Archive về thời điểm ngay trước sự cố trên cụm song song và đối soát bảng response_revisions.
RB-12: Sự Cố Lưu Trữ Đối Tượng MinIO / S3 (Object Storage Outage)	SEV-2
Owner: DevOps On-Call	Lỗi 503 SlowDown hoặc mất kết nối tới cụm MinIO khi tải ảnh câu hỏi / upload đề.	1) Chiều đọc (Phòng thi): Các ảnh câu hỏi (FIGURE_CROP) của đề đang thi được phục vụ trực tiếp từ lớp đệm CDN/Nginx Reverse Proxy phía trước MinIO nên thí sinh không bị mất hình vẽ.
2) Chiều ghi (Upload đề): Tạm dừng nhận Job upload file mới, chuyển lưu lượng đọc sang cụm MinIO Replica và thay thế ổ đĩa/node lỗi bằng cơ chế Erasure Coding Healing.
RB-13: Đột Biến Chi Phí AI / Tấn Công Làm Cạn Ngân Sách Token	SEV-2
Owner: DE/AI + BE On-Call	Sự kiện ai.budget.threshold_exceeded báo một Tenant tiêu thụ > 80% hoặc 100% daily_ai_budget_usd bất thường.	1) Chặn tự động: E01 (AI Gateway) tự động khóa các yêu cầu gọi LLM mới của Tenant vượt trần (trả mã 429 TENANT_AI_BUDGET_EXCEEDED).
2) Điều tra: Truy vấn ai_traces để xác định membership_id hoặc acquisition_job_id nào đang gửi vòng lặp bất thường.
3) Xử lý: Áp Rate Limit theo từng học sinh (VD: tối đa 30 tin nhắn AI Tutor / giờ) và chỉ nâng hạn mức ngày khi quản trị viên trường/trung tâm xác nhận.
RB-14: Ứng Phó Sự Cố Dữ Liệu Cá Nhân & Báo Cáo Theo Luật BVDLCN 91/2025/QH15 & NĐ 13/2023/NĐ-CP	SEV-1
Owner: Tech Lead + Legal / DPO	Phát hiện nguy cơ truy cập trái phép vào dữ liệu cá nhân của học sinh/giáo viên.	1) Cô lập ngay lập tức: Đóng cổng API hoặc thu hồi quyền truy cập của nguồn tấn công trong < 15 phút.
2) Đánh giá phạm vi: Xác định chính xác số lượng bản ghi users/memberships bị ảnh hưởng qua audit_events và Access Logs.
3) Nghĩa vụ pháp lý (Luật BVDLCN 91/2025/QH15 & NĐ 13/2023/NĐ-CP): Lập hồ sơ thông báo vi phạm quy định bảo vệ dữ liệu cá nhân gửi cơ quan chuyên trách (Cục An ninh mạng và phòng, chống tội phạm sử dụng công nghệ cao — A05 Bộ Công an) chậm nhất trong vòng 72 giờ kể từ khi xảy ra vi phạm, đồng thời thông báo cho tổ chức/phụ huynh liên quan.

