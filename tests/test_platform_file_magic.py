"""Tests for A01/A05/B03 byte-signature sniffer & OOXML XML-bomb/macro guard (P69)."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.platform.file_magic import (
    FileSignatureError,
    inspect_uploaded_bytes,
)


def _make_docx_bytes(doc_xml: bytes, *, include_macro: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", b"<Types/>")
        zf.writestr("word/document.xml", doc_xml)
        if include_macro:
            zf.writestr("word/vbaProject.bin", b"MACRO")
    return buf.getvalue()


def test_accepts_real_pdf_png_and_detects_ooxml_mathtype_routing() -> None:
    pdf = inspect_uploaded_bytes(filename="de_toan_12.pdf", raw_bytes=b"%PDF-1.7\n%...")
    assert pdf.detected_kind == "PDF"
    assert pdf.mime_type == "application/pdf"

    png = inspect_uploaded_bytes(filename="hinh1.png", raw_bytes=b"\x89PNG\r\n\x1a\n\x00\x00")
    assert png.detected_kind == "PNG"

    docx_ole = _make_docx_bytes(b"<w:document><w:body><w:object/></w:body></w:document>")
    res = inspect_uploaded_bytes(filename="toan11_ole.docx", raw_bytes=docx_ole)
    assert res.detected_kind == "DOCX"
    assert res.has_ooxml_mathtype_or_drawings is True


def test_blocks_spoofed_exe_html_macros_and_xml_size_limit() -> None:
    with pytest.raises(FileSignatureError, match="executable"):
        inspect_uploaded_bytes(filename="evil.pdf", raw_bytes=b"MZ\x90\x00exe-payload")

    with pytest.raises(FileSignatureError, match="HTML"):
        inspect_uploaded_bytes(filename="fake.pdf", raw_bytes=b"<html><body>xss</body></html>")

    with pytest.raises(FileSignatureError, match="expected PDF"):
        inspect_uploaded_bytes(filename="img_as_pdf.pdf", raw_bytes=b"\x89PNG\r\n\x1a\n")

    macro_docx = _make_docx_bytes(b"<w:document/>", include_macro=True)
    with pytest.raises(FileSignatureError, match="vbaProject.bin"):
        inspect_uploaded_bytes(filename="macro.docx", raw_bytes=macro_docx)

    huge_xml = b"<w:document>" + (b"x" * 2048) + b"</w:document>"
    huge_docx = _make_docx_bytes(huge_xml)
    with pytest.raises(FileSignatureError, match="exceeds"):
        inspect_uploaded_bytes(filename="bomb.docx", raw_bytes=huge_docx, max_doc_xml_bytes=512)
