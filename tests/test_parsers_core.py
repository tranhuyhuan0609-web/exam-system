"""Tests for A05 document parser registry & routing core rules."""

from __future__ import annotations

import pytest

from app.modules.parsers.public import (
    MAX_DOCUMENT_XML_BYTES,
    DocumentFormat,
    DocumentXmlTooLargeError,
    ExtractionRun,
    ParserDefinition,
    ParserRoutingError,
    ParserStrategy,
    build_libreoffice_job_spec,
    guard_docx_xml_size,
    plan_page_reprocess_run,
    route_document_parser,
)

CLEAN_XML = b"<w:p><w:r><w:t>Cau 1</w:t></w:r></w:p>"


def test_docx_xml_size_guard_enforces_25mb_limit() -> None:
    guard_docx_xml_size(MAX_DOCUMENT_XML_BYTES)
    with pytest.raises(DocumentXmlTooLargeError):
        guard_docx_xml_size(MAX_DOCUMENT_XML_BYTES + 1)


def test_size_guard_measures_real_bytes_and_a_docx_without_bytes_fails_closed() -> None:
    # Regression: document_xml_bytes=None used to skip the guard, and a declared size was trusted.
    oversized = b"x" * (MAX_DOCUMENT_XML_BYTES + 1)
    with pytest.raises(DocumentXmlTooLargeError):
        route_document_parser(doc_format=DocumentFormat.DOCX, document_xml=oversized)
    with pytest.raises(ParserRoutingError):
        route_document_parser(doc_format=DocumentFormat.DOCX, document_xml=None)
    with pytest.raises(ParserRoutingError):
        route_document_parser(doc_format=DocumentFormat.DOCX, document_xml=b"")
    with pytest.raises(TypeError):  # the argument is required, there is no silent default
        route_document_parser(doc_format=DocumentFormat.DOCX)  # type: ignore[call-arg]
    with pytest.raises(ParserRoutingError):
        route_document_parser(doc_format=DocumentFormat.DOCX, document_xml="<w:p/>")  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, None, "10"])
def test_size_guard_rejects_non_positive_or_non_integer_sizes(bad: object) -> None:
    with pytest.raises(ParserRoutingError):
        guard_docx_xml_size(bad)  # type: ignore[arg-type]


def test_routes_legacy_doc_and_mathtype_docx_to_libreoffice_headless() -> None:
    legacy = route_document_parser(doc_format=DocumentFormat.DOC, document_xml=None)
    assert legacy.strategy == ParserStrategy.LIBREOFFICE_HEADLESS

    mathtype = route_document_parser(
        doc_format=DocumentFormat.DOCX,
        document_xml=b'<w:p><w:OBJECT><o:OLEObject Type="Embed"/></w:OBJECT></w:p>',
    )
    assert mathtype.strategy == ParserStrategy.LIBREOFFICE_HEADLESS
    assert "<w:object" in mathtype.detected_markers

    clean_docx = route_document_parser(doc_format=DocumentFormat.DOCX, document_xml=CLEAN_XML)
    assert clean_docx.strategy == ParserStrategy.NATIVE_DOCX_XML


def test_pdf_routing_uses_text_density_and_rejects_non_finite_values() -> None:
    scanned = route_document_parser(doc_format=DocumentFormat.PDF, document_xml=None, avg_text_chars_per_page=3.0)
    assert scanned.strategy == ParserStrategy.OCR_PIPELINE
    digital = route_document_parser(doc_format=DocumentFormat.PDF, document_xml=None, avg_text_chars_per_page=900)
    assert digital.strategy == ParserStrategy.PDF_ENGINE
    for bad in (float("nan"), float("inf"), -1.0, None, True):
        with pytest.raises(ParserRoutingError):
            route_document_parser(
                doc_format=DocumentFormat.PDF,
                document_xml=None,
                avg_text_chars_per_page=bad,  # type: ignore[arg-type]
            )


def test_unknown_document_format_is_rejected() -> None:
    with pytest.raises(ParserRoutingError):
        route_document_parser(doc_format="RTF", document_xml=None)  # type: ignore[arg-type]


def test_concurrent_libreoffice_jobs_get_distinct_profiles_and_process_group_isolation() -> None:
    specs = [
        build_libreoffice_job_spec(
            job_id=f"job-{i}",
            input_path=f"/work/doc-{i}.docx",
            output_dir="/work/out",
        )
        for i in range(20)
    ]
    profiles = {s.profile_uri for s in specs}
    assert len(profiles) == 20
    assert all(s.start_new_session is True for s in specs)
    assert all(any(arg == f"-env:UserInstallation={s.profile_uri}" for arg in s.argv) for s in specs)

    with pytest.raises(ParserRoutingError, match="job_id"):
        build_libreoffice_job_spec(job_id="../escape", input_path="a.doc", output_dir="/tmp")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"input_path": "-env:UserInstallation=file:///etc", "output_dir": "/work/out"},
        {"input_path": "/work/a.docx", "output_dir": "--outdir"},
        {"input_path": " -x", "output_dir": "/work/out"},
        {"input_path": "", "output_dir": "/work/out"},
        {"input_path": "/work/a.docx", "output_dir": "   "},
        {"input_path": "/work/a\x00.docx", "output_dir": "/work/out"},
        {"input_path": "/work/a\n--bad.docx", "output_dir": "/work/out"},
    ],
)
def test_libreoffice_paths_cannot_inject_command_line_options(kwargs: dict[str, str]) -> None:
    with pytest.raises(ParserRoutingError):
        build_libreoffice_job_spec(job_id="job-1", **kwargs)


@pytest.mark.parametrize("job_id", ["", "a" * 65, "job\n", "job 1", "../x", "a/b"])
def test_libreoffice_job_id_must_match_strict_pattern(job_id: str) -> None:
    # "job\n" matters: ``$`` in a non-fullmatch regex would accept a trailing newline.
    with pytest.raises(ParserRoutingError):
        build_libreoffice_job_spec(job_id=job_id, input_path="/work/a.docx", output_dir="/work/out")


@pytest.mark.parametrize("timeout", [0, -5, 601])
def test_libreoffice_timeout_is_bounded(timeout: int) -> None:
    with pytest.raises(ParserRoutingError):
        build_libreoffice_job_spec(job_id="j", input_path="/a.docx", output_dir="/o", timeout_seconds=timeout)


def _prior() -> ExtractionRun:
    return ExtractionRun(
        run_id="run-1",
        document_id="doc-99",
        parser_code="pdf-engine",
        parser_version="1.0.0",
        strategy=ParserStrategy.PDF_ENGINE,
        target_pages=(1, 2, 3, 4),
    )


_PARSER_DEF = ParserDefinition(
    parser_code="pdf-engine",
    parser_version="1.1.0",
    strategy=ParserStrategy.OCR_PIPELINE,
    supports_formats=(DocumentFormat.PDF,),
)


def test_page_reprocess_run_preserves_parent_and_updates_parser_version() -> None:
    reprocess = plan_page_reprocess_run(
        new_run_id="run-2",
        prior_run=_prior(),
        pages=(4, 2, 4),
        parser_def=_PARSER_DEF,
    )
    assert reprocess.parent_run_id == "run-1"
    assert reprocess.parser_version == "1.1.0"
    assert reprocess.target_pages == (2, 4)


@pytest.mark.parametrize("pages", [(), (0,), (-3, 2), (1, True), (1.5,), (10**9,)])
def test_page_reprocess_rejects_empty_non_positive_non_integer_or_huge_pages(pages: tuple) -> None:
    with pytest.raises(ParserRoutingError):
        plan_page_reprocess_run(new_run_id="run-2", prior_run=_prior(), pages=pages, parser_def=_PARSER_DEF)


def test_page_reprocess_rejects_unsafe_run_id() -> None:
    with pytest.raises(ParserRoutingError):
        plan_page_reprocess_run(new_run_id="../run", prior_run=_prior(), pages=(1,), parser_def=_PARSER_DEF)
