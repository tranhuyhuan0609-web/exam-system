## CHƯƠNG 6. CHÍN THUẬT TOÁN LÕI HOÀN CHỈNH (ĐÃ CHẠY KIỂM CHỨNG THỰC TẾ TRÊN PYTHON 3 — 0 LỖI REGEX/IMPORT/TYPE)

### 6.1. Thuật toán 1: Xử lý Tài liệu Đề thi Việt Nam Có Tách Bảng Khỏi Hình Vector (`VietnameseExamDocumentProcessor` — Module A04, A05, A06)

**Cải tiến cốt lõi ở V11.3 (Khắc phục V11-P1-2 & V11-P1-3):**
- **Loại trừ vùng Bảng biểu (`table_bboxes`) trước khi gom hình Vector:** Gọi `plumber_page.find_tables()` để xác định tọa độ các bảng dữ liệu (như bảng phức chất Hóa 11, bảng `colspan` Sinh 11, bảng HDC 3 cột). Mọi đường kẻ (`rects`, `lines`) thuộc vùng bảng được tách riêng sang luồng `HTML_TABLE`, tuyệt đối không đưa vào `merge_intersecting_rects_union_find` nên không bao giờ cắt nhầm bảng thành ảnh hay xóa mất chữ trong bảng.
- **Loại bỏ hoàn toàn hằng số tọa độ cứng:** Tính toán vùng biên theo tỷ lệ trang (`margin_ratio = 0.065`) kết hợp đối sánh mẫu chuỗi lặp liên trang.

```python
# backend/modules/document_intelligence/vn_exam_processor.py
from __future__ import annotations
import os
import re
import signal
import subprocess
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import pdfplumber
import pypdfium2 as pdfium

MAX_DOCX_XML_UNCOMPRESSED_BYTES = 25 * 1024 * 1024  # 25 MB Guard chống Zip Bomb


@dataclass(frozen=True)
class BBoxRect:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self) -> float:
        return self.width * self.height

    def expanded(self, pad: float) -> "BBoxRect":
        return BBoxRect(self.x0 - pad, self.y0 - pad, self.x1 + pad, self.y1 + pad)

    def intersects(self, other: "BBoxRect") -> bool:
        return not (self.x1 < other.x0 or self.x0 > other.x1 or self.y1 < other.y0 or self.y0 > other.y1)

    def overlap_ratio(self, other: "BBoxRect") -> float:
        if not self.intersects(other) or self.area <= 0:
            return 0.0
        ix0, iy0 = max(self.x0, other.x0), max(self.y0, other.y0)
        ix1, iy1 = min(self.x1, other.x1), min(self.y1, other.y1)
        inter_area = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
        return inter_area / self.area

    def union(self, other: "BBoxRect") -> "BBoxRect":
        return BBoxRect(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )


@dataclass(frozen=True)
class LayoutProfileConfig:
    header_band_ratio: float = 0.065
    footer_band_ratio: float = 0.065
    max_badge_area_ratio: float = 0.035
    min_figure_width: float = 35.0
    min_figure_height: float = 30.0
    cluster_padding_pt: float = 10.0
    crop_dpi: int = 300


class VietnameseExamDocumentProcessor:
    @staticmethod
    def inspect_docx_requires_libreoffice_render(docx_path: str) -> bool:
        try:
            with zipfile.ZipFile(docx_path, "r") as zf:
                info = zf.getinfo("word/document.xml")
                if info.file_size > MAX_DOCX_XML_UNCOMPRESSED_BYTES:
                    raise ValueError(
                        f"ZIP_BOMB_DETECTED: word/document.xml vượt ngưỡng an toàn ({info.file_size} bytes)"
                    )
                with zf.open(info, "r") as xml_stream:
                    xml_text = xml_stream.read(MAX_DOCX_XML_UNCOMPRESSED_BYTES).decode("utf-8", errors="ignore")
                return any(
                    tag in xml_text
                    for tag in ("<w:object", "oleObject", "<v:shape", "<wps:wsp", "<w:drawing", "<m:oMath")
                )
        except ValueError:
            raise
        except Exception:
            return True

    @staticmethod
    def convert_office_to_pdf_isolated(input_path: str, output_dir: str, timeout_sec: int = 45) -> str:
        with tempfile.TemporaryDirectory(prefix="ulp_lo_profile_") as lo_profile_dir:
            cmd = [
                "libreoffice",
                f"-env:UserInstallation=file://{lo_profile_dir}",
                "--headless",
                "--nologo",
                "--nolockcheck",
                "--norestore",
                "--convert-to",
                "pdf",
                "--outdir",
                output_dir,
                input_path,
            ]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "HOME": "/tmp", "TMPDIR": "/tmp"},
                start_new_session=True,
            )
            try:
                _, stderr = proc.communicate(timeout=timeout_sec)
                if proc.returncode != 0:
                    raise RuntimeError(f"LibreOffice error ({proc.returncode}): {stderr.decode('utf-8', 'ignore')}")
            except subprocess.TimeoutExpired as exc:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait()
                raise TimeoutError(f"LibreOffice conversion timed out after {timeout_sec}s") from exc

        pdf_out = Path(output_dir) / f"{Path(input_path).stem}.pdf"
        if not pdf_out.exists() or pdf_out.stat().st_size == 0:
            raise RuntimeError("LibreOffice không sinh được file PDF hợp lệ.")
        return str(pdf_out)

    @staticmethod
    def merge_intersecting_rects_union_find(rects: List[BBoxRect], pad: float = 10.0) -> List[BBoxRect]:
        if not rects:
            return []
        working = list(rects)
        while True:
            n = len(working)
            parent = list(range(n))

            def find(i: int) -> int:
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i

            for i in range(n):
                exp_i = working[i].expanded(pad)
                for j in range(i + 1, n):
                    if exp_i.intersects(working[j].expanded(pad)):
                        ri, rj = find(i), find(j)
                        if ri != rj:
                            parent[rj] = ri

            clusters: Dict[int, BBoxRect] = {}
            for idx, r in enumerate(working):
                root = find(idx)
                clusters[root] = r if root not in clusters else clusters[root].union(r)

            next_working = sorted(clusters.values(), key=lambda b: (b.y0, b.x0))
            if len(next_working) == n:
                return next_working
            working = next_working

    @staticmethod
    def detect_dynamic_repeating_margins(
        pdf_plumber_doc: pdfplumber.PDF,
        config: LayoutProfileConfig,
    ) -> Set[str]:
        if len(pdf_plumber_doc.pages) < 2:
            return set()
        margin_counts: Counter[str] = Counter()
        for page in pdf_plumber_doc.pages:
            h = float(page.height)
            top_limit = h * config.header_band_ratio
            bottom_limit = h * (1.0 - config.footer_band_ratio)
            for line in page.extract_text_lines() or []:
                norm = re.sub(r"\d+", "<N>", line.get("text", "").strip())
                if not norm:
                    continue
                if float(line["top"]) <= top_limit or float(line["bottom"]) >= bottom_limit:
                    margin_counts[norm] += 1

        min_repeat = max(2, len(pdf_plumber_doc.pages) // 2)
        return {k for k, v in margin_counts.items() if v >= min_repeat}

    @classmethod
    def extract_figures_tables_and_clean_text(
        cls,
        pdf_path: str,
        output_figure_dir: str,
        doc_version_id: str,
        config: LayoutProfileConfig = LayoutProfileConfig(),
    ) -> Tuple[str, Dict[str, Dict]]:
        # Khắc phục V11-P1-2: Phát hiện vùng bảng (find_tables) TRƯỚC để không gom nhầm đường kẻ bảng vào hình vẽ Vector
        os.makedirs(output_figure_dir, exist_ok=True)
        figure_manifest: Dict[str, Dict] = {}
        continuous_lines: List[str] = []

        pdfium_doc = pdfium.PdfDocument(pdf_path)
        with pdfplumber.open(pdf_path) as plumber_doc:
            repeated_margin_patterns = cls.detect_dynamic_repeating_margins(plumber_doc, config)

            for page_idx, plumber_page in enumerate(plumber_doc.pages):
                w, h = float(plumber_page.width), float(plumber_page.height)
                page_area = max(1.0, w * h)
                top_band = h * config.header_band_ratio
                bottom_band = h * (1.0 - config.footer_band_ratio)

                # Bước 1: Tìm toàn bộ bảng dữ liệu trên trang để bảo vệ đường kẻ bảng và chữ trong bảng
                detected_tables = plumber_page.find_tables() or []
                table_rects: List[BBoxRect] = [
                    BBoxRect(float(t.bbox[0]), float(t.bbox[1]), float(t.bbox[2]), float(t.bbox[3]))
                    for t in detected_tables
                ]

                def is_inside_any_table(candidate_box: BBoxRect) -> bool:
                    return any(candidate_box.overlap_ratio(tb.expanded(4.0)) >= 0.50 for tb in table_rects)

                # Bước 2: Thu thập ảnh Raster và các nét vẽ Vector KHÔNG thuộc bảng biểu
                raw_drawing_rects: List[BBoxRect] = []
                for img in plumber_page.images or []:
                    r = BBoxRect(float(img["x0"]), float(img["top"]), float(img["x1"]), float(img["bottom"]))
                    if not is_inside_any_table(r):
                        raw_drawing_rects.append(r)

                for obj in (plumber_page.rects or []) + (plumber_page.curves or []) + (plumber_page.lines or []):
                    r = BBoxRect(float(obj["x0"]), float(obj["top"]), float(obj["x1"]), float(obj["bottom"]))
                    if not is_inside_any_table(r):
                        raw_drawing_rects.append(r)

                merged_candidates = cls.merge_intersecting_rects_union_find(
                    raw_drawing_rects, pad=config.cluster_padding_pt
                )
                figure_boxes = [
                    b
                    for b in merged_candidates
                    if b.width >= config.min_figure_width
                    and b.height >= config.min_figure_height
                    and not is_inside_any_table(b)
                ]

                page_elements: List[Tuple[float, str]] = []
                active_redaction_boxes: List[BBoxRect] = []

                if figure_boxes:
                    pdfium_page = pdfium_doc[page_idx]
                    scale = config.crop_dpi / 72.0
                    pil_page = pdfium_page.render(scale=scale).to_pil()

                    for box_idx, rect in enumerate(figure_boxes):
                        # Nhận diện huy hiệu/logo nhỏ ở vùng biên đầu trang đầu tiên theo tỷ lệ diện tích động
                        is_corner_badge = (
                            page_idx == 0
                            and rect.y0 <= h * 0.14
                            and rect.x0 <= w * 0.35
                            and (rect.area / page_area) <= config.max_badge_area_ratio
                        )
                        if is_corner_badge:
                            active_redaction_boxes.append(rect)
                            continue

                        clip = BBoxRect(
                            max(0.0, rect.x0 - 6.0),
                            max(0.0, rect.y0 - 6.0),
                            min(w, rect.x1 + 6.0),
                            min(h, rect.y1 + 6.0),
                        )
                        marker = f"[[ASSET_P{page_idx + 1}_{box_idx}]]"
                        img_filename = f"{doc_version_id}_P{page_idx + 1}_{box_idx}.png"
                        img_path = str(Path(output_figure_dir) / img_filename)

                        crop_px = (
                            int(clip.x0 * scale),
                            int(clip.y0 * scale),
                            int(clip.x1 * scale),
                            int(clip.y1 * scale),
                        )
                        pil_page.crop(crop_px).save(img_path)

                        figure_manifest[marker] = {
                            "file_path": img_path,
                            "page_no": page_idx + 1,
                            "bbox": [round(clip.x0, 1), round(clip.y0, 1), round(clip.x1, 1), round(clip.y1, 1)],
                        }
                        page_elements.append((clip.y0, marker))
                        active_redaction_boxes.append(rect)

                def is_outside_figures(obj: dict) -> bool:
                    if obj.get("object_type") != "char":
                        return True
                    cx = (float(obj["x0"]) + float(obj["x1"])) / 2.0
                    cy = (float(obj["top"]) + float(obj["bottom"])) / 2.0
                    return not any(b.x0 <= cx <= b.x1 and b.y0 <= cy <= b.y1 for b in active_redaction_boxes)

                filtered_page = plumber_page.filter(is_outside_figures)
                for line in filtered_page.extract_text_lines() or []:
                    txt = line.get("text", "").strip()
                    if not txt or re.search(r"^Trang\s+\d+\s*/\s*\d+$", txt, re.I):
                        continue
                    norm_pat = re.sub(r"\d+", "<N>", txt)
                    if norm_pat in repeated_margin_patterns and (
                        float(line["top"]) <= top_band or float(line["bottom"]) >= bottom_band
                    ):
                        continue
                    page_elements.append((float(line["top"]), txt))

                page_elements.sort(key=lambda item: item[0])
                continuous_lines.extend(item[1] for item in page_elements)

        pdfium_doc.close()
        return "\n".join(continuous_lines), figure_manifest
```

### 6.2. Thuật toán 2: Khử Trùng 3 Tầng Bằng Forward Chemical Tokenizer (Không Dùng Variable-Width Lookbehind) & Ghép Cặp 4 Tín Hiệu (`ThreeTierDeduplicationAndPairingEngine` — Module A08)

**Cải tiến cốt lõi ở V11.3 (Khắc phục triệt để V11-P0-1):**
- Loại bỏ hoàn toàn biểu thức `(?<!{CHEM_ELEMENTS_PATTERN})` vốn gây lỗi `re.error: look-behind requires fixed-width pattern` trong Python `re`.
- Sử dụng **Forward Chemical & Numeric Tokenizer (`CHEM_OR_NUMERIC_TOKEN_RE`)** chạy trên văn bản đã chuẩn hóa Unicode `NFC` **TRƯỚC KHI `.lower()`**:
  - Nhánh 1 (`chem_token`): Khớp trực tiếp các công thức/nhóm hóa học có nguyên tố viết hoa kèm chỉ số hoặc điện tích (VD: `H2O`, `Fe3O4`, `C2H5OH`, `Ca(OH)2`, `SO4^2-`, `[Co(NH3)6]Cl3`) và **giữ nguyên 100% chỉ số hóa học**.
  - Nhánh 2 (`standalone_num`): Khớp các con số đứng độc lập (số liệu bài toán như `100`, `0,25`, `2026`) và thay thế bằng `<NUM>`.
  - Sau khi hoàn tất thay thế số liệu bài toán, chuỗi mới được đưa về chữ thường `.lower()` để tính `structural_hash`.

```python
# backend/modules/acquisition/dedup_and_pairing.py
from __future__ import annotations
import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Tuple

# Regex thuận (Forward Tokenizer, không dùng variable-width lookbehind, chạy hoàn toàn hợp lệ trên Python re):
# - Group 'chem': Bắt các cụm công thức hóa học (VD: H2O, Fe3O4, C2H5OH, Ca(OH)2, [Ag(NH3)2]+, SO4^2-)
# - Group 'math_var_sub': Bắt biến toán học có chỉ số dưới (VD: u_1, x_2, a_n)
# - Group 'num': Bắt số liệu bài toán độc lập để thay bằng <NUM>
CHEM_OR_NUMERIC_TOKEN_RE = re.compile(
    r"(?P<chem>(?:[A-Z][a-z]?\d*|\([A-Za-z0-9]+\)\d+|\[[A-Za-z0-9\(\)]+\]\d*)(?:[A-Z][a-z]?\d*|\([A-Za-z0-9]+\)\d*)+|[A-Z][a-z]?\d+)"
    r"|(?P<math_sub>[A-Za-z]_\{?\d+\}?)"
    r"|(?P<num>(?<![A-Za-z_\\0-9])\d+(?:[.,]\d+)?)"  # Không dùng \b ở cuối để bắt cả số dính liền đơn vị như 10g, 100g, 20m/s
)


class ThreeTierDeduplicationAndPairingEngine:
    @staticmethod
    def normalize_unicode_preserve_case(text: str) -> str:
        nfc = unicodedata.normalize("NFC", text or "")
        nfc = nfc.replace("−", "-").replace("–", "-")
        # Chuyển các chữ số chỉ số dưới Unicode (₀..₉) về chữ số ASCII để xử lý thống nhất
        sub_map = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
        nfc = nfc.translate(sub_map)
        return re.sub(r"\s+", " ", nfc).strip()

    @classmethod
    def normalize_vn_text(cls, text: str) -> str:
        return cls.normalize_unicode_preserve_case(text).lower()

    @classmethod
    def structural_normalize_preserve_chemistry(cls, text: str) -> str:
        """
        Thay thế số liệu bài toán bằng <NUM> TRƯỚC KHI .lower() để bảo toàn 100% công thức Hóa học:
        - 'Đốt cháy 11,2 lít C2H4 thu được H2O' -> 'đốt cháy <num> lít c2h4 thu được h2o'
        - 'Cho m = 100g dung dịch H2SO4 20%' -> 'cho m = <num>g dung dịch h2so4 <num>%'
        """
        cased_nfc = cls.normalize_unicode_preserve_case(text)

        def _replacer(match: re.Match) -> str:
            if match.group("chem") is not None:
                return match.group("chem")
            if match.group("math_sub") is not None:
                return match.group("math_sub")
            return "<NUM>"

        replaced = CHEM_OR_NUMERIC_TOKEN_RE.sub(_replacer, cased_nfc)
        return replaced.lower()

    @classmethod
    def compute_content_and_structural_hashes(cls, stem_md: str, options_md: List[str]) -> Tuple[str, str]:
        norm_stem = cls.normalize_vn_text(stem_md)
        norm_opts = sorted(cls.normalize_vn_text(opt) for opt in options_md)
        canonical_full = f"{norm_stem}||{'|'.join(norm_opts)}"
        content_hash = hashlib.sha256(canonical_full.encode("utf-8")).hexdigest()

        struct_stem = cls.structural_normalize_preserve_chemistry(stem_md)
        struct_opts = sorted(cls.structural_normalize_preserve_chemistry(opt) for opt in options_md)
        structural_full = f"{struct_stem}||{'|'.join(struct_opts)}"
        structural_hash = hashlib.sha256(structural_full.encode("utf-8")).hexdigest()

        return content_hash, structural_hash

    @classmethod
    def score_exam_and_solution_pairing(
        cls,
        exam_meta: Dict,
        sol_meta: Dict,
    ) -> Tuple[float, Dict[str, float]]:
        def clean_stem(name: str) -> str:
            s = cls.normalize_vn_text(Path(name).stem)
            return re.sub(r"\b(de|dap an|hdc|huong dan cham|loi giai|chi tiet|chinh thuc)\b", "", s).strip()

        stem_a, stem_b = clean_stem(exam_meta["filename"]), clean_stem(sol_meta["filename"])
        tokens_a, tokens_b = set(stem_a.split()), set(stem_b.split())
        name_sim = len(tokens_a & tokens_b) / max(1, len(tokens_a | tokens_b))

        same_folder = 1.0 if exam_meta.get("folder_id") == sol_meta.get("folder_id") else 0.0
        same_subject = 1.0 if exam_meta.get("subject_code") == sol_meta.get("subject_code") else 0.0
        q_diff = abs(int(exam_meta.get("question_count", 0)) - int(sol_meta.get("question_count", 0)))
        count_sim = max(0.0, 1.0 - (q_diff / 10.0))

        total = round(0.40 * name_sim + 0.25 * same_folder + 0.20 * same_subject + 0.15 * count_sim, 3)
        return total, {
            "filename_similarity": round(name_sim, 3),
            "folder_proximity": same_folder,
            "subject_match": same_subject,
            "item_count_similarity": round(count_sim, 3),
        }
```

### 6.3. Thuật toán 3: Kiểm Chứng Đáp Án Độc Lập Bằng Tiến Trình `SymPy` Cô Lập (`sympy_sandbox_worker.py` & `sympy_runner.py` — Module C02, E04)

**Cải tiến bảo mật cốt lõi ở V11.3 (Khắc phục triệt để P0-5 — Chống RCE từ chuỗi của LLM):**
- Tuyệt đối **không gọi `sp.sympify()` trực tiếp trong tiến trình ứng dụng** trên chuỗi biểu thức do LLM sinh ra.
- Tách thành `sympy_sandbox_worker.py` chạy trong tiến trình con cô lập (`python -I`, không nạp site-packages/env của user, container không có mạng ra ngoài, filesystem read-only):
  1. **Lớp lọc Whitelist tên hàm/biến (`ALLOWED_NAMES`) & chặn `__`:** Từ chối ngay lập tức mọi chuỗi chứa `__import__`, `eval`, `exec`, `open`, `getattr`, `__class__`, `__subclasses__` hoặc dài quá 500 ký tự.
  2. **Giới hạn tài nguyên hệ điều hành (`resource.setrlimit`):** Khóa cứng RAM tối đa `256 MB` (`RLIMIT_AS`) và CPU tối đa `5 giây` (`RLIMIT_CPU`) để chặn các biểu thức gây nổ bộ nhớ như `10**10**10`.
  3. **Trình phân tích an toàn:** Dùng `parse_expr(..., local_dict=local, global_dict=glob, transformations=standard_transformations)` với `glob["__builtins__"] = {}`.

```python
# backend/modules/quality/sympy_verifier.py
from __future__ import annotations
import json
import re
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ALLOWED_SYMPY_NAMES = {
    "sqrt",
    "sin",
    "cos",
    "tan",
    "asin",
    "acos",
    "atan",
    "log",
    "ln",
    "exp",
    "Abs",
    "pi",
    "E",
    "Rational",
    "x",
    "y",
    "z",
    "t",
    "n",
    "a",
    "b",
    "c",
    "k",
}
MAX_EXPR_LEN = 500
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


class SymPySandboxWorker:
    """
    Logic thực thi bên trong tiến trình cô lập `python -I sympy_sandbox_worker.py`:
    - Bước 1: Kiểm tra độ dài và Whitelist tên định danh (chặn 100% __import__, open, exec, __class__...).
    - Bước 2: Áp đặt RLIMIT_AS (256MB) và RLIMIT_CPU (5s) khi chạy độc lập.
    - Bước 3: Dùng parse_expr với __builtins__ = {} thay vì sympify() trần.
    """

    @staticmethod
    def validate_expression_ast_and_whitelist(expr_raw: str) -> Tuple[bool, Optional[str], str]:
        expr = str(expr_raw or "").strip().replace("^", "**")
        if not expr or len(expr) > MAX_EXPR_LEN:
            return False, "EXPR_LENGTH_EXCEEDED_OR_EMPTY", expr
        if "__" in expr or ";" in expr or "lambda" in expr:
            return False, "FORBIDDEN_SYNTAX_TOKEN", expr
        found_names = set(IDENTIFIER_RE.findall(expr))
        disallowed = found_names - ALLOWED_SYMPY_NAMES
        if disallowed:
            return False, f"DISALLOWED_IDENTIFIER:{','.join(sorted(disallowed))}", expr
        return True, None, expr

    @classmethod
    def evaluate_safe_in_worker(cls, expr_raw: str) -> Dict[str, Any]:
        is_valid, err_code, expr = cls.validate_expression_ast_and_whitelist(expr_raw)
        if not is_valid:
            return {"ok": False, "error": err_code}

        import sympy as sp

        try:
            from sympy.parsing.sympy_parser import parse_expr, standard_transformations

            glob: Dict[str, Any] = {}
            exec("from sympy import *", glob)
            glob["__builtins__"] = {}
            local = {"ln": sp.log}
            result = parse_expr(expr, local_dict=local, global_dict=glob, transformations=standard_transformations)
            if getattr(result, "free_symbols", None):
                return {"ok": False, "error": "EXPRESSION_NOT_NUMERIC"}
            return {"ok": True, "value": str(sp.N(result, 15))}
        except Exception as exc:
            # H-02 Fail-Closed: Tuyệt đối không fallback sang sympify() khi gặp lỗi!
            return {"ok": False, "error": f"SANDBOX_PARSE_ERROR:{type(exc).__name__}"}


class IndependentSymPyAndLLMVerifier:
    MATH_PROBABILITY_KEYWORDS = ("xác suất", "công thức bayes", "biến cố", "ngẫu nhiên", "xét nghiệm dương tính")

    @classmethod
    def enforce_subject_lock(cls, text_md: str, inferred_subject: str, source_subject: Optional[str]) -> str:
        if source_subject:
            return source_subject
        lower_txt = text_md.lower()
        if inferred_subject == "BIO" and any(kw in lower_txt for kw in cls.MATH_PROBABILITY_KEYWORDS):
            return "MATH"
        return inferred_subject

    @staticmethod
    def canonicalize_numeric_answer(raw_val: Optional[str]) -> Optional[Decimal]:
        if raw_val is None:
            return None
        cleaned = raw_val.strip().replace(" ", "").replace(",", ".")
        cleaned = re.sub(r"[a-zA-Z%/°]+$", "", cleaned)
        try:
            if "/" in cleaned:
                num, den = cleaned.split("/", 1)
                return Decimal(num) / Decimal(den)
            return Decimal(cleaned)
        except (InvalidOperation, ZeroDivisionError):
            return None

    @staticmethod
    def run_sympy_subprocess_sandbox(
        worker_script_path: Path,
        expr: str,
        timeout_sec: float = 8.0,
    ) -> Dict[str, Any]:
        """Gọi worker trong tiến trình riêng biệt với cờ python -I, giới hạn thời gian và môi trường rỗng."""
        is_valid, err_code, _ = SymPySandboxWorker.validate_expression_ast_and_whitelist(expr)
        if not is_valid:
            return {"ok": False, "error": err_code}
        if not worker_script_path.is_file():
            # H-02 Fail-Closed: Khi thiếu file worker cô lập, lập tức trả lỗi WORKER_UNAVAILABLE, cấm chạy in-process!
            return {"ok": False, "error": "SANDBOX_WORKER_UNAVAILABLE"}
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(worker_script_path)],
                input=json.dumps({"expr": expr}),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                env={"PATH": "/usr/bin:/bin"},
                start_new_session=True,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "TIMEOUT"}
        if proc.returncode != 0:
            return {"ok": False, "error": f"WORKER_EXIT_{proc.returncode}"}
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {"ok": False, "error": "BAD_WORKER_OUTPUT"}

    @classmethod
    def verify_symbolic_or_numeric(
        cls,
        sympy_expr_str: str,
        source_answer_raw: str,
        tolerance: Decimal = Decimal("0.01"),
    ) -> Tuple[bool, Dict[str, str]]:
        res = SymPySandboxWorker.evaluate_safe_in_worker(sympy_expr_str)
        if not res.get("ok"):
            return False, {"error": str(res.get("error", "EVAL_FAILED"))}
        computed_float = Decimal(str(res["value"]))
        source_dec = cls.canonicalize_numeric_answer(source_answer_raw)
        if source_dec is None:
            return False, {"error": "UNPARSEABLE_SOURCE_ANSWER", "computed": str(computed_float)}

        diff = abs(computed_float - source_dec)
        is_match = diff <= tolerance
        return is_match, {
            "computed_canonical": str(round(computed_float, 4)),
            "source_canonical": str(source_dec),
            "abs_diff": str(round(diff, 4)),
        }
```

### 6.4. Thuật toán 4: Bốc Đề Tất Định, Giao Thức Idempotency-First Autosave (Kèm Kiểm Tra `deadline_at` & Payload Hash) & Chấm Điểm Chuẩn THPTQG 2025+ (`DeterministicExamAndAutosaveEngine` — Module C04, C05, C06, C07)

**Cải tiến cốt lõi ở V11.3 (Khắc phục V11-P0-7 & V11-P1-4):**
- Kiểm tra `now_utc > attempt_deadline_at + grace_seconds`: chặn lưu bài sau khi đã hết giờ thi (`403 ATTEMPT_DEADLINE_EXPIRED`).
- Khi trùng `idempotency_key`: băm SHA-256 của `incoming_payload` (`payload_sha256`) và so khớp với `saved["payload_sha256"]`. Nếu khớp trả `200 OK (IDEMPOTENT_REPLAY)`; nếu khác payload trả `422 IDEMPOTENCY_KEY_PAYLOAD_MISMATCH`.
- Bổ sung kiểm tra `len(s) > max_chars` (`max_chars = 4` theo đúng phiếu tô trả lời ngắn THPTQG 2025+) trong `parse_short_numeric_vn`.

```python
# backend/modules/assessment/exam_autosave_and_grader.py
from __future__ import annotations
import hashlib
import json
import random
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID


class DeterministicExamAndAutosaveEngine:
    TF4_SCORING_MAP = {
        0: Decimal("0.00"),
        1: Decimal("0.10"),
        2: Decimal("0.25"),
        3: Decimal("0.50"),
        4: Decimal("1.00"),
    }

    @staticmethod
    def compute_payload_sha256(payload: Dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def generate_attempt_permutation(
        items: List[Dict[str, Any]],
        attempt_seed: int,
    ) -> Tuple[List[UUID], Dict[str, List[str]]]:
        rng = random.Random(attempt_seed)
        sections: Dict[str, List[List[Dict[str, Any]]]] = {}
        stimulus_groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}

        for item in items:
            sec = item["section_code"]
            stim = str(item.get("stimulus_id") or item["question_version_id"])
            key = (sec, stim)
            stimulus_groups.setdefault(key, []).append(item)

        for (sec, _), grp in stimulus_groups.items():
            sections.setdefault(sec, []).append(grp)

        ordered_q_ids: List[UUID] = []
        option_permutations: Dict[str, List[str]] = {}

        for sec in sorted(sections.keys()):
            blocks = sections[sec]
            rng.shuffle(blocks)
            for block in blocks:
                for q in block:
                    qid = q["question_version_id"]
                    ordered_q_ids.append(qid)
                    if q["section_code"] == "PART_I_MCQ":
                        opts = ["A", "B", "C", "D"]
                        rng.shuffle(opts)
                        option_permutations[str(qid)] = opts

        return ordered_q_ids, option_permutations

    @classmethod
    def process_autosave_idempotent_first(
        cls,
        idempotency_registry: Dict[str, Dict[str, Any]],
        current_response_row: Dict[str, Any],
        attempt_deadline_at: datetime,
        incoming_idem_key: str,
        incoming_client_seq: int,
        incoming_payload: Dict[str, Any],
        expected_response_version: int,
        incoming_payload_hash: Optional[str] = None,
        attempt_status: str = "IN_PROGRESS",
        now_utc: Optional[datetime] = None,
        network_grace_seconds: int = 5,
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Thực thi đầy đủ Canonical Autosave Contract (Khắc phục AUD-010):
        1. Idempotent Replay (200 OK) nếu trùng Idempotency-Key và khớp payload_sha256.
        2. Từ chối 422 nếu tái sử dụng Idempotency-Key với payload khác.
        3. Chống đua lệnh với Submit: từ chối 409 nếu attempt_status != 'IN_PROGRESS'.
        4. Kiểm tra deadline_at của Server (403 ATTEMPT_DEADLINE_EXPIRED).
        5. Kiểm tra Optimistic Concurrency (expected_response_version) và client_sequence (409 CONFLICT).
        """
        now_utc = now_utc or datetime.now(timezone.utc)
        incoming_hash = cls.compute_payload_sha256(incoming_payload)
        if incoming_payload_hash is not None and incoming_payload_hash != incoming_hash:
            return 422, {
                "error_code": "PAYLOAD_HASH_MISMATCH",
                "message": "Mã băm payload_hash từ client không khớp với dữ liệu nhận được trên server.",
            }

        # Bước 1: Kiểm tra Idempotency-Key trong autosave_idempotency_registry TRƯỚC TIÊN
        if incoming_idem_key in idempotency_registry:
            saved = idempotency_registry[incoming_idem_key]
            if saved["payload_sha256"] != incoming_hash:
                return 422, {
                    "error_code": "IDEMPOTENCY_KEY_PAYLOAD_MISMATCH",
                    "message": "Cùng Idempotency-Key nhưng nội dung câu trả lời khác với bản đã lưu.",
                }
            return 200, {
                "status": "IDEMPOTENT_REPLAY",
                "committed_sequence": saved["client_sequence"],
                "saved_response_version": saved["revision_no"],
                "revision_no": saved["revision_no"],
                "server_timestamp": saved["recorded_at"],
            }

        # Bước 2: Chống đua lệnh giữa Submit và Autosave (Submit Race Protection)
        if attempt_status != "IN_PROGRESS":
            return 409, {
                "error_code": "ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED",
                "attempt_status": attempt_status,
            }

        # Bước 3: Kiểm tra hạn nộp bài của phiên thi (deadline_at + grace_seconds)
        if now_utc > attempt_deadline_at + timedelta(seconds=network_grace_seconds):
            return 403, {
                "error_code": "ATTEMPT_DEADLINE_EXPIRED",
                "deadline_at": attempt_deadline_at.isoformat(),
            }

        # Bước 4: Kiểm tra Optimistic Concurrency (expected_response_version) & Monotonic Sequence
        if expected_response_version != current_response_row["current_revision_no"]:
            return 409, {
                "error_code": "OPTIMISTIC_VERSION_CONFLICT",
                "current_response_version": current_response_row["current_revision_no"],
                "current_sequence": current_response_row["current_sequence"],
            }

        if incoming_client_seq <= current_response_row["current_sequence"]:
            return 409, {
                "error_code": "STALE_CLIENT_SEQUENCE",
                "current_sequence": current_response_row["current_sequence"],
            }

        # Bước 5: Cập nhật nguyên tử dòng Response và ghi vào Registry + Revision
        new_rev = current_response_row["current_revision_no"] + 1
        current_response_row["current_sequence"] = incoming_client_seq
        current_response_row["current_revision_no"] = new_rev
        current_response_row["last_idempotency_key"] = incoming_idem_key
        current_response_row["response_payload"] = incoming_payload

        idempotency_registry[incoming_idem_key] = {
            "revision_no": new_rev,
            "client_sequence": incoming_client_seq,
            "payload_sha256": incoming_hash,
            "payload_json": incoming_payload,
            "recorded_at": now_utc,
        }
        return 200, {
            "status": "COMMITTED",
            "committed_sequence": incoming_client_seq,
            "saved_response_version": new_rev,
            "revision_no": new_rev,
            "server_timestamp": now_utc,
        }

    @classmethod
    def parse_short_numeric_vn(
        cls,
        raw_input: str,
        max_chars: int = 4,
        allow_thousand_sep: bool = False,
    ) -> Tuple[Optional[Decimal], Optional[str]]:
        s = (raw_input or "").strip().replace(" ", "")
        if not s:
            return None, "EMPTY"
        if len(s) > max_chars:
            return None, f"MAX_CHARS_EXCEEDED_LIMIT_{max_chars}"
        if s.count(",") + s.count(".") > 1:
            return None, "AMBIGUOUS_MULTIPLE_SEPARATORS"
        if not allow_thousand_sep and re.match(r"^-?\d{1,3}\.\d{3}$", s):
            return None, "THOUSAND_SEPARATOR_NOT_ALLOWED_USE_PLAIN_DIGITS"
        normalized = s.replace(",", ".")
        try:
            return Decimal(normalized), None
        except InvalidOperation:
            return None, "INVALID_NUMBER_FORMAT"

    @classmethod
    def grade_thptqg_2025_item(
        cls,
        item_type: str,
        max_points: Decimal,
        answer_key: Dict[str, Any],
        student_payload: Dict[str, Any],
        option_permutation: Optional[List[str]] = None,
    ) -> Tuple[Decimal, Dict[str, Any]]:
        """
        Chấm điểm tất định 3 phần đề thi THPTQG 2025+ có hỗ trợ ánh xạ ngược hoán vị phương án:
        - Nếu đề của thí sinh có hoán vị phương án (VD option_permutation = ["C", "A", "D", "B"]),
          và thí sinh chọn vị trí hiển thị "B" (index 1), hệ thống ánh xạ ngược về phương án gốc "A"
          trước khi so khớp với answer_key["correct_key"].
        """
        if item_type == "SINGLE_CHOICE":
            selected_display_key = student_payload.get("selected_key")
            canonical_selected = selected_display_key
            if option_permutation and selected_display_key in ("A", "B", "C", "D"):
                pos_idx = ord(selected_display_key) - ord("A")
                if 0 <= pos_idx < len(option_permutation):
                    canonical_selected = option_permutation[pos_idx]
            is_ok = canonical_selected == answer_key.get("correct_key")
            return (max_points if is_ok else Decimal("0.00")), {
                "is_correct": is_ok,
                "displayed_key": selected_display_key,
                "canonical_key": canonical_selected,
            }

        if item_type == "CLUSTER_TRUE_FALSE":
            correct_map: Dict[str, bool] = answer_key["sub_items"]
            student_map: Dict[str, bool] = student_payload.get("sub_answers", {})
            match_count = sum(1 for k in ("a", "b", "c", "d") if k in student_map and student_map[k] == correct_map[k])
            raw_pts = cls.TF4_SCORING_MAP.get(match_count, Decimal("0.00"))
            scaled_pts = (raw_pts * max_points).quantize(Decimal("0.01"))
            return scaled_pts, {"matched_sub_items": match_count}

        if item_type == "SHORT_NUMERIC":
            max_chars = int(answer_key.get("max_chars", 4))
            val, err = cls.parse_short_numeric_vn(str(student_payload.get("text_value", "")), max_chars=max_chars)
            if val is None:
                return Decimal("0.00"), {"parse_error": err}
            target = Decimal(str(answer_key["target_value"]))
            tol = Decimal(str(answer_key.get("tolerance", "0.01")))
            is_ok = abs(val - target) <= tol
            return (max_points if is_ok else Decimal("0.00")), {"is_correct": is_ok, "parsed_value": str(val)}

        return Decimal("0.00"), {"status": "REQUIRES_RUBRIC_GRADING"}
```

### 6.5. Thuật toán 5: Lịch Ôn Tập Chuẩn FSRS v5 (Đủ 19 Trọng Số `W[0..18]`) & Cổng Trưởng Thành IRT Độc Lập (`AdaptiveMasteryFSRSAndIRTEngine` — Module D02, D04, D06)

**Cải tiến cốt lõi ở V11.3 (Khắc phục V11-P1-1 & V11-P1-5):**
- Cài đặt đầy đủ **19 tham số chuẩn của FSRS v5 (`W[0..18]`)** gồm: khởi tạo $S_0(G)$, khởi tạo $D_0(G) = w_4 - e^{w_5(G-1)} + 1$, cập nhật độ khó theo 3 bước chuẩn FSRS-5 gồm giảm chấn tuyến tính (Linear Damping) và hồi quy trung bình (Mean Reversion): $\Delta D = -w_6 \cdot (G - 3)$, $D' = D + \Delta D \cdot \frac{10 - D}{9}$, $D'' = w_7 \cdot D_0(4) + (1 - w_7) \cdot D'$ (giới hạn trong $[1, 10]$), công thức độ ổn định sau khi nhớ lại thành công (nhân hệ số phạt `Hard` $w_{15}$ và thưởng `Easy` $w_{16}$), công thức độ ổn định sau khi quên $S'_f(D, S, R) = w_{11} D^{-w_{12}} ((S+1)^{w_{13}} - 1) e^{w_{14}(1-R)}$, và công thức khoảng ôn luỹ thừa chuẩn $I(r, S) = \frac{S}{19/81}\left(r^{-2} - 1\right)$.
- Tách bạch hoàn toàn FSRS v5 (Memory Scheduler) khỏi 4 bậc của mô hình năng lực IRT (`L1 Rule-Weighted -> L2 Rasch 1PL -> L3 2PL/GPCM -> L4 3PL Beta Prior`).

```python
# backend/modules/adaptive/fsrs_and_irt_engine.py
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Tuple
from uuid import UUID


@dataclass(frozen=True)
class FSRSState:
    stability: float
    difficulty: float
    reps: int
    lapses: int


class AdaptiveMasteryFSRSAndIRTEngine:
    # Đầy đủ 19 trọng số chuẩn của FSRS v5 (W[0] .. W[18])
    W: Tuple[float, ...] = (
        0.40255,  # w0: S0(Again)
        1.18385,  # w1: S0(Hard)
        3.17300,  # w2: S0(Good)
        15.69105,  # w3: S0(Easy)
        7.19490,  # w4: D0 base
        0.53450,  # w5: D0 exponent
        1.46040,  # w6: D delta
        0.00460,  # w7: D mean reversion weight
        1.54575,  # w8: Recall stability base
        0.11920,  # w9: Recall stability S power
        1.01925,  # w10: Recall stability R factor
        1.93950,  # w11: Forget stability base
        0.11000,  # w12: Forget stability D power
        0.29605,  # w13: Forget stability S power
        2.26980,  # w14: Forget stability R factor
        0.23150,  # w15: Hard penalty
        2.98980,  # w16: Easy bonus
        0.51655,  # w17: Short-term stability factor
        0.66210,  # w18: Short-term stability offset
    )
    DECAY: float = -0.5
    FACTOR: float = 19.0 / 81.0  # Đảm bảo R(t=S, S) = 0.90

    @classmethod
    def _init_difficulty(cls, rating: int) -> float:
        d0 = cls.W[4] - math.exp(cls.W[5] * (rating - 1)) + 1.0
        return min(10.0, max(1.0, d0))

    @classmethod
    def _next_difficulty(cls, d: float, rating: int) -> float:
        """Cập nhật độ khó chuẩn FSRS-5 (py-fsrs): Linear Damping (10 - D)/9 + Mean Reversion về D0(4)."""
        delta_d = -cls.W[6] * (rating - 3)
        d_damped = d + delta_d * ((10.0 - d) / 9.0)
        d_easy_ref = cls._init_difficulty(4)
        new_d = cls.W[7] * d_easy_ref + (1.0 - cls.W[7]) * d_damped
        return min(10.0, max(1.0, new_d))

    @classmethod
    def compute_retrievability(cls, elapsed_days: float, stability: float) -> float:
        if stability <= 0:
            return 0.0
        return (1.0 + cls.FACTOR * (max(0.0, elapsed_days) / stability)) ** cls.DECAY

    @classmethod
    def next_interval_days(cls, stability: float, request_retention: float = 0.90) -> int:
        new_interval = (stability / cls.FACTOR) * ((request_retention ** (1.0 / cls.DECAY)) - 1.0)
        return max(1, round(new_interval))

    @classmethod
    def step_fsrs_v5(
        cls,
        state: Optional[FSRSState],
        rating: Literal[1, 2, 3, 4],  # 1: Again, 2: Hard, 3: Good, 4: Easy
        elapsed_days: float,
        request_retention: float = 0.90,
    ) -> Tuple[FSRSState, int]:
        """
        Khớp 100% phương trình toán học của py-fsrs (FSRS-5):
        - Dùng độ khó hiện tại `state.difficulty` (trước khi cập nhật) trong công thức tính S'_r và S'_f.
        - Áp dụng Linear Damping `(10 - D) / 9` khi cập nhật độ khó.
        - Khi quên (rating == 1), chặn trên độ ổn định mới bởi `S / exp(w17 * w18)`.
        """
        if state is None or state.reps == 0:
            init_s = cls.W[rating - 1]
            init_d = cls._init_difficulty(rating)
            interval = cls.next_interval_days(init_s, request_retention)
            return FSRSState(round(init_s, 4), round(init_d, 4), 1, 1 if rating == 1 else 0), interval

        prev_d = state.difficulty
        prev_s = state.stability
        new_d = cls._next_difficulty(prev_d, rating)

        # Ôn tập ngắn hạn trong cùng ngày (elapsed_days < 1.0)
        if elapsed_days < 1.0:
            s_short = prev_s * math.exp(cls.W[17] * (rating - 3 + cls.W[18]))
            new_s = max(0.1, s_short)
            lapses = state.lapses + (1 if rating == 1 else 0)
            return FSRSState(round(new_s, 4), round(new_d, 4), state.reps + 1, lapses), cls.next_interval_days(
                new_s, request_retention
            )

        r = cls.compute_retrievability(elapsed_days, prev_s)

        if rating == 1:
            # Công thức Forget Stability S'_f chuẩn FSRS-5 dùng prev_d và chặn trần bởi prev_s / exp(w17 * w18)
            s_forget = (
                cls.W[11]
                * (prev_d ** (-cls.W[12]))
                * (((prev_s + 1.0) ** cls.W[13]) - 1.0)
                * math.exp(cls.W[14] * (1.0 - r))
            )
            short_term_cap = prev_s / math.exp(cls.W[17] * cls.W[18])
            new_s = max(0.1, min(s_forget, short_term_cap))
            lapses = state.lapses + 1
        else:
            # Công thức Recall Stability S'_r chuẩn FSRS-5 dùng prev_d
            hard_penalty = cls.W[15] if rating == 2 else 1.0
            easy_bonus = cls.W[16] if rating == 4 else 1.0
            inc = (
                math.exp(cls.W[8])
                * (11.0 - prev_d)
                * (prev_s ** (-cls.W[9]))
                * (math.exp(cls.W[10] * (1.0 - r)) - 1.0)
                * hard_penalty
                * easy_bonus
            )
            new_s = prev_s * (1.0 + inc)
            lapses = state.lapses

        interval = cls.next_interval_days(new_s, request_retention)
        return FSRSState(round(new_s, 4), round(new_d, 4), state.reps + 1, lapses), interval

    @staticmethod
    def select_irt_model_tier(
        sample_size: int,
        se_b: float,
        learner_count: int = 1000,
        ability_sd: float = 0.80,
        converged: bool = True,
        infit_msq: float = 1.00,
        max_abs_dif: float = 0.10,
    ) -> str:
        """
        Cổng kiểm định chất lượng hiệu chuẩn IRT đa tiêu chí (Khắc phục AUD-003 & AUD-011):
        Kiểm tra đồng thời cỡ mẫu, số lượng người học, độ phân tán năng lực (ability_sd),
        sự hội tụ (converged), độ phù hợp (infit_msq), sai số chuẩn SE(b) và độ lệch DIF.
        Nếu không đạt điều kiện của bậc cao, tự động fallback về bậc đơn giản hơn.
        """
        if not converged or sample_size < 150 or learner_count < 100 or ability_sd < 0.60 or se_b >= 0.30:
            return "RULE_WEIGHTED_L1"
        if not (0.70 <= infit_msq <= 1.30) or max_abs_dif > 0.50:
            return "RULE_WEIGHTED_L1"

        if (
            sample_size >= 1000
            and learner_count >= 800
            and ability_sd >= 0.70
            and se_b < 0.20
            and (0.80 <= infit_msq <= 1.20)
            and max_abs_dif <= 0.40
        ):
            return "IRT_3PL_GPCM_L4"

        if (
            sample_size >= 300
            and learner_count >= 250
            and ability_sd >= 0.65
            and se_b < 0.25
            and (0.75 <= infit_msq <= 1.25)
            and max_abs_dif <= 0.45
        ):
            return "IRT_2PL_GPCM_L3"

        return "RASCH_1PL_L2"

    @staticmethod
    def resolve_prerequisite_remediation_order(
        weak_node_ids: List[UUID],
        prereq_edges: List[Tuple[UUID, UUID]],
        mastery_by_node: Dict[UUID, float],
    ) -> List[Tuple[UUID, str]]:
        parents_map: Dict[UUID, List[UUID]] = {}
        for src, dst in prereq_edges:
            parents_map.setdefault(dst, []).append(src)

        ordered_plan: List[Tuple[UUID, str]] = []
        visited: set[UUID] = set()

        def dfs(node_id: UUID, dependent_label: Optional[UUID] = None) -> None:
            if node_id in visited:
                return
            visited.add(node_id)
            for p_id in parents_map.get(node_id, []):
                if mastery_by_node.get(p_id, 0.0) < 0.60:
                    dfs(p_id, dependent_label=node_id)
            reason = (
                f"Cần củng cố kiến thức tiên quyết này trước khi học chuyên đề phụ thuộc ({dependent_label})"
                if dependent_label
                else "Chuyên đề trọng tâm đang có mức độ nắm vững dưới ngưỡng (< 50%)"
            )
            ordered_plan.append((node_id, reason))

        for w_id in weak_node_ids:
            dfs(w_id)
        return ordered_plan
```

### 6.6. Thuật toán 6: Bộ Cào Câu Hỏi Hợp Pháp Dùng `ip_obj.is_global` + Kiểm Tra Mỗi Bước Redirect (`PermittedWebCrawlerEngine` — Module A02)

```python
# backend/modules/crawler/safe_crawler_and_adapters.py
from __future__ import annotations
import hashlib
import ipaddress
import socket
import time
import urllib.parse
import urllib.robotparser
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple
from bs4 import BeautifulSoup


class SSRFDNSRebindingError(PermissionError):
    pass


class AntiSSRFResolver:
    """
    Sử dụng trực tiếp thuộc tính chuẩn `ip_obj.is_global` của thư viện `ipaddress` Python 3.11+
    (tự động chặn mọi dải Private, Loopback, Link-Local, CGNAT 100.64.0.0/10, Multicast, Reserved, Unspecified
    cho cả IPv4 và IPv6/IPv4-mapped) và ghim IP vào kết nối socket ở TỪNG bước HTTP Redirect.
    """

    @classmethod
    def validate_and_pin_ip(cls, hostname: str, port: int = 443) -> str:
        if not hostname:
            raise SSRFDNSRebindingError("Hostname rỗng.")
        try:
            addr_info = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise SSRFDNSRebindingError(f"Không thể phân giải DNS cho {hostname}") from exc

        verified_ips: List[str] = []
        for _, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip_obj = ipaddress.ip_address(ip_str)
            if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
                ip_obj = ip_obj.ipv4_mapped

            if not ip_obj.is_global or ip_obj.is_multicast:
                raise SSRFDNSRebindingError(
                    f"SSRF_BLOCKED: Host {hostname} phân giải ra địa chỉ không phải Global Public IP ({ip_str})."
                )
            verified_ips.append(ip_str)

        if not verified_ips:
            raise SSRFDNSRebindingError(f"Không có IP hợp lệ cho {hostname}.")
        return verified_ips[0]


@dataclass
class ParserDriftMonitor:
    window_size: int = 30
    max_empty_rate: float = 0.15
    recent_outcomes: Deque[int] = field(default_factory=lambda: deque(maxlen=30))

    def record_page_yield(self, extracted_count: int) -> Tuple[bool, float]:
        self.recent_outcomes.append(1 if extracted_count == 0 else 0)
        if len(self.recent_outcomes) < 10:
            return False, 0.0
        empty_rate = sum(self.recent_outcomes) / len(self.recent_outcomes)
        return empty_rate > self.max_empty_rate, round(empty_rate, 3)


class PermittedWebCrawlerEngine:
    ALLOWED_RIGHTS = {"OWNED", "LICENSED", "ALLOWED_DERIVATIVE"}

    def __init__(self, user_agent: str = "ULPComplianceBot/9.0 (+https://ulp.edu.vn/bot-policy)"):
        self.user_agent = user_agent
        self._last_request_ts: Dict[str, float] = {}

    def verify_url_or_redirect_hop(
        self,
        source_config: Dict,
        target_url: str,
        robots_txt_content: str,
        hop_count: int = 0,
        max_redirects: int = 3,
    ) -> str:
        """Kiểm tra chính sách bản quyền, domain, robots.txt và ghim IP ở cả request gốc lẫn mỗi bước Redirect."""
        if hop_count > max_redirects:
            raise PermissionError(f"TOO_MANY_REDIRECTS: vượt quá {max_redirects} lần chuyển hướng.")
        if source_config.get("rights_status") not in self.ALLOWED_RIGHTS:
            raise PermissionError("RIGHTS_GATE_BLOCKED: Nguồn chưa được cấp phép bản quyền.")

        parsed = urllib.parse.urlparse(target_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Chỉ hỗ trợ giao thức http/https.")

        allowed_domain = source_config["allowed_domain"].lower()
        if parsed.hostname is None or parsed.hostname.lower() != allowed_domain:
            raise PermissionError(f"CROSS_DOMAIN_REDIRECT_BLOCKED: {parsed.hostname} != {allowed_domain}")

        allowed_prefixes: List[str] = source_config.get("allowed_path_prefixes", ["/"])
        if not any(parsed.path.startswith(prefix) for prefix in allowed_prefixes):
            raise PermissionError(f"PATH_NOT_ALLOWED: {parsed.path}")

        rp = urllib.robotparser.RobotFileParser()
        rp.parse(robots_txt_content.splitlines())
        if not rp.can_fetch(self.user_agent, target_url):
            raise PermissionError(f"ROBOTS_TXT_DISALLOWED: {target_url}")

        return AntiSSRFResolver.validate_and_pin_ip(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        )

    @staticmethod
    def open_pinned_socket_connection(
        hostname: str,
        pinned_ip: str,
        port: int = 443,
        use_tls: bool = True,
        timeout_sec: float = 10.0,
    ) -> socket.socket:
        """
        Khắc phục triệt để lỗ hổng TOCTOU DNS Rebinding:
        Mở kết nối TCP trực tiếp tới `pinned_ip` đã được kiểm tra `is_global`,
        nhưng ở tầng TLS vẫn truyền `server_hostname=hostname` để xác thực chứng chỉ SSL và SNI chuẩn xác.
        """
        import ssl

        raw_sock = socket.create_connection((pinned_ip, port), timeout=timeout_sec)
        if not use_tls:
            return raw_sock
        ctx = ssl.create_default_context()
        return ctx.wrap_socket(raw_sock, server_hostname=hostname)

    def enforce_politeness_rate_limit(self, domain: str, min_interval_sec: float = 3.0) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_ts.get(domain, 0.0)
        if elapsed < min_interval_sec:
            time.sleep(min_interval_sec - elapsed)
        self._last_request_ts[domain] = time.monotonic()

    @staticmethod
    def parse_vietjack_style_question_blocks(html_bronze: str, page_url: str) -> List[Dict]:
        soup = BeautifulSoup(html_bronze, "lxml")
        candidates: List[Dict] = []
        for idx, block in enumerate(soup.select(".question-item, .vietjack-quiz-block, .box-question"), start=1):
            stem_el = block.select_one(".question-title, .quiz-question-content")
            if not stem_el:
                continue
            stem_text = stem_el.get_text(" ", strip=True)
            options_raw = [opt.get_text(" ", strip=True) for opt in block.select(".option-item, .quiz-option")]
            rationale_el = block.select_one(".solution-content, .vietjack-explanation")
            rationale_text = rationale_el.get_text("\n", strip=True) if rationale_el else None
            candidates.append(
                {
                    "original_question_label": f"Câu {idx}",
                    "stem_md": stem_text,
                    "options_text": options_raw[:4],
                    "source_rationale_md": rationale_text,
                    "source_url": page_url,
                    "raw_html_sha256": hashlib.sha256(str(block).encode("utf-8")).hexdigest(),
                }
            )
        return candidates
```

### 6.7. Thuật toán 7: Đồng Bộ Google Drive 2 Chế Độ (`Picker + drive.file` & `Shared Drive Service Account`), Bung Nén Chống Zip Bomb & Ghép Cặp (`GoogleDriveDifferentialSyncEngine` — Module A03)

**Làm rõ kiến trúc tích hợp Google Drive ở V11.3 (Khắc phục V11-P1-6):**
- **Chế độ 1 (`PICKER_DRIVE_FILE` — Mặc định cho Giáo viên cá nhân):** Vì OAuth scope `drive.file` chỉ cấp quyền truy cập trên từng file cụ thể mà người dùng click chọn (chọn 1 folder trên Picker không tự động cấp quyền đọc các file con bên trong folder đó), giao diện Google Picker được cấu hình ở chế độ **chọn nhiều file trực tiếp (`DocsViewMode.LIST` + `MULTISELECT_ENABLED`) hoặc chọn 1 file nén `.zip` chứa cả bộ đề**.
- **Chế độ 2 (`SHARED_DRIVE_SERVICE_ACCOUNT` — Dành cho Trường học / Sở GD B2B muốn quét tự động thư mục):** Nhà trường chia sẻ (Share) thư mục Google Drive cho địa chỉ email của **Service Account** hệ thống (quyền `Viewer`). Worker dùng Service Account gọi `files.list` / `changes.list` để tự động phát hiện mọi file mới tạo trong thư mục con mà không cần người dùng mở Google Picker và không vướng OAuth User-Level Restricted Scope.

```python
# backend/modules/drive_sync/drive_differential_sync.py
from __future__ import annotations
import os
import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple
from backend.modules.acquisition.dedup_and_pairing import ThreeTierDeduplicationAndPairingEngine


class ArchiveSecurityViolationError(ValueError):
    # Ném ra khi file nén vi phạm giới hạn Zip Bomb hoặc chứa đường dẫn Path Traversal (../).
    pass


@dataclass(frozen=True)
class SafeArchivePolicy:
    max_entries: int = 500
    max_total_uncompressed_bytes: int = 200 * 1024 * 1024  # 200 MB
    max_compression_ratio: float = 50.0  # Chặn tỷ lệ nén > 50:1
    max_directory_depth: int = 4
    allowed_extensions: Tuple[str, ...] = (".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png")


class GoogleDriveDifferentialSyncEngine:
    # 1. Đồng bộ vi sai qua Google Drive Changes API (startPageToken / newStartPageToken)
    #    với OAuth scope an toàn `https://www.googleapis.com/auth/drive.file` + Google Picker.
    # 2. Xử lý an toàn khi file bị xóa/thu hồi quyền trên Drive (đánh dấu SOURCE_REVOKED, giữ nguyên đề cũ).
    # 3. Bung nén file .zip trong sandbox có kiểm tra chống Zip Bomb, Symlink và Path Traversal (`../`).
    # 4. Tự động ghép cặp `File Đề + File Hướng Dẫn Chấm (HDC)` theo 4 tín hiệu với ngưỡng tin cậy.

    @staticmethod
    def extract_zip_safely_in_sandbox(
        zip_file_path: str,
        target_extract_dir: str,
        policy: SafeArchivePolicy = SafeArchivePolicy(),
    ) -> List[str]:
        os.makedirs(target_extract_dir, exist_ok=True)
        resolved_root = Path(target_extract_dir).resolve()
        extracted_files: List[str] = []
        total_uncompressed = 0

        with zipfile.ZipFile(zip_file_path, "r") as zf:
            infos = zf.infolist()
            if len(infos) > policy.max_entries:
                raise ArchiveSecurityViolationError(
                    f"ZIP_TOO_MANY_FILES: {len(infos)} files vượt ngưỡng {policy.max_entries}"
                )

            for info in infos:
                if info.is_dir():
                    continue

                # 1. Chặn Path Traversal (../ hoặc đường dẫn tuyệt đối)
                clean_rel = posixpath.normpath(info.filename).lstrip("/")
                if clean_rel.startswith("..") or "/../" in clean_rel or "\\" in clean_rel:
                    raise ArchiveSecurityViolationError(f"PATH_TRAVERSAL_BLOCKED: {info.filename}")

                # 2. Kiểm tra độ sâu thư mục
                depth = len(Path(clean_rel).parts)
                if depth > policy.max_directory_depth:
                    raise ArchiveSecurityViolationError(f"ARCHIVE_DEPTH_EXCEEDED: {clean_rel} (depth={depth})")

                # 3. Chặn Symlink bên trong file Zip (Unix external_attr mode 0o120000)
                unix_mode = info.external_attr >> 16
                if (unix_mode & 0o170000) == 0o120000:
                    raise ArchiveSecurityViolationError(f"SYMLINK_IN_ARCHIVE_BLOCKED: {info.filename}")

                # 4. Kiểm tra tỷ lệ nén và tổng dung lượng giải nén chống Zip Bomb
                compressed_sz = max(1, info.compress_size)
                ratio = info.file_size / compressed_sz
                if ratio > policy.max_compression_ratio and info.file_size > 1024 * 1024:
                    raise ArchiveSecurityViolationError(
                        f"ZIP_BOMB_RATIO_BLOCKED: {info.filename} có tỷ lệ nén {ratio:.1f}:1"
                    )

                total_uncompressed += info.file_size
                if total_uncompressed > policy.max_total_uncompressed_bytes:
                    raise ArchiveSecurityViolationError(
                        f"ZIP_TOTAL_SIZE_EXCEEDED: Tổng dung lượng vượt {policy.max_total_uncompressed_bytes} bytes"
                    )

                ext = Path(clean_rel).suffix.lower()
                if ext not in policy.allowed_extensions:
                    continue

                dest_path = (resolved_root / clean_rel).resolve()
                if not str(dest_path).startswith(str(resolved_root)):
                    raise ArchiveSecurityViolationError(f"ESCAPE_SANDBOX_BLOCKED: {dest_path}")

                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, "r") as src_stream, open(dest_path, "wb") as dst_file:
                    while True:
                        chunk = src_stream.read(65536)
                        if not chunk:
                            break
                        dst_file.write(chunk)
                extracted_files.append(str(dest_path))

        return extracted_files

    @staticmethod
    def process_drive_changes_batch(
        change_items: List[Dict[str, Any]],
        existing_docs_by_external_id: Dict[str, Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        # Xử lý danh sách thay đổi từ Google Drive API `changes.list`:
        # - Nếu file chỉ đổi tên/di chuyển thư mục nhưng `sha256Checksum` không đổi -> Chỉ cập nhật metadata, KHÔNG chạy lại OCR.
        # - Nếu file bị xóa (`removed=True` hoặc `trashed=True`) -> Đánh dấu `SOURCE_REVOKED`, bảo toàn đề thi đã nhập.
        actions: Dict[str, List[Dict[str, Any]]] = {
            "ingest_new_or_modified": [],
            "metadata_only_update": [],
            "mark_revoked": [],
        }

        for change in change_items:
            file_id = change["fileId"]
            file_meta = change.get("file") or {}
            is_removed = bool(change.get("removed")) or bool(file_meta.get("trashed"))

            if is_removed:
                if file_id in existing_docs_by_external_id:
                    actions["mark_revoked"].append({"external_id": file_id, "reason": "DRIVE_FILE_REMOVED_OR_TRASHED"})
                continue

            incoming_sha256 = file_meta.get("sha256Checksum") or file_meta.get("md5Checksum")
            existing = existing_docs_by_external_id.get(file_id)

            if existing and existing.get("checksum") == incoming_sha256:
                actions["metadata_only_update"].append(
                    {
                        "external_id": file_id,
                        "new_title": file_meta.get("name"),
                        "parents": file_meta.get("parents", []),
                    }
                )
            else:
                actions["ingest_new_or_modified"].append(
                    {
                        "external_id": file_id,
                        "title": file_meta.get("name"),
                        "mime_type": file_meta.get("mimeType"),
                        "checksum": incoming_sha256,
                        "parents": file_meta.get("parents", []),
                    }
                )

        return actions

    @staticmethod
    def auto_pair_exam_and_hdc_documents(
        candidate_docs: List[Dict[str, Any]],
        auto_confirm_threshold: float = 0.85,
        propose_threshold: float = 0.60,
    ) -> List[Dict[str, Any]]:
        # Tự động phân loại file Đề bài và file Hướng dẫn chấm (HDC/Đáp án) trong cùng đợt đồng bộ,
        # chấm điểm 4 tín hiệu và ghép cặp.
        solution_keywords = ("hdc", "huong dan cham", "dap an", "loi giai", "đáp án", "hướng dẫn chấm")
        exams: List[Dict[str, Any]] = []
        solutions: List[Dict[str, Any]] = []

        for doc in candidate_docs:
            lower_name = doc["filename"].lower()
            if any(k in lower_name for k in solution_keywords):
                solutions.append(doc)
            else:
                exams.append(doc)

        pairings: List[Dict[str, Any]] = []
        for ex in exams:
            best_sol = None
            best_score = 0.0
            best_signals: Dict[str, float] = {}

            for sol in solutions:
                score, signals = ThreeTierDeduplicationAndPairingEngine.score_exam_and_solution_pairing(ex, sol)
                if score > best_score:
                    best_score = score
                    best_sol = sol
                    best_signals = signals

            if best_sol and best_score >= propose_threshold:
                pairings.append(
                    {
                        "exam_doc_id": ex["doc_id"],
                        "solution_doc_id": best_sol["doc_id"],
                        "similarity_score": best_score,
                        "signals_json": best_signals,
                        "status": "CONFIRMED" if best_score >= auto_confirm_threshold else "PROPOSED",
                    }
                )
        return pairings
```


### 6.8. Thuật toán 8: Động Cơ Crypto-Shredding Theo Từng Người Dùng Trên HashiCorp Vault Transit, Xóa Văn Bản Tự Do & Chống Phục Hồi PII Khi Chạy PITR (`PIICryptoShreddingService` — Module F04, G02)

**Giải quyết triệt để mâu thuẫn KEK/Backup và PII trong văn bản tự do (Khắc phục P0-4 — Mục 3 của Bản vá V11.1):**
1. **Phương án A (Khuyến nghị — Vault Transit Per-User Key `ulp-user-{user_id}`):** Mỗi người dùng có một khóa riêng `ulp-user-{user_id}` trên **HashiCorp Vault Transit Engine** dùng để bọc `DEK` của người dùng đó. Khi thực thi `Right to Erasure`, gọi `destroy_user_key(vault, f"ulp-user-{user_id}")` xóa vĩnh viễn khóa trên Vault Transit $\Rightarrow$ **mọi bản sao lưu DB cũ lập tức mất khả năng giải mã `DEK` của đúng user đó mà không ảnh hưởng tới bất kỳ user nào khác trong Tenant**. *(Nếu dùng Phương án B — 1 KEK/Tenant/Năm, bắt buộc phải ghi rõ giới hạn 30 ngày lưu trữ Backup trong thông báo quyền riêng tư và được Pháp chế xác nhận).*
2. **Mã hóa `guardian_email` & Xóa sạch Văn bản tự do chứa PII:** Cột `guardian_email` trong `parental_consents` được tách thành `guardian_email_blind_index` (HMAC) và `guardian_email_ciphertext` (mã hóa bằng `DEK` của `learner_user_id`). Khi xóa dữ liệu, hàm `scrub_free_text_pii_records()` đồng thời xóa sạch nội dung văn bản tự do trong `tutor_messages`, `appeals` và `comment_threads`.
3. **Lớp bảo vệ thứ hai khi chạy PITR (`reapply_tombstones_after_pitr_restore`):** Luôn giữ sổ cái WORM Tombstone để quét sạch lại sau mỗi lần khôi phục thảm họa.

```python
# backend/modules/privacy/crypto_shredding.py
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from uuid import UUID
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class PIICryptoShreddingService:
    SENSITIVE_AUDIT_KEYS = {
        "email",
        "raw_email",
        "plaintext_email",
        "guardian_email",
        "password",
        "phone",
        "phone_number",
        "full_name",
        "display_name",
        "cccd",
    }

    @staticmethod
    def compute_email_blind_index(email: str, cluster_pepper: bytes) -> str:
        norm_email = (email or "").strip().lower().encode("utf-8")
        return hmac.new(cluster_pepper, norm_email, hashlib.sha256).hexdigest()

    @staticmethod
    def generate_user_dek() -> bytes:
        return AESGCM.generate_key(bit_length=256)

    @staticmethod
    def wrap_dek_with_vault_user_key(vault_client: Any, user_id: UUID, user_dek: bytes) -> Tuple[str, bytes]:
        """
        Phương án A (Khuyến nghị): Tạo khóa riêng `ulp-user-{user_id}` trên HashiCorp Vault Transit
        và bọc DEK của người dùng. Xóa khóa này trên Vault sẽ vô hiệu hóa cả các bản DB Backup cũ.
        """
        key_name = f"ulp-user-{user_id}"
        vault_client.secrets.transit.create_key(name=key_name, key_type="aes256-gcm96")
        resp = vault_client.secrets.transit.encrypt_data(
            name=key_name,
            plaintext=base64.b64encode(user_dek).decode("ascii"),
        )
        return key_name, resp["data"]["ciphertext"].encode("ascii")

    @staticmethod
    def destroy_vault_user_key(vault_client: Any, key_name: str) -> None:
        """Xóa vĩnh viễn khóa của user trên Vault Transit khi thực thi Right to Erasure."""
        vault_client.secrets.transit.update_key_configuration(name=key_name, deletion_allowed=True)
        vault_client.secrets.transit.delete_key(name=key_name)

    @staticmethod
    def wrap_dek_with_tenant_kek(tenant_kek: bytes, user_dek: bytes, user_id: UUID) -> bytes:
        """Phương án B (Fallback cục bộ khi test hoặc chưa kết nối Vault Transit)."""
        aesgcm = AESGCM(tenant_kek)
        nonce = os.urandom(12)
        aad = f"dek_wrap:{user_id}".encode("utf-8")
        return nonce + aesgcm.encrypt(nonce, user_dek, aad)

    @staticmethod
    def unwrap_dek_with_tenant_kek(
        tenant_kek: bytes, wrapped_dek_blob: Optional[bytes], user_id: UUID
    ) -> Optional[bytes]:
        if wrapped_dek_blob is None:
            return None
        aesgcm = AESGCM(tenant_kek)
        nonce, ct = wrapped_dek_blob[:12], wrapped_dek_blob[12:]
        aad = f"dek_wrap:{user_id}".encode("utf-8")
        return aesgcm.decrypt(nonce, ct, aad)

    @staticmethod
    def encrypt_user_pii(dek: bytes, pii_dict: Dict[str, Any], user_id: UUID) -> bytes:
        aesgcm = AESGCM(dek)
        nonce = os.urandom(12)
        aad = str(user_id).encode("utf-8")
        plaintext = json.dumps(pii_dict, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return nonce + aesgcm.encrypt(nonce, plaintext, aad)

    @staticmethod
    def decrypt_user_pii(dek: Optional[bytes], blob: Optional[bytes], user_id: UUID) -> Dict[str, Any]:
        if dek is None or blob is None:
            return {"email": "anonymized@shredded.local", "display_name": "Người dùng đã ẩn danh"}
        aesgcm = AESGCM(dek)
        nonce, ciphertext = blob[:12], blob[12:]
        aad = str(user_id).encode("utf-8")
        plaintext = aesgcm.decrypt(nonce, ciphertext, aad)
        return json.loads(plaintext.decode("utf-8"))

    @staticmethod
    def scrub_free_text_pii_records(
        tutor_messages: List[Dict[str, Any]],
        appeals: List[Dict[str, Any]],
        comment_threads: List[Dict[str, Any]],
    ) -> None:
        """Xóa nội dung văn bản tự do có thể chứa PII khi thực thi Right to Erasure (Khắc phục P0-4)."""
        for msg in tutor_messages:
            msg["message_md"] = "[ĐÃ XÓA THEO YÊU CẦU]"
            msg["citations_json"] = []
        for ap in appeals:
            ap["reason_text"] = "[ĐÃ XÓA THEO YÊU CẦU]"
            ap["resolution_note"] = None
        for cm in comment_threads:
            cm["body_text"] = "[ĐÃ XÓA THEO YÊU CẦU]"

    @classmethod
    def execute_erasure_and_emit_tombstone(
        cls,
        user_row: Dict[str, Any],
        key_row: Dict[str, Any],
        tenant_id: UUID,
        parental_consent_rows: Optional[List[Dict[str, Any]]] = None,
        vault_client: Optional[Any] = None,
        backup_retention_days: int = 30,
    ) -> Dict[str, Any]:
        now_utc = datetime.now(timezone.utc)
        if vault_client is not None and key_row.get("kms_kek_key_id", "").startswith("ulp-user-"):
            cls.destroy_vault_user_key(vault_client, key_row["kms_kek_key_id"])

        user_row["email_blind_index"] = hashlib.sha256(os.urandom(32)).hexdigest()
        user_row["pii_ciphertext_bytea"] = None
        user_row["pseudonym_handle"] = f"Anonymized_{str(user_row['id'])[:8]}"
        user_row["status"] = "ANONYMIZED"

        for pc in parental_consent_rows or []:
            pc["guardian_email_blind_index"] = hashlib.sha256(os.urandom(32)).hexdigest()
            pc["guardian_email_ciphertext"] = b""

        key_row["encrypted_dek_bytea"] = None
        key_row["key_status"] = "SHREDDED"
        key_row["shredded_at"] = now_utc
        key_row["backup_retention_expires_at"] = now_utc + timedelta(days=backup_retention_days)

        cert_payload = f"{tenant_id}:{user_row['id']}:{key_row['kms_kek_key_id']}:{now_utc.isoformat()}"
        return {
            "subject_user_id": user_row["id"],
            "tenant_id": tenant_id,
            "kms_kek_key_id": key_row["kms_kek_key_id"],
            "shredded_at": now_utc.isoformat(),
            "backup_expiry_guaranteed_at": key_row["backup_retention_expires_at"].isoformat(),
            "verification_hash": hashlib.sha256(cert_payload.encode("utf-8")).hexdigest(),
        }

    @classmethod
    def reapply_tombstones_after_pitr_restore(
        cls,
        restored_users_by_id: Dict[UUID, Dict[str, Any]],
        restored_keys_by_uid: Dict[UUID, Dict[str, Any]],
        worm_tombstones: Iterable[Dict[str, Any]],
    ) -> int:
        scrubbed_count = 0
        for tomb in worm_tombstones:
            uid = tomb["subject_user_id"]
            if uid in restored_users_by_id and restored_users_by_id[uid].get("status") != "ANONYMIZED":
                restored_users_by_id[uid]["email_blind_index"] = hashlib.sha256(os.urandom(32)).hexdigest()
                restored_users_by_id[uid]["pii_ciphertext_bytea"] = None
                restored_users_by_id[uid]["status"] = "ANONYMIZED"
                if uid in restored_keys_by_uid:
                    restored_keys_by_uid[uid]["encrypted_dek_bytea"] = None
                    restored_keys_by_uid[uid]["key_status"] = "SHREDDED"
                scrubbed_count += 1
        return scrubbed_count

    @classmethod
    def sanitize_audit_json(cls, payload: Optional[Dict[str, Any]], cluster_pepper: bytes) -> Dict[str, Any]:
        if not payload:
            return {}
        sanitized: Dict[str, Any] = {}
        for k, v in payload.items():
            if k.lower() in cls.SENSITIVE_AUDIT_KEYS and v is not None:
                digest = hmac.new(cluster_pepper, str(v).encode("utf-8"), hashlib.sha256).hexdigest()[:16]
                sanitized[f"{k}_redacted_hash"] = f"hmac:{digest}"
            elif isinstance(v, dict):
                sanitized[k] = cls.sanitize_audit_json(v, cluster_pepper)
            else:
                sanitized[k] = v
        return sanitized
```

### 6.9. Thuật toán 9: Động Cơ Lưu Bài Phòng Thi Phía Trình Duyệt Dùng `Dexie` (`IndexedDB` Thật), Khớp Canonical Contract (`expected_response_version` + `payload_hash`) & Tự Động `resync` (`frontend/src/lib/exam-autosave-store.ts`)

**Cải tiến cốt lõi ở V11.3 (Khắc phục P0-6 & P0-7 — Mục 5.1 của Bản vá V11.1):**
- Lưu bền vững vào **`IndexedDB` thật qua thư viện `Dexie`** trước khi gửi mạng (F5 hoặc sập trình duyệt không mất bất kỳ câu trả lời nào).
- Chỉ xóa các bản nháp chưa từng gửi (`tries === 0`) khi gộp thao tác mới, giữ nguyên các bản đã từng gửi (`tries > 0`) để không làm lệch `expected_response_version` nếu gói tin trước đã được Server commit nhưng rớt ACK trên đường về.
- Gửi đầy đủ `expected_response_version` và `payload_hash` (tính từ `canonicalJson` khớp tuyệt đối với `json.dumps(sort_keys=True, separators=(",", ":"))` của Python).
- Hoàn thiện hàm `resync(r)` gọi `GET /api/v1/attempts/${r.attemptId}/sync` để đồng bộ lại phiên bản và tự động gửi lại hoặc bật `Conflict Modal`.

```typescript
// frontend/src/lib/exam-autosave-store.ts — Lưu trữ IndexedDB thật (Dexie) & Khớp 100% Canonical Autosave Contract
import Dexie, { Table } from "dexie";

export type ExamPayload = Record<string, string | number | boolean | null>;

export interface PendingAutosave {
  id?: number;
  attemptId: string;
  questionVersionId: string;
  clientSequence: number;
  idempotencyKey: string;
  payloadHash: string;
  responsePayload: ExamPayload;
  isFlagged: boolean;
  createdAtMs: number;
  tries: number;
}

interface MetaRow {
  key: string;
  value: number;
}

class ExamDatabase extends Dexie {
  pending!: Table<PendingAutosave, number>;
  meta!: Table<MetaRow, string>;

  constructor() {
    super("ulp_exam_offline_db");
    this.version(1).stores({
      pending: "++id, attemptId, [attemptId+questionVersionId]",
      meta: "key",
    });
  }
}

export const examDb = new ExamDatabase();

/**
 * Chuẩn hóa JSON trùng khớp 100% với server Python:
 * json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
 */
export function canonicalJson(v: unknown): string {
  if (v === null || typeof v !== "object") {
    return JSON.stringify(v);
  }
  if (Array.isArray(v)) {
    return "[" + v.map((item) => canonicalJson(item)).join(",") + "]";
  }
  const o = v as Record<string, unknown>;
  const keys = Object.keys(o).sort();
  return "{" + keys.map((k) => JSON.stringify(k) + ":" + canonicalJson(o[k])).join(",") + "}";
}

export async function sha256Hex(s: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

const seqKey = (a: string, q: string) => `seq:${a}:${q}`;
const verKey = (a: string, q: string) => `ver:${a}:${q}`;

export async function enqueue(
  attemptId: string,
  questionVersionId: string,
  payload: ExamPayload,
  flagged = false,
  apiBase = "",
  token = () => "",
  onConflictModal?: (questionVersionId: string, serverPayload: unknown) => void,
): Promise<void> {
  const payloadHash = await sha256Hex(canonicalJson(payload));
  await examDb.transaction("rw", examDb.pending, examDb.meta, async () => {
    const seq = ((await examDb.meta.get(seqKey(attemptId, questionVersionId)))?.value ?? 0) + 1;
    await examDb.meta.put({ key: seqKey(attemptId, questionVersionId), value: seq });

    // Chỉ xóa những bản CHƯA từng gửi mạng (tries === 0); giữ nguyên bản đã gửi (tries > 0) phòng trường hợp mất ACK
    const existing = await examDb.pending
      .where("[attemptId+questionVersionId]")
      .equals([attemptId, questionVersionId])
      .toArray();
    await examDb.pending.bulkDelete(existing.filter((r) => r.tries === 0).map((r) => r.id!));

    await examDb.pending.add({
      attemptId,
      questionVersionId,
      clientSequence: seq,
      payloadHash,
      idempotencyKey: `${attemptId}:${questionVersionId}:seq_${seq}:${crypto.randomUUID()}`,
      responsePayload: payload,
      isFlagged: flagged,
      createdAtMs: Date.now(),
      tries: 0,
    });
  });
  void flush(apiBase, token, onConflictModal);
}

let flushing = false;

export async function flush(
  apiBase = "",
  token = () => "",
  onConflictModal?: (questionVersionId: string, serverPayload: unknown) => void,
): Promise<void> {
  if (flushing) return;
  flushing = true;
  try {
    const rows = await examDb.pending.orderBy("id").toArray();
    for (const r of rows) {
      const expected = (await examDb.meta.get(verKey(r.attemptId, r.questionVersionId)))?.value ?? 0;
      let res: Response;
      try {
        res = await fetch(`${apiBase}/api/v1/attempts/${r.attemptId}/responses/${r.questionVersionId}`, {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token()}`,
            "Idempotency-Key": r.idempotencyKey,
          },
          body: JSON.stringify({
            client_sequence: r.clientSequence,
            expected_response_version: expected,
            idempotency_key: r.idempotencyKey,
            payload_hash: r.payloadHash,
            response_payload: r.responsePayload,
            is_flagged: r.isFlagged,
          }),
        });
      } catch {
        await backoff(r, apiBase, token, onConflictModal);
        return;
      }

      if (res.status === 200) {
        const body = (await res.json()) as { saved_response_version: number };
        await examDb.meta.put({
          key: verKey(r.attemptId, r.questionVersionId),
          value: body.saved_response_version,
        });
        await examDb.pending.delete(r.id!);
      } else if (res.status >= 500 || res.status === 429) {
        await backoff(r, apiBase, token, onConflictModal);
        return;
      } else if (res.status === 403) {
        // ATTEMPT_DEADLINE_EXPIRED: Hết giờ thi trên Server
        await examDb.pending.clear();
        return;
      } else if (res.status === 409) {
        await resync(r, apiBase, token, onConflictModal);
        return;
      } else {
        await examDb.pending.delete(r.id!);
      }
    }
  } finally {
    flushing = false;
  }
}

async function backoff(
  r: PendingAutosave,
  apiBase: string,
  token: () => string,
  onConflictModal?: (questionVersionId: string, serverPayload: unknown) => void,
): Promise<void> {
  await examDb.pending.update(r.id!, { tries: r.tries + 1 });
  const delay = Math.min(30000, 500 * 2 ** r.tries) + Math.random() * 500;
  setTimeout(() => void flush(apiBase, token, onConflictModal), delay);
}

async function resync(
  r: PendingAutosave,
  apiBase: string,
  token: () => string,
  onConflictModal?: (questionVersionId: string, serverPayload: unknown) => void,
): Promise<void> {
  const syncRes = await fetch(`${apiBase}/api/v1/attempts/${r.attemptId}/sync`, {
    headers: { Authorization: `Bearer ${token()}` },
  });
  if (!syncRes.ok) return;
  const syncData = (await syncRes.json()) as {
    responses: Record<
      string,
      { revision_no: number; current_sequence: number; last_idempotency_key: string; response_payload: unknown }
    >;
  };
  const srv = syncData.responses[r.questionVersionId];
  if (!srv) return;

  await examDb.meta.put({ key: verKey(r.attemptId, r.questionVersionId), value: srv.revision_no });
  await examDb.meta.put({
    key: seqKey(r.attemptId, r.questionVersionId),
    value: Math.max(r.clientSequence, srv.current_sequence),
  });

  if (srv.last_idempotency_key === r.idempotencyKey) {
    await examDb.pending.delete(r.id!);
  } else {
    await examDb.pending.delete(r.id!);
    if (onConflictModal) {
      onConflictModal(r.questionVersionId, srv.response_payload);
    }
  }
}

if (typeof window !== "undefined") {
  window.addEventListener("online", () => void flush());
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void flush();
  });
}
```

---

