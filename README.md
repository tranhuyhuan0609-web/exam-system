# Exam System

Hệ thống thi trắc nghiệm online. Ban đầu phục vụ **dưới 100 người dùng đồng thời**, sau đó nâng cấp dần lên quy mô lớn.

**Stack:** Python 3.13 · FastAPI · PostgreSQL 17 · SQLAlchemy 2 (async) · HTML/JS thuần (Jinja2)

## Chạy local

```bash
# 1. Bật PostgreSQL
docker compose up -d db

# 2. Tạo môi trường Python
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements-dev.txt
copy .env.example .env          # Linux/macOS: cp .env.example .env

# 3. Tạo bảng + dữ liệu mẫu + tài khoản admin
alembic upgrade head
python -m app.cli seed-demo
python -m app.cli create-admin --email admin@example.com --name "Admin Demo"

# 4. Chạy server
uvicorn app.main:app --reload
```

Mở http://localhost:8000 · Kiểm tra: `/health` · `/health/db` · `/docs`

Chạy cả app, worker AI lẫn DB bằng Docker: `docker compose up --build`

### Lưu trữ file (S3)
- Mặc định `STORAGE_BACKEND=local`: file nằm trong `data/media`, chỉ thư mục `figures/` được public.
- Dùng S3: `docker compose up -d s3` (RustFS, tương thích S3; MinIO đã ngừng phát hành image), rồi đặt trong `.env`: `STORAGE_BACKEND=s3`, `S3_ENDPOINT=localhost:9000`, `S3_PUBLIC_URL=http://localhost:9000`. Cũng dùng được MinIO tự build, AWS S3, Cloudflare R2…
- Chuyển file cũ sang storage đang cấu hình: `python -m app.cli migrate-files`
- Bucket chỉ cho đọc công khai `figures/*`; file gốc (`raw/`) và ảnh trang đề (`pages/`) chỉ admin xem qua app.

### AI nhập đề (PDF / Word / ảnh)
1. Lấy API key tại Google AI Studio, ghi vào `.env`: `GEMINI_API_KEY=...`
2. Chạy worker xử lý job (terminal riêng): `python -m app.worker`
   - PDF và ảnh chạy được ngay trên máy. File Word cần LibreOffice, nên hãy chạy worker bằng Docker: `docker compose up worker`
3. Vào **Quản trị → 🤖 AI nhập đề**, tải đề lên, chờ AI đọc và giải, duyệt rồi bấm “Đưa vào ngân hàng”.

Test & lint: `pytest -q` · `ruff check .` (test dùng database riêng `exam_test`, tự tạo nếu chưa có)

Sau khi sửa model: `alembic revision --autogenerate -m "..."` rồi `alembic upgrade head`.

## Lộ trình

Mỗi phần làm xong sẽ được review và duyệt trước khi sang phần tiếp theo.

- [x] **Phần 0:** Khởi tạo repo, bộ khung FastAPI, Docker Postgres, CI

### Giai đoạn 1 — MVP: Tự luyện theo chương/bài (Tuần 1–3)
Học sinh vào bất cứ lúc nào, chọn bài (VD: Vật lí 11 – Chương 1), làm bài tính giờ cá nhân, nộp và xem điểm ngay.
- [x] **Phần 1:** Database & migration (`users`, `subjects`, `exams`, `questions`, `exam_attempts`) + đăng nhập bằng email, phân quyền admin/học sinh
- [x] **Phần 2:** Trang quản trị — môn học, câu hỏi (LaTeX, lựa chọn dạng JSONB, lời giải, độ khó), đề `PRACTICE`
- [x] **Phần 3:** Làm bài — danh sách đề, bấm "Bắt đầu" tạo lượt thi (`expires_at = NOW() + duration`), đồng hồ theo giờ server, API đề **lọc bỏ `is_correct`**, hiển thị công thức bằng KaTeX
- [x] **Phần 4:** Nộp bài — kiểm tra hạn nộp, chấm điểm đồng bộ, trả lời giải chi tiết; làm lại nhiều lần (`max_attempts = NULL`) và lịch sử điểm

### Giai đoạn 1.5 — AI bóc tách & giải đề (sau Phần 4)
- [x] Upload PDF / Word / ảnh → job nền (`import_jobs` trong PostgreSQL, worker riêng; file lưu volume, sau này chuyển MinIO/S3)
- [x] Chuẩn hoá: `.docx` → PDF bằng LibreOffice headless (trong container worker) để giữ MathType; PDF → ảnh trang (PyMuPDF); ảnh chụp → xoay thẳng
- [x] Gemini (Flash, tự chuyển model dự phòng khi quá tải): đọc đề sang Markdown + LaTeX, Structured Outputs (lời giải từng bước trước đáp án), bật `code_execution` để tính số chính xác
- [x] Cắt hình vẽ (đồ thị, mạch điện) theo bounding box, gắn vào câu hỏi
- [x] Màn hình duyệt nháp: ảnh đề gốc bên trái, câu hỏi + lời giải AI bên phải → “Đưa vào ngân hàng đề”

- [x] Dạng câu Đúng/Sai và Trả lời ngắn (định dạng THPT 2025): soạn, nhập file, AI, làm bài, chấm điểm theo thang THPT

### Bổ sung schema & hạ tầng (đón đầu crawler)
- [x] `source_type` (soạn tay / nhập file / AI / crawler), `content_hash` SHA-256 chống trùng, `is_verified` (đề chỉ dùng câu đã duyệt)
- [x] PostgreSQL có pgvector (`pgvector/pgvector:pg17`), lưu trữ S3 (RustFS), ảnh trang 300 DPI
- [x] Bắt đầu tách Clean Architecture: `app/domain/`, `app/infrastructure/`

### Tự luận & tuỳ chọn cấu trúc đề
- [x] Dạng câu **Tự luận** (điểm tuỳ chỉnh), học sinh trình bày lời giải (có xem trước LaTeX)
- [x] **Gemini chấm tự luận** ở worker: so với lời giải mẫu (hoặc tự giải), cho điểm từng phần theo bậc 0,25 và nhận xét; điểm tạm tính cho tới khi chấm xong
- [x] Nút **“🤖 AI giải”** trong form soạn câu: điền lời giải từng bước (và đáp án cho trắc nghiệm / đúng sai / trả lời ngắn)
- [x] Học sinh **tự chọn cấu trúc đề**: chỉ trắc nghiệm, TN + đúng/sai, TN + ĐS + trả lời ngắn, đủ các phần (thời gian tính theo tỉ lệ số câu)
- [x] Hình vẽ: bỏ chữ watermark (chữ màu nhạt) khi cắt hình

### Báo cáo sau khi nộp & cờ phân vân (GĐ3 roadmap mới)
- [x] Cắm cờ 🚩 câu phân vân (auto-save cùng đáp án, khôi phục khi F5)
- [x] Hộp xác nhận nộp bài: liệt kê câu bỏ trống / câu cắm cờ, bấm để nhảy tới câu
- [x] Dashboard: thời gian, trung bình giây/câu, đúng/sai/bỏ trống, biểu đồ tỉ lệ đúng theo mức độ, cảnh báo chuyên đề dưới 50%
- [x] Xem lại có bộ lọc Tất cả / Sai / Bỏ trống / Phân vân

### Giai đoạn 2 — Trải nghiệm thực tế & Ngân hàng đề động (Tuần 4–6)
- [x] Auto-save 2 tầng: IndexedDB mỗi lần chọn + đồng bộ ngầm lên server (2s sau thao tác cuối, chậm nhất 10s; tự thử lại khi mất mạng)
- [x] Khôi phục phiên `IN_PROGRESS`: các câu đã chọn (server + thay đổi chưa kịp gửi trên máy) + số giây còn lại
- [x] Sinh đề theo ma trận (phạm vi × mức độ × dạng câu × số câu), ưu tiên câu chưa gặp, đảo câu trong từng phần và đảo vị trí A/B/C/D
- [x] Lưu cấu trúc đề của lượt thi vào `paper_snapshot` (JSONB) để F5 không bị xáo lại
- [x] Worker tự thu bài mỗi phút: bài quá hạn `expires_at` → `SUBMITTED`, chấm theo đáp án đã auto-save

### Giai đoạn 3 — Thi theo ca cố định & Chịu tải cao (Tuần 7–9)
- [ ] Chế độ `SCHEDULED`: `available_from` / `available_until`, `max_attempts = 1`, hạn nộp `LEAST(NOW() + duration, available_until)`
- [ ] Redis: cache đề trước giờ G, auto-save ghi vào Redis Hash
- [ ] Nộp bài bất đồng bộ qua message queue (RabbitMQ/Kafka), worker chấm và ghi PostgreSQL

### Giai đoạn 4 — Chống gian lận & Phân tích dữ liệu (Tuần 10–12)
- [ ] Bắt chuyển tab (`visibilitychange`), `blur`, thoát fullscreen, chặn chuột phải/copy
- [ ] Ghi log vi phạm real-time, tự thu bài khi vượt ngưỡng
- [ ] Phổ điểm, bảng xếp hạng
- [ ] Tỷ lệ đúng/sai từng câu để phát hiện câu lỗi và độ khó thực tế
