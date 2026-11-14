"""A01 / A05 / B03 real byte-signature sniffer & OOXML XML-bomb / macro guard.

Guards: magic bytes versus extension, executable/HTML masquerading, OOXML macro projects, ZIP path
traversal, duplicate entries, entry-count and total-uncompressed-size caps, bounded decompression of
the XML parts (the declared size is never trusted), and DTD/entity declarations (XXE and
billion-laughs) in every XML part. Legacy ``.doc`` must really be a Word compound file, not an
Excel/PowerPoint one, and must not carry a VBA project.
"""

from __future__ import annotations

import io
import re
import zipfile
import zlib
from dataclasses import dataclass

MAX_WORD_DOCUMENT_XML_BYTES = 25 * 1024 * 1024  # A05 limit: 25 MB
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_ZIP_ENTRIES = 2_000
MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_AUX_XML_BYTES = 5 * 1024 * 1024
_READ_CHUNK = 64 * 1024

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

_OOXML_ROUTING_MARKERS = (b"<w:object", b"<v:shape", b"<w:drawing")
_XML_DTD_MARKERS = (b"<!doctype", b"<!entity")
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")


def _utf16(name: str) -> bytes:
    return name.encode("utf-16-le")


_OLE_WORD_STREAM = _utf16("WordDocument")
_OLE_OTHER_OFFICE_STREAMS = tuple(_utf16(n) for n in ("Workbook", "PowerPoint Document", "Book\x00"))
_OLE_VBA_STORAGE = _utf16("VBA\x00")


class FileSignatureError(ValueError):
    """Raised when uploaded file bytes violate magic-signature, MIME, or OOXML safety."""


@dataclass(frozen=True)
class VerifiedFileInspection:
    detected_kind: str
    mime_type: str
    size_bytes: int
    has_ooxml_mathtype_or_drawings: bool = False


def _require_bytes(raw_bytes: object) -> bytes:
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise FileSignatureError("file payload must be bytes")
    return bytes(raw_bytes)


def detect_magic_kind(raw_bytes: bytes) -> str:
    """Detect canonical media/document type purely from leading magic bytes."""
    raw_bytes = _require_bytes(raw_bytes)
    if not raw_bytes:
        raise FileSignatureError("empty file payload rejected")

    head = raw_bytes[:32]
    if head.startswith(b"MZ") or head.startswith(b"\x7fELF") or head.startswith(b"#!"):
        raise FileSignatureError("executable binary signature rejected")
    text_head = raw_bytes[:1024].lstrip(b"\xef\xbb\xbf \t\r\n\x0b\x0c").lower()
    if text_head.startswith((b"<!doctype html", b"<html", b"<script", b"<svg", b"<?xml")):
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


def _read_bounded(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    """Decompress at most ``limit`` bytes; a part that inflates beyond it is rejected.

    The size declared in the ZIP header is attacker controlled, so the real output is counted.
    """
    out = bytearray()
    with zf.open(info) as handle:
        while True:
            chunk = handle.read(_READ_CHUNK)
            if not chunk:
                break
            out += chunk
            if len(out) > limit:
                raise FileSignatureError("XML part exceeds the allowed decompressed size")
    return bytes(out)


def _inspect_docx_archive(
    raw_bytes: bytes,
    *,
    max_doc_xml_bytes: int = MAX_WORD_DOCUMENT_XML_BYTES,
) -> bool:
    """Verify OOXML `.docx` safety and detect MathType OLE / Drawings for A05 routing."""
    try:
        return _inspect_docx_archive_unchecked(raw_bytes, max_doc_xml_bytes=max_doc_xml_bytes)
    except FileSignatureError:
        raise
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        zlib.error,
        EOFError,
        OSError,
        RuntimeError,
        NotImplementedError,
        ValueError,
        OverflowError,
    ) as exc:
        raise FileSignatureError("corrupt or unsupported OOXML archive") from exc


def _inspect_docx_archive_unchecked(raw_bytes: bytes, *, max_doc_xml_bytes: int) -> bool:
    zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise FileSignatureError("ZIP archive has too many entries")
        names = [i.filename for i in infos]
        if len(set(names)) != len(names):
            raise FileSignatureError("ZIP archive contains duplicate entry names")
        if sum(i.file_size for i in infos) > MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES:
            raise FileSignatureError("ZIP archive total uncompressed size exceeds the limit")

        for info in infos:
            member_name = info.filename
            normalised = member_name.replace("\\", "/")
            if (
                normalised.startswith("/")
                or _DRIVE_PREFIX_RE.match(normalised)
                or ".." in normalised.split("/")
                or "\x00" in member_name
            ):
                raise FileSignatureError("unsafe ZIP entry path")
            if member_name.lower().endswith("vbaproject.bin"):
                raise FileSignatureError("macro-enabled vbaProject.bin rejected")
            if info.flag_bits & 0x1:
                raise FileSignatureError("encrypted ZIP entries rejected")

        name_set = set(names)
        if "[Content_Types].xml" not in name_set or "word/document.xml" not in name_set:
            raise FileSignatureError("ZIP archive is not a valid DOCX package")

        has_ole_or_shapes = False
        for info in infos:
            lower = info.filename.lower()
            is_main = info.filename == "word/document.xml"
            if not (is_main or lower.endswith((".xml", ".rels"))):
                continue
            limit = max_doc_xml_bytes if is_main else MAX_AUX_XML_BYTES
            if info.file_size > limit:
                raise FileSignatureError(
                    f"{'word/document.xml' if is_main else 'XML part'} exceeds {limit} bytes limit"
                )
            data = _read_bounded(zf, info, limit)
            lowered = data.lower()
            if any(marker in lowered for marker in _XML_DTD_MARKERS):
                raise FileSignatureError("DTD or entity declarations in OOXML rejected")
            if is_main:
                # the whole (bounded) part is scanned, not just a prefix
                has_ole_or_shapes = any(marker in data for marker in _OOXML_ROUTING_MARKERS)
        return has_ole_or_shapes


def _inspect_legacy_doc(raw_bytes: bytes) -> None:
    """A real .doc is an OLE2 compound file holding a ``WordDocument`` stream and no VBA storage."""
    if _OLE_WORD_STREAM not in raw_bytes:
        raise FileSignatureError("OLE2 file is not a Word document")
    if any(marker in raw_bytes for marker in _OLE_OTHER_OFFICE_STREAMS):
        raise FileSignatureError("OLE2 file mixes Word with another Office application")
    if _OLE_VBA_STORAGE in raw_bytes:
        raise FileSignatureError("macro-enabled legacy document rejected")


def inspect_uploaded_bytes(
    *,
    filename: str,
    raw_bytes: bytes,
    max_doc_xml_bytes: int = MAX_WORD_DOCUMENT_XML_BYTES,
    max_upload_bytes: int = MAX_UPLOAD_BYTES,
) -> VerifiedFileInspection:
    """Verify filename extension matches actual magic signature and OOXML safety rules."""
    if not isinstance(filename, str) or not filename.strip() or any(ord(c) < 32 or ord(c) == 127 for c in filename):
        raise FileSignatureError("invalid upload filename")
    raw_bytes = _require_bytes(raw_bytes)
    if isinstance(max_upload_bytes, bool) or not isinstance(max_upload_bytes, int) or max_upload_bytes <= 0:
        raise FileSignatureError("max_upload_bytes must be a positive integer")
    if isinstance(max_doc_xml_bytes, bool) or not isinstance(max_doc_xml_bytes, int) or max_doc_xml_bytes <= 0:
        raise FileSignatureError("max_doc_xml_bytes must be a positive integer")
    if len(raw_bytes) > max_upload_bytes:
        raise FileSignatureError("upload exceeds the maximum allowed size")

    lower = filename.strip().lower()
    ext = "." + lower.rsplit(".", 1)[1] if "." in lower else ""
    expected_kind = _EXT_TO_KIND.get(ext)
    if expected_kind is None:
        raise FileSignatureError("unsupported upload file extension")

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
    if detected == "DOC_LEGACY":
        _inspect_legacy_doc(raw_bytes)

    return VerifiedFileInspection(
        detected_kind=detected,
        mime_type=_KIND_TO_MIME[detected],
        size_bytes=len(raw_bytes),
        has_ooxml_mathtype_or_drawings=False,
    )
