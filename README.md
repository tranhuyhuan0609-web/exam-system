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

Chạy cả app lẫn DB bằng Docker: `docker compose up --build`

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
- [ ] **Phần 4:** Nộp bài — kiểm tra hạn nộp, chấm điểm đồng bộ, trả lời giải chi tiết; làm lại nhiều lần (`max_attempts = NULL`) và lịch sử điểm

### Giai đoạn 1.5 — AI bóc tách & giải đề (sau Phần 4)
- [ ] Upload PDF / Word / ảnh → job nền (`import_jobs` trong PostgreSQL, worker riêng; file lưu volume, sau này chuyển MinIO/S3)
- [ ] Chuẩn hoá: `.docx` → PDF bằng LibreOffice headless (trong container worker) để giữ MathType; PDF → ảnh trang (PyMuPDF); ảnh chụp → xoay thẳng
- [ ] Gemini 2.5 Flash/Pro: đọc đề sang Markdown + LaTeX, Structured Outputs (lời giải từng bước trước đáp án), bật `code_execution` để tính số chính xác
- [ ] Cắt hình vẽ (đồ thị, mạch điện) theo bounding box, gắn vào câu hỏi
- [ ] Màn hình duyệt nháp: ảnh đề gốc bên trái, câu hỏi + lời giải AI bên phải → “Đưa vào ngân hàng đề”

### Giai đoạn 2 — Trải nghiệm thực tế & Ngân hàng đề động (Tuần 4–6)
- [ ] Auto-save 2 tầng: IndexedDB mỗi lần chọn + đồng bộ debounce lên server mỗi 5–10s
- [ ] Khôi phục phiên `IN_PROGRESS`: các câu đã chọn + số giây còn lại
- [ ] Sinh đề theo ma trận độ khó (Nhận biết / Thông hiểu / Vận dụng), đảo vị trí A/B/C/D
- [ ] Lưu cấu trúc đề của lượt thi vào `paper_snapshot` (JSONB) để F5 không bị xáo lại
- [ ] Background worker tự thu bài quá hạn `expires_at` → `SUBMITTED` và chấm điểm

### Giai đoạn 3 — Thi theo ca cố định & Chịu tải cao (Tuần 7–9)
- [ ] Chế độ `SCHEDULED`: `available_from` / `available_until`, `max_attempts = 1`, hạn nộp `LEAST(NOW() + duration, available_until)`
- [ ] Redis: cache đề trước giờ G, auto-save ghi vào Redis Hash
- [ ] Nộp bài bất đồng bộ qua message queue (RabbitMQ/Kafka), worker chấm và ghi PostgreSQL

### Giai đoạn 4 — Chống gian lận & Phân tích dữ liệu (Tuần 10–12)
- [ ] Bắt chuyển tab (`visibilitychange`), `blur`, thoát fullscreen, chặn chuột phải/copy
- [ ] Ghi log vi phạm real-time, tự thu bài khi vượt ngưỡng
- [ ] Phổ điểm, bảng xếp hạng
- [ ] Tỷ lệ đúng/sai từng câu để phát hiện câu lỗi và độ khó thực tế
