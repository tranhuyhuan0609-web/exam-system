"""Office (.doc/.docx) & image conversion and OOXML fallback extractors for A05 parsers."""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

import docx
import numpy as np
from lxml import etree  # type: ignore[import-untyped]
from PIL import Image, ImageOps

from app.modules.parsers.internal.routing import build_libreoffice_job_spec

_DOCX_TABLE_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
_HDC_HEADER_RE = re.compile(r"(?:câu|cau|bài|bai).*?(?:nội\s*dung|noi\s*dung|hướng\s*dẫn).*?(?:điểm|diem)", re.I)
_ROMAN_OR_NUM_Q_RE = re.compile(
    r"^\s*(?:Câu|Cau|Bài|Bai)\s+([IVXLC]+|\d{1,3})\s*(?:[\.:\)\-–(]|$)",
    re.IGNORECASE,
)
_Q_LABEL_CELL_RE = re.compile(
    r"^\s*(?:([IVXLCDM]+|\d{1,3})(?:\.(\d+[a-z]?)|([a-z]))?|(?:Câu|Cau|Bài|Bai)\s+([IVXLCDM]+|\d{1,3}))\b",
    re.IGNORECASE,
)


def find_soffice_executable(configured: str | None = None) -> str | None:
    """Locate the LibreOffice `soffice` binary on Windows, Linux, or macOS."""
    env_path = os.environ.get("SOFFICE_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    if configured:
        found = shutil.which(configured) or (configured if Path(configured).exists() else None)
        if found:
            return found
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (
        r"C:\Program Files\LibreOffice\program\soffice.com",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.com",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice",
        "/usr/bin/libreoffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ):
        if Path(candidate).exists():
            return candidate
    return None


def convert_office_bytes_to_pdf(
    raw_bytes: bytes,
    *,
    filename: str,
    timeout_seconds: int = 90,
) -> bytes | None:
    """Convert .doc / .docx bytes to PDF bytes using LibreOffice headless in an isolated profile."""
    soffice = find_soffice_executable()
    if not soffice or not raw_bytes:
        return None

    ext = Path(filename or "document.docx").suffix.lower()
    if ext not in (".docx", ".doc"):
        ext = ".docx"

    job_id = uuid.uuid4().hex[:16]
    with tempfile.TemporaryDirectory(prefix="a05_lo_") as tmpdir:
        tmp_path = Path(tmpdir)
        input_path = tmp_path / f"input{ext}"
        profile_dir = tmp_path / "profile"
        out_dir = tmp_path / "out"
        profile_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        input_path.write_bytes(raw_bytes)

        spec = build_libreoffice_job_spec(
            job_id=job_id,
            input_path=str(input_path),
            output_dir=str(out_dir),
            profile_root=profile_dir.as_posix(),
            timeout_seconds=timeout_seconds,
        )
        # LibreOffice on Windows requires `file:///C:/...` URI (not `file://C:/...`) for UserInstallation.
        argv = (
            soffice,
            "--headless",
            "--norestore",
            "--invisible",
            f"-env:UserInstallation={profile_dir.as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(input_path),
        )
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                timeout=spec.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

        if proc.returncode != 0:
            return None
        pdf_path = out_dir / "input.pdf"
        if not pdf_path.exists():
            pdfs = list(out_dir.glob("*.pdf"))
            if not pdfs:
                return None
            pdf_path = pdfs[0]
        data = pdf_path.read_bytes()
        return data if data.lstrip().startswith(b"%PDF") else None


def preprocess_photo_exam_bytes(raw_bytes: bytes) -> bytes:
    """Enhance a photographed exam page (grayscale normalization + shadow suppression) without OpenCV."""
    if not raw_bytes:
        return raw_bytes
    with Image.open(io.BytesIO(raw_bytes)) as source:
        img = ImageOps.exif_transpose(source).convert("L")
        arr = np.asarray(img, dtype=np.float32)

        # Estimate local background brightness via coarse block max-pooling so dark shadows/vignettes lighten cleanly.
        h, w = arr.shape
        block = max(16, min(h, w) // 20)
        pad_h = (block - (h % block)) % block
        pad_w = (block - (w % block)) % block
        padded = np.pad(arr, ((0, pad_h), (0, pad_w)), mode="edge")
        bh, bw = padded.shape[0] // block, padded.shape[1] // block
        grid_max = padded.reshape(bh, block, bw, block).max(axis=(1, 3))
        bg = np.array(
            Image.fromarray(grid_max.astype(np.float32), mode="F").resize((w, h), resample=Image.Resampling.BILINEAR)
        )
        normalized = np.clip((arr / np.maximum(bg, 32.0)) * 245.0, 0.0, 255.0)
        # Boost dark ink gently while keeping diagram shading legible
        enhanced = np.where(normalized < 175.0, normalized * 0.72, np.minimum(255.0, normalized * 1.04))
        out_img = Image.fromarray(enhanced.astype(np.uint8), mode="L").convert("RGB")
        buf = io.BytesIO()
        out_img.save(buf, format="PNG")
        return buf.getvalue()


def convert_image_bytes_to_pdf(raw_bytes: bytes) -> bytes:
    """Wrap a photo exam page (after background normalization) into an A4-scaled PDF page."""
    cleaned_png = preprocess_photo_exam_bytes(raw_bytes)
    with Image.open(io.BytesIO(cleaned_png)) as img:
        rgb = img.convert("RGB")
        # Fit comfortably into A4 aspect (max width 1654px @ 200dpi equivalent) with high print DPI metadata
        buf = io.BytesIO()
        rgb.save(buf, format="PDF", resolution=150.0)
        return buf.getvalue()


def _clean_cell(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _extract_docx_omml_fallback_text(xml_element: Any) -> str:
    """Read paragraph/cell text including plain text inside OMML (<m:t>) tags when present."""
    parts: list[str] = []
    for node in xml_element.iter():
        tag = node.tag.rsplit("}", 1)[-1] if isinstance(node.tag, str) else ""
        if tag in ("t", "delText") and node.text:
            parts.append(node.text)
        elif tag == "br":
            parts.append("\n")
    return "".join(parts)


def extract_docx_hdc_questions(raw_bytes: bytes) -> list[dict[str, Any]] | None:
    """Detect and parse 3+ column Grading Rubric (HDC) DOCX tables like `Câu | Nội dung | Điểm`.

    Returns structured question dicts if an HDC rubric table is detected, otherwise None.
    """
    if not raw_bytes:
        return None
    try:
        doc = docx.Document(io.BytesIO(raw_bytes))
    except Exception:
        return None

    best_table = None
    best_score = 0
    for table in doc.tables:
        if len(table.rows) < 4 or len(table.columns) < 3:
            continue
        header_preview = " ".join(
            _clean_cell(c.text) for row in table.rows[:6] for c in row.cells
        )
        if _HDC_HEADER_RE.search(header_preview):
            score = len(table.rows)
            if score > best_score:
                best_score = score
                best_table = table

    if best_table is None:
        return None

    questions: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    seq = 0

    for row in best_table.rows:
        # Deduplicate merged horizontal cells by underlying XML `_tc` identity
        unique_cells = []
        seen_tc: set[int] = set()
        for cell in row.cells:
            tc_id = id(cell._tc)
            if tc_id not in seen_tc:
                seen_tc.add(tc_id)
                unique_cells.append(cell)

        if not unique_cells:
            continue
        texts = [_clean_cell(_extract_docx_omml_fallback_text(c._tc)) for c in unique_cells]
        row_joined = " | ".join(t for t in texts if t)
        if not row_joined:
            continue
        if _HDC_HEADER_RE.search(row_joined) or any(
            kw in row_joined.upper() for kw in ("SỞ GIÁO DỤC", "KỲ THI", "HDC CHÍNH THỨC", "MÔN THI")
        ):
            continue
        if re.fullmatch(r"HẾT\.?", row_joined.strip(), re.I):
            continue

        col0 = texts[0]
        # Top-level Roman section heading row, e.g. col0="I", col1="Câu 1. (5 điểm)"
        col1 = texts[1] if len(texts) > 1 else ""
        pts_raw = texts[-1] if len(texts) >= 3 else ""

        top_q_match = _ROMAN_OR_NUM_Q_RE.match(col1) or (
            re.fullmatch(r"[IVXLCDM]+", col0) and len(texts) <= 3 and ("câu" in col1.lower() or "điểm" in col1.lower())
        )
        if top_q_match or (len(col0) <= 6 and re.fullmatch(r"[IVXLCDM]+", col0) and col1):
            is_new_major = bool(
                top_q_match
                or (re.fullmatch(r"[IVXLCDM]+", col0) and (current is None or current["label_key"] != col0))
            )
            if is_new_major:
                if current is not None:
                    questions.append(current)
                seq += 1
                pts_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:điểm|đ)", col1, re.I)
                label = col1 or f"Câu {col0 or seq}"
                if not re.match(r"^(?:Câu|Bài)\s", label, re.I):
                    label = f"Câu {col0 or seq}"
                current = {
                    "question_no": seq,
                    "original_question_label": label[:60],
                    "points": float(pts_match.group(1).replace(",", ".")) if pts_match else 0.0,
                    "lines": [col1] if col1 and col1 != label else [],
                    "sub_points_sum": 0.0,
                    "label_key": col0,
                }
                continue

        if current is None:
            seq += 1
            current = {
                "question_no": seq,
                "original_question_label": f"Câu {seq}",
                "points": 0.0,
                "lines": [],
                "sub_points_sum": 0.0,
                "label_key": col0,
            }

        part_prefix = f"{col0}: " if col0 and col0 != current["label_key"] else ""
        body_cells = [t for t in texts[1:-1] if t] if len(texts) >= 3 else [t for t in texts[1:] if t]
        body = " | ".join(body_cells).strip()
        pts_val = None
        if re.fullmatch(r"\d+(?:[.,]\d+)?\s*(?:đ|điểm)?", pts_raw, re.I):
            pts_val = float(re.search(r"\d+(?:[.,]\d+)?", pts_raw).group(0).replace(",", "."))  # type: ignore[union-attr]
        elif body:
            # 2-col or unmerged score at tail
            pass
        if body:
            pts_suffix = f" ({pts_val:g}đ)" if pts_val is not None else ""
            current["lines"].append(f"- {part_prefix}{body}{pts_suffix}")
        elif col0 and col0 != current["label_key"] and pts_raw:
            current["lines"].append(f"- {col0}: {pts_raw}")
        if pts_val is not None:
            current["sub_points_sum"] += pts_val

    if current is not None:
        questions.append(current)

    if not questions:
        return None

    for q in questions:
        if q["points"] <= 0 and q["sub_points_sum"] > 0:
            q["points"] = round(q["sub_points_sum"], 2)
        elif q["points"] <= 0:
            q["points"] = 1.0
        q["content_md"] = "\n".join(q["lines"]).strip() or q["original_question_label"]
    return questions


def find_tesseract_executable() -> str | None:
    """Locate the Tesseract OCR executable if installed."""
    env_path = os.environ.get("TESSERACT_CMD")
    if env_path and Path(env_path).exists():
        return env_path
    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        "/usr/bin/tesseract",
    ):
        if Path(candidate).exists():
            return candidate
    return None


_OCR_Q_PREFIX_RE = re.compile(
    r"^(\s*)(?:C[aàáâăeêhiuo]{0,2}[uwmn]|C[aâ]u)\s+([0-9]{1,2}|[$S])\s*(?=(?:\(|[\.:\)\-–]|\s+[A-ZÀ-Ỹ0-9G]))",
    re.IGNORECASE,
)


def _normalize_ocr_line(line: str) -> str:
    m = _OCR_Q_PREFIX_RE.match(line)
    if not m:
        return line
    num_raw = m.group(2)
    num = "5" if num_raw in ("$", "S", "s") else num_raw
    rest = line[m.end() :]
    rest = re.sub(r"^\s*G(\d)", r"(\1", rest)
    rest = re.sub(r"\bdi[eêéìi]m\b", "điểm", rest, flags=re.IGNORECASE)
    return f"{m.group(1)}Câu {num} {rest}".strip()


def ocr_image_to_text_lines(
    raw_bytes: bytes,
    *,
    lang: str | None = None,
) -> tuple[float, float, list[tuple[str, tuple[float, float, float, float]]]]:
    """Run local Tesseract OCR on preprocessed photo bytes -> `(w_pt, h_pt, [(text, (x0, y0, x1, y1)), ...])`."""
    tess = find_tesseract_executable()
    if not tess or not raw_bytes:
        return 595.0, 842.0, []
    try:
        import pytesseract  # type: ignore[import-untyped]
    except ImportError:
        return 595.0, 842.0, []

    pytesseract.pytesseract.tesseract_cmd = tess
    cleaned_png = preprocess_photo_exam_bytes(raw_bytes)
    with Image.open(io.BytesIO(cleaned_png)) as orig_img:
        orig_w, orig_h = orig_img.size
        # Coordinates of the wrapped PDF match the original image dimensions at 150 DPI.
        pdf_scale = 72.0 / 150.0
        w_pt = round(orig_w * pdf_scale, 1)
        h_pt = round(orig_h * pdf_scale, 1)

        ocr_factor = 2.0 if orig_w < 1300 else 1.0
        ocr_img = (
            orig_img.resize((int(orig_w * ocr_factor), int(orig_h * ocr_factor)), Image.Resampling.LANCZOS)
            if ocr_factor > 1.0
            else orig_img
        )

        chosen_lang = lang
        if chosen_lang is None:
            try:
                avail = set(pytesseract.get_languages(config=""))
                chosen_lang = "vie+eng" if "vie" in avail else "eng"
            except Exception:
                chosen_lang = "eng"

        try:
            data = pytesseract.image_to_data(
                ocr_img,
                lang=chosen_lang,
                config="--psm 6",
                output_type=pytesseract.Output.DICT,
            )
        except Exception:
            return w_pt, h_pt, []

    # Group OCR words by (block_num, par_num, line_num) into lines with accurate PDF-point bboxes.
    grouped: dict[tuple[int, int, int], list[tuple[int, int, int, int, str]]] = {}
    n = len(data.get("text", []))
    for i in range(n):
        word = (data["text"][i] or "").strip()
        if not word:
            continue
        conf = int(float(data.get("conf", [0])[i] or 0))
        if conf < 15:
            continue
        key = (int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i]))
        left = int(data["left"][i])
        top = int(data["top"][i])
        width = int(data["width"][i])
        height = int(data["height"][i])
        grouped.setdefault(key, []).append((left, top, left + width, top + height, word))

    coord_scale = pdf_scale / ocr_factor
    lines_out: list[tuple[str, tuple[float, float, float, float]]] = []
    for words in grouped.values():
        x0 = min(w[0] for w in words) * coord_scale
        y0 = min(w[1] for w in words) * coord_scale
        x1 = max(w[2] for w in words) * coord_scale
        y1 = max(w[3] for w in words) * coord_scale
        text = _normalize_ocr_line(" ".join(w[4] for w in words).strip())
        if text:
            lines_out.append((text, (x0, y0, x1, y1)))
    lines_out.sort(key=lambda item: (item[1][1], item[1][0]))
    return w_pt, h_pt, lines_out


def extract_docx_plain_text(raw_bytes: bytes) -> str:
    """Fallback paragraph + OOXML + data-table text extractor when LibreOffice is unavailable."""
    if not raw_bytes:
        return ""
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            xml_bytes = zf.read("word/document.xml")
        root = etree.fromstring(xml_bytes)
    except Exception:
        return ""

    out_lines: list[str] = []
    body = root.find("w:body", _DOCX_TABLE_NS)
    if body is None:
        return ""

    for child in body:
        local = child.tag.rsplit("}", 1)[-1]
        if local == "p":
            txt = _clean_cell(_extract_docx_omml_fallback_text(child))
            if txt:
                out_lines.append(txt)
        elif local == "tbl":
            rows_text: list[list[str]] = []
            for tr in child.findall(".//w:tr", _DOCX_TABLE_NS):
                cells = [
                    _clean_cell(_extract_docx_omml_fallback_text(tc))
                    for tc in tr.findall("./w:tc", _DOCX_TABLE_NS)
                ]
                if any(cells):
                    rows_text.append(cells)
            # Skip pure 2-col exam banner header tables, emit real data tables as Markdown
            joined = " ".join(" ".join(r) for r in rows_text[:2]).upper()
            if len(rows_text) <= 3 and ("KỲ THI" in joined or "TRẠI HÈ" in joined or "CHÍNH THỨC" in joined):
                continue
            for r_idx, r in enumerate(rows_text):
                out_lines.append("| " + " | ".join(r) + " |")
                if r_idx == 0 and len(rows_text) > 1:
                    out_lines.append("| " + " | ".join("---" for _ in r) + " |")
    return "\n".join(out_lines)
