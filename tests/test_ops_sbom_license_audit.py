"""Tests for GATE-09 CycloneDX SBOM & ADR-010 license auditor (P65)."""

from __future__ import annotations

from pathlib import Path

from app.ops.sbom_license_audit import (
    audit_cyclonedx_sbom,
    audit_requirements_lines,
)

ROOT = Path(__file__).resolve().parent.parent


def test_sbom_blocks_agplv3_unless_commercial_exception_and_allows_mit_apache() -> None:
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "components": [
            {"name": "fastapi", "version": "0.115.0", "licenses": [{"license": {"id": "MIT"}}]},
            {"name": "pypdfium2", "version": "5.0.0", "licenses": [{"license": {"id": "Apache-2.0"}}]},
            {"name": "PyMuPDF", "version": "1.24.0", "licenses": [{"license": {"id": "AGPL-3.0-only"}}]},
        ],
    }

    blocked = audit_cyclonedx_sbom(sbom)
    assert blocked.passed is False
    assert blocked.scanned_components == 3
    assert blocked.blocking_violations[0].package_name == "PyMuPDF"

    waived = audit_cyclonedx_sbom(sbom, commercial_exceptions={"pymupdf"})
    assert waived.passed is True
    assert waived.blocking_violations == ()


def test_requirements_scanner_detects_remaining_pymupdf_until_w05_finishes() -> None:
    req_lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    current = audit_requirements_lines(req_lines)
    # pymupdf>=1.24 is still listed in requirements.txt until ADR-010 PR C removes it.
    assert current.passed is False
    assert {v.package_name.lower() for v in current.blocking_violations} == {"pymupdf"}

    cleaned = [line for line in req_lines if not line.strip().lower().startswith("pymupdf")]
    clean_report = audit_requirements_lines(cleaned)
    assert clean_report.passed is True
