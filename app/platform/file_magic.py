"""A01 / A05 / B03 real byte-signature sniffer & OOXML XML-bomb / macro guard."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

MAX_WORD_DOCUMENT_XML_BYTES = 25 * 1024 * 1024  # A05 limit: 25 MB

_EXT_TO_KIND: dict[str, str] = {
    ".pdf": "PDF",
    ".docx": "DOCX",
    ".doc": "DOC_LEGACY",
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".webp": "WEBP",
}

_KIND_TO_MIME: dict[str, str] = {
    "PDF": "application/pdf",
    "DOCX": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "DOC_LEGACY": "application/msword",
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}


class FileSignatureError(ValueError):
    """Raised when uploaded file bytes violate magic-signature, MIME, or OOXML safety."""


@dataclass(frozen=True)
class VerifiedFileInspection:
    detected_kind: str
    mime_type: str
    size_bytes: int
    has_ooxml_mathtype_or_drawings: bool = False


def detect_magic_kind(raw_bytes: bytes) -> str:
    """Detect canonical media/document type purely from leading magic bytes."""
    if not raw_bytes:
        raise FileSignatureError("empty file payload rejected")

    head = raw_bytes[:32]
    if head.startswith(b"MZ") or head.startswith(b"\x7fELF") or head.startswith(b"#!"):
        raise FileSignatureError("executable binary signature rejected")
    if head.lstrip().lower().startswith((b"<!doctype html", b"<html", b"<script")):
        raise FileSignatureError("HTML/script masquerading as document rejected")

    if raw_bytes.startswith(b"%PDF-"):
        return "PDF"
    if raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if raw_bytes.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if len(raw_bytes) >= 12 and raw_bytes[:4] == b"RIFF" and raw_bytes[8:12] == b"WEBP":
        return "WEBP"
    if raw_bytes.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "DOC_LEGACY"
    if raw_bytes.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "ZIP_OR_OOXML"

    raise FileSignatureError("unrecognized or unsupported binary file signature")


def _inspect_docx_archive(
    raw_bytes: bytes,
    *,
    max_doc_xml_bytes: int = MAX_WORD_DOCUMENT_XML_BYTES,
) -> bool:
    """Verify OOXML `.docx` safety and detect MathType OLE / Drawings for A05 routing."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
    except zipfile.BadZipFile as exc:
        raise FileSignatureError("corrupt OOXML archive") from exc

    with zf:
        names = set(zf.namelist())
        for member_name in names:
            if member_name.startswith(("/", "\\")) or ".." in member_name.replace("\\", "/").split("/"):
                raise FileSignatureError(f"unsafe ZIP entry path: {member_name!r}")
            if member_name.lower().endswith("vbaproject.bin"):
                raise FileSignatureError("macro-enabled vbaProject.bin rejected")

        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise FileSignatureError("ZIP archive is not a valid DOCX package")

        doc_info = zf.getinfo("word/document.xml")
        if doc_info.file_size > max_doc_xml_bytes:
            raise FileSignatureError(
                f"word/document.xml exceeds {max_doc_xml_bytes} bytes limit ({doc_info.file_size})"
            )

        xml_prefix = zf.open(doc_info).read(min(doc_info.file_size, 262_144))
        has_ole_or_shapes = any(
            marker in xml_prefix for marker in (b"<w:object", b"<v:shape", b"<w:drawing")
        )
        return has_ole_or_shapes


def inspect_uploaded_bytes(
    *,
    filename: str,
    raw_bytes: bytes,
    max_doc_xml_bytes: int = MAX_WORD_DOCUMENT_XML_BYTES,
) -> VerifiedFileInspection:
    """Verify filename extension matches actual magic signature and OOXML safety rules."""
    lower = filename.strip().lower()
    ext = "." + lower.rsplit(".", 1)[1] if "." in lower else ""
    expected_kind = _EXT_TO_KIND.get(ext)
    if expected_kind is None:
        raise FileSignatureError(f"unsupported upload file extension: {filename!r}")

    detected = detect_magic_kind(raw_bytes)
    if expected_kind == "DOCX":
        if detected != "ZIP_OR_OOXML":
            raise FileSignatureError(f"extension {ext} does not match detected signature {detected}")
        has_shapes = _inspect_docx_archive(raw_bytes, max_doc_xml_bytes=max_doc_xml_bytes)
        return VerifiedFileInspection(
            detected_kind="DOCX",
            mime_type=_KIND_TO_MIME["DOCX"],
            size_bytes=len(raw_bytes),
            has_ooxml_mathtype_or_drawings=has_shapes,
        )

    if detected != expected_kind:
        raise FileSignatureError(f"extension {ext} expected {expected_kind}, got {detected}")

    return VerifiedFileInspection(
        detected_kind=detected,
        mime_type=_KIND_TO_MIME[detected],
        size_bytes=len(raw_bytes),
        has_ooxml_mathtype_or_drawings=False,
    )
