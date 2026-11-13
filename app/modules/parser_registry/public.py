"""Public API of the parser_registry module (A05)."""

from .internal.routing import (
    DEFAULT_OCR_CHAR_THRESHOLD,
    MAX_DOCUMENT_XML_BYTES,
    DocumentFormat,
    DocumentXmlTooLargeError,
    ExtractionRun,
    LibreOfficeJobSpec,
    ParserDefinition,
    ParserRoutingError,
    ParserStrategy,
    RoutingDecision,
    build_libreoffice_job_spec,
    guard_docx_xml_size,
    plan_page_reprocess_run,
    route_document_parser,
)

__all__ = [
    "DEFAULT_OCR_CHAR_THRESHOLD",
    "DocumentFormat",
    "DocumentXmlTooLargeError",
    "ExtractionRun",
    "LibreOfficeJobSpec",
    "MAX_DOCUMENT_XML_BYTES",
    "ParserDefinition",
    "ParserRoutingError",
    "ParserStrategy",
    "RoutingDecision",
    "build_libreoffice_job_spec",
    "guard_docx_xml_size",
    "plan_page_reprocess_run",
    "route_document_parser",
]
