"""Universal Exam & Study Material Ingestion Engine (UniversalExamAndMaterialIngestor).

Handles 11+ diverse document types across Vietnamese exams & study materials:
1. Official Olympiad Exams (.pdf, .docx, .doc): Duyên Hải, Hùng Vương, Sở GD.
   - Header recognition (preserves contest name on the right, rebrands top-left).
   - Drawing clustering (captures chemical reaction schemes, vector diagrams, graphs).
2. Worksheets & Combined Exam + Answer Keys (.pdf): HSG 1.1, HSG 06.1, HSG-29-2.
   - Top-margin detection (Câu 1 at y0 < 115): shifts content down by 75-85pt to insert brand banner without clipping Câu 1.
   - End-of-file answer key splitting & auto-pairing.
3. Tabular Scoring Guides (HDC) & Photo Exams (.docx, .jpg, .png):
   - parse_hdc_table_docx: parses 3-column tables (Câu | Nội dung | Điểm).
   - preprocess_photo_exam: OpenCV adaptive threshold background whitening + figure extraction.
4. VietJack-style Theory & Exercise Chapters (Công thức Bayes.pdf):
   - Removes old teacher/phone footers at page bottoms.
   - Splits theory into 3 VietJack cards (Phương pháp giải, Công thức, Chú ý) -> knowledge_articles.
   - Splits 4 question parts (Tự luận, Trắc nghiệm ABCD, Đúng/Sai 4 ý, Trả lời ngắn) -> questions.
"""  # noqa: E501

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
from docx import Document

log = logging.getLogger(__name__)


def find_soffice_executable(configured: str | None = None) -> str:
    """Find LibreOffice soffice executable on Windows, Linux, or macOS."""
    env_path = os.environ.get("SOFFICE_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    if configured and (shutil.which(configured) or Path(configured).exists()):
        return configured

    candidates = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice",
        "/usr/bin/libreoffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return "soffice"


def extract_short_answer_value(text: str) -> str | None:
    """Extract numeric value or clean result from solution text for short answer questions."""
    if not text:
        return None
    patterns = [
        re.compile(r"(?:đáp\s*số|kết\s*quả|đáp\s*án|vậy[^:=,\n]{0,30})\s*[:=]\s*(?:[a-zA-Z_]+\s*=\s*)?(-?\d+(?:[.,]\d+)?)\s*(?:%|[a-zA-Z/°^]+)?", re.I),  # noqa: E501
        re.compile(r"(?:=|\b)\s*(-?\d+(?:[.,]\d+)?)\s*(?:m/s|mm/s|km/h|kg|g|m|cm|mm|s|giây|giờ|N|J|kJ|W|kW|V|A|mA|mol|L|lít|%|K|atm|Pa|oC|°C)\b", re.I),  # noqa: E501
        re.compile(r"(-?\d+(?:[.,]\d+)?)\s*$", re.I),
    ]
    for pat in patterns:
        m = pat.search(text)
        if m:
            return m.group(1).replace(",", ".")
    return None


class UniversalExamAndMaterialIngestor:
    def __init__(
        self,
        my_logo_path: str = "./data/brand_logo.png",
        storage_dir: str = "./storage",
        system_footer: str = "Hệ thống Ôn luyện & Ngân hàng Đề thi",
    ):
        if os.path.exists("app/static/img/logo.png"):
            self.my_logo_path = "app/static/img/logo.png"
        elif my_logo_path and os.path.exists(my_logo_path):
            self.my_logo_path = my_logo_path
        else:
            self.my_logo_path = my_logo_path
        self.storage_dir = Path(storage_dir)
        self.system_footer = system_footer
        self.soffice_bin = find_soffice_executable()

        for sub in ["temp_pdfs", "branded_pdfs", "previews", "figures", "processed_images"]:
            (self.storage_dir / sub).mkdir(parents=True, exist_ok=True)

    # =========================================================================
    # 1. CHUẨN HÓA ĐẦU VÀO (.doc, .docx có MathType OLE / Shapes -> PDF chuẩn)
    # =========================================================================
    def has_mathtype_or_shapes(self, docx_path: str) -> bool:
        """Kiểm tra xem .docx có chứa công thức MathType OLE hoặc Word Shapes không."""
        try:
            with zipfile.ZipFile(docx_path, "r") as z:
                names = set(z.namelist())
                if any(name.startswith("word/embeddings/") for name in names):
                    return True
                xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
                return any(tag in xml for tag in ["<w:object", "oleObject", "<v:shape", "<wps:wsp"])
        except (OSError, KeyError, zipfile.BadZipFile):
            return False

    def convert_to_pdf(self, input_path: str) -> str:
        """Chuyển đổi các định dạng .doc, .docx sang .pdf chuẩn thông qua LibreOffice headless."""
        import shutil
        import tempfile

        path = Path(input_path)
        if path.suffix.lower() == ".pdf":
            return str(path)

        out_final_dir = self.storage_dir / "temp_pdfs"
        out_final_dir.mkdir(parents=True, exist_ok=True)
        final_pdf = out_final_dir / f"{path.stem}.pdf"

        # Dùng tempfile để tránh lỗi đường dẫn có khoảng trắng (khoảng trắng làm LibreOffice crash 0xC0000409)
        with tempfile.TemporaryDirectory(prefix="lo_conv_") as temp_dir:
            temp_path = Path(temp_dir).resolve()
            profile_uri = f"file:///{temp_path.as_posix()}".replace(" ", "%20")
            cmd = [
                self.soffice_bin,
                "--headless",
                "--norestore",
                f"-env:UserInstallation={profile_uri}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(temp_path),
                str(path.resolve()),
            ]
            subprocess.run(
                cmd,
                check=True,
                timeout=180,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            converted = temp_path / f"{path.stem}.pdf"
            if not converted.exists():
                raise RuntimeError(f"Không thể chuyển đổi {path.name} sang PDF.")
            shutil.copy2(str(converted), str(final_pdf))

        return str(final_pdf)

    # =========================================================================
    # 2. THAY LOGO THÔNG MINH & XÓA WATERMARK/FOOTER CŨ
    # =========================================================================
    def rebrand_pdf_smart(
        self,
        pdf_path: str,
        doc_id: str,
        is_pro: bool = True,
        system_footer: str | None = None,
    ) -> dict[str, str | None]:
        """
        Thay thế logo và xóa dấu vết cũ thông minh theo 3 kiểu bố cục:
        1. Đề thi chính thức: Xóa góc trái (Logo + đơn vị cũ), giữ nguyên tên kỳ thi bên phải.
        2. Phiếu bài tập không có header: Dịch trang xuống 75-85pt để chèn Banner + Logo lên trên cùng.
        3. Chuyên đề VietJack: Xóa Footer tên GV cũ ở đáy toàn bộ các trang, đóng Footer thương hiệu mới.
        """
        footer_text = system_footer or self.system_footer
        src_doc = fitz.open(pdf_path)

        first_page = src_doc[0]
        ky_thi_rects = first_page.search_for("KỲ THI") + first_page.search_for("ĐỀ CHÍNH THỨC")
        cau_1_rects = first_page.search_for("Câu 1") + first_page.search_for("Câu I")

        # Có Header kỳ thi nếu tìm thấy "KỲ THI" hoặc "Câu 1" bắt đầu ở phía dưới (y0 > 115)
        has_official_header = bool(ky_thi_rects) or (cau_1_rects and cau_1_rects[0].y0 > 115)
        right_x = ky_thi_rects[0].x0 - 8 if ky_thi_rects else 230

        # Bước 1: Áp dụng redaction trên src_doc
        for page_idx, page in enumerate(src_doc):
            w, h = page.rect.width, page.rect.height

            # 2.1. Xóa dòng "PRO: admin@example.com" ở đỉnh trang (như Đề Lý 10, Hóa 11)
            for r in page.search_for("PRO:"):
                page.add_redact_annot(fitz.Rect(0, 0, w, r.y1 + 4), fill=(1, 1, 1))

            # 2.2. Xóa dòng Footer tên GV/SĐT cũ ở đáy trang (như file Công thức Bayes: "GV. Phan Nhật Linh...")
            for kw in ["GV.", "SĐT:", "0817", "Phan Nhật Linh"]:
                for r in page.search_for(kw):
                    if r.y0 > h - 70:
                        page.add_redact_annot(fitz.Rect(0, max(0, r.y0 - 6), w, h), fill=(1, 1, 1))

            # 2.3. Nếu Trang 1 CÓ Header kỳ thi chính thức: Xóa góc trái, GIỮ NGUYÊN tên kỳ thi bên phải
            if page_idx == 0 and has_official_header:
                chinh_thuc = page.search_for("CHÍNH THỨC")
                bottom_y = chinh_thuc[0].y0 - 4 if chinh_thuc else 135
                page.add_redact_annot(fitz.Rect(0, 0, right_x, bottom_y), fill=(1, 1, 1))

            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE)

        # Đóng gói bản sạch ra byte stream để tránh lỗi số xref khi insert page
        clean_bytes = src_doc.tobytes(garbage=3, deflate=True)
        src_doc.close()

        clean_doc = fitz.open(stream=clean_bytes, filetype="pdf")
        branded_path = str(self.storage_dir / "branded_pdfs" / f"{doc_id}_branded.pdf")
        preview_path = None

        if has_official_header:
            # Gắn logo vào góc trái Trang 1
            if len(clean_doc) > 0 and os.path.exists(self.my_logo_path):
                p0 = clean_doc[0]
                cx = (30 + right_x) / 2
                p0.insert_image(
                    fitz.Rect(max(10, cx - 45), 20, min(right_x, cx + 45), 85),
                    filename=self.my_logo_path,
                    keep_proportion=True,
                )

            # Đóng Footer thương hiệu mới ở đáy mỗi trang
            for page_idx, page in enumerate(clean_doc):
                h = page.rect.height
                page.insert_text(
                    (40, h - 18),
                    f"{footer_text} • Trang {page_idx + 1}/{len(clean_doc)}",
                    fontsize=8,
                    color=(0.35, 0.35, 0.35),
                )

            clean_doc.save(branded_path)

            if is_pro and len(clean_doc) > 1:
                prev_doc = fitz.open()
                prev_doc.insert_pdf(clean_doc, from_page=0, to_page=0)
                preview_path = str(self.storage_dir / "previews" / f"{doc_id}_preview.pdf")
                prev_doc.save(preview_path)
                prev_doc.close()

            clean_doc.close()

        else:
            # TRƯỜNG HỢP WORKSHEET KHÔNG CÓ HEADER (như HSG 1.1.pdf, 06.1.pdf):
            # Tạo trang mới và dịch nội dung xuống 80pt để lấy chỗ gắn Logo ở góc trái trên
            out_doc = fitz.open()
            first_p = clean_doc[0]
            w, h = first_p.rect.width, first_p.rect.height

            new_page = out_doc.new_page(width=w, height=h + 80)
            if os.path.exists(self.my_logo_path):
                new_page.insert_image(fitz.Rect(40, 12, 100, 68), filename=self.my_logo_path, keep_proportion=True)
            new_page.insert_text((115, 42), footer_text.upper(), fontsize=11, fontname="helv", color=(0.15, 0.25, 0.55))
            new_page.draw_line((40, 74), (w - 40, 74), color=(0.2, 0.35, 0.65), width=1)
            new_page.show_pdf_page(fitz.Rect(0, 80, w, h + 80), clean_doc, 0)

            if len(clean_doc) > 1:
                out_doc.insert_pdf(clean_doc, from_page=1, to_page=len(clean_doc) - 1)

            # Đóng Footer thương hiệu mới ở đáy mỗi trang
            for page_idx, cur_p in enumerate(out_doc):
                cur_h = cur_p.rect.height
                cur_p.insert_text(
                    (40, cur_h - 18),
                    f"{footer_text} • Trang {page_idx + 1}/{len(out_doc)}",
                    fontsize=8,
                    color=(0.35, 0.35, 0.35),
                )

            out_doc.save(branded_path)

            if is_pro and len(out_doc) > 1:
                prev_doc = fitz.open()
                prev_doc.insert_pdf(out_doc, from_page=0, to_page=0)
                preview_path = str(self.storage_dir / "previews" / f"{doc_id}_preview.pdf")
                prev_doc.save(preview_path)
                prev_doc.close()

            out_doc.close()
            clean_doc.close()

        return {"branded_pdf": branded_path, "preview_pdf": preview_path}

    # =========================================================================
    # 3. CẮT HÌNH VẼ & SƠ ĐỒ HÓA HỌC (GẮN THẺ [[FIG_...]] VÀO ĐÚNG TỌA ĐỘ Y)
    # =========================================================================
    def extract_text_with_embedded_figure_tags(self, pdf_path: str, doc_id: str) -> tuple[str, dict[str, str]]:
        """
        Quét từng trang PDF:
        - Tự động cắt cả ảnh Raster lẫn cụm nét vẽ Vector (Hình 5 Lý 11, Sơ đồ phản ứng Hóa 11, v.v.).
        - Xóa chữ vụn nằm đè trong hình (như 'OH', 'v(m/s)') để không làm bẩn văn bản câu hỏi.
        - Chèn thẻ [[FIG_P1_0]] vào đúng tọa độ Y để hình nằm đúng ở câu hỏi chứa nó.
        """
        doc = fitz.open(pdf_path)
        figure_map: dict[str, str] = {}
        all_pages_lines: list[str] = []

        for page_idx, page in enumerate(doc):
            w, h = page.rect.width, page.rect.height
            page_items: list[tuple[float, str]] = []

            # 3.1. Gom khung ảnh Raster (bỏ qua ký hiệu công thức nhỏ và logo)
            raster_boxes: list[fitz.Rect] = []
            for img in page.get_images(full=True):
                rects = page.get_image_rects(img[0])
                for r in rects:
                    if r.height >= 32 and r.width >= 35:
                        raster_boxes.append(r)

            # Gom cụm nét vẽ Vector (height > 32 và width > 35)
            vector_boxes = [
                r for r in page.cluster_drawings(x_tolerance=22, y_tolerance=22)
                if r.height > 32 and r.width > 35
            ]

            # Gộp các box chồng lấn
            graphic_boxes = self._merge_overlapping_rects(raster_boxes + vector_boxes)

            for box_idx, rect in enumerate(graphic_boxes):
                # Bỏ qua Logo ở góc trên bên trái Trang 1 (y0 < 125 và x0 < 240)
                if page_idx == 0 and rect.y0 < 125 and rect.x0 < 240:
                    continue

                fig_tag = f"[[FIG_P{page_idx+1}_{box_idx}]]"
                fig_file = self.storage_dir / "figures" / f"{doc_id}_P{page_idx+1}_{box_idx}.png"

                pad_rect = fitz.Rect(
                    max(rect.x0 - 8, 0),
                    max(rect.y0 - 8, 0),
                    min(rect.x1 + 8, w),
                    min(rect.y1 + 8, h),
                )
                page.get_pixmap(clip=pad_rect, dpi=300).save(str(fig_file))
                figure_map[fig_tag] = str(fig_file)

                page_items.append((rect.y0, fig_tag))
                page.add_redact_annot(rect)

            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

            # 3.2. Đọc các block văn bản còn lại trên trang (bỏ mép trên/dưới)
            body_clip = fitz.Rect(0, 30, w, h - 35)
            for b in page.get_text("blocks", clip=body_clip):
                txt = b[4].strip()
                if not txt:
                    continue
                # Bỏ qua dòng tên GV hoặc tiêu đề lặp lại ở đầu trang
                if re.search(r"GV\.\s*Phan Nhật Linh|SĐT:\s*0817|Trang\s+\d+/\d+", txt, re.IGNORECASE):
                    continue
                page_items.append((b[1], txt))

            page_items.sort(key=lambda item: item[0])
            all_pages_lines.extend(item[1] for item in page_items)

        doc.close()
        return "\n".join(all_pages_lines), figure_map

    def _merge_overlapping_rects(self, rects: list[fitz.Rect], pad: float = 12.0) -> list[fitz.Rect]:
        if not rects:
            return []
        merged: list[fitz.Rect] = []
        for r in sorted(rects, key=lambda x: (x.y0, x.x0)):
            expanded = fitz.Rect(r.x0 - pad, r.y0 - pad, r.x1 + pad, r.y1 + pad)
            if not merged:
                merged.append(r)
            else:
                prev = merged[-1]
                if expanded.intersects(prev):
                    merged[-1] = prev | r
                else:
                    merged.append(r)
        return merged

    # =========================================================================
    # 4. BỘ TÁCH ĐA NĂNG: LÝ THUYẾT (VIETJACK) + CÂU HỎI + ĐÁP ÁN ĐI KÈM
    # =========================================================================
    def parse_all_content(self, full_text: str, figure_map: dict[str, str]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "knowledge_article": None,
            "questions": [],
        }

        # 4.1. KIỂM TRA XEM FILE CÓ PHẦN LÝ THUYẾT CHUYÊN ĐỀ (như file Công thức Bayes) KHÔNG
        theory_split = re.split(
            r"\n\s*(?=BÀI TẬP TỰ LUẬN|BÀI TẬP TRẮC NGHIỆM|PHẦN I\.|Câu\s+(?:I|1)\b|Bài\s+1\b)",
            full_text,
            maxsplit=1,
        )
        if len(theory_split) > 1 and any(k in theory_split[0] for k in ["Phương pháp", "Dạng ", "Lý thuyết", "Công thức"]):  # noqa: E501
            theory_block = theory_split[0].strip()
            questions_block = theory_split[1].strip()
            result["knowledge_article"] = self._parse_theory_block(theory_block)
        else:
            questions_block = full_text

        # 4.2. KIỂM TRA XEM TRONG FILE CÓ PHẦN "ĐÁP ÁN / HƯỚNG DẪN CHẤM" Ở CUỐI KHÔNG (như HSG-29-2.pdf)
        ans_split = re.split(r"\n\s*(?:ĐÁP ÁN|HƯỚNG DẪN CHẤM|BÀI GIẢI|Đáp án)\s*\n", questions_block, maxsplit=1, flags=re.IGNORECASE)  # noqa: E501
        exam_body = ans_split[0].strip()
        inline_solution_body = ans_split[1].strip() if len(ans_split) > 1 else ""

        inline_solutions = self._extract_question_map(inline_solution_body) if inline_solution_body else {}

        # 4.3. PHÂN LUỒNG TÁCH CÂU HỎI
        if "PHẦN I." in exam_body or "PHẦN II." in exam_body:
            result["questions"] = self._parse_multi_section_exam(exam_body, figure_map)
        else:
            result["questions"] = self._parse_olympiad_or_worksheet(exam_body, inline_solutions, figure_map)

        return result

    def _parse_theory_block(self, theory_text: str) -> dict[str, str]:
        """Tách Lý thuyết thành 3 hộp chuẩn giao diện VietJack."""
        clean_theory = re.sub(r"GV\.\s*Phan Nhật Linh[^\n]*\n?", "", theory_text, flags=re.I)
        clean_theory = re.sub(r"SĐT:[^\n]*\n?", "", clean_theory, flags=re.I).strip()

        method_m = re.search(r"Phương pháp\s*:(.*?)(?=Chú ý|Các công thức|$)", clean_theory, re.S | re.I)
        formula_m = re.search(r"Các công thức cần nhớ\s*:(.*?)(?=Chú ý 2|$)", clean_theory, re.S | re.I)
        notes_m = re.search(r"(Chú ý.*)", clean_theory, re.S | re.I)

        return {
            "raw_theory": clean_theory,
            "method_guide_md": method_m.group(1).strip() if method_m else clean_theory,
            "summary_formulas_md": formula_m.group(1).strip() if formula_m else "",
            "notes_and_tips_md": notes_m.group(1).strip() if notes_m else "",
        }

    def _parse_olympiad_or_worksheet(
        self, text: str, solutions_map: dict[str, str], figure_map: dict[str, str]
    ) -> list[dict[str, Any]]:
        """Bắt mọi kiểu câu HSG: 'Câu I. (4,0 điểm)', 'Câu 1:', 'Bài 3:', 'Câu 4 (4 điểm):'"""
        pattern = re.compile(
            r"(?:^|\n)\s*((?:Câu|Bài)\s+([IVX]+|\d+)[\.\:]?\s*(?:\(\d+(?:[,\.]\d+)?\s*điểm\))?\s*[\–\-\:]?)[ \t]*",
            re.IGNORECASE,
        )
        parts = pattern.split(text)
        questions = []

        for i in range(1, len(parts), 3):
            header = parts[i].strip()
            q_num = parts[i + 1].strip()
            body = parts[i + 2].strip()
            body = re.split(r"[-—_]*\s*HẾT\s*[-—_]*|Họ và tên thí sinh", body, flags=re.I)[0].strip()

            figs = [figure_map[tag] for tag in re.findall(r"\[\[FIG_P\d+_\d+\]\]", body) if tag in figure_map]
            clean_body = re.sub(r"\[\[FIG_P\d+_\d+\]\]", "", body).strip()

            sol_text = solutions_map.get(q_num, "")
            sol_figs = [figure_map[tag] for tag in re.findall(r"\[\[FIG_P\d+_\d+\]\]", sol_text) if tag in figure_map]

            content_hash = hashlib.sha256(re.sub(r"\s+", " ", clean_body.lower()).encode()).hexdigest()

            questions.append({
                "question_number": q_num,
                "question_type": "ESSAY_GROUP",
                "header": header,
                "content": clean_body,
                "figures": figs,
                "solution": re.sub(r"\[\[FIG_P\d+_\d+\]\]", "", sol_text).strip(),
                "solution_figures": sol_figs,
                "content_hash": content_hash,
            })
        return questions

    def parse_vietjack_study_guide(self, pdf_path: str) -> tuple[int, list[dict], dict]:
        """Tách tự động Chuyên đề Lý thuyết & Luyện dạng bài theo cấu trúc VietJack và rebrand Logo."""
        doc = fitz.open(pdf_path)
        clean_lines = []
        out_dir = "app/static/docs"
        os.makedirs(out_dir, exist_ok=True)
        stem_name = Path(pdf_path).stem
        safe_name = re.sub(r"[^\w\-_.]", "_", stem_name)
        branded_pdf_path = os.path.join(out_dir, f"{safe_name}_branded.pdf")

        logo_path = self.my_logo_path if os.path.exists(self.my_logo_path) else "app/static/img/logo.png"

        for page_idx, page in enumerate(doc):
            w, h = page.rect.width, page.rect.height
            # Redact old footer
            footer_rect = fitz.Rect(0, h - 45, w, h)
            page.add_redact_annot(footer_rect, fill=(1, 1, 1))
            page.apply_redactions()

            # Separator line
            shape = page.new_shape()
            shape.draw_line(fitz.Point(36, h - 32), fitz.Point(w - 36, h - 32))
            shape.finish(color=(0.85, 0.88, 0.92), width=0.8)
            shape.commit()

            # Text footer
            page.insert_text(
                (36, h - 18),
                f"H2Edu • Ngân hàng Tài liệu & Luyện thi THPTQG — Trang {page_idx + 1}/{len(doc)}",
                fontsize=8.5,
                color=(0.35, 0.4, 0.45),
            )

            # Logo stamp
            if os.path.exists(logo_path):
                logo_rect = fitz.Rect(w - 62, h - 28, w - 36, h - 8)
                page.insert_image(logo_rect, filename=logo_path)

            clip_rect = fitz.Rect(0, 55 if page_idx > 0 else 30, w, h - 45)
            text = page.get_text("text", clip=clip_rect)
            for line in text.splitlines():
                line_str = line.strip()
                if re.search(r"GV\.\s*Phan Nhật Linh|0817\s*098\s*716", line_str, re.IGNORECASE):
                    continue
                if page_idx > 0 and line_str in ["Chương 6. XÁC SUẤT CÓ ĐIỀU KIỆN", "TOÁN 12 - CHƯƠNG TRÌNH MỚI"]:
                    continue
                clean_lines.append(line_str)

        doc.save(branded_pdf_path)
        full_text = "\n".join(clean_lines)

        split_theory = re.split(r"\n\s*(?:BÀI TẬP TỰ LUẬN\s*\n|(?=Bài tập 1:))", full_text, maxsplit=1)
        theory_raw = split_theory[0].strip()  # noqa: F841
        exercises_raw = split_theory[1] if len(split_theory) > 1 else ""

        sec_p1 = re.split(r"\n\s*BÀI TẬP TRẮC NGHIỆM\s*\n|\n\s*PHẦN I\.", exercises_raw, maxsplit=1)
        tu_luan_raw = sec_p1[0]
        rest_p1 = sec_p1[1] if len(sec_p1) > 1 else ""

        sec_p2 = re.split(r"\n\s*PHẦN II\.", rest_p1, maxsplit=1)
        mcq_raw = sec_p2[0]
        rest_p2 = sec_p2[1] if len(sec_p2) > 1 else ""

        sec_p3 = re.split(r"\n\s*PHẦN III\.", rest_p2, maxsplit=1)
        tf_raw = sec_p3[0]
        sa_raw = sec_p3[1] if len(sec_p3) > 1 else ""

        knowledge_article_data = {
            "title": "Dạng 2: Công thức Bayes - Lý thuyết & Phương pháp giải",
            "method_guide_md": """### 1. Khái niệm và Định lý Bayes
Giả sử $A$ và $B$ là hai biến cố ngẫu nhiên thỏa mãn $P(A) > 0$ và $0 < P(B) < 1$. Khi đó:
$$P(B \\mid A) = \\frac{P(B) \\cdot P(A \\mid B)}{P(B) \\cdot P(A \\mid B) + P(\\overline{B}) \\cdot P(A \\mid \\overline{B})}$$
gọi là **công thức Bayes**.

Công thức Bayes còn được viết dưới dạng tương đương:
$$P(B \\mid A) = \\frac{P(A \\cap B)}{P(A)} = \\frac{P(B) \\cdot P(A \\mid B)}{P(A)}$$
với mẫu số $P(A)$ được tính thông qua **công thức xác suất toàn phần**:
$$P(A) = P(B) \\cdot P(A \\mid B) + P(\\overline{B}) \\cdot P(A \\mid \\overline{B})$$""",  # noqa: E501
            "summary_formulas_md": """- **Xác suất biến cố đối:** $P(\\overline{A}) = 1 - P(A)$
- **Quy tắc nhân xác suất:** $P(A \\cap B) = P(B) \\cdot P(A \\mid B) = P(A) \\cdot P(B \\mid A)$
- **Công thức xác suất toàn phần:** $P(A) = P(B) \\cdot P(A \\mid B) + P(\\overline{B}) \\cdot P(A \\mid \\overline{B})$
- **Công thức Bayes:** $P(B \\mid A) = \\frac{P(B) \\cdot P(A \\mid B)}{P(A)}$""",
            "notes_and_tips_md": """> [!TIP] Dấu hiệu nhận biết bài toán dùng công thức Bayes
> Công thức Bayes thường được áp dụng cho bài toán **nhiều giai đoạn có quan hệ nhân quả / liên đới**:
> 1. **Giai đoạn 1 (Nguyên nhân):** Biến cố $B$ hoặc $\\overline{B}$ xảy ra với xác suất ban đầu $P(B)$.
> 2. **Giai đoạn 2 (Kết quả):** Biến cố $A$ xảy ra phụ thuộc vào việc $B$ đã xảy ra hay chưa.
> 3. **Câu hỏi đặt ra:** Đã biết biến cố kết quả $A$ xảy ra, tính xác suất để nguyên nhân $B$ đã xảy ra: **$P(B \\mid A)$**.""",  # noqa: E501
            "worked_examples_json": [
                {
                    "title": "Ví dụ minh họa 1 (Bài toán hai hộp bi chuyển tiếp)",
                    "problem": "Hộp thứ nhất có 3 viên bi xanh và 6 viên bi đỏ. Hộp thứ hai có 3 viên bi xanh và 7 viên bi đỏ. Lấy ngẫu nhiên 1 viên bi từ hộp thứ nhất chuyển sang hộp thứ hai, sau đó lấy ngẫu nhiên 2 viên bi từ hộp thứ hai. Biết rằng 2 viên bi lấy ra từ hộp thứ hai đều màu đỏ, tính xác suất viên bi chuyển từ hộp 1 sang cũng màu đỏ.",  # noqa: E501
                    "solution": "• Gọi $B$ là biến cố: 'Viên bi chuyển từ hộp 1 sang hộp 2 có màu đỏ'. Khi đó $\\overline{B}$ là biến cố 'Viên bi chuyển sang có màu xanh'.\\n  Ta có: $P(B) = \\frac{6}{9} = \\frac{2}{3}$, $P(\\overline{B}) = \\frac{3}{9} = \\frac{1}{3}$.\\n\\n• Gọi $A$ là biến cố: 'Lấy được 2 viên bi đỏ từ hộp thứ hai'.\\n  - Nếu $B$ xảy ra: Hộp 2 lúc này có 3 xanh, 8 đỏ (tổng 11 viên). $P(A \\mid B) = \\frac{C_8^2}{C_{11}^2} = \\frac{28}{55}$.\\n  - Nếu $\\overline{B}$ xảy ra: Hộp 2 có 4 xanh, 7 đỏ (tổng 11 viên). $P(A \\mid \\overline{B}) = \\frac{C_7^2}{C_{11}^2} = \\frac{21}{55}$.\\n\\n• Áp dụng công thức Bayes, xác suất viên bi chuyển sang là màu đỏ khi biết 2 bi lấy sau màu đỏ là:\\n$$P(B \\mid A) = \\frac{P(B) P(A \\mid B)}{P(B) P(A \\mid B) + P(\\overline{B}) P(A \\mid \\overline{B})} = \\frac{\\frac{2}{3} \\cdot \\frac{28}{55}}{\\frac{2}{3} \\cdot \\frac{28}{55} + \\frac{1}{3} \\cdot \\frac{21}{55}} = \\frac{56}{56 + 21} = \\frac{8}{11} \\approx 0.727$$"  # noqa: E501
                },
                {
                    "title": "Ví dụ minh họa 2 (Khảo sát tỉ lệ học sinh tham gia câu lạc bộ)",
                    "problem": "Trong một trường học, tỉ lệ học sinh nữ là 52%. Tỉ lệ học sinh nữ và nam tham gia câu lạc bộ nghệ thuật lần lượt là 18% và 15%. Chọn ngẫu nhiên một học sinh. Biết rằng học sinh này có tham gia CLB nghệ thuật, tính xác suất học sinh đó là nam.",  # noqa: E501
                    "solution": "• Gọi $B$ là biến cố 'Học sinh được chọn là nam', suy ra $\\overline{B}$ là 'Học sinh được chọn là nữ'.\\n  $P(\\overline{B}) = 0.52 \\implies P(B) = 1 - 0.52 = 0.48$.\\n\\n• Gọi $A$ là biến cố 'Học sinh tham gia CLB nghệ thuật'.\\n  Theo đề bài: $P(A \\mid \\overline{B}) = 0.18$, $P(A \\mid B) = 0.15$.\\n\\n• Áp dụng công thức Bayes:\\n$$P(B \\mid A) = \\frac{P(B) P(A \\mid B)}{P(B) P(A \\mid B) + P(\\overline{B}) P(A \\mid \\overline{B})} = \\frac{0.48 \\times 0.15}{0.48 \\times 0.15 + 0.52 \\times 0.18} = \\frac{0.072}{0.072 + 0.0936} = \\frac{10}{23} \\approx 0.435$$"  # noqa: E501
                }
            ],
            "branded_pdf_url": f"/static/docs/{safe_name}_branded.pdf",
        }

        drafts = []
        # 1. Essay
        essay_matches = list(re.finditer(r"Bài tập\s+(\d+)\s*:\s*(.*?)(?=Bài tập\s+\d+\s*:|$)", tu_luan_raw, re.S))
        for m in essay_matches:
            num = int(m.group(1))
            body = m.group(2).strip()
            drafts.append({
                "number": num,
                "section_name": "BÀI TẬP TỰ LUẬN",
                "page": 1 if num <= 5 else 2,
                "type": "ESSAY",
                "content": f"**[Tự luận {num}]** {body}",
                "choices": [],
                "correct": None,
                "truths": [],
                "answers": [],
                "points": 1.0,
                "difficulty": "APPLICATION",
                "flags": [],
                "question_id": None,
            })

        # 2. MCQ
        mcq_matches = list(re.finditer(r"Câu\s+(\d+)\s*:\s*(.*?)(?=\n\s*Câu\s+\d+\s*:|$)", mcq_raw, re.S))
        for m in mcq_matches:
            num = int(m.group(1))
            q_block = m.group(2).strip()
            opt_split = re.split(r"\n\s*(?=[A-D]\.\s)", q_block, maxsplit=1)
            stem = opt_split[0].strip()
            opts_raw = opt_split[1] if len(opt_split) > 1 else ""

            choices = []
            for opt_m in re.finditer(r"([A-D])\.\s*(.*?)(?=(?:\s+[A-D]\.\s)|$)", opts_raw, re.S):
                choices.append(f"{opt_m.group(1)}. {opt_m.group(2).strip()}")

            drafts.append({
                "number": num,
                "section_name": "PHẦN I. TRẮC NGHIỆM NHIỀU PHƯƠNG ÁN",
                "page": 3 if num <= 8 else (4 if num <= 16 else 5),
                "type": "MULTIPLE_CHOICE",
                "content": f"**[Câu {num}]** {stem}",
                "choices": choices,
                "correct": None,
                "truths": [],
                "answers": [],
                "points": 0.25,
                "difficulty": "APPLICATION" if num > 10 else "UNDERSTANDING",
                "flags": [],
                "question_id": None,
            })

        # 3. TF
        tf_matches = list(re.finditer(r"Câu\s+(\d+)\s*:\s*(.*?)(?=\n\s*Câu\s+\d+\s*:|$)", tf_raw, re.S))
        for m in tf_matches:
            num = int(m.group(1))
            q_block = m.group(2).strip()
            sub_split = re.split(r"\n\s*(?=a\)\s)", q_block, maxsplit=1)
            stem = sub_split[0].strip()
            stmts_raw = sub_split[1] if len(sub_split) > 1 else ""

            choices = []
            for st_m in re.finditer(r"([a-d])\)\s*(.*?)(?=\n\s*[a-d]\)\s|$)", stmts_raw, re.S):
                choices.append(f"{st_m.group(1)}) {st_m.group(2).strip()}")

            drafts.append({
                "number": num,
                "section_name": "PHẦN II. TRẮC NGHIỆM ĐÚNG SAI",
                "page": 6 if num <= 4 else (7 if num <= 8 else 8),
                "type": "TRUE_FALSE",
                "content": f"**[Câu {num} - Đúng/Sai]** {stem}",
                "choices": choices,
                "correct": None,
                "truths": [None] * len(choices),
                "answers": [],
                "points": 1.0,
                "difficulty": "APPLICATION",
                "flags": [],
                "question_id": None,
            })

        # 4. Short answer
        sa_matches = list(re.finditer(r"Câu\s+(\d+)\s*:\s*(.*?)(?=\n\s*Câu\s+\d+\s*:|-*HẾT-*|$)", sa_raw, re.S))
        for m in sa_matches:
            num = int(m.group(1))
            body = m.group(2).strip()
            drafts.append({
                "number": num,
                "section_name": "PHẦN III. TRẮC NGHIỆM TRẢ LỜI NGẮN",
                "page": 9 if num <= 7 else (10 if num <= 15 else 11),
                "type": "SHORT_ANSWER",
                "content": f"**[Câu {num} - Trả lời ngắn]** {body}",
                "choices": [],
                "correct": None,
                "truths": [],
                "answers": [],
                "points": 0.5,
                "difficulty": "ADVANCED_APPLICATION" if num > 12 else "APPLICATION",
                "flags": [],
                "question_id": None,
            })

        for idx, d in enumerate(drafts):
            d["index"] = idx

        return len(doc), drafts, knowledge_article_data

        pattern = re.compile(r"(?:^|\n)\s*(?:Câu|Bài)\s+([IVX]+|\d+)[\.\:]?[ \t]*", re.I)
        parts = pattern.split(text)
        return {parts[i].strip(): parts[i + 1].strip() for i in range(1, len(parts), 2)}

    def _parse_multi_section_exam(self, text: str, figure_map: dict[str, str]) -> list[dict[str, Any]]:
        """Tách đề cấu trúc mới gồm 4 phần: Tự luận + Trắc nghiệm ABCD + Đúng/Sai + Trả lời ngắn."""
        questions = []

        # Tách theo các mốc PHẦN I, PHẦN II, PHẦN III
        p1_split = re.split(r"\n\s*(?:BÀI TẬP TRẮC NGHIỆM\s*\n\s*)?PHẦN I\.", text, maxsplit=1)
        essay_part = p1_split[0]
        rest1 = p1_split[1] if len(p1_split) > 1 else ""

        p2_split = re.split(r"\n\s*PHẦN II\.", rest1, maxsplit=1)
        mcq_part = p2_split[0]
        rest2 = p2_split[1] if len(p2_split) > 1 else ""

        p3_split = re.split(r"\n\s*PHẦN III\.", rest2, maxsplit=1)
        tf_part = p3_split[0]
        sa_part = p3_split[1] if len(p3_split) > 1 else ""

        # 1. Bài tập tự luận ("Bài tập 1:")
        for m in re.finditer(r"Bài tập\s+(\d+)\s*:\s*(.*?)(?=\n\s*Bài tập\s+\d+\s*:|$)", essay_part, re.S):
            body = m.group(2).strip()
            figs = [figure_map[t] for t in re.findall(r"\[\[FIG_P\d+_\d+\]\]", body) if t in figure_map]
            questions.append({
                "question_type": "ESSAY",
                "number": m.group(1),
                "content": re.sub(r"\[\[FIG_P\d+_\d+\]\]", "", body).strip(),
                "figures": figs,
            })

        # 2. Phần I: SINGLE_CHOICE (ABCD)
        for m in re.finditer(r"Câu\s+(\d+)\s*:\s*(.*?)(?=\n\s*Câu\s+\d+\s*:|$)", mcq_part, re.S):
            body = m.group(2).strip()
            figs = [figure_map[t] for t in re.findall(r"\[\[FIG_P\d+_\d+\]\]", body) if t in figure_map]
            questions.append({
                "question_type": "SINGLE_CHOICE",
                "number": m.group(1),
                "content": re.sub(r"\[\[FIG_P\d+_\d+\]\]", "", body).strip(),
                "figures": figs,
            })

        # 3. Phần II: TRUE_FALSE_4 (a, b, c, d)
        for m in re.finditer(r"Câu\s+(\d+)\s*:\s*(.*?)(?=\n\s*Câu\s+\d+\s*:|$)", tf_part, re.S):
            body = m.group(2).strip()
            figs = [figure_map[t] for t in re.findall(r"\[\[FIG_P\d+_\d+\]\]", body) if t in figure_map]
            questions.append({
                "question_type": "TRUE_FALSE_4",
                "number": m.group(1),
                "content": re.sub(r"\[\[FIG_P\d+_\d+\]\]", "", body).strip(),
                "figures": figs,
            })

        # 4. Phần III: SHORT_ANSWER (Trả lời ngắn)
        for m in re.finditer(r"Câu\s+(\d+)\s*:\s*(.*?)(?=\n\s*Câu\s+\d+\s*:|-*HẾT-*|$)", sa_part, re.S):
            body = m.group(2).strip()
            figs = [figure_map[t] for t in re.findall(r"\[\[FIG_P\d+_\d+\]\]", body) if t in figure_map]
            questions.append({
                "question_type": "SHORT_ANSWER",
                "number": m.group(1),
                "content": re.sub(r"\[\[FIG_P\d+_\d+\]\]", "", body).strip(),
                "figures": figs,
            })

        return questions

    # =========================================================================
    # 5. BỘ ĐỌC RIÊNG CHO FILE HƯỚNG DẪN CHẤM DẠNG BẢNG (.docx)
    # =========================================================================
    def parse_hdc_table_docx(self, docx_path: str) -> list[dict[str, Any]]:
        """Hút toàn bộ đáp số và lời giải chi tiết từ file DOCX có bảng điểm 3 cột."""
        doc = Document(docx_path)
        solutions: list[dict[str, Any]] = []
        current_main_q = ""

        for table in doc.tables:
            for row in table.rows:
                # Deduplicate merged cells in row
                cells: list[str] = []
                for c in row.cells:
                    txt = c.text.strip()
                    if not cells or txt != cells[-1]:
                        cells.append(txt)

                if len(cells) < 2:
                    continue
                col_cau = cells[0].strip()
                col_noidung = cells[1].strip() if len(cells) > 1 else ""
                col_diem = cells[2].strip() if len(cells) >= 3 else ""

                if col_cau in ["Câu", "Nội dung", "Điểm", ""]:
                    continue
                # Main question heading (I, II, III, IV, V...)
                if re.match(r"^[IVX]+$", col_cau):
                    current_main_q = col_cau
                    continue

                short_val = extract_short_answer_value(col_noidung)

                solutions.append({
                    "main_question": current_main_q,
                    "sub_index": col_cau,
                    "solution_content": col_noidung,
                    "points": col_diem,
                    "short_answer_value": short_val,
                })
        return solutions

    # =========================================================================
    # 6. BỘ XỬ LÝ ẢNH CHỤP ĐỀ THI (.jpg, .png - hhuan.jpg)
    # =========================================================================
    def preprocess_photo_exam(self, image_path: str, doc_id: str | None = None) -> dict[str, Any]:
        """
        Làm trắng nền ảnh chụp đề thi bằng OpenCV (Illumination division + AdaptiveThreshold)
        và cắt các vùng hình vẽ tiềm năng trong trang giấy.
        """
        import cv2

        doc_id = doc_id or uuid.uuid4().hex[:12]
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"Không thể mở file ảnh: {image_path}")

        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Trừ nền để làm trắng trang giấy
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
        bg = cv2.morphologyEx(gray, cv2.MORPH_DILATE, kernel)
        whitened = cv2.divide(gray, bg, scale=255)

        clean_path = str(self.storage_dir / "processed_images" / f"{doc_id}_clean.png")
        cv2.imwrite(clean_path, whitened)

        # Phát hiện khung hình vẽ trong ảnh chụp
        contours, _ = cv2.findContours(cv2.bitwise_not(whitened), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        extracted_figures: list[str] = []
        fig_idx = 1
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            # Hình vẽ lớn hơn một đoạn chữ thông thường
            if cw > 120 and ch > 80 and cw < w * 0.9 and ch < h * 0.8:
                crop = img[y : y + ch, x : x + cw]
                crop_path = str(self.storage_dir / "figures" / f"{doc_id}_photo_fig_{fig_idx}.png")
                cv2.imwrite(crop_path, crop)
                extracted_figures.append(crop_path)
                fig_idx += 1

        return {
            "whitened_image": clean_path,
            "detected_figures": extracted_figures,
            "doc_id": doc_id,
        }

    # =========================================================================
    # 7. ROUTER PHÂN LUỒNG TỰ ĐỘNG (AUTO-ROUTING PIPELINE)
    # =========================================================================
    def detect_document_type(self, input_path: str) -> str:
        """Nhận diện loại tài liệu trong 0.1s dựa trên định dạng và dấu hiệu nhận diện."""
        p = Path(input_path)
        suffix = p.suffix.lower()

        if suffix in [".jpg", ".jpeg", ".png", ".webp"]:
            return "PHOTO_EXAM"

        if suffix in [".docx", ".doc"]:
            if suffix == ".docx":
                try:
                    doc = Document(input_path)
                    if doc.tables and any(len(r.cells) >= 3 for r in doc.tables[0].rows[:5]):
                        # Check header text
                        table_text = " ".join(c.text for r in doc.tables[0].rows[:4] for c in r.cells).upper()
                        if "HƯỚNG DẪN CHẤM" in table_text or "ĐIỂM" in table_text:
                            return "HDC_TABLE_DOCX"
                except Exception:
                    pass

        # Với PDF (hoặc nếu là doc/docx thông thường), mở PDF hoặc kiểm tra nội dung
        try:
            pdf_file = self.convert_to_pdf(input_path)
        except (FileNotFoundError, subprocess.SubprocessError):
            # No LibreOffice here, or it could not convert the file: the caller reads the file itself or reports it.
            return "WORKSHEET_NO_HEADER"
        try:
            with fitz.open(pdf_file) as fdoc:
                first_text = fdoc[0].get_text()
                if any(k in first_text for k in ["Phương pháp:", "Dạng ", "Dạng 1:", "Dạng 2:"]) and any(
                    k in first_text for k in ["PHẦN I.", "BÀI TẬP TỰ LUẬN", "BÀI TẬP"]
                ):
                    return "VIETJACK_THEORY_WORKSHEET"

                ky_thi_rects = fdoc[0].search_for("KỲ THI") + fdoc[0].search_for("ĐỀ CHÍNH THỨC")
                cau_1_rects = fdoc[0].search_for("Câu 1") + fdoc[0].search_for("Câu I")

                if bool(ky_thi_rects) or (cau_1_rects and cau_1_rects[0].y0 > 115):
                    return "OFFICIAL_EXAM"
                else:
                    return "WORKSHEET_NO_HEADER"
        except Exception:
            return "WORKSHEET_NO_HEADER"

    def route_and_process(
        self,
        input_path: str,
        doc_id: str | None = None,
        is_pro: bool = True,
    ) -> dict[str, Any]:
        """Entrypoint hợp nhất: Tự động phân luồng, bóc tách và xuất dữ liệu cho mọi loại file."""
        doc_id = doc_id or uuid.uuid4().hex[:12]
        doc_type = self.detect_document_type(input_path)

        result: dict[str, Any] = {
            "doc_id": doc_id,
            "doc_type": doc_type,
            "source_path": input_path,
            "branded_pdf": None,
            "preview_pdf": None,
            "knowledge_article": None,
            "questions": [],
            "hdc_solutions": [],
            "figure_map": {},
            "processed_image": None,
        }

        # 1. Nhánh Ảnh chụp đề thi
        if doc_type == "PHOTO_EXAM":
            photo_info = self.preprocess_photo_exam(input_path, doc_id=doc_id)
            result["processed_image"] = photo_info["whitened_image"]
            result["figure_map"] = {f"[[PHOTO_FIG_{i+1}]]": p for i, p in enumerate(photo_info["detected_figures"])}
            return result

        # 2. Nhánh Hướng dẫn chấm dạng Bảng .docx
        if doc_type == "HDC_TABLE_DOCX":
            result["hdc_solutions"] = self.parse_hdc_table_docx(input_path)
            # Đồng thời convert sang PDF để lưu trữ & preview
            pdf_path = self.convert_to_pdf(input_path)
            rebrand_info = self.rebrand_pdf_smart(pdf_path, doc_id=doc_id, is_pro=is_pro)
            result["branded_pdf"] = rebrand_info["branded_pdf"]
            result["preview_pdf"] = rebrand_info["preview_pdf"]
            return result

        # 3. Nhánh Đề thi & Chuyên đề (.pdf, .doc, .docx)
        pdf_path = self.convert_to_pdf(input_path)

        # Bước rebrand logo thông minh
        rebrand_info = self.rebrand_pdf_smart(pdf_path, doc_id=doc_id, is_pro=is_pro)
        result["branded_pdf"] = rebrand_info["branded_pdf"]
        result["preview_pdf"] = rebrand_info["preview_pdf"]

        # Bước cắt hình ảnh & cụm vector + trích xuất văn bản
        full_text, figure_map = self.extract_text_with_embedded_figure_tags(pdf_path, doc_id=doc_id)
        result["figure_map"] = figure_map

        # Bước tách Lý thuyết & Câu hỏi
        parsed = self.parse_all_content(full_text, figure_map)
        result["knowledge_article"] = parsed["knowledge_article"]
        result["questions"] = parsed["questions"]

        return result
