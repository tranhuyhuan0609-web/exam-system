# Pilot 10–15 giáo viên: triển khai và vận hành

Một máy chủ (4 vCPU / 8 GB RAM, Ubuntu 22.04+), một tên miền, HTTPS tự động. Chạy bằng
[`docker-compose.pilot.yml`](../../docker-compose.pilot.yml): `db`, `pgbouncer`, `redis`, `migration`, `app`, `worker`,
`frontend`, `caddy`. Không có crawler (kafka, mongo, crawler-*), ClamAV, Vault hay S3.

## 1. Chuẩn bị (10 phút)

1. Trỏ bản ghi DNS `A` của tên miền (ví dụ `thi.truong.edu.vn`) về IP máy chủ. Mở cổng 80 và 443; **không** mở 5432,
   6379, 8000, 3000 (compose cũng không publish các cổng đó).
2. Cài Docker Engine + plugin compose, clone repo, rồi:
   ```bash
   cp .env.pilot.example .env.pilot && chmod 600 .env.pilot
   # điền PILOT_DOMAIN, mật khẩu DB, GEMINI_API_KEY...; mỗi bí mật: openssl rand -base64 32
   ```
3. **Sao lưu `PII_MASTER_KEY` ra ngoài máy chủ** (trình quản lý mật khẩu). Mất khóa này thì hồ sơ cá nhân đã mã hóa của
   người dùng ghi danh qua API không đọc lại được.

## 2. Chạy

```bash
C="docker compose --env-file .env.pilot -f docker-compose.pilot.yml"
$C up -d --build                  # build ~10 phút lần đầu; migration chạy trước app/worker
$C ps                             # app, frontend, caddy: running/healthy; migration: exited (0)
$C --profile tools run --rm seed  # 1 admin + 15 giáo viên + 3 học sinh thử + 3 đề mẫu + 15 lớp
```

`seed` ghi `pilot-out/pilot_accounts.csv` (tenant, email, vai trò, **mật khẩu riêng của từng tài khoản**). Gửi cho mỗi giáo
viên đúng dòng của họ rồi **xóa file**. Chạy lại `seed` không đổi mật khẩu cũ (cột hiện `(unchanged)`).
Muốn dùng email thật của giáo viên: `--emails-file` (mỗi dòng một email) và `PILOT_EMAIL_DOMAIN` cho các tài khoản còn lại.

Đăng nhập tại `https://<PILOT_DOMAIN>/dang-nhap` với tenant `pilot`. Mỗi giáo viên có sẵn **lớp thử nghiệm** (mã
`PILOT-GVnn`, có 3 học sinh thử) với **đề mẫu mã 101** đã giao: vào lớp là thấy sổ điểm; đăng nhập `hsNN` để làm bài thử.

## 3. Bốn luồng giáo viên (kiểm tra thủ công sau khi dựng)

| # | Luồng | Cách thử |
|---|---|---|
| 1 | Tải đề `.pdf`/`.docx`, AI giải, duyệt, lưu ngân hàng | `/teacher/exam-builder` → thẻ **2. Upload đề PDF** (GV tải lên = GV duyệt). `.docx`/`.zip` qua `/studio/imports` (tài khoản biên tập) |
| 2 | Tạo đề theo ma trận THPTQG 2025+ | `/teacher/exam-builder` → thẻ **1**: chủ đề lấy từ kho thật; đủ 3 phần (ABCD / Đúng-Sai 4 ý / Trả lời ngắn) |
| 3 | Mở lớp, giao đề | `/teacher/classes/<id>`: mã lớp, **Giao bài** chọn đề đã xuất bản |
| 4 | Học sinh làm bài → bảng điểm | `hsNN` vào `/phong-thi/<attempt>`, nộp bài; giáo viên xem điểm và **Xuất CSV** |

Kho câu hỏi của trường mới trống ở thẻ 1 cho tới khi có câu hỏi đã xuất bản và gắn chủ đề: 22 câu mẫu (Bayes) đã có sẵn
cho 3 đề mẫu; đề từ PDF của giáo viên thêm vào kho.

## 4. Vận hành

- **Sao lưu**: `deploy/backup.sh` (pg_dump + thư mục file, giữ 14 ngày). Đặt cron `0 2 * * *` và **chép ra ngoài máy chủ**.
  Khôi phục DB: `docker compose ... exec -T db pg_restore -U exam -d exam --clean --if-exists < exam-<stamp>.dump`.
- **Cập nhật**: `git pull && $C up -d --build` (migration tự chạy trước khi app/worker khởi động lại).
- **Nhật ký**: `$C logs -f app worker frontend caddy`. DB ghi các truy vấn chậm hơn 500 ms.
- **Giới hạn AI**: `AI_DAILY_LIMITS` đặt trần số lần gọi mỗi ngày; hết hạn mức Gemini thì chuyển sang Groq/OpenRouter nếu có khóa.

## 5. Điều pilot này CHƯA làm (hãy đọc trước khi nhận dữ liệu học sinh thật)

- **Khóa PII**: `KEY_SERVICE_BACKEND=env` giữ khóa cố định trong biến môi trường nên *không* hủy được khóa trong bản sao
  lưu cũ (crypto-shredding chỉ xóa bản ghi trong DB sống). Trước khi nhận dữ liệu học sinh (Nghị định 13/2023/NĐ-CP, Luật
  BVDLCN 91/2025/QH15) chuyển sang Vault Transit: xem [`docs/pilot/PRODUCTION_HARDENING.md`](PRODUCTION_HARDENING.md).
- **Quét virus** upload (ClamAV) chưa bật; tệp tải lên vẫn được kiểm tra loại/magic bytes và chạy trong worker không đặc quyền.
- Một máy chủ duy nhất: không chịu lỗi phần cứng. Sao lưu ngoài máy chủ là bắt buộc.
- Chưa thử tải 5.000 học sinh; kích thước máy này đã đo ở mức nào, xem mục 6 của `PRODUCTION_HARDENING.md`.
