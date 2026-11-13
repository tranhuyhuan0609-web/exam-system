"""Unit tests for A03 safe ZIP inspection and sandbox extraction (pure, no conftest)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.modules.drive_sync.public import (
    ArchiveSecurityViolationError,
    SafeArchivePolicy,
    extract_zip_safely,
    inspect_zip,
)


def _incompressible_bytes(length: int) -> bytes:
    # Deterministic pseudo-random bytes so deflate ratio stays near 1:1.
    state = 0x12345678
    out = bytearray(length)
    for idx in range(length):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        out[idx] = (state >> 16) & 0xFF
    return bytes(out)


def test_small_zip_bomb_rejected_by_compression_ratio(tmp_path: Path) -> None:
    bomb_zip = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("zeros.pdf", b"\x00" * (256 * 1024))

    with pytest.raises(ArchiveSecurityViolationError, match="ZIP_BOMB_RATIO_BLOCKED"):
        inspect_zip(bomb_zip)


@pytest.mark.parametrize(
    "bad_name",
    [
        "../evil.pdf",
        "folder/../../evil.pdf",
        "/etc/passwd",
        "subdir\\..\\..\\evil.pdf",
        "C:evil.pdf",
    ],
)
def test_path_traversal_and_absolute_paths_rejected(tmp_path: Path, bad_name: str) -> None:
    evil_zip = tmp_path / "traversal.zip"
    with zipfile.ZipFile(evil_zip, "w", compression=zipfile.ZIP_STORED) as zf:
        info = zipfile.ZipInfo("placeholder")
        info.filename = bad_name
        zf.writestr(info, b"payload")

    with pytest.raises(ArchiveSecurityViolationError, match="PATH_TRAVERSAL_BLOCKED"):
        inspect_zip(evil_zip)


def test_zip_with_501_files_rejected(tmp_path: Path) -> None:
    many_zip = tmp_path / "many.zip"
    with zipfile.ZipFile(many_zip, "w", compression=zipfile.ZIP_STORED) as zf:
        for i in range(501):
            zf.writestr(f"q_{i:03d}.pdf", b"x")

    with pytest.raises(ArchiveSecurityViolationError, match="ZIP_TOO_MANY_FILES"):
        inspect_zip(many_zip)


def test_zip_with_5_nested_directories_rejected(tmp_path: Path) -> None:
    deep_zip = tmp_path / "deep.zip"
    with zipfile.ZipFile(deep_zip, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("d1/d2/d3/d4/d5/exam.pdf", b"pdf-bytes")

    with pytest.raises(ArchiveSecurityViolationError, match="ARCHIVE_DEPTH_EXCEEDED"):
        inspect_zip(deep_zip)


def test_zip_with_symlink_rejected(tmp_path: Path) -> None:
    sym_zip = tmp_path / "symlink.zip"
    with zipfile.ZipFile(sym_zip, "w") as zf:
        info = zipfile.ZipInfo("link.pdf")
        info.create_system = 3
        info.external_attr = (0o120777) << 16
        zf.writestr(info, b"/etc/passwd")

    with pytest.raises(ArchiveSecurityViolationError, match="SYMLINK_IN_ARCHIVE_BLOCKED"):
        inspect_zip(sym_zip)


def test_zip_total_uncompressed_size_limit_rejected(tmp_path: Path) -> None:
    oversized_zip = tmp_path / "oversized.zip"
    with zipfile.ZipFile(oversized_zip, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("part1.pdf", _incompressible_bytes(700))
        zf.writestr("part2.pdf", _incompressible_bytes(700))

    strict_policy = SafeArchivePolicy(max_total_uncompressed_bytes=1024)
    with pytest.raises(ArchiveSecurityViolationError, match="ZIP_TOTAL_SIZE_EXCEEDED"):
        inspect_zip(oversized_zip, policy=strict_policy)


def test_valid_zip_accepted_and_extracted_safely(tmp_path: Path) -> None:
    good_zip = tmp_path / "exams.zip"
    content_pdf = _incompressible_bytes(512)
    with zipfile.ZipFile(good_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("toan/lop12/chuong1/de_01/de_thi.pdf", content_pdf)
        zf.writestr("toan/lop12/chuong1/de_01/notes.txt", b"ignored-ext")

    report = inspect_zip(good_zip)
    assert report.file_count == 2
    assert report.max_observed_depth == 4
    assert report.max_observed_ratio < 50.0

    out_dir = tmp_path / "extracted"
    extracted = extract_zip_safely(good_zip, out_dir)
    assert len(extracted) == 1
    assert extracted[0].name == "de_thi.pdf"
    assert extracted[0].read_bytes() == content_pdf
    assert not (out_dir / "toan/lop12/chuong1/de_01/notes.txt").exists()
