# H2Edu

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
- [x] **Phần 1:** Database & migration (`users`, `subjects` (nay là `categories`), `exams`, `questions`, `exam_attempts`) + đăng nhập bằng email, phân quyền admin/học sinh
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

### Cây mục lục đa cấp
- [x] Bảng `categories` (`parent_id`, `path` dạng `/1/5/12/`, `depth`) thay cho Môn / Chương / Bài cố định, không giới hạn số cấp; migration giữ nguyên dữ liệu cũ
- [x] Câu hỏi, đề, job nhập file gắn `root_id` (môn) + `category_id` (mục cụ thể); lọc cả nhánh bằng `path LIKE`
- [x] Trang quản trị **Mục lục**: thêm mục con, đổi tên, sắp xếp ▲▼, chuyển sang nhánh khác (cập nhật path cả nhánh), xoá nhánh chưa dùng
- [x] Trang học sinh: sidebar cây mục lục (kèm số đề), bấm một mục để lọc đề trong cả nhánh, breadcrumb “Vật lí › Lớp 11 › Chương 1”

### Học sinh tự tạo bài kiểm tra
- [x] Chọn một mục (môn / chương / bài) → chọn số câu trắc nghiệm, đúng/sai, trả lời ngắn, tự luận và mức độ; hiển thị số câu còn trong ngân hàng
- [x] Thời gian tự tính theo số câu (học sinh sửa được), câu lấy ngẫu nhiên trong cả nhánh, ưu tiên câu chưa gặp
- [x] Bài lưu dạng đề cá nhân (`visibility = PERSONAL`), chỉ chủ bài thấy; trang “Bài tự tạo của tôi”: tiếp tục, làm lại, xem kết quả, xoá

### Giáo viên gói Pro — bài kiểm tra định kỳ riêng tư
- [x] Đăng ký tài khoản Giáo viên, gửi yêu cầu nâng cấp; admin bật / gia hạn / tắt Pro ở trang **Người dùng**
- [x] Tạo bài kiểm tra: phạm vi mục lục, số câu từng phần, mức độ, giờ mở – giờ đóng, thời gian làm bài; đề ngẫu nhiên từng học sinh hoặc chung một đề (xem trước, bốc lại)
- [x] Bài không hiện trên web (`visibility = PRIVATE`): học sinh mở link `/k/<token>`, đăng nhập và nhập mã; đếm ngược tới giờ mở, mỗi học sinh 1 lượt, hạn nộp `LEAST(bắt đầu + thời lượng, giờ đóng)`
- [x] Học sinh xem điểm, báo cáo và lời giải sau giờ đóng bài
- [x] Kết quả cho giáo viên: danh sách học sinh, điểm, thời gian làm, điểm trung bình, phổ điểm, xuất Excel

### Giai đoạn 2 — Trải nghiệm thực tế & Ngân hàng đề động (Tuần 4–6)
- [x] Auto-save 2 tầng: IndexedDB mỗi lần chọn + đồng bộ ngầm lên server (2s sau thao tác cuối, chậm nhất 10s; tự thử lại khi mất mạng)
- [x] Khôi phục phiên `IN_PROGRESS`: các câu đã chọn (server + thay đổi chưa kịp gửi trên máy) + số giây còn lại
- [x] Sinh đề theo ma trận (phạm vi × mức độ × dạng câu × số câu), ưu tiên câu chưa gặp, đảo câu trong từng phần và đảo vị trí A/B/C/D
- [x] Lưu cấu trúc đề của lượt thi vào `paper_snapshot` (JSONB) để F5 không bị xáo lại
- [x] Worker tự thu bài mỗi phút: bài quá hạn `expires_at` → `SUBMITTED`, chấm theo đáp án đã auto-save

### Thu thập đề (data pipeline Medallion)
- [x] Scrapy, một spider chung theo cấu hình `configs/sites/<site>.yaml` (vietjack, loigiaihay — Vật lí 11), tôn trọng robots.txt, 3 s/trang, bỏ qua trang đã tải trong `refresh_days`
- [x] **Bronze**: HTML gốc trong MongoDB (`raw_documents`), file PDF/Word/ảnh gốc trong bucket `bronze-raw`; chỉ ID tài liệu đi qua Kafka `raw.exam.ingestion`; lỗi vào `raw.exam.ingestion.dlq` → MongoDB `dlq_events` (có `replay-dlq`)
- [x] **Silver**: parser từng site (trắc nghiệm, đúng/sai, trả lời ngắn), MathML → LaTeX bằng code, tải mọi `<img>` về storage của mình, Gemini Flash chuẩn hoá công thức còn dạng chữ/ảnh và viết lời giải khi nguồn không có (chỉ gọi khi cần, theo lô)
- [x] Chống trùng: SHA-256 (`content_hash`), rồi embedding Gemini + pgvector (HNSW, cosine ≥ 0.92) chỉ để chọn ứng viên — xác nhận trùng khi đề gần như giống hệt và cùng đáp án (đề Vật lí cùng chủ đề có cosine 0.92–0.97 dù khác đáp án)
- [x] **Gold**: tự gắn chương/bài theo luật trong YAML ("bài N" → mục "Bài N: …"), đẩy vào `questions` (`source_type = CRAWLER`, có `source_url`); trang chưa gắn được vào hàng đợi admin
- [x] Admin **Thu thập**: nguồn, lượt chạy, chạy ngay, hàng đợi duyệt, gán trang nguồn vào mục lục, duyệt / bỏ theo lô
- [x] Mở rộng: Vật lí + Toán 10–12, 3 bộ sách (Kết nối tri thức, Chân trời sáng tạo, Cánh diều) trên vietjack; Vật lí 10–11 trên loigiaihay (Toán của loigiaihay tải câu bằng JavaScript — chưa lấy)
- [x] **Tự dựng mục lục từ nguồn** (`auto_toc`): spider đọc chương / bài trên trang mục lục của nguồn, Silver tìm-hoặc-tạo `Môn › Bộ sách › Chương › Bài` (khớp theo số "Chương I" = "Chương 1"); `adopt-series` chuyển chương cũ vào bộ sách
- [x] Admin: chạy riêng một cuốn, tạm dừng / bật lại lịch từng nguồn, trang **Chất lượng dữ liệu** (theo nguồn × môn, lời giải AI viết, cảnh báo thường gặp, câu mới theo ngày)
- [x] Gemini hết quota (429): câu chờ AI giữ nguyên, Silver tự nghỉ rồi làm tiếp
- [x] Thêm nguồn **hoc247** (đề giữa kì Toán/Vật lí 10–12; đáp án nguồn có chỗ sai nên AI kiểm tra lại — `verify_answers`) và **tuyensinh247** (luyện chuyên đề THPT Vật lí/Toán, nhãn mức độ có sẵn)
- [x] Giảm gọi AI: ảnh công thức có LaTeX trong `alt` dùng thẳng; công thức dạng chữ (π, cos(…), x^2, ≤…) chuyển sang LaTeX bằng code
- [x] Kiểm tra đáp án theo mẫu (`verify_ratio`, hoc247: 20%), lô AI theo nhà cung cấp (`AI_BATCH_SIZES`), embedding gom lô (1 request cho cả nhóm câu), `renormalize` cho câu đang chờ AI
- [x] Chuỗi AI dự phòng **Gemini → Groq → OpenRouter** (API tương thích OpenAI, gói miễn phí), hạn mức/ngày (`AI_DAILY_LIMITS`), nghỉ 10 phút khi gặp 429, bảng `ai_usage`; embedding lỗi thì câu chờ và tự thử lại
- [ ] Playwright cho nguồn render bằng JS / kho PDF (cấu hình `renderer: playwright` đã có chỗ)

### Giai đoạn 3 — Thi theo ca cố định & Chịu tải cao (Tuần 7–9)
- [ ] Chế độ `SCHEDULED`: `available_from` / `available_until`, `max_attempts = 1`, hạn nộp `LEAST(NOW() + duration, available_until)`
- [ ] Redis: cache đề trước giờ G, auto-save ghi vào Redis Hash
- [ ] Nộp bài bất đồng bộ qua message queue (RabbitMQ/Kafka), worker chấm và ghi PostgreSQL

### Giai đoạn 4 — Chống gian lận & Phân tích dữ liệu (Tuần 10–12)
- [ ] Bắt chuyển tab (`visibilitychange`), `blur`, thoát fullscreen, chặn chuột phải/copy
- [ ] Ghi log vi phạm real-time, tự thu bài khi vượt ngưỡng
- [ ] Phổ điểm, bảng xếp hạng
- [ ] Tỷ lệ đúng/sai từng câu để phát hiện câu lỗi và độ khó thực tế
