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

# 3. Chạy server
uvicorn app.main:app --reload
```

Kiểm tra: http://localhost:8000/health · http://localhost:8000/health/db · http://localhost:8000/docs

Chạy cả app lẫn DB bằng Docker: `docker compose up --build`

Test & lint: `pytest -q` · `ruff check .`

## Lộ trình

Mỗi phần làm xong sẽ được review và duyệt trước khi sang phần tiếp theo.

### Giai đoạn 1 — Core (<100 người)
- [x] **Phần 0:** Khởi tạo repo, bộ khung FastAPI, Docker Postgres, CI
- [ ] **Phần 1:** Mô hình dữ liệu (Alembic migrations) + đăng nhập bằng email, phân quyền (admin, thí sinh)
- [ ] **Phần 2:** Ngân hàng câu hỏi — CRUD câu hỏi, phân loại theo chủ đề & độ khó (Nhận biết / Thông hiểu / Vận dụng)
- [ ] **Phần 3:** Tạo bài thi — chọn câu hỏi, thời lượng, giờ mở/đóng đề
- [ ] **Phần 4:** Làm bài — giao diện thí sinh, đồng hồ đếm ngược theo giờ server, payload **không chứa đáp án**
- [ ] **Phần 5:** Nộp bài & chấm điểm trắc nghiệm tự động, xem kết quả

### Giai đoạn 2 — Reliability
- [ ] Auto-save đáp án (LocalStorage/IndexedDB + đồng bộ server có debounce, 5–10s)
- [ ] Khôi phục phiên khi rớt mạng / F5 (trả đúng trạng thái bài làm + thời gian còn lại)
- [ ] Audit trail — lịch sử chọn đáp án
- [ ] Đồng bộ đồng hồ kiểu NTP đơn giản (bù trừ độ trễ mạng)

### Giai đoạn 3 — Scale & Security
- [ ] Cache đề thi trên Redis
- [ ] Đẩy nộp bài/chấm điểm qua message queue
- [ ] Sinh mã đề ngẫu nhiên theo ma trận độ khó/chủ đề
- [ ] Chống gian lận tầng trình duyệt (chuyển tab, fullscreen, copy/paste)

### Về sau
- [ ] Online proctoring (webcam, nhận diện khuôn mặt)
- [ ] Tích hợp Safe Exam Browser cho kỳ thi quan trọng
