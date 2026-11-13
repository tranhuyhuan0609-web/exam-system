"""A05 Document Import & Parser Registry core rules.

XML size guard, MathType/OLE LibreOffice routing, isolated profiles and versioned reprocess runs.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum

MAX_DOCUMENT_XML_BYTES = 25 * 1024 * 1024
DEFAULT_OCR_CHAR_THRESHOLD = 40
MAX_REPROCESS_PAGE = 100_000
_SAFE_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SAFE_RUN_ID_RE = _SAFE_JOB_ID_RE
# Paths handed to the soffice command line: no option injection, no control characters.
_FORBIDDEN_PATH_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_LIBREOFFICE_XML_MARKERS: tuple[str, ...] = ("<w:object", "<v:shape", "<w:drawing")


class DocumentFormat(StrEnum):
    DOC = "DOC"
    DOCX = "DOCX"
    PDF = "PDF"


class ParserStrategy(StrEnum):
    NATIVE_DOCX_XML = "NATIVE_DOCX_XML"
    LIBREOFFICE_HEADLESS = "LIBREOFFICE_HEADLESS"
    PDF_ENGINE = "PDF_ENGINE"
    OCR_PIPELINE = "OCR_PIPELINE"


class DocumentXmlTooLargeError(ValueError):
    """Raised when word/document.xml exceeds the 25 MB safety guard before XML parsing."""


class ParserRoutingError(ValueError):
    """Raised on unsupported format or unsafe process/profile arguments."""


@dataclass(frozen=True)
class ParserDefinition:
    parser_code: str
    parser_version: str
    strategy: ParserStrategy
    supports_formats: tuple[DocumentFormat, ...]


@dataclass(frozen=True)
class RoutingDecision:
    strategy: ParserStrategy
    reasons: tuple[str, ...]
    detected_markers: tuple[str, ...]


@dataclass(frozen=True)
class LibreOfficeJobSpec:
    job_id: str
    profile_uri: str
    argv: tuple[str, ...]
    start_new_session: bool
    timeout_seconds: int


@dataclass(frozen=True)
class ExtractionRun:
    run_id: str
    document_id: str
    parser_code: str
    parser_version: str
    strategy: ParserStrategy
    target_pages: tuple[int, ...]
    parent_run_id: str | None = None


def guard_docx_xml_size(document_xml_bytes: int) -> None:
    """Reject empty or >25 MB word/document.xml before reading into XML parser."""
    if isinstance(document_xml_bytes, bool) or not isinstance(document_xml_bytes, int):
        raise ParserRoutingError("word/document.xml size must be an integer")
    if document_xml_bytes <= 0:
        raise ParserRoutingError("word/document.xml size must be positive")
    if document_xml_bytes > MAX_DOCUMENT_XML_BYTES:
        raise DocumentXmlTooLargeError(
            f"word/document.xml ({document_xml_bytes} bytes) exceeds 25 MB safety cap ({MAX_DOCUMENT_XML_BYTES} bytes)"
        )


def _safe_path_arg(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ParserRoutingError(f"{name} must be a non-empty string")
    if value.startswith("-") or value.strip().startswith("-"):
        raise ParserRoutingError(f"{name} must not start with '-' (command-line option injection)")
    if _FORBIDDEN_PATH_CHARS.search(value):
        raise ParserRoutingError(f"{name} contains control characters")
    return value


def route_document_parser(
    *,
    doc_format: DocumentFormat,
    document_xml: bytes | bytearray | None,
    avg_text_chars_per_page: float | None = None,
) -> RoutingDecision:
    """Deterministically select parser strategy per A05 security & MathType/drawing rules.

    ``document_xml`` is required (no default): the real ``word/document.xml`` bytes for DOCX, ``None`` for other
    formats. The size guard measures the real bytes and the markers are searched in them; a caller-declared size
    or snippet is never trusted. A DOCX without bytes fails closed.
    """
    if doc_format == DocumentFormat.DOC:
        return RoutingDecision(
            strategy=ParserStrategy.LIBREOFFICE_HEADLESS,
            reasons=("legacy .doc binary requires LibreOffice headless conversion",),
            detected_markers=(),
        )

    if doc_format == DocumentFormat.DOCX:
        if not isinstance(document_xml, (bytes, bytearray)):
            raise ParserRoutingError("DOCX routing requires the real word/document.xml bytes")
        guard_docx_xml_size(len(document_xml))
        lowered = bytes(document_xml).lower()
        found = tuple(marker for marker in _LIBREOFFICE_XML_MARKERS if marker.encode("ascii") in lowered)
        if found:
            return RoutingDecision(
                strategy=ParserStrategy.LIBREOFFICE_HEADLESS,
                reasons=("embedded MathType OLE/drawing markup requires LibreOffice conversion",),
                detected_markers=found,
            )
        return RoutingDecision(
            strategy=ParserStrategy.NATIVE_DOCX_XML,
            reasons=("clean OOXML without MathType OLE or drawing tags",),
            detected_markers=(),
        )

    if doc_format == DocumentFormat.PDF:
        if (
            isinstance(avg_text_chars_per_page, bool)
            or not isinstance(avg_text_chars_per_page, (int, float))
            or not math.isfinite(avg_text_chars_per_page)
            or avg_text_chars_per_page < 0
        ):
            raise ParserRoutingError("PDF routing requires a finite, non-negative avg_text_chars_per_page")
        if avg_text_chars_per_page < DEFAULT_OCR_CHAR_THRESHOLD:
            return RoutingDecision(
                strategy=ParserStrategy.OCR_PIPELINE,
                reasons=("low selectable text density indicates scanned PDF",),
                detected_markers=(),
            )
        return RoutingDecision(
            strategy=ParserStrategy.PDF_ENGINE,
            reasons=("selectable text PDF routed to native PDF engine",),
            detected_markers=(),
        )

    raise ParserRoutingError(f"unsupported document format: {doc_format}")


def build_libreoffice_job_spec(
    *,
    job_id: str,
    input_path: str,
    output_dir: str,
    profile_root: str = "/tmp/lo-profiles",
    timeout_seconds: int = 60,
) -> LibreOfficeJobSpec:
    """Build isolated per-job LibreOffice UserInstallation argv + process-group kill contract."""
    if not isinstance(job_id, str) or not _SAFE_JOB_ID_RE.fullmatch(job_id):
        raise ParserRoutingError(f"unsafe or invalid LibreOffice job_id: {job_id!r}")
    if timeout_seconds <= 0 or timeout_seconds > 600:
        raise ParserRoutingError(f"timeout_seconds out of bounds (1..600): {timeout_seconds}")
    _safe_path_arg("input_path", input_path)
    _safe_path_arg("output_dir", output_dir)
    _safe_path_arg("profile_root", profile_root)
    clean_root = profile_root.rstrip("/")
    profile_uri = f"file://{clean_root}/{job_id}"
    argv = (
        "soffice",
        "--headless",
        "--norestore",
        "--invisible",
        f"-env:UserInstallation={profile_uri}",
        "--convert-to",
        "pdf",
        "--outdir",
        output_dir,
        input_path,
    )
    return LibreOfficeJobSpec(
        job_id=job_id,
        profile_uri=profile_uri,
        argv=argv,
        start_new_session=True,
        timeout_seconds=timeout_seconds,
    )


def plan_page_reprocess_run(
    *,
    new_run_id: str,
    prior_run: ExtractionRun,
    pages: tuple[int, ...],
    parser_def: ParserDefinition,
) -> ExtractionRun:
    """Create a new versioned extraction run scoped strictly to the requested subset of pages."""
    if not isinstance(new_run_id, str) or not _SAFE_RUN_ID_RE.fullmatch(new_run_id):
        raise ParserRoutingError(f"unsafe or invalid new_run_id: {new_run_id!r}")
    if not pages:
        raise ParserRoutingError("reprocess requires at least one page number")
    if any(isinstance(p, bool) or not isinstance(p, int) for p in pages):
        raise ParserRoutingError("page numbers must be integers")
    unique_sorted = tuple(sorted(set(pages)))
    if unique_sorted[0] <= 0:
        raise ParserRoutingError("page numbers must be >= 1")
    if unique_sorted[-1] > MAX_REPROCESS_PAGE:
        raise ParserRoutingError(f"page numbers must be <= {MAX_REPROCESS_PAGE}")
    return ExtractionRun(
        run_id=new_run_id,
        document_id=prior_run.document_id,
        parser_code=parser_def.parser_code,
        parser_version=parser_def.parser_version,
        strategy=parser_def.strategy,
        target_pages=unique_sorted,
        parent_run_id=prior_run.run_id,
    )
