"""Deterministic High-Precision 6-Step Exam Extraction Engine:
Bóc tách câu hỏi từ mọi định dạng trong kho Google Drive / Upload (.rar, .zip, .doc, .docx, .pdf)
vào ngân hàng đề với chi phí API AI gần như bằng 0.

Các bước thực hiện:
Bước 1: Chuẩn hóa File & Tự động ghép cặp (Đề + Hướng dẫn chấm)
Bước 2: Rút trích Văn bản, Công thức LaTeX và Bảng biểu (Nhánh A - 0 tốn API)
Bước 3: Cắt toàn bộ Hình vẽ (Hình 1, Hình 2a...) và Lọc bỏ Logo (Nhánh B)
Bước 4: Tách Câu hỏi lớn & Các ý nhỏ (1, 2, a, b) bằng Regex State Machine
Bước 5: Ghép Đáp án tự động & Tinh chỉnh bằng 1 Request AI duy nhất
Bước 6: Băm SHA-256 chống trùng & Upsert vào PostgreSQL + MinIO
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from app.domain.question_hash import normalize_text

log = logging.getLogger(__name__)

# ==============================================================================
# BƯỚC 1: CHUẨN HÓA FILE & TỰ ĐỘNG GHÉP CẶP (ĐỀ + HƯỚNG DẪN CHẤM)
# ==============================================================================

SOLUTION_KEYWORDS = [
    "đáp án", "dap an", "hdc", "hướng dẫn chấm", "huong dan cham",
    "hds", "hướng dẫn giải", "huong dan giai", "lời giải", "loi giai",
    "biểu điểm", "bieu diem", "thang điểm", "thang diem", "da_", "ans", "key"
]

def is_solution_name(filename: str) -> bool:
    """Kiểm tra tên file có phải là file Đáp án / Hướng dẫn chấm hay không."""
    stem = Path(filename).stem
    # Chuẩn hóa cả dấu gạch ngang, gạch dưới thành dấu cách để so khớp từ khóa
    name_clean = " ".join(re.sub(r"[\-_.]+", " ", unicodedata.normalize("NFC", stem).lower()).split())
    if any(k in name_clean for k in SOLUTION_KEYWORDS):
        return True
    return bool(re.search(r"\b(?:da|hdc|hd cham|dap an|dapan|key|ans)\b", name_clean))

def find_soffice(configured: str = "soffice") -> str:
    """Tìm đường dẫn thực thi LibreOffice soffice trên Windows, Linux, macOS."""
    if shutil.which(configured) or (os.path.isabs(configured) and Path(configured).exists()):
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
    return configured

def convert_doc_to_docx(doc_path: Path | str, out_dir: Path | str, soffice: str = "soffice") -> Path:
    """Nâng cấp file .doc (Word 97-2003) lên .docx bằng LibreOffice headless."""
    soffice_bin = find_soffice(soffice)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc_path = Path(doc_path)
    user_prof = out_dir / "lo_profile"
    subprocess.run(
        [
            soffice_bin,
            "--headless",
            "--norestore",
            f"-env:UserInstallation=file:///{user_prof.as_posix()}",
            "--convert-to",
            "docx",
            "--outdir",
            str(out_dir),
            str(doc_path),
        ],
        check=True,
        timeout=180,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    result = out_dir / (doc_path.stem + ".docx")
    if not result.exists():
        raise RuntimeError(f"Chuyển đổi {doc_path.name} sang .docx thất bại.")
    return result


def has_mathtype_ole(docx_path: Path | str) -> bool:
    """Return whether a DOCX contains embedded MathType/OLE objects."""
    try:
        with zipfile.ZipFile(docx_path) as archive:
            names = set(archive.namelist())
            if any(name.startswith("word/embeddings/") for name in names):
                return True
            xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
            return "<w:object" in xml or "oleObject" in xml
    except (OSError, KeyError, zipfile.BadZipFile):
        return False

def unpack_archive(archive_path: Path | str, out_dir: Path | str) -> list[Path]:
    """Giải nén tự động file .zip hoặc .rar bằng zipfile hoặc patool."""
    archive_path = Path(archive_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = archive_path.suffix.lower()

    if suffix == ".zip":
        with zipfile.ZipFile(archive_path, "r") as z:
            z.extractall(out_dir)
    else:
        try:
            import patoolib
            patoolib.extract_archive(str(archive_path), outdir=str(out_dir), verbosity=-1)
        except Exception as exc:
            log.warning("patool could not extract %s: %s", archive_path.name, exc)
            raise RuntimeError(f"Không thể giải nén file archive {archive_path.name}: {exc}") from exc

    extracted = [
        p for p in out_dir.rglob("*")
        if p.is_file() and not p.name.startswith((".", "~$"))
        and p.suffix.lower() in (".docx", ".doc", ".pdf")
    ]
    return extracted

def pair_exam_and_solution_files(files: list[Path], soffice: str = "soffice") -> list[tuple[Path, Path | None]]:
    """Loại bỏ bản trùng .pdf nếu đã có .docx, chuyển .doc thành .docx, và ghép cặp Đề + Đáp án."""
    # 1. Nâng cấp .doc -> .docx
    processed_files: list[Path] = []
    for f in files:
        if f.suffix.lower() == ".doc":
            try:
                docx = convert_doc_to_docx(f, f.parent, soffice)
                processed_files.append(docx)
            except Exception as e:
                log.warning("Could not convert .doc %s: %e", f.name, e)
                processed_files.append(f)
        else:
            processed_files.append(f)

    # 2. Loại bỏ .pdf trùng nếu đã có .docx cùng tên gốc trong cùng thư mục
    docx_stems = {f.parent / f.stem for f in processed_files if f.suffix.lower() == ".docx"}
    filtered_files: list[Path] = []
    for f in processed_files:
        if f.suffix.lower() == ".pdf" and (f.parent / f.stem) in docx_stems:
            continue  # Bỏ qua bản pdf vì đã có docx cùng thư mục
        filtered_files.append(f)

    # 3. Phân tách file Đề và file Đáp án
    solutions = [f for f in filtered_files if is_solution_name(f.name)]
    exams = [f for f in filtered_files if not is_solution_name(f.name)]

    pairs: list[tuple[Path, Path | None]] = []
    if len(exams) == 1 and len(solutions) == 1:
        pairs.append((exams[0], solutions[0]))
        return pairs

    # Ghép theo tên tương đồng.  So sánh tên đã chuẩn hóa để các biến thể
    # "ĐỀ 01", "01-DE1" và "ĐÁP ÁN 01" không bị bỏ sót vì dấu nối/ký tự Việt.
    used_solutions = set()
    for ex in exams:
        ex_clean = _pairing_stem(ex.stem)
        matched_sol: Path | None = None
        for sol in solutions:
            if sol in used_solutions:
                continue
            sol_clean = _pairing_stem(sol.stem)
            if ex_clean and sol_clean and (
                ex_clean == sol_clean
                or ex_clean in sol_clean
                or sol_clean in ex_clean
            ):
                matched_sol = sol
                used_solutions.add(sol)
                break
        pairs.append((ex, matched_sol))

    return pairs


def _pairing_stem(stem: str) -> str:
    """Return a stable comparison key for an exam/solution filename."""
    value = unicodedata.normalize("NFC", stem).casefold()
    value = unicodedata.normalize("NFD", value)
    value = "".join(char for char in value if unicodedata.category(char) != "Mn").replace("đ", "d")
    value = re.sub(
        r"\b(?:de\s*(?:thi|chinh\s*thuc)?|dap\s*an|hdc|huong\s*dan\s*(?:cham|giai))\b",
        " ",
        value,
    )
    return re.sub(r"[^0-9a-z]+", "", value)


# ==============================================================================
# BƯỚC 2: RÚT TRÍCH VĂN BẢN, CÔNG THỨC LATEX VÀ BẢNG BIỂU (NHÁNH A - 0 TỐN API)
# ==============================================================================

def docx_to_markdown_latex(docx_path: Path | str) -> str:
    """Biến toàn bộ file Word thành Markdown + LaTeX, ép bảng phức tạp ra HTML <table> giữ colspan/rowspan."""
    import pypandoc
    with tempfile.TemporaryDirectory(prefix="pandoc-media-") as media_dir:
        md_text = pypandoc.convert_file(
            str(docx_path),
            to="markdown-simple_tables-multiline_tables-grid_tables",
            format="docx",
            extra_args=["--wrap=none", f"--extract-media={media_dir}"],
        )
    return md_text

def pdf_to_continuous_text(pdf_path: Path | str) -> str:
    """PyMuPDF đọc luồng văn bản xuyên suốt tất cả các trang, loại bỏ Header/Footer và tiêu đề đầu trang 1."""
    import pymupdf
    with pymupdf.open(pdf_path) as doc:
        full_pages_text = []

        for page_idx, page in enumerate(doc):
            # Cắt bỏ 50px mép trên (chứa "PRO: admin...") và 50px mép dưới (chứa "Trang 1/3")
            clean_rect = pymupdf.Rect(0, 50, page.rect.width, max(50.0, page.rect.height - 50))
            blocks = page.get_text("blocks", clip=clean_rect)
            blocks.sort(key=lambda b: (round(b[1], 1), round(b[0], 1)))

            page_lines = []
            for b in blocks:
                text = b[4].strip()
                # Bỏ qua khối tiêu đề trường/kỳ thi ở đầu trang 1
                if page_idx == 0 and any(k in text.upper() for k in ["KỲ THI CHỌN", "ĐỀ CHÍNH THỨC", "THỜI GIAN LÀM BÀI", "HƯỚNG DẪN CHẤM ĐỀ"]):
                    continue
                page_lines.append(text)

            full_pages_text.append("\n".join(page_lines))

        return "\n".join(full_pages_text)


# ==============================================================================
# BƯỚC 3: CẮT HÌNH VẼ THEO CHÚ THÍCH CAPTION VÀ LỌC BỎ LOGO (NHÁNH B)
# ==============================================================================

CAPTION_REGEX = re.compile(r"(?:^|\b)((?:Hình|Sơ đồ|Đồ thị)\s*\d+(?:[\.\,]\d+|[a-zA-Z])?)\b", re.IGNORECASE)


def extract_figures_by_caption(pdf_path: Path | str) -> dict[str, bytes]:
    """Quét nhãn chú thích 'Hình X', 'Hình 2a', 'Hình 1.1' trên bản PDF và chụp vùng hình tương ứng.
    Bỏ qua logo ở y0 < 160 trên trang 1.
    Trả về dict: {'Hình 1': png_bytes, 'Hình 2a': png_bytes, 'Hình 1.1': png_bytes}
    """
    import pymupdf
    with pymupdf.open(pdf_path) as doc:
        figure_map: dict[str, bytes] = {}

        for page_idx, page in enumerate(doc):
            # 1. Danh sách các ảnh Raster nhúng trên trang (bỏ qua logo ở y0 < 160 trang 1)
            raster_boxes = []
            for img in page.get_images(full=True):
                rects = page.get_image_rects(img[0])
                if rects:
                    r = rects[0]
                    if not (page_idx == 0 and r.y0 < 160):
                        raster_boxes.append(r)

            # 2. Quét các dòng chữ chứa caption
            blocks = page.get_text("blocks")
            for b in blocks:
                match = CAPTION_REGEX.search(b[4])
                if not match:
                    continue
                fig_label = re.sub(r"\s+", " ", match.group(1).strip())
                fig_label = fig_label[0].upper() + fig_label[1:]
                cap_rect = pymupdf.Rect(b[:4])

                # Tìm ảnh raster nằm ngay sát phía trên chú thích này
                matched_raster = None
                for r_box in raster_boxes:
                    if abs(r_box.y1 - cap_rect.y0) < 90 and abs(r_box.x0 - cap_rect.x0) < 220:
                        matched_raster = r_box
                        break

                if matched_raster:
                    crop_rect = matched_raster
                else:
                    # Nếu là hình vẽ Vector / Word Shapes, chụp khung phía trên dòng chữ caption
                    crop_rect = pymupdf.Rect(
                        max(cap_rect.x0 - 80, 20),
                        max(cap_rect.y0 - 240, 50),
                        min(cap_rect.x1 + 140, page.rect.width - 20),
                        cap_rect.y0 + 2,
                    )

                crop_rect = pymupdf.Rect(crop_rect) & page.rect
                if crop_rect.is_empty or crop_rect.width < 5 or crop_rect.height < 5:
                    continue  # a caption at the very top of a page: nothing above it to cut
                pix = page.get_pixmap(clip=crop_rect, dpi=300)
                figure_map[fig_label] = pix.tobytes("png")

        return figure_map


def extract_chemistry_schemes(pdf_path: Path | str) -> list[tuple[int, float, bytes]]:
    """Extract unlabeled chemistry reaction schemes from raster/vector PDF graphics.

    The source PDF is never modified.  Each result is ``(page, y0, png)`` and
    can be attached to the question occupying the same page.
    """
    import pymupdf

    schemes: list[tuple[int, float, bytes]] = []
    with pymupdf.open(pdf_path) as doc:
        for page_index, page in enumerate(doc):
            boxes = list(page.cluster_drawings(x_tolerance=25, y_tolerance=25))
            for image in page.get_images(full=True):
                rects = page.get_image_rects(image[0])
                if rects:
                    boxes.append(rects[0])

            unique: list[pymupdf.Rect] = []
            for box in boxes:
                rect = pymupdf.Rect(box)
                if page_index == 0 and rect.y0 < 140:
                    continue
                if rect.height < 30 or rect.width < 20:
                    continue
                if any(abs(rect.x0 - old.x0) < 3 and abs(rect.y0 - old.y0) < 3 for old in unique):
                    continue
                unique.append(rect)

            for rect in sorted(unique, key=lambda item: (item.y0, item.x0)):
                padded = pymupdf.Rect(
                    max(0, rect.x0 - 10),
                    max(0, rect.y0 - 10),
                    min(page.rect.width, rect.x1 + 10),
                    min(page.rect.height, rect.y1 + 10),
                )
                pix = page.get_pixmap(clip=padded, dpi=300, alpha=False)
                schemes.append((page_index + 1, rect.y0, pix.tobytes("png")))
    return schemes


# ==============================================================================
# BƯỚC 4: TÁCH CÂU HỎI LỚN & Ý NHỎ (1, 2, a, b) BẰNG REGEX STATE MACHINE
# ==============================================================================

MAIN_QUESTION_PATTERN = re.compile(
    r"(?:^|\n)\s*((?:Câu|Bài)\s+(?:[IVX]+|\d+)[\.\:]?\s*(?:\(\d+[,\.]\d+\s*điểm\))?\s*[\–\-\:]?\s*[^\n]*)",
    re.IGNORECASE,
)
SUB_LEVEL_1_PATTERN = re.compile(r"(?:^|\n)\s*(\d+[\.\)])\s+")
SUB_LEVEL_2_PATTERN = re.compile(r"(?:^|\n)\s*(\(?[a-d][\.\)])\s+")

@dataclass
class SubQuestionItem:
    sub_index: str
    content: str
    solution: str | None = None
    short_answer_value: str | None = None

@dataclass
class StructuredQuestion:
    index: int
    header: str
    number: int
    topic_raw: str
    total_points: float
    stem_content: str
    content: str  # Toàn bộ nội dung hoàn chỉnh của bài
    sub_questions: list[SubQuestionItem] = field(default_factory=list)
    attached_figures: list[str] = field(default_factory=list)
    figure_bytes: dict[str, bytes] = field(default_factory=dict)
    solution: str | None = None
    kntt_unit_code: str | None = None
    difficulty: str = "HSG"
    content_hash: str = ""

def split_into_structured_questions(
    full_text: str, figure_map: dict[str, bytes] | None = None
) -> list[StructuredQuestion]:
    """Phân tầng tách:
    Tầng 1: Tách Bài/Câu lớn (lấy số điểm và chuyên đề).
    Tầng 2: Tách phần giả thiết chung (Stem) và các ý 1., 2.
    Tầng 3: Tách các ý nhỏ a), b) bên trong.
    Gắn hình vẽ tương ứng nếu trong nội dung có nhắc tới 'Hình X'.
    """
    figure_map = figure_map or {}
    parts = MAIN_QUESTION_PATTERN.split(full_text)
    questions: list[StructuredQuestion] = []
    q_counter = 1

    for i in range(1, len(parts), 2):
        header = parts[i].strip()
        body = parts[i + 1].strip()

        # Rút trích điểm số
        pts_match = re.search(r"\((\d+[,\.]\d+)\s*điểm\)", header)
        points = float(pts_match.group(1).replace(",", ".")) if pts_match else 2.0

        # Rút trích số thứ tự bài
        num_match = re.search(r"(?:Câu|Bài)\s*([IVX]+|\d+)", header, re.I)
        num_str = num_match.group(1) if num_match else str(q_counter)
        if num_str.isdigit():
            q_num = int(num_str)
        else:
            roman_map = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
            q_num = roman_map.get(num_str.upper(), q_counter)

        # Chuyên đề
        topic_raw = re.sub(r"^.*?\(\d+[,\.]\d+\s*điểm\)\s*[\–\-\:]?\s*", "", header).strip()

        # Tìm các hình vẽ được nhắc tới
        attached_figs = [
            fig_key for fig_key in figure_map.keys()
            if re.search(rf"\b{re.escape(fig_key)}\b", body, re.IGNORECASE)
        ]
        fig_data = {fk: figure_map[fk] for fk in attached_figs if fk in figure_map}

        # Tách ý lớn 1., 2.
        sub_splits = SUB_LEVEL_1_PATTERN.split(body)
        stem_content = sub_splits[0].strip()
        sub_questions: list[SubQuestionItem] = []

        if len(sub_splits) > 1:
            for j in range(1, len(sub_splits), 2):
                s_idx = sub_splits[j].strip(". )")
                s_body = sub_splits[j + 1].strip()
                sub_questions.append(SubQuestionItem(sub_index=s_idx, content=s_body))
        else:
            # Kiểm tra ý a), b) nếu không có 1., 2.
            sub2 = SUB_LEVEL_2_PATTERN.split(body)
            if len(sub2) > 1:
                stem_content = sub2[0].strip()
                for j in range(1, len(sub2), 2):
                    s_idx = sub2[j].strip(". )(")
                    s_body = sub2[j + 1].strip()
                    sub_questions.append(SubQuestionItem(sub_index=s_idx, content=s_body))
            else:
                sub_questions.append(SubQuestionItem(sub_index="1", content=body))

        # Toàn bộ nội dung hoàn chỉnh của bài giữ nguyên các ý
        full_q_content = body if not stem_content or body == stem_content else body

        # SHA-256 chống trùng
        digest = hashlib.sha256(normalize_text(full_q_content).encode("utf-8")).hexdigest()

        questions.append(
            StructuredQuestion(
                index=len(questions),
                header=header,
                number=q_num,
                topic_raw=topic_raw,
                total_points=points,
                stem_content=stem_content or full_q_content,
                content=full_q_content,
                sub_questions=sub_questions,
                attached_figures=attached_figs,
                figure_bytes=fig_data,
                content_hash=digest,
            )
        )
        q_counter += 1

    return questions


# ==============================================================================
# BƯỚC 5: GHÉP ĐÁP ÁN TỰ ĐỘNG & TINH CHỈNH BẰNG 1 REQUEST AI DUY NHẤT
# ==============================================================================

SHORT_ANSWER_VALUE_PATTERNS = [
    re.compile(r"(?:đáp\s*số|kết\s*quả|đáp\s*án|vậy[^:=,\n]{0,30})\s*[:=]\s*(?:[a-zA-Z_]+\s*=\s*)?(-?\d+(?:[.,]\d+)?)\s*(?:%|[a-zA-Z/°^]+)?", re.I),
    re.compile(r"(?:=|\b)\s*(-?\d+(?:[.,]\d+)?)\s*(?:m/s|kg|g|m|cm|mm|s|N|J|kJ|W|kW|V|A|mA|mol|L|lít|%|K|atm|Pa)\b", re.I),
    re.compile(r"(-?\d+(?:[.,]\d+)?)\s*$", re.I),
]

def extract_short_answer_value(text: str) -> str | None:
    """Rút trích con số kết quả cuối cùng từ lời giải để phục vụ câu hỏi điền đáp số tự động."""
    if not text:
        return None
    for pat in SHORT_ANSWER_VALUE_PATTERNS:
        m = pat.search(text)
        if m:
            val = m.group(1).replace(",", ".")
            return val
    return None

def pair_solutions_into_questions(
    questions: list[StructuredQuestion],
    solution_text: str,
) -> None:
    """Ghép trực tiếp lời giải từ file/văn bản HDC vào từng câu hỏi và từng ý nhỏ."""
    ans_structured = split_into_structured_questions(solution_text)
    ans_by_num: dict[int, list[StructuredQuestion]] = {}
    for answer in ans_structured:
        ans_by_num.setdefault(answer.number, []).append(answer)

    for index, q in enumerate(questions):
        numbered = ans_by_num.get(q.number, [])
        # A number can restart in each section.  Only use number matching when
        # it is unambiguous; otherwise preserve document order.
        sol_q = numbered[0] if len(numbered) == 1 else (
            ans_structured[index] if index < len(ans_structured) else None
        )
        if not sol_q:
            continue

        q.solution = sol_q.content

        # Ghép lời giải vào từng ý con tương ứng
        if q.sub_questions and sol_q.sub_questions:
            sol_sub_map = {s.sub_index: s.content for s in sol_q.sub_questions}
            for idx, sq in enumerate(q.sub_questions):
                matching_ans = sol_sub_map.get(sq.sub_index)
                if not matching_ans and idx < len(sol_q.sub_questions):
                    matching_ans = sol_q.sub_questions[idx].content
                if matching_ans:
                    sq.solution = matching_ans
                    sq.short_answer_value = extract_short_answer_value(matching_ans)


# ==============================================================================
# BƯỚC 6: BĂM SHA-256 CHỐNG TRÙNG & CHUYỂN THÀNH DRAFT
# ==============================================================================

def to_pipeline_draft(
    sq: StructuredQuestion,
    figure_key: str | None = None,
    default_difficulty: str = "HSG",
) -> dict[str, Any]:
    """Chuyển đổi StructuredQuestion thành bản ghi draft tương thích hoàn toàn với ImportJob.drafts."""
    from app.application.knowledge_linker import infer_knowledge_metadata

    # Gắn mã bài học SGK KNTT
    meta = infer_knowledge_metadata(sq.content, default_difficulty)
    kntt_code = sq.kntt_unit_code or meta["kntt_unit_code"]

    # Tổng hợp short answer value
    short_val = None
    for sub in sq.sub_questions:
        if sub.short_answer_value:
            short_val = sub.short_answer_value
            break
    if not short_val and sq.solution:
        short_val = extract_short_answer_value(sq.solution)

    answers = [short_val] if short_val else []

    return {
        "number": sq.number,
        "page": 1,
        "type": "ESSAY",
        "content": sq.content,
        "choices": [],
        "correct": None,
        "truths": [],
        "answers": answers,
        "short_answer": short_val,
        "final_value": short_val,
        "explanation": sq.solution or "",
        "difficulty": default_difficulty,
        "difficulty_from_matrix": False,
        "kntt_unit_code": kntt_code,
        "is_thinking_skill": meta["is_thinking_skill"],
        "suitable_targets": meta["suitable_targets"],
        "access_tier": "PRO" if default_difficulty == "HSG" else "FREE",
        "figure": figure_key,
        "figure_captions": {fk: "stem" for fk in sq.attached_figures},
        "flags": [] if sq.solution else ["no_solution"],
        "key_from_file": bool(sq.solution),
        "question_id": None,
        "content_hash": sq.content_hash,
        "points": sq.total_points,
    }
