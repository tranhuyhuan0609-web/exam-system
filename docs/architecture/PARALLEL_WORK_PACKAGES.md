# Gói việc song song cho agent thứ hai (ULP V11.3)

Tài liệu này giao việc cho một agent lập trình chạy song song với luồng chính (ví dụ Gemini). Mỗi gói tự đứng được: đọc
mục 1 và mục 2 trước, rồi làm lần lượt từng gói ở mục 3. Luồng chính (Claude) giữ quyền tích hợp, rà soát và merge.

## 1. Bối cảnh tối thiểu

- Repo: `exam-system` — FastAPI + SQLAlchemy 2.0 + Alembic + PostgreSQL 17, đang nâng cấp lên kiến trúc ULP V11.3.
- Đọc trước: `CLAUDE.md`, `docs/architecture/IMPLEMENTATION_PLAN_V11_3.md` (mục 2 "Quyết định đã chốt" và mục 3).
- Đặc tả nằm ở `docs/architecture/chapters/`. Mỗi gói bên dưới chỉ rõ file và khoảng dòng cần đọc; không cần đọc hết.
- Bố cục module bắt buộc: `app/modules/<tên>/public.py` (cổng vào duy nhất), `app/modules/<tên>/internal/` (mã riêng),
  hợp đồng dùng chung ở `app/contracts/` (chỉ được import thư viện chuẩn và pydantic). Xem `app/modules/quality/` và
  `app/modules/attempts/` làm mẫu.

## 2. Quy tắc chung cho mọi gói

**Phạm vi được sửa**

| Được phép | Không được đụng |
|---|---|
| Thư mục module của gói (`app/modules/<tên>/`), file test của gói, file hợp đồng của gói trong `app/contracts/` | `app/platform/`, `app/ai/`, `app/application/`, `app/routers/`, `app/pipeline/`, `app/infrastructure/pdf_engine.py`, `universal_ingestor.py`, `app/main.py`, `app/model_registry.py` |
| Thêm **đúng một khối** `[importlinter:contract:internal-<tên>]` vào cuối `.importlinter` (chép mẫu khối `internal-quality`) | Migration đã có trong `alembic/versions/`, `.github/`, `requirements.txt`, `CLAUDE.md`, mọi file trong `docs/architecture/chapters/` |
| Thêm dòng vào cuối `requirements-dev.txt` nếu gói cho phép | `tests/golden/`, `tests/security/`, `tests/conftest.py` |

**Cách làm**

1. Mỗi gói một nhánh riêng từ `main` mới nhất, tên như ghi trong gói. Mỗi gói một pull request vào `main`. Không tự merge.
2. Mã, chú thích, thông điệp commit bằng tiếng Anh. Không thêm dòng `Co-Authored-By`.
3. Không gọi AI, không gọi mạng trong mã của các gói này. Không thêm thư viện vào `requirements.txt`; nếu cần thì ghi
   trong mô tả PR và dừng phần đó lại.
4. Gói "thuần" (không có bảng): test không dùng fixture của `tests/conftest.py` và phải chạy được với `--noconftest`.
5. Mã mẫu trong đặc tả là điểm xuất phát, không phải để chép nguyên: sửa cho qua `ruff`, có kiểu dữ liệu rõ, không dùng
   `dict` trần làm đầu vào/đầu ra khi có thể dùng dataclass đóng băng hoặc model Pydantic.
6. Gặp chỗ đặc tả mâu thuẫn hoặc thiếu: **không đoán**. Làm theo cách hiểu chặt nhất, và ghi rõ trong mô tả PR mục
   "Điểm cần chủ dự án quyết" kèm các phương án.
7. Không ghi mã cổng nghiệm thu nào ngoài danh sách trong `docs/architecture/platform_manifest.yaml`
   (script `ci/check_gate_ids.sh` sẽ đánh trượt).

**Lệnh kiểm tra trước khi mở PR** (Windows, chạy từ gốc repo, dùng virtualenv có sẵn)

```
.venv\Scripts\ruff.exe check .
.venv\Scripts\lint-imports.exe
bash ci/check_gate_ids.sh
.venv\Scripts\python.exe -m pytest <file test của gói> --noconftest -q -p no:cacheprovider
```

Gói có bảng (chỉ P6) chạy thêm bộ test đầy đủ với database riêng để không đụng luồng chính:

```
set TEST_DATABASE_URL=postgresql+asyncpg://exam:exam@localhost:5432/exam_test_p6
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

**Mô tả PR phải có:** file đã tạo/sửa; lệnh đã chạy và số test qua/trượt/bỏ qua; mọi chỗ lệch hoặc phải diễn giải đặc tả;
"Điểm cần chủ dự án quyết" (nếu có); phần nào chưa kiểm chứng được.

## 3. Các gói việc

Thứ tự đề xuất: P1 → P2 → P3 → P4 → P5 → P7 → P6. Các gói P1–P5 và P7 không phụ thuộc nhau và không phụ thuộc luồng chính.

| Gói | Module | Loại | Cần trước | Ước lượng |
|---|---|---|---|---|
| P1 | C07 chấm điểm THPTQG 2025+ | Hàm thuần | — | nhỏ |
| P2 | A08 băm và khử trùng câu hỏi | Hàm thuần | — | vừa |
| P3 | D04 lịch ôn tập FSRS-5 | Hàm thuần | — | vừa |
| P4 | C03 kiểm tra cấu trúc đề | Hàm thuần | — | nhỏ |
| P5 | C06 kho lưu bài ngoại tuyến (TypeScript + Dexie) | Frontend | — | vừa |
| P7 | A02/A03 lõi an toàn (chống SSRF, bung nén an toàn) | Hàm thuần | — | vừa |
| P6 | B04 + B06 cây phân loại và chương trình học | Có bảng + migration | PR G03 đã vào `main` | lớn |

---

### P1 — C07: chấm điểm tất định THPTQG 2025+

- **Nhánh:** `feat/c07-scoring-core`
- **Đọc:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` dòng "C07"; `chapters/05_event_bus_va_pydantic_contracts.md`
  mục 5.2 (`NumericParsePolicy`); `chapters/06_9_thuat_toan_loi.md` dòng 745–820 (`parse_short_numeric_vn`,
  `grade_thptqg_2025_item`).
- **Tạo:** `app/contracts/scoring.py` (`NumericParsePolicy` đúng như 5.2), `app/modules/grading/{__init__,public}.py`,
  `app/modules/grading/internal/{__init__,scoring}.py`, `tests/test_scoring_core.py`.
- **Việc cần làm:** hàm thuần chấm một câu và tổng điểm một bài, dùng `Decimal`:
  - Phần I (một lựa chọn): đúng được điểm của câu, sai 0.
  - Phần II (đúng/sai 4 ý): đúng 1 ý 0,10 · 2 ý 0,25 · 3 ý 0,50 · 4 ý 1,00.
  - Phần III (trả lời ngắn): chuẩn hóa theo `NumericParsePolicy` (tối đa 4 ký tự, dấu `,` thành `.`, từ chối dấu phân
    cách hàng nghìn kiểu `1.000`), so với đáp án theo `tolerance`.
  - Ánh xạ ngược hoán vị phương án: đầu vào có `option_permutation` (thứ tự A/B/C/D đã chia cho thí sinh).
- **Nghiệm thu:**
  - Test tham số hóa phủ mọi tổ hợp Phần II (0..4 ý đúng) và bảng điểm ở trên.
  - Đề Toán 22 câu làm đúng hết ra đúng 10,00; đề Lý 28 câu làm đúng hết ra đúng 10,00 (12×0,25 + 4×1 + 6×0,5 và
    18×0,25 + 4×1 + 6×0,25).
  - Phần III: `"0,5"` khớp `0.5`; `"1.000"` bị từ chối với mã lỗi rõ; chuỗi dài hơn 4 ký tự bị từ chối; số âm hợp lệ.
  - Cùng đầu vào luôn cho cùng kết quả; không dùng `float` ở bất kỳ đâu trong phép cộng điểm.
- **Không làm:** bảng `grades`, phúc khảo, router.

### P2 — A08: băm nội dung, băm cấu trúc và khử trùng

- **Nhánh:** `feat/a08-dedup-core`
- **Đọc:** `chapters/03_...md` dòng "A08"; `chapters/06_9_thuat_toan_loi.md` dòng 323–428. Tham khảo mã cũ
  `app/domain/question_hash.py` (chỉ đọc, không sửa).
- **Tạo:** `app/modules/dedup/{__init__,public}.py`, `app/modules/dedup/internal/{__init__,hashing}.py`,
  `tests/test_dedup_core.py`.
- **Việc cần làm:** hàm thuần
  - `normalize_text`: chuẩn hóa Unicode NFC, gộp khoảng trắng, bỏ khác biệt hoa/thường theo đặc tả.
  - `content_hash`: SHA-256, **không đổi** khi đổi thứ tự các phương án A/B/C/D.
  - `structural_hash`: thay số liệu của bài toán bằng `<NUM>` (kể cả số dính đơn vị như `10g`, `200g`) nhưng **giữ
    nguyên chỉ số trong công thức hóa học** (`H2O`, `Fe3O4`, `C2H5OH`, `H2SO4`). Dùng bộ tách token tiến (forward
    tokenizer) như đặc tả, không dùng lookbehind độ rộng thay đổi.
- **Nghiệm thu:**
  - `H2SO4` và `H2SO3` cho `structural_hash` khác nhau; `m = 10g` và `m = 20g` cho `structural_hash` giống nhau.
  - Cùng câu hỏi ở dạng NFC và NFD cho cùng `content_hash`.
  - Hoán vị 4 phương án không đổi `content_hash`; đổi nội dung một phương án thì đổi.
  - Ít nhất 30 ca tham số hóa, trong đó có: số thập phân dấu phẩy, số mũ, phần trăm, công thức có ngoặc (`Ca(OH)2`),
    ion (`Fe3+`, `SO4 2-`), đơn vị (`m/s2`, `cm3`).
- **Không làm:** bảng `duplicate_clusters`, tìm tương đồng bằng `pgvector`, ghép cặp đề với đáp án.

### P3 — D04: lịch ôn tập FSRS-5

- **Nhánh:** `feat/d04-fsrs-core`
- **Đọc:** `chapters/03_...md` dòng "D04"; `chapters/06_9_thuat_toan_loi.md` dòng 820–1030 (chỉ phần FSRS, bỏ phần IRT).
- **Tạo:** `app/modules/flashcards/{__init__,public}.py`, `app/modules/flashcards/internal/{__init__,fsrs.py}`,
  `tests/test_fsrs_core.py`. Được thêm `fsrs` (thư viện tham chiếu `py-fsrs`) vào cuối `requirements-dev.txt`, **chỉ
  dùng trong test**.
- **Việc cần làm:** hàm thuần tính độ ổn định, độ khó, khả năng nhớ và khoảng ngày ôn kế tiếp theo 4 mức đánh giá
  (1 Again, 2 Hard, 3 Good, 4 Easy), đủ 19 trọng số `W[0..18]`; trạng thái là dataclass đóng băng có `scheduler_version`;
  ngày ôn tính theo múi giờ `Asia/Ho_Chi_Minh`.
- **Nghiệm thu:**
  - Khớp thư viện tham chiếu trên ít nhất 200 chuỗi đánh giá sinh ngẫu nhiên có hạt giống cố định (so độ ổn định, độ
    khó và khoảng ngày). Nếu có ca lệch, không nới ngưỡng: ghi rõ trong PR.
  - Cùng đầu vào cho cùng kết quả; không đọc đồng hồ hệ thống bên trong hàm (thời điểm là tham số).
  - Ca biên: thẻ mới, quên liên tiếp nhiều lần, ôn trễ nhiều ngày.
- **Không làm:** bảng `flashcards`, phần IRT/mastery.

### P4 — C03: kiểm tra cấu trúc đề THPTQG 2025+

- **Nhánh:** `feat/c03-blueprint-core`
- **Đọc:** `chapters/03_...md` dòng "C03".
- **Tạo:** `app/contracts/blueprints.py`, `app/modules/frameworks/{__init__,public}.py`,
  `app/modules/frameworks/internal/{__init__,blueprints.py}`, `tests/test_blueprint_core.py`.
- **Việc cần làm:**
  - Hằng số mô tả hai khung: Toán (90 phút, 22 câu: 12 + 4 + 6, điểm 0,25 / 1,0 / 0,5) và Lý/Hóa/Sinh (50 phút, 28 câu:
    18 + 4 + 6, điểm 0,25 / 1,0 / 0,25), tổng đều 10 điểm.
  - `validate_blueprint(blueprint)`: trả danh sách lỗi có mã (sai số câu mỗi phần, sai tổng điểm, sai thời lượng).
  - `shortage_report(blueprint, available)`: với ma trận yêu cầu (chuyên đề × độ khó × loại câu) và số câu sẵn có, trả
    số câu còn thiếu theo từng ô.
- **Nghiệm thu:** hai khung chuẩn hợp lệ và tổng đúng 10,00 bằng `Decimal`; mỗi loại sai có test riêng;
  `shortage_report` đúng khi đủ, thiếu một ô, thiếu nhiều ô, và khi kho rỗng.
- **Không làm:** bảng `assessment_frameworks`, bốc đề.

### P5 — C06: kho lưu bài ngoại tuyến bằng Dexie (TypeScript)

- **Nhánh:** `feat/c06-dexie-autosave-store`
- **Đọc:** `CLAUDE.md` mục H-04; `chapters/06_9_thuat_toan_loi.md` dòng 1578–1804; `app/contracts/autosave.py` (hàm
  `canonical_json`, `compute_payload_hash` — phía TypeScript phải cho ra **cùng chuỗi và cùng hash**).
- **Tạo:** thư mục `frontend/` mới và **chỉ** thư mục đó: `package.json`, `tsconfig.json`, cấu hình test (vitest),
  `frontend/src/lib/exam-autosave-store.ts`, `frontend/src/lib/canonical-json.ts`, test tương ứng, và
  `frontend/src/lib/__fixtures__/payload-hash-vectors.json`. Thêm `tests/test_payload_hash_vectors.py` ở phía Python
  đọc **cùng file vector** đó.
- **Việc cần làm:**
  - Lưu mỗi thay đổi vào IndexedDB thật qua Dexie **trước** khi gửi mạng.
  - Gửi kèm `client_sequence`, `expected_response_version`, `idempotency_key`, `payload_hash`.
  - Khi gộp thao tác mới: chỉ xóa bản nháp chưa từng gửi (`tries === 0`), giữ bản đã từng gửi.
  - Thử lại với khoảng chờ tăng dần có nhiễu; tự xả hàng đợi khi có mạng lại (`online`, `visibilitychange`).
  - Gặp `409` lệch phiên bản thì gọi đồng bộ lại rồi mới gửi tiếp; gặp `IDEMPOTENT_REPLAY` thì coi là thành công.
- **Nghiệm thu:**
  - `tsc --noEmit` sạch; test chạy với `fake-indexeddb`.
  - File vector có ít nhất 20 payload (khóa lộn thứ tự, lồng nhau, tiếng Việt có dấu, chuỗi rỗng, `null`, số nguyên,
    mảng). Cùng file đó qua được cả test TypeScript lẫn test Python với hash trùng từng ca.
  - Mô phỏng: mất mạng rồi có lại (hàng đợi xả đúng thứ tự), tải lại trang giữa chừng (dữ liệu còn), hai lần gửi cùng
    khóa (không tạo bản ghi trùng), `409` rồi đồng bộ lại.
- **Lưu ý đã biết:** số thực như `1.0` in ra khác nhau giữa Python và JavaScript. Không đưa số thực vào payload; ghi rõ
  ràng buộc này trong mã và có test từ chối.
- **Không làm:** giao diện phòng thi, Next.js routing, gọi API thật.

### P7 — A02/A03: lõi an toàn (không gọi mạng thật)

- **Nhánh:** `feat/a02-a03-safety-core`
- **Đọc:** `chapters/03_...md` dòng "A02" và "A03"; `chapters/06_9_thuat_toan_loi.md` dòng 1031–1100 (`AntiSSRFResolver`)
  và 1194–1260 (`SafeArchivePolicy`).
- **Tạo:** `app/modules/crawler/{__init__,public}.py`, `app/modules/crawler/internal/{__init__,ssrf.py}`,
  `app/modules/drive_sync/{__init__,public}.py`, `app/modules/drive_sync/internal/{__init__,safe_archive.py}`,
  `tests/test_ssrf_guard.py`, `tests/test_safe_archive.py`.
- **Việc cần làm:**
  - `is_public_address(ip)` và `check_resolved_addresses(host, addresses)`: chỉ chấp nhận địa chỉ toàn cục; từ chối
    loopback, mạng riêng, link-local, CGNAT `100.64.0.0/10`, `0.0.0.0/8`, IPv6 `::1`, `fc00::/7`, `fe80::/10`, và
    IPv4 bọc trong IPv6 (`::ffff:0:0/96`). Hàm nhận danh sách địa chỉ đã phân giải làm tham số (không tự gọi DNS).
  - `SafeArchivePolicy` + `inspect_zip(path)`: từ chối khi quá 4 cấp thư mục, quá 500 file, tổng dung lượng giải nén
    quá 200 MB, tỷ lệ nén từ 50:1 trở lên, hoặc có đường dẫn chứa `..`/đường dẫn tuyệt đối. Kiểm tra bằng thông tin
    trong mục lục zip **trước** khi giải nén bất cứ thứ gì.
- **Nghiệm thu:**
  - Bảng test địa chỉ: mỗi dải bị cấm có ít nhất một ca; `169.254.169.254`, `::ffff:127.0.0.1`, `::ffff:10.0.0.1` bị
    từ chối; một host phân giải ra **cả** địa chỉ công khai lẫn riêng bị từ chối.
  - Zip thử tạo ngay trong test (không tải file ngoài): zip bom nhỏ tự sinh (tỷ lệ nén cao), zip có `../evil`, zip 501
    file, zip lồng 5 cấp, và một zip hợp lệ được chấp nhận.
- **Không làm:** tải trang, `robots.txt`, OAuth Google, ghép cặp đề với đáp án.

### P6 — B04 + B06: cây phân loại và chương trình học (có bảng)

- **Chỉ bắt đầu khi** PR "G03: transactional outbox…" đã vào `main` (để chuỗi migration có một đầu duy nhất).
- **Nhánh:** `feat/b04-b06-taxonomy-curriculum`
- **Đọc:** `chapters/03_...md` dòng "B04" và "B06"; `chapters/04_postgresql_ddl_rls_grants.sql` nhóm 2 (dòng 262–372:
  `taxonomy_nodes`, `taxonomy_node_versions`, `taxonomy_aliases`, `curriculum_versions`, `curriculum_nodes`,
  `taxonomy_mappings` — **không** làm `knowledge_edges`, `graph_validation_runs`); phần policy `hybrid_read_policy` của
  các bảng này (dòng ~2048–2100) và quyền ở mục 5. Mẫu cách làm: migration và module của G03
  (`alembic/versions/a7b8c9d0e1f2_g03_outbox_inbox.py`, `app/modules/outbox/`), builder trong `app/platform/rls.py`,
  test mẫu `tests/test_rls_builders.py`.
- **Tạo:** một migration mới nối sau đầu hiện tại; `app/modules/taxonomy/` và `app/modules/curriculum/` (bố cục chuẩn,
  model ở `internal/models.py`); `tests/test_taxonomy.py`, `tests/test_curriculum.py`. Được sửa thêm **một dòng import**
  cho mỗi module trong `app/model_registry.py` (ngoại lệ duy nhất của gói này).
- **Việc cần làm:**
  - Bảng đúng tên cột, kiểu, ràng buộc của DDL. RLS **chỉ** dựng bằng builder: `hybrid_write_rls`, `hybrid_read_root`
    (bảng gốc), `hybrid_read_child` (bảng con, qua bảng cha). Quyền từng role đúng mục 5 của DDL.
  - Dịch vụ: tạo nút, tạo phiên bản mới của nút (không đổi `id` và `code_path`), truy vấn nhánh con bằng toán tử `<@`
    của `ltree`, ánh xạ nhiều-nhiều giữa nút chương trình và nút phân loại.
  - Seed cây SGK Kết nối tri thức môn Toán và Vật lí lớp 10–11–12 vào tenant hệ thống, lấy dữ liệu từ
    `app/domain/k12_curriculum_data.py` (chỉ đọc). Seed chạy bằng lệnh riêng, không nằm trong migration.
- **Nghiệm thu:**
  - `alembic upgrade head && alembic check` sạch trên database riêng (`exam_p6_dev`), không chạy trên database `exam`.
  - Test bằng role runtime (qua `app.platform.tenancy`), không bằng chủ schema: tenant B thấy nút hệ thống đang hoạt
    động, không thấy nút của tenant A, không ghi được vào tenant hệ thống.
  - Tạo phiên bản mới của một nút không làm đổi `id`; truy vấn nhánh con trả đúng tập nút.
  - `tests/security/` và `tests/test_db_foundation.py` vẫn xanh (bảng mới phải bật `FORCE ROW LEVEL SECURITY`).
  - Bộ test đầy đủ không có test nào đang qua bị trượt.
- **Không làm:** router HTTP (chờ module xác thực F05), đồ thị tri thức, báo cáo độ phủ.

## 4. Những việc luồng chính đang giữ (agent thứ hai không làm)

Chuyển engine PDF và bộ đề mẫu (`tests/golden/`); G03 và các bổ sung của nó; F05/F04 (xác thực, phân quyền, đồng ý của
phụ huynh, mã hóa); E01 (cổng AI); mọi thay đổi ở `app/platform/`; tích hợp, rà soát và merge mọi PR.

---

## 4. Đợt 2 (giao ngày 13/11/2026)

Đợt 1 (P1–P7) đã được rà soát và gộp vào `main` qua nhánh tích hợp. Trước khi làm tiếp: `git fetch origin` rồi tạo mọi
nhánh mới từ `origin/main` mới nhất. Quy tắc ở mục 2 giữ nguyên, cộng thêm các điều rút ra từ đợt 1:

1. **`.importlinter`: chỉ thêm khối của chính gói đang làm.** Ở đợt 1, PR của P2 và P7 chép cả khối của gói khác nên CI đỏ
   (`"app.modules.grading" not present in the graph`). Nhánh nào cũng phải xanh khi đứng một mình trên `main`.
2. **Không gọi hàm trong giá trị mặc định của tham số** (`policy: X = X()` bị ruff báo `B008`). Dùng hằng ở mức module.
3. Chạy `ruff check .` bằng đúng `.venv\Scripts\ruff.exe` của repo trước khi mở PR.
4. Gói frontend chạy thêm `npm run typecheck` và `npm test` trong `frontend/` (CI đã có job `frontend`).
5. Chuỗi hiển thị cho người dùng không nằm trong lõi: lõi trả mã lý do (`reason_code`), không trả câu tiếng Việt.

Thứ tự đề xuất: P13 → P8 → P9 → P10 → P11 → P12. Các gói không phụ thuộc nhau.

| Gói | Module | Loại | Cần trước | Ước lượng |
|---|---|---|---|---|
| P8 | C04 hoán vị đề tất định | Hàm thuần | P1 (đã có) | nhỏ |
| P9 | A08/A03 ghép cặp đề – đáp án 4 tín hiệu | Hàm thuần | P2 (đã có) | vừa |
| P10 | D06 cổng chọn bậc IRT + D02 thứ tự học bù | Hàm thuần | — | vừa |
| P11 | B05 chặn chu trình đồ thị tri thức + D05 sổ lỗi | Hàm thuần | — | vừa |
| P12 | A02 lõi cào phần 2 (kiểm tra redirect, giãn nhịp, giám sát parser) | Hàm thuần | P7 (đã có) | vừa |
| P13 | C06 gia cố kho lưu bài ngoại tuyến | Frontend | P5 (đã có) — chủ dự án đã duyệt 13/11 | nhỏ |

### P6 — đã xong

P6 đã được tích hợp. Luồng chính đổi mã migration thành `c9d0e1f2a3b4` (nối sau `b8c9d0e1f2a3` của G03) vì trùng mã;
quyền chỉ cấp cho `ulp_app_rw` là đúng đặc tả mục 5. Migration mới của đợt sau phải nối sau head hiện tại
(`alembic heads`), không tự đặt mã trùng với file đã có.

### P8 — C04: hoán vị câu hỏi và phương án tất định

- Nhánh `feat/c04-permutation-core`. Module `app/modules/assembly/` (`public.py`, `internal/permutation.py`), test
  `tests/test_permutation_core.py`.
- Đặc tả: `chapters/06_9_thuat_toan_loi.md` dòng 617–650 (`generate_attempt_permutation`).
- Yêu cầu:
  - Đầu vào là dataclass đóng băng (`question_version_id`, `section_code`, `stimulus_id | None`, `item_type`), đầu ra là
    dataclass (`ordered_question_ids`, `option_permutations`). Cùng `attempt_seed` ⇒ cùng kết quả, không phụ thuộc thứ tự
    phần tử đầu vào (sắp đầu vào theo khoá ổn định trước khi xáo).
  - Các phần giữ thứ tự `PART_I` → `PART_II` → `PART_III` → `PART_IV`; câu chung một `stimulus_id` luôn liền nhau và giữ
    thứ tự gốc trong nhóm; chỉ câu `SINGLE_CHOICE` có hoán vị phương án.
  - Ngữ nghĩa hoán vị phải khớp P1: `option_permutation[vị trí hiển thị] = khoá gốc`. Test vòng kín với
    `app.modules.grading.public.grade_item` (chỉ import trong test): chọn đúng phương án ở vị trí hiển thị ⇒ được điểm.
  - Không dùng trạng thái `random` toàn cục. Có ít nhất 3 vector vàng ghi cứng (seed → kết quả) để bắt thay đổi thuật toán.
- Điểm cần chủ dự án quyết (ghi trong PR): phương án cố định vị trí kiểu "Cả A, B, C đều đúng" — đặc tả chưa nói.

### P9 — A08/A03: ghép cặp đề và đáp án bằng 4 tín hiệu

- Nhánh `feat/a08-pairing-core`. Thêm `app/modules/dedup/internal/pairing.py`, xuất qua `app/modules/dedup/public.py`; test
  `tests/test_pairing_core.py`. Không sửa `hashing.py`.
- Đặc tả: dòng 402–427 (`score_exam_and_solution_pairing`) và 1352–1395 (`auto_pair_exam_and_hdc_documents`).
- Yêu cầu:
  - Trọng số 0,40 tên file / 0,25 cùng thư mục / 0,20 cùng môn / 0,15 số câu; ngưỡng 0,85 `CONFIRMED`, 0,60 `PROPOSED`.
    Tính bằng `Decimal` hoặc làm tròn tại một chỗ duy nhất.
  - Nhận diện file đáp án và làm sạch tên phải đúng cho cả có dấu, không dấu, NFC/NFD, gạch dưới/gạch nối
    (`Đáp án`, `dap_an`, `HDC`, `hướng dẫn chấm`, `loi-giai`). Mã mẫu trong đặc tả bỏ sót dạng có dấu — sửa và ghi trong PR.
  - Thiếu `folder_id` hoặc `subject_code` ở cả hai bên **không** được tính là "giống nhau".
  - Một file đáp án chỉ gán cho tối đa một đề (ghép theo điểm giảm dần, hoà thì theo `doc_id`); kết quả tất định.
- Điểm cần chủ dự án quyết: mã mẫu cho phép một đáp án ghép nhiều đề; gói này làm chặt hơn.

### P10 — D06: cổng chọn bậc IRT; D02: thứ tự học bù kiến thức tiên quyết

- Nhánh `feat/d06-irt-tier-core`. Module `app/modules/mastery/` (`internal/irt_tier.py`) và `app/modules/learning_path/`
  (`internal/remediation.py`), mỗi module một khối `.importlinter`; test `tests/test_irt_tier_core.py`,
  `tests/test_remediation_core.py`.
- Đặc tả: dòng 957–1029; nguồn chuẩn ngưỡng là `irt_tier_registry` trong `docs/architecture/platform_manifest.yaml`.
- Yêu cầu D06: trả về bậc **và** danh sách tiêu chí không đạt của bậc kế trên. Có test đọc `platform_manifest.yaml` và
  khẳng định mọi ngưỡng trong mã bằng đúng manifest (4 bậc, `fallback_to`). Test biên từng ngưỡng (bằng / ngay dưới), NaN
  và số âm bị từ chối.
- Yêu cầu D02: chịu được đồ thị có chu trình và cạnh trùng (không đệ quy vô hạn; dùng duyệt lặp), thứ tự tất định, nút tiên
  quyết đứng trước nút phụ thuộc, trả `reason_code` (`WEAK_TARGET`, `PREREQUISITE_OF`) kèm id nút phụ thuộc.
- Điểm cần chủ dự án quyết: đặc tả ghi ngưỡng tiên quyết 0,60 nhưng câu giải thích ghi "< 50%"; để thành tham số, mặc
  định 0,60.

### P11 — B05: chặn chu trình đồ thị tri thức; D05: máy trạng thái sổ lỗi

- Nhánh `feat/b05-d05-cores`. Module `app/modules/knowledge_graph/` (`internal/cycles.py`) và
  `app/modules/error_notebook/` (`internal/state.py`); test `tests/test_graph_cycles.py`, `tests/test_error_notebook_core.py`.
- Đặc tả: dòng của module B05 và D05 trong `chapters/03_dac_ta_47_modules_va_6_workspaces.md`; các bảng tương ứng trong
  `chapters/04_postgresql_ddl_rls_grants.sql` (chỉ để lấy tên trạng thái và cột, không tạo bảng).
- Yêu cầu B05: hàm `would_create_cycle(edges, new_edge)` và `find_cycle(edges)` trả về đường đi của chu trình để API sau
  này trả `422` có nội dung; tự nối chính nó bị từ chối; chạy được 50.000 cạnh dưới 1 giây (duyệt lặp).
- Yêu cầu D05: hàm chuyển trạng thái thuần `apply_attempt(state, is_correct, at)`; đúng **2 lần liên tiếp** mới
  `RESOLVED`; sai một lần thì đếm lại từ 0 và mở lại nếu đang `RESOLVED`; tên trạng thái lấy đúng từ DDL.

### P12 — A02: lõi cào phần 2

- Nhánh `feat/a02-crawl-policy-core`. Thêm vào `app/modules/crawler/internal/` các file `redirects.py`, `politeness.py`,
  `drift.py`; test `tests/test_crawl_policy_core.py`. Không sửa `ssrf.py`.
- Đặc tả: dòng 1087–1168.
- Yêu cầu:
  - `verify_url_or_redirect_hop`: chỉ `https` (và cổng 443 trừ khi cấu hình), từ chối URL có `user:pass@`, host là IP
    trần, tên miền ngoài danh sách cho phép; mỗi bước redirect kiểm lại bằng `check_resolved_addresses` (P7) với hàm phân
    giải DNS **được tiêm vào**; giới hạn số bước (mặc định 5); phát hiện vòng lặp redirect. Không mở socket trong gói này.
  - Giãn nhịp theo tên miền: đồng hồ được tiêm vào, không `time.sleep` trong lõi — trả về số giây phải chờ.
  - `ParserDriftMonitor`: cửa sổ 30, ngưỡng 15%, chưa đủ 10 mẫu thì chưa báo; không dùng giá trị mặc định khả biến.

### P13 — C06: gia cố kho lưu bài ngoại tuyến (đã duyệt 13/11)

- Nhánh `feat/c06-autosave-hardening`. Chỉ sửa `frontend/src/lib/exam-autosave-store.ts` và test của nó.
- Đợt 1 làm đúng theo mã mẫu Mục 6.9, nhưng mã mẫu có ba chỗ có thể làm mất bài của học sinh. Chủ dự án đã duyệt lệch
  đặc tả theo hướng an toàn hơn; ghi rõ điều này trong mô tả PR. Mỗi điểm dưới đây phải có test riêng:

- `401` và `403` không mang `error_code = ATTEMPT_DEADLINE_EXPIRED`: **giữ** hàng đợi, dừng gửi, báo lên giao diện để đăng
  nhập lại. Hiện tại `401` làm xoá từng bản ghi, `403` bất kỳ xoá toàn bộ hàng đợi.
- Hết giờ / đã nộp: chỉ xoá các bản ghi của đúng `attemptId` đó, không `clear()` cả bảng.
- `409` lần thứ hai sau khi đồng bộ lại: không xoá âm thầm; chuyển bản ghi sang trạng thái `conflict` và gọi
  `onConflictModal` để học sinh chọn.
- Không tạo `defaultExamAutosaveStore` ngay khi import; tạo lười qua hàm.
- Bản ghi bị giữ lại không được chặn các câu khác: hàng đợi bỏ qua bản ghi `conflict` và gửi tiếp các bản ghi sau.
- Thêm `getQueueState()` trả số bản ghi đang chờ / đang xung đột / cần đăng nhập lại để giao diện hiển thị.

---

## 5. Việc sửa sau rà soát đợt 2 (giao ngày 13/11/2026)

Đợt 2 (P8–P13) đã được rà soát và gộp vào `main`. Các điểm dưới đây là thiếu sót tìm thấy khi rà soát; mỗi gói một nhánh
từ `origin/main` mới nhất, một PR, kèm test cho từng điểm.

### P14 — C06: phần còn thiếu của P13 (ưu tiên cao)

- Nhánh `feat/c06-autosave-queue-state`. Chỉ sửa `frontend/src/lib/exam-autosave-store.ts` và test của nó.
- `getQueueState()` chưa có (P13 đã yêu cầu): trả `{ pending, conflict, rejected, authRequired }` để giao diện hiển thị.
- Bản ghi `conflict` hiện không có cách xử lý nên nằm lại mãi. Thêm `resolveConflict(id, "keep_mine" | "take_server")`:
  `keep_mine` đưa bản ghi về `pending` với `expected_response_version` mới nhất; `take_server` xoá bản ghi.
- Các lỗi `4xx` còn lại (ví dụ `422 PAYLOAD_HASH_MISMATCH`, `400`, `404`) hiện vẫn xoá câu trả lời. Đổi thành trạng thái
  `rejected` kèm `error_code`, không gửi lại tự động, không chặn các bản ghi sau; chỉ xoá khi giao diện gọi `discard(id)`.
- Sau khi gặp `401`/`403` xác thực, không gọi lại `onAuthRequired` cho mỗi lần `enqueue`: ghi nhớ trạng thái và chỉ gửi lại
  khi giao diện gọi `resume()` (hoặc token thay đổi).

### P15 — A02: chuẩn hoá đường dẫn trước khi so danh sách cho phép

- Nhánh `feat/a02-path-normalization`. Chỉ sửa `app/modules/crawler/internal/redirects.py` và test.
- `allowed_path_prefixes` đang so trên đường dẫn thô: `/allowed/../admin` và `/allowed/%2e%2e/admin` lọt qua tiền tố
  `/allowed/`. Giải mã phần trăm một lần, từ chối đường dẫn còn chứa `%` sau giải mã, `\`, ký tự điều khiển hoặc đoạn `..`,
  rồi mới so tiền tố; tiền tố `/a` không được khớp `/ab` (so theo ranh giới đoạn).
- `robots_txt_content=None` hiện được hiểu là "cho phép". Thêm tham số bắt buộc nêu rõ ý định (`robots: RobotsPolicy`
  với ba trạng thái: nội dung, `NOT_FOUND`, `UNAVAILABLE`); `UNAVAILABLE` thì từ chối.

### P16 — sửa nhỏ ở các lõi thuần

- Nhánh `fix/core-review-followups`.
- D02 `resolve_prerequisite_remediation_order`: giữ thứ tự ưu tiên của `weak_node_ids` do nơi gọi truyền vào (hiện bị sắp
  lại theo UUID); nút vừa là mục tiêu yếu vừa là tiên quyết phải mang `WEAK_TARGET`.
- C04 `generate_attempt_permutation`: `section_code` lạ phải báo lỗi rõ ràng, không âm thầm xếp cuối.
- A08 ghép cặp: file gộp cả đề và đáp án (ví dụ "Đề + đáp án Toán 11") không được coi là file đáp án thuần; trả loại
  `EXAM_WITH_SOLUTION` và không đưa vào ghép cặp.
- D05 `apply_attempt`: từ chối `at` không có múi giờ và `at` sớm hơn `last_wrong_at`.

---

## 6. Đợt 3 (giao ngày 13/11/2026)

Làm sau P14–P16 ở mục 5. Tám gói dưới đây đều là lõi thuần (không bảng, không migration, không mạng, không AI), không phụ
thuộc nhau và không đụng phần luồng chính đang làm (bộ đọc PDF, `app/platform/`, các router). Quy tắc ở mục 2 và năm điều rút
ra ở mục 4 vẫn áp dụng. Thêm ba điều từ lần rà soát đợt 2:

1. **Làm đủ mọi gạch đầu dòng của gói.** Ở P13, `getQueueState()` có trong yêu cầu nhưng không được làm. Mô tả PR phải có
   bảng "yêu cầu → test chứng minh" cho từng gạch đầu dòng.
2. **Dữ liệu người dùng không bị xoá âm thầm.** Mọi nhánh lỗi phải giữ lại dữ liệu hoặc trả mã lỗi để nơi gọi quyết định.
3. **So khớp đường dẫn, tên miền, tên file trên dạng đã chuẩn hoá**, không so trên chuỗi thô.

Thứ tự đề xuất: P19 → P20 → P17 → P18 → P21 → P22 → P23 → P24.

| Gói | Module | Loại | Ước lượng |
|---|---|---|---|
| P17 | A06 hình học bố cục trang | Hàm thuần | vừa |
| P18 | C02 chuẩn hoá đáp án số và khoá môn học | Hàm thuần | nhỏ |
| P19 | C05 lọc đáp án khỏi dữ liệu gửi cho phòng thi | Hàm thuần (bảo mật) | nhỏ |
| P20 | F04/G02 chỉ mục mù, làm sạch nhật ký, xoá văn bản tự do | Hàm thuần (bảo mật) | vừa |
| P21 | B07 chuẩn hoá văn bản tìm kiếm tiếng Việt | Hàm thuần | nhỏ |
| P22 | C05/C06 đồng hồ máy chủ và đếm ngược phía trình duyệt | Frontend | nhỏ |
| P23 | A01 máy trạng thái đợt nạp và khoá khử trùng tài liệu | Hàm thuần | vừa |
| P24 | E01 trần ngân sách AI và xoay khoá nhà cung cấp | Hàm thuần | vừa |

### P17 — A06: hình học bố cục trang

- Nhánh `feat/a06-layout-geometry`. Module `app/modules/layout/` (`internal/geometry.py`, `internal/margins.py`); test
  `tests/test_layout_geometry.py`.
- Đặc tả: `chapters/06_9_thuat_toan_loi.md` dòng 29–195 (`BBoxRect`, `merge_intersecting_rects_union_find`,
  `detect_dynamic_repeating_margins`). **Không** làm phần đọc PDF (dòng 197 trở đi) và không import thư viện PDF nào.
- Yêu cầu:
  - `BBoxRect` đóng băng, từ chối toạ độ NaN/vô hạn và hình có `x1 < x0` hoặc `y1 < y0`; `overlap_ratio` của hình diện tích 0
    không chia cho 0.
  - Gộp hình giao nhau bằng union-find có nén đường; kết quả không phụ thuộc thứ tự đầu vào (sắp kết quả theo `(y0, x0)`);
    2.000 hình chạy dưới 1 giây (dùng lưới hoặc quét theo trục, không so mọi cặp nếu vượt thời gian).
  - Phát hiện lề lặp (đầu trang, chân trang, watermark): đầu vào là danh sách dòng văn bản theo trang (chuỗi + hộp), đầu ra
    là các dải cần bỏ; văn bản chỉ khác nhau ở số trang ("Trang 1/4", "Trang 2/4") vẫn được coi là lặp; tài liệu 1 trang
    không bỏ gì.

### P18 — C02: chuẩn hoá đáp án số và khoá môn học

- Nhánh `feat/c02-answer-canonicalization`. Thêm `app/modules/quality/internal/answers.py`, xuất qua
  `app/modules/quality/public.py`; test `tests/test_answer_canonicalization.py`. **Không sửa** `sympy_runner.py`,
  `sympy_sandbox_worker.py` và không gọi SymPy.
- Đặc tả: dòng 500–526 (`enforce_subject_lock`, `canonicalize_numeric_answer`).
- Yêu cầu:
  - `canonicalize_numeric_answer` trả `Decimal | None`: nhận `0,5`, `0.5`, `-1,25`, `3/4`, `50%` (thành `0.5` hay `50` là
    điểm cần quyết — làm theo mã mẫu là bỏ ký hiệu, và ghi rõ trong PR), đơn vị ở cuối (`10 m/s`, `2,5kg`, `30°`). Mã mẫu
    cắt nhầm dấu `/` của phân số khi có đơn vị (`3/4 kg`) và chấp nhận `1.000` như một nghìn hay một — phải từ chối dạng mơ
    hồ này giống `parse_short_numeric_vn` của P1. Không dùng `float`.
  - `enforce_subject_lock`: môn của nguồn luôn thắng; từ khoá so trên văn bản đã chuẩn hoá NFC và không phân biệt hoa thường;
    danh sách từ khoá là tham số có giá trị mặc định theo đặc tả; trả thêm `reason_code`.

### P19 — C05: lọc đáp án khỏi dữ liệu gửi cho phòng thi (bảo mật)

- Nhánh `feat/c05-paper-sanitizer`. Module `app/modules/delivery/` (`internal/sanitizer.py`), hợp đồng
  `app/contracts/delivery.py`; test `tests/test_paper_sanitizer.py`.
- Đặc tả: dòng module C05 trong `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng 37) và các bảng `question_versions`,
  `question_options`, `question_version_answers`, `assessment_answer_keys` trong DDL (chỉ để lấy tên cột).
- Yêu cầu:
  - Đầu ra dựng theo **danh sách cho phép** các trường (model Pydantic `extra="forbid"`), không phải xoá dần trường cấm: trường
    lạ ở đầu vào không bao giờ lọt ra.
  - Không trường nào trong đầu ra mang đáp án, lời giải, điểm từng ý, `is_correct`, thứ tự gốc của phương án, hay id cho
    phép suy ra phương án đúng. Phương án được xếp theo hoán vị của P8 và gắn lại nhãn hiển thị A–D.
  - Test "quét rò rỉ": với 200 đề sinh ngẫu nhiên có cài chuỗi đánh dấu vào mọi trường đáp án (kể cả lồng sâu trong JSON),
    JSON đầu ra không chứa chuỗi đánh dấu nào.

### P20 — F04/G02: chỉ mục mù, làm sạch nhật ký, xoá văn bản tự do (bảo mật)

- Nhánh `feat/f04-pii-helpers-core`. Module `app/modules/privacy/` (`internal/blind_index.py`, `internal/audit_sanitizer.py`,
  `internal/scrub.py`); test `tests/test_pii_helpers_core.py`. Chỉ dùng thư viện chuẩn (`hmac`, `hashlib`).
  **Không** làm phần mã hoá AES/Vault (dòng 1430–1484): phần đó cần thư viện mới và thuộc luồng chính.
- Đặc tả: dòng 1418–1429 (`compute_email_blind_index`), 1486–1503 (`scrub_free_text_pii_records`), 1560–1576
  (`sanitize_audit_json`).
- Yêu cầu:
  - Chỉ mục mù email: chuẩn hoá NFKC + bỏ khoảng trắng + chữ thường trước khi HMAC-SHA256; `pepper` rỗng hoặc ngắn hơn 32
    byte bị từ chối; so sánh bằng `hmac.compare_digest`.
  - Làm sạch nhật ký: mã mẫu chỉ đệ quy vào `dict` — phải đi cả vào danh sách lồng nhau; so khoá không phân biệt hoa thường
    và kiểu viết (`phoneNumber`, `phone_number`, `Phone-Number`); ngoài so theo tên khoá, che thêm giá trị **trông giống**
    email hoặc số điện thoại Việt Nam nằm trong chuỗi tự do; không sửa đối tượng đầu vào; giới hạn độ sâu để chặn đầu vào
    lồng vô hạn.
  - Xoá văn bản tự do: hàm thuần trả bản ghi mới (mã mẫu sửa tại chỗ) và trả số bản ghi đã xoá theo từng loại.

### P21 — B07: chuẩn hoá văn bản tìm kiếm tiếng Việt

- Nhánh `feat/b07-search-normalization`. Module `app/modules/search/` (`internal/normalize.py`); test
  `tests/test_search_normalization.py`.
- Đặc tả: dòng module B07 trong chương 03 (dòng 27).
- Yêu cầu:
  - `fold_for_search(text)`: NFC → bỏ dấu (kể cả `đ/Đ`) → chữ thường → gộp khoảng trắng; kết quả phải **bằng đúng** kết quả
    của hàm `f_unaccent` + `lower` trong PostgreSQL. Có test đối chiếu với database (file test riêng, dùng fixture của
    `tests/conftest.py`) trên ít nhất 300 chuỗi gồm toàn bộ nguyên âm có dấu tiếng Việt, dạng NFD và ký hiệu toán.
  - `parse_search_query(raw)`: tách cụm trong ngoặc kép, từ loại trừ bằng `-`, bỏ ký tự điều khiển, giới hạn 200 ký tự và
    20 từ; trả dataclass — **không** ghép chuỗi SQL trong gói này.
  - Dùng lại `fold_vietnamese_ascii` của P9 nếu hợp, nhưng không import `internal` của module khác: chuyển hàm dùng chung sang
    `app/contracts/text.py` trong chính PR này và sửa P9 import từ đó.

### P22 — C05/C06: đồng hồ máy chủ và đếm ngược phía trình duyệt

- Nhánh `feat/c06-server-clock`. File `frontend/src/lib/server-clock.ts`, `frontend/src/lib/exam-countdown.ts` và test của
  chúng. Không sửa `exam-autosave-store.ts`.
- Yêu cầu:
  - `ServerClock`: ước lượng độ lệch giờ từ các cặp (giờ gửi, giờ nhận, giờ máy chủ) theo cách của NTP, lấy mẫu có độ trễ
    khứ hồi nhỏ nhất trong 5 mẫu gần nhất; đồng hồ và hàm `fetch` được tiêm vào; dùng đồng hồ đơn điệu
    (`performance.now`) để người dùng đổi giờ máy không làm sai thời gian còn lại.
  - `ExamCountdown`: nhận `deadline_at` (ISO, giờ máy chủ), phát sự kiện mỗi giây, ở mốc còn 5 phút, 1 phút và khi hết giờ
    (mỗi mốc đúng một lần, kể cả khi tab bị treo rồi tỉnh lại); hết giờ thì gọi `onExpire` đúng một lần.
  - Test bằng đồng hồ giả: máy chạy nhanh/chậm 10 phút, đổi giờ giữa chừng, tab ngủ 3 phút.

### P23 — A01: máy trạng thái đợt nạp và khoá khử trùng tài liệu

- Nhánh `feat/a01-ingestion-core`. Module `app/modules/ingestion/` (`internal/state.py`, `internal/fingerprint.py`); test
  `tests/test_ingestion_core.py`.
- Đặc tả: dòng module A01 trong chương 03 (dòng 7); bảng `acquisition_jobs`, `pipeline_stage_runs`, `dead_letter_items`
  trong DDL (lấy đúng tên trạng thái từ các ràng buộc `CHECK`, không tự đặt tên mới).
- Yêu cầu:
  - Bảng chuyển trạng thái tường minh cho đợt nạp và cho từng chặng; chuyển không hợp lệ ném lỗi có mã; `retry` chỉ từ trạng
    thái lỗi, `cancel` không được từ trạng thái kết thúc; quá số lần thử thì sang hàng chờ lỗi (`dead_letter`).
  - Thử lại là idempotent: áp cùng một sự kiện hai lần cho cùng kết quả (test 50 kịch bản sinh ngẫu nhiên có seed).
  - Khoá khử trùng: SHA-256 đọc theo khối từ luồng byte (không nạp cả file vào bộ nhớ), kèm kích thước; hàm quyết định
    "trùng → trả bản cũ" là hàm thuần nhận tập khoá đã có.

### P24 — E01: trần ngân sách AI và xoay khoá nhà cung cấp

- Nhánh `feat/e01-gateway-core`. Module `app/modules/ai_gateway/` (`internal/budget.py`, `internal/key_ring.py`); test
  `tests/test_ai_gateway_core.py`. Không gọi nhà cung cấp AI nào, không import `app/ai/`.
- Đặc tả: dòng module E01 trong chương 03 (dòng 56).
- Yêu cầu:
  - Trần ngân sách theo ngày cho từng tenant, tính bằng `Decimal`; "đặt chỗ trước, quyết toán sau" để hai yêu cầu đồng thời
    không cùng vượt trần; chạm trần thì từ chối 100% kèm mã lỗi; ngày tính theo `Asia/Ho_Chi_Minh`; đồng hồ được tiêm vào.
  - Vòng khoá: gặp `429` thì đánh dấu khoá nghỉ đến thời điểm cho trước và chọn khoá kế tiếp trong O(1); mọi khoá đều nghỉ
    thì trả thời gian phải chờ, không ngủ trong lõi; khoá hỏng vĩnh viễn (`401`) bị loại hẳn; không bao giờ ghi giá trị khoá
    vào thông báo lỗi, `repr` hay log (test kiểm tra điều này).
  - Điểm cần chủ dự án quyết: trần mặc định `daily_ai_budget_usd` — để thành tham số bắt buộc, không đặt mặc định.
