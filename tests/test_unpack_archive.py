"""`unpack_archive` of the AI import: ZIP only, through the A03 central-directory checks; everything else fails closed
(ADR-010: no GPL archive tool such as patool, no external binary)."""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest

from app.ai.extract_engine import UnsupportedArchiveError, unpack_archive


def _zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def test_a_zip_of_exam_files_is_unpacked_and_only_papers_are_kept(tmp_path):
    source = _zip(
        tmp_path / "de.zip",
        {
            "De 1.docx": b"docx",
            "Dap an 1.pdf": b"%PDF",
            "a/b/c/d/e/De 2.doc": b"doc",  # deeper than the Drive default, allowed for school folders
            "readme.txt": b"skipped",
            "logo.png": b"skipped",
        },
    )
    files = unpack_archive(source, tmp_path / "out")
    assert sorted(p.name for p in files) == ["Dap an 1.pdf", "De 1.docx", "De 2.doc"]
    assert all(p.is_file() and p.is_relative_to(tmp_path / "out") for p in files)


def test_rar_and_other_formats_fail_closed_with_a_message_that_says_to_send_a_zip(tmp_path):
    for name in ("de.rar", "de.7z", "de.tar", "de"):
        archive = tmp_path / name
        archive.write_bytes(b"Rar!\x1a\x07\x00")
        with pytest.raises(UnsupportedArchiveError, match=r"\.zip"):
            unpack_archive(archive, tmp_path / "out")
    assert not (tmp_path / "out").exists(), "nothing is created for a refused format"


def test_path_traversal_is_refused_before_anything_is_written(tmp_path):
    source = _zip(tmp_path / "evil.zip", {"ok.pdf": b"%PDF", "../escape.pdf": b"x"})
    with pytest.raises(UnsupportedArchiveError, match="PATH_TRAVERSAL_BLOCKED"):
        unpack_archive(source, tmp_path / "out")
    assert not (tmp_path / "escape.pdf").exists()
    assert not (tmp_path / "out" / "ok.pdf").exists(), "the safe entries of a refused archive are not written either"


def test_a_compression_bomb_is_refused(tmp_path):
    source = _zip(tmp_path / "bomb.zip", {"big.pdf": b"\0" * (20 * 1024 * 1024)})
    with pytest.raises(UnsupportedArchiveError, match="ZIP_BOMB_RATIO_BLOCKED"):
        unpack_archive(source, tmp_path / "out")


def test_a_symlink_entry_is_refused(tmp_path):
    source = tmp_path / "link.zip"
    with zipfile.ZipFile(source, "w") as archive:
        info = zipfile.ZipInfo("link.pdf")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")
    with pytest.raises(UnsupportedArchiveError, match="SYMLINK_IN_ARCHIVE_BLOCKED"):
        unpack_archive(source, tmp_path / "out")


def test_a_file_that_is_not_a_zip_is_refused(tmp_path):
    fake = tmp_path / "de.zip"
    fake.write_bytes(b"this is not a zip")
    with pytest.raises(UnsupportedArchiveError, match="không phải file .zip hợp lệ"):
        unpack_archive(fake, tmp_path / "out")
