"""Tests for A05 document parser registry & routing core rules."""

from __future__ import annotations

import pytest

from app.modules.parser_registry.public import (
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


def test_docx_xml_size_guard_enforces_25mb_limit() -> None:
    guard_docx_xml_size(MAX_DOCUMENT_XML_BYTES)
    with pytest.raises(DocumentXmlTooLargeError):
        route_document_parser(
            doc_format=DocumentFormat.DOCX,
            document_xml_bytes=MAX_DOCUMENT_XML_BYTES + 1,
        )


def test_routes_legacy_doc_and_mathtype_docx_to_libreoffice_headless() -> None:
    legacy = route_document_parser(doc_format=DocumentFormat.DOC)
    assert legacy.strategy == ParserStrategy.LIBREOFFICE_HEADLESS

    mathtype = route_document_parser(
        doc_format=DocumentFormat.DOCX,
        document_xml_bytes=120_000,
        xml_snippet='<w:p><w:object><o:OLEObject Type="Embed"/></w:object></w:p>',
    )
    assert mathtype.strategy == ParserStrategy.LIBREOFFICE_HEADLESS
    assert "<w:object" in mathtype.detected_markers

    clean_docx = route_document_parser(
        doc_format=DocumentFormat.DOCX,
        document_xml_bytes=45_000,
        xml_snippet="<w:p><w:r><w:t>Cau 1</w:t></w:r></w:p>",
    )
    assert clean_docx.strategy == ParserStrategy.NATIVE_DOCX_XML


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


def test_page_reprocess_run_preserves_parent_and_updates_parser_version() -> None:
    prior = ExtractionRun(
        run_id="run-1",
        document_id="doc-99",
        parser_code="pdf-engine",
        parser_version="1.0.0",
        strategy=ParserStrategy.PDF_ENGINE,
        target_pages=(1, 2, 3, 4),
    )
    reprocess = plan_page_reprocess_run(
        new_run_id="run-2",
        prior_run=prior,
        pages=(4, 2, 4),
        parser_def=ParserDefinition(
            parser_code="pdf-engine",
            parser_version="1.1.0",
            strategy=ParserStrategy.OCR_PIPELINE,
            supports_formats=(DocumentFormat.PDF,),
        ),
    )
    assert reprocess.parent_run_id == "run-1"
    assert reprocess.parser_version == "1.1.0"
    assert reprocess.target_pages == (2, 4)
