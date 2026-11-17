# Từ pilot lên production: nghiệm thu bảo mật (Wave 0), Vault bền vững, kiểm thử tải (Wave 2)

Làm trong lúc 10–15 giáo viên đang dùng thử. Mỗi mục ghi rõ **đã chạy thật ngày 2026-11-16** hay **bạn cần chạy trên máy chủ**.

## 1. Nghiệm thu RLS và 7 DB roles trên PostgreSQL 17 (đã chạy)

Quy trình (lặp lại được trên máy chủ):

```bash
docker compose ... exec -T db psql -U exam -d postgres -c "CREATE DATABASE exam_rls_verify"
DATABASE_URL=postgresql+asyncpg://exam:<mk>@db:5432/exam_rls_verify alembic upgrade head
docker compose ... exec -T db psql -U exam -d exam_rls_verify -v ON_ERROR_STOP=1 < tests/security/seed_rls_fixture.sql
docker compose ... exec -T db psql -U exam -d exam_rls_verify -v ON_ERROR_STOP=1 < tests/security/verify_rls_hardening.sql
```

Kết quả trên `pgvector/pgvector:pg17` (PostgreSQL 17.11), schema do Alembic tạo từ đầu:

- `verify_rls_hardening.sql` chạy hết, không có `RAISE EXCEPTION` (kết thúc bằng `ROLLBACK` theo thiết kế).
- 7 role `ulp_*` (`ulp_migrator`, `ulp_app_rw`, `ulp_exam_session_rw`, `ulp_worker_ingestion`, `ulp_worker_grading`,
  `ulp_system_scheduler`, `ulp_analytics_ro`): `rolbypassrls = false`, `rolsuper = false`.
- `ulp_exam_session_rw` **không** đọc được `question_version_answers`; `ulp_worker_ingestion` **không** đọc được
  `assessment_answer_keys` (`has_table_privilege` = false).
- 106 bảng bật `ENABLE` + `FORCE ROW LEVEL SECURITY`. 40 bảng còn lại là danh mục toàn hệ thống được khai báo miễn
  trừ (test `test_every_table_is_under_forced_rls_or_explicitly_exempt` giữ danh sách này; thêm bảng mới mà quên RLS thì test đỏ).

Cổng này cũng chạy trong CI mỗi lần push (`pytest`), nên một thay đổi làm lộ đáp án sẽ làm CI đỏ.

## 2. Vault bền vững thay cho `-dev` (đã kiểm tra với Vault 1.17 thật)

`docker-compose.yml` chạy Vault `-dev` (trong RAM, tự mở khóa, **mất sạch khi khởi động lại**). Pilot dùng khóa cố định trong
`.env.pilot`. Trước khi nhận dữ liệu học sinh thật, chuyển sang Vault chế độ server (lưu đĩa) bằng
[`docker-compose.vault.yml`](../../docker-compose.vault.yml) + [`deploy/vault/`](../../deploy/vault):

```bash
C="docker compose --env-file .env.pilot -f docker-compose.pilot.yml -f docker-compose.vault.yml"
$C up -d vault
# 1. khởi tạo MỘT LẦN: in ra 5 khóa mở khóa + root token. Chia cho 5 người khác nhau, KHÔNG lưu trên máy chủ
$C exec vault vault operator init -key-shares=5 -key-threshold=3
# 2. mở khóa (3 khóa khác nhau); LÀM LẠI sau mỗi lần Vault khởi động lại
$C exec vault vault operator unseal
# 3. bật engine Transit, nạp chính sách tối thiểu, tạo token cho ứng dụng (định kỳ 30 ngày, tự gia hạn)
$C exec -e VAULT_TOKEN=<root> vault vault secrets enable transit
$C exec -e VAULT_TOKEN=<root> vault vault policy write ulp-transit /vault/policy/ulp-transit-policy.hcl
$C exec -e VAULT_TOKEN=<root> vault vault token create -policy=ulp-transit -period=720h
# 4. dán token vào .env.pilot (VAULT_TOKEN=...), rồi khởi động lại app và worker
$C up -d app worker
# 5. thu hồi root token: vault token revoke <root>
```

Đã chạy thật với Vault 1.17 ở chế độ server: khởi tạo, mở khóa, token **chỉ** tạo/đọc/xóa khóa `transit/keys/user-*` và
`encrypt`/`decrypt` trên đó (tạo khóa tên khác, đọc `sys/mounts`, hoặc `encrypt` vào tên khóa mới đều bị **403**);
khởi động lại container thì Vault **sealed**, mở khóa xong khóa người dùng vẫn giải mã được; xóa khóa thì giải mã và
mã hóa lại đều bị từ chối (không "sống lại" khóa đã hủy).

Lưu ý quan trọng:

- Vault bị sealed (sau khởi động lại) thì mọi thao tác PII (ghi danh thành viên, đọc hồ sơ, xóa dữ liệu) trả lỗi cho tới
  khi có người mở khóa; **đăng nhập và làm bài không phụ thuộc Vault** (đăng nhập dùng chỉ mục mù HMAC).
- Nếu trong thời gian pilot có người dùng được **ghi danh qua API** (`POST /api/v1/memberships`; các tài khoản do `seed_pilot`
  tạo trực tiếp thì không), DEK của họ đang được bọc bằng khóa cố định, không phải Vault. Phải bọc lại (đọc bằng dịch vụ
  `env`, ghi bằng dịch vụ `vault`) **trước** khi đổi `KEY_SERVICE_BACKEND`; chưa có script cho việc này vì pilot không tạo
  loại tài khoản đó. Cần thì báo để làm.
- Để khỏi phải mở khóa tay, dùng auto-unseal bằng KMS của nhà cung cấp đám mây (thêm khối `seal "awskms"` vào `vault.hcl`).
- Listener không có TLS vì chỉ `app`/`worker` trong mạng Docker riêng truy cập; nếu Vault ra ngoài mạng đó phải bật TLS.

## 3. Kiểm thử tải bằng k6 (cần chạy trên máy chủ thật)

`tests/load/k6_exam_5000ccu.js` mô phỏng 5.000 học sinh tự lưu bài rồi 3.000 người nộp cùng lúc. Chạy ở kích thước đầy đủ
trên máy 4 vCPU pilot là vô nghĩa, nên script nhận kích thước qua biến (mặc định vẫn là 5.000/3.000 của cổng GATE-05, ngưỡng
không đổi):

```bash
# 1. tạo dữ liệu thật cho từng VU (tenant tải riêng, đề, bài làm IN_PROGRESS); SECRET_KEY của API = LOAD_TEST_JWT_SECRET
LOAD_TEST_JWT_SECRET=<SECRET_KEY> python -m tests.load.seed_attempts --materialize --count 300 --out tests/load/data/sessions.json
# 2. leo thang từng nấc, KHÔNG chạy trên máy đang có giáo viên dùng thật
k6 run -e API_BASE_URL=https://<domain-test> -e PEAK_VUS=100  -e SUBMIT_VUS=80  -e RAMP=1m -e STEADY=3m -e SUBMIT_AT=4m30s tests/load/k6_exam_5000ccu.js
k6 run -e API_BASE_URL=https://<domain-test> -e PEAK_VUS=300  -e SUBMIT_VUS=250 -e RAMP=1m -e STEADY=5m -e SUBMIT_AT=6m30s tests/load/k6_exam_5000ccu.js
```

Ngưỡng (p95 autosave < 120 ms, p99 < 300 ms, lỗi < 0,01%) là mức đạt. Số VU lớn nhất vượt ngưỡng chính là sức chứa của máy
này; 5.000 CCU cần nhiều máy API (chạy nhiều bản `app` sau cân bằng tải, PgBouncer riêng, PostgreSQL tách máy).

Cấu hình PgBouncer đã đặt sẵn trong compose: `POOL_MODE=transaction`, `SERVER_RESET_QUERY=DISCARD ALL`,
`DEFAULT_POOL_SIZE=25`, `MAX_CLIENT_CONN=500`. Khi tinh chỉnh: tổng `DEFAULT_POOL_SIZE` × số database/user phải nhỏ hơn
`max_connections` của PostgreSQL (100 trong pilot, trừ vài kết nối cho migration và quản trị); nếu k6 báo `cl_waiting` lớn
mà CPU PostgreSQL còn rảnh thì tăng `DEFAULT_POOL_SIZE`, ngược lại thì thêm máy chứ đừng thêm kết nối.

## 4. Còn lại trước khi gọi là production

- Quét virus upload (ClamAV) rồi nối vào luồng tải tệp; hiện cấu hình có sẵn nhưng pilot không chạy `clamd`.
- Sao lưu ngoài máy chủ + thử khôi phục định kỳ (xem `deploy/backup.sh`).
- Nhiều máy chủ (xem mục 3) và PostgreSQL có replica; giám sát (cảnh báo khi `app`/`db` không healthy).
- Rà lại pháp lý dữ liệu học sinh (Nghị định 13/2023/NĐ-CP, Luật BVDLCN 91/2025/QH15): chủ thể dữ liệu, đồng ý của phụ huynh
  với người dưới 16 tuổi, thời hạn lưu trữ; đây là việc của người chịu trách nhiệm dữ liệu, không phải của mã nguồn.
