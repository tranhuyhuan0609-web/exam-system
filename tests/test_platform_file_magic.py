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


# --- regression + boundary tests --------------------------------------------------------------

from app.platform.file_magic import detect_magic_kind  # noqa: E402

_OLE_HEAD = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _zip_bytes(entries: list[tuple[str, bytes]], *, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=compression) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


def _docx(doc_xml: bytes = b"<w:document/>", extra: list[tuple[str, bytes]] | None = None) -> bytes:
    return _zip_bytes([("[Content_Types].xml", b"<Types/>"), ("word/document.xml", doc_xml), *(extra or [])])


def _inspect(name: str, data: bytes, **kw):
    return inspect_uploaded_bytes(filename=name, raw_bytes=data, **kw)


def test_markers_beyond_the_first_256kb_are_still_detected() -> None:
    filler = b"<w:p>" + b"x" * 300_000 + b"</w:p>"
    res = _inspect("late.docx", _docx(b"<w:document>" + filler + b"<w:object/></w:document>"))
    assert res.has_ooxml_mathtype_or_drawings is True


def test_clean_docx_has_no_routing_flag() -> None:
    assert _inspect("clean.docx", _docx()).has_ooxml_mathtype_or_drawings is False


def test_actual_decompressed_size_is_enforced_not_the_declared_size() -> None:
    data = _docx(b"<w:document>" + b"a" * 5_000 + b"</w:document>")
    zf_bytes = bytearray(data)
    # forge the declared uncompressed size of word/document.xml to a tiny value in both headers
    for sig, off in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
        pos = 0
        while (pos := zf_bytes.find(sig, pos)) != -1:
            name_len_off = 26 if sig == b"PK\x03\x04" else 28
            name_len = int.from_bytes(zf_bytes[pos + name_len_off : pos + name_len_off + 2], "little")
            name_start = pos + (30 if sig == b"PK\x03\x04" else 46)
            if bytes(zf_bytes[name_start : name_start + name_len]) == b"word/document.xml":
                zf_bytes[pos + off : pos + off + 4] = (10).to_bytes(4, "little")
            pos += 4
    with pytest.raises(FileSignatureError):
        _inspect("lying.docx", bytes(zf_bytes), max_doc_xml_bytes=1024)


@pytest.mark.parametrize(
    "xml",
    [
        b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "x">]><w:document>&a;</w:document>',
        b"<!doctype x><w:document/>",
        b'<w:document><!ENTITY lol "lol"></w:document>',
    ],
)
def test_dtd_and_entity_declarations_are_rejected_in_document_xml(xml: bytes) -> None:
    with pytest.raises(FileSignatureError, match="DTD"):
        _inspect("xxe.docx", _docx(xml))


def test_dtd_in_a_secondary_xml_part_is_rejected() -> None:
    bomb = b'<!DOCTYPE lolz [<!ENTITY lol "lol">]><x>&lol;</x>'
    with pytest.raises(FileSignatureError, match="DTD"):
        _inspect("styles.docx", _docx(extra=[("word/styles.xml", bomb)]))


def test_oversized_secondary_xml_part_is_rejected() -> None:
    from app.platform import file_magic

    big = b"<x>" + b"a" * (file_magic.MAX_AUX_XML_BYTES + 1) + b"</x>"
    with pytest.raises(FileSignatureError):
        _inspect("big.docx", _docx(extra=[("word/big.xml", big)]))


def test_entry_count_and_total_size_caps() -> None:
    from app.platform import file_magic

    many = [(f"word/media/{i}.png", b"x") for i in range(file_magic.MAX_ZIP_ENTRIES + 1)]
    with pytest.raises(FileSignatureError, match="too many"):
        _inspect("many.docx", _docx(extra=many))


def test_duplicate_entry_names_are_rejected() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", b"<Types/>")
        zf.writestr("word/document.xml", b"<w:document/>")
        with pytest.warns(UserWarning):
            zf.writestr("word/document.xml", b"<w:document><w:object/></w:document>")
    with pytest.raises(FileSignatureError, match="duplicate"):
        _inspect("dup.docx", buf.getvalue())


@pytest.mark.parametrize("name", ["../evil.xml", "/abs.xml", "a/../../b.xml", "C:/win.xml", "a\\..\\b.xml"])
def test_zip_slip_entry_names_are_rejected(name: str) -> None:
    with pytest.raises(FileSignatureError, match="unsafe"):
        _inspect("slip.docx", _docx(extra=[(name, b"x")]))


def test_missing_parts_and_garbage_zip_are_rejected_with_own_error() -> None:
    with pytest.raises(FileSignatureError):
        _inspect("nodoc.docx", _zip_bytes([("[Content_Types].xml", b"<Types/>")]))
    with pytest.raises(FileSignatureError):
        _inspect("trunc.docx", b"PK\x03\x04" + b"\x00" * 10)
    with pytest.raises(FileSignatureError):
        _inspect("empty.docx", b"PK\x05\x06" + b"\x00" * 18)
    good = _docx()
    with pytest.raises(FileSignatureError):
        _inspect("cut.docx", good[: len(good) // 2])


def test_corrupt_deflate_stream_raises_file_signature_error() -> None:
    data = bytearray(_docx(b"<w:document>" + b"abc" * 500 + b"</w:document>"))
    idx = data.find(b"word/document.xml") + len("word/document.xml") + 4
    for i in range(idx, idx + 20):
        data[i] ^= 0xFF
    with pytest.raises(FileSignatureError):
        _inspect("corrupt.docx", bytes(data))


def test_vba_project_anywhere_in_the_package_is_rejected_case_insensitively() -> None:
    with pytest.raises(FileSignatureError, match="vbaProject"):
        _inspect("m.docx", _docx(extra=[("word/VBAPROJECT.BIN", b"x")]))


def test_upload_size_cap() -> None:
    with pytest.raises(FileSignatureError, match="maximum"):
        _inspect("big.pdf", b"%PDF-" + b"0" * 100, max_upload_bytes=50)
    assert _inspect("ok.pdf", b"%PDF-" + b"0" * 45, max_upload_bytes=50).size_bytes == 50


def test_legacy_doc_must_be_a_word_compound_file() -> None:
    word = _OLE_HEAD + b"\x00" * 64 + "WordDocument".encode("utf-16-le") + b"\x00" * 64
    assert _inspect("old.doc", word).detected_kind == "DOC_LEGACY"
    xls = _OLE_HEAD + b"\x00" * 64 + "Workbook".encode("utf-16-le") + b"\x00" * 64
    with pytest.raises(FileSignatureError, match="not a Word"):
        _inspect("sheet.doc", xls)
    with pytest.raises(FileSignatureError, match="not a Word"):
        _inspect("blank.doc", _OLE_HEAD + b"\x00" * 512)
    mixed = word + "Workbook".encode("utf-16-le")
    with pytest.raises(FileSignatureError, match="another Office"):
        _inspect("mixed.doc", mixed)
    macro = word + "VBA\x00".encode("utf-16-le")
    with pytest.raises(FileSignatureError, match="macro"):
        _inspect("macro.doc", macro)


@pytest.mark.parametrize(
    "payload",
    [
        b"   \n\n<html><body>x</body></html>",
        b"\xef\xbb\xbf<!DOCTYPE html><html>",
        b"\t\t<SCRIPT>alert(1)</SCRIPT>",
        b"<svg xmlns='http://www.w3.org/2000/svg' onload='x()'/>",
        b"<?xml version='1.0'?><svg/>",
        b" " * 40 + b"<html>",
        b"#!/bin/sh\nrm -rf /",
        b"\x7fELF\x02\x01\x01",
        b"MZ\x90\x00",
    ],
)
def test_polyglot_html_script_svg_and_executables_are_rejected_for_any_extension(payload: bytes) -> None:
    for name in ("a.pdf", "a.png", "a.jpg", "a.webp", "a.doc"):
        with pytest.raises(FileSignatureError):
            _inspect(name, payload)


def test_extension_must_match_signature_in_every_direction() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 8
    jpg = b"\xff\xd8\xff\xe0" + b"0" * 8
    webp = b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 4
    pdf = b"%PDF-1.4"
    zip_docx = _docx()
    matrix = {"a.png": png, "a.jpg": jpg, "a.jpeg": jpg, "a.webp": webp, "a.pdf": pdf, "a.docx": zip_docx}
    for name, data in matrix.items():
        assert _inspect(name, data).size_bytes == len(data)
        for other_name, other_data in matrix.items():
            if other_data is not data and not (name in {"a.jpg", "a.jpeg"} and other_name in {"a.jpg", "a.jpeg"}):
                with pytest.raises(FileSignatureError):
                    _inspect(name, other_data)


def test_docx_extension_with_pdf_or_ole_signature_is_rejected() -> None:
    with pytest.raises(FileSignatureError):
        _inspect("a.docx", b"%PDF-1.4")
    with pytest.raises(FileSignatureError):
        _inspect("a.docx", _OLE_HEAD + b"0" * 32)


def test_riff_that_is_not_webp_and_truncated_signatures_are_rejected() -> None:
    with pytest.raises(FileSignatureError):
        _inspect("a.webp", b"RIFF\x00\x00\x00\x00WAVE")
    with pytest.raises(FileSignatureError):
        detect_magic_kind(b"RIFF")
    with pytest.raises(FileSignatureError):
        detect_magic_kind(b"%PDF")  # missing dash
    with pytest.raises(FileSignatureError):
        detect_magic_kind(b"\xff\xd8")


@pytest.mark.parametrize(
    "name",
    ["a.exe", "a.pdf.exe", "pdf", "", "   ", ".", "a.", "a.PDF.js", "a.svg", "a.pdf\x00.png", "a\n.pdf", None, 5],
)
def test_unsupported_or_hostile_filenames_are_rejected(name) -> None:
    with pytest.raises(FileSignatureError):
        _inspect(name, b"%PDF-1.4")


def test_extension_is_case_and_whitespace_insensitive() -> None:
    assert _inspect("  REPORT.PDF ", b"%PDF-1.4").detected_kind == "PDF"


def test_non_bytes_and_empty_payloads_are_rejected() -> None:
    for payload in (None, "text", 5, b""):
        with pytest.raises(FileSignatureError):
            _inspect("a.pdf", payload)  # type: ignore[arg-type]
    assert _inspect("a.pdf", bytearray(b"%PDF-1.4")).detected_kind == "PDF"


def test_error_messages_do_not_echo_filenames() -> None:
    with pytest.raises(FileSignatureError) as info:
        _inspect("SECRET-STUDENT-NAME.exe", b"%PDF-1.4")
    assert "SECRET-STUDENT-NAME" not in str(info.value)


def test_invalid_limit_arguments_fail_closed() -> None:
    for kw in (
        dict(max_upload_bytes=0),
        dict(max_upload_bytes=True),
        dict(max_doc_xml_bytes=0),
        dict(max_doc_xml_bytes=-1),
    ):
        with pytest.raises(FileSignatureError):
            _inspect("a.pdf", b"%PDF-1.4", **kw)
