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


# --- regression + boundary tests --------------------------------------------------------------

import pytest  # noqa: E402

from app.ops.sbom_license_audit import SbomAuditError  # noqa: E402


def _sbom(*components) -> dict:
    return {"bomFormat": "CycloneDX", "components": list(components)}


def _comp(name: str, license_value=None, **extra) -> dict:
    comp = {"name": name, "version": "1.0", **extra}
    if license_value is not None:
        comp["licenses"] = license_value
    return comp


def _lic(text: str) -> list:
    return [{"license": {"name": text}}]


@pytest.mark.parametrize(
    "text",
    [
        "GNU Affero General Public License v3",
        "GNU Affero General Public License v3 or later (AGPLv3+)",
        "AGPLv3",
        "AGPL-3.0",
        "agpl-3.0-or-later",
        "GNU AFFERO GENERAL PUBLIC LICENSE",
        "SSPL-1.0",
        "Server Side Public License",
        "AGPL 3.0",
    ],
)
def test_agpl_and_sspl_spellings_are_blocked(text: str) -> None:
    report = audit_cyclonedx_sbom(_sbom(_comp("libx", _lic(text))))
    assert report.passed is False and report.blocking_violations[0].license_id == text
    spdx = audit_cyclonedx_sbom(_sbom(_comp("libx", [{"expression": text}])))
    assert spdx.passed is False


@pytest.mark.parametrize("text", ["MIT", "Apache-2.0", "BSD-3-Clause", "ISC", "MPL-2.0", "Python-2.0", "Unlicense"])
def test_permissive_licenses_pass(text: str) -> None:
    report = audit_cyclonedx_sbom(_sbom(_comp("libx", [{"license": {"id": text}}])))
    assert report.passed is True and report.warnings == ()


@pytest.mark.parametrize("text", ["GPL-2.0", "GPL-3.0-only", "GNU General Public License v3", "LGPL-2.1", "GNU Lesser General Public License"])
def test_gpl_family_warns_without_blocking(text: str) -> None:
    report = audit_cyclonedx_sbom(_sbom(_comp("libx", _lic(text))))
    assert report.passed is True and len(report.warnings) == 1


def test_component_without_a_license_is_blocked_unless_waived() -> None:
    for comp in (_comp("nolic"), _comp("empty", []), _comp("junk", "MIT"), _comp("blank", [{"license": {}}]), _comp("none", [None])):
        assert audit_cyclonedx_sbom(_sbom(comp)).blocking_violations[0].license_id == "UNDECLARED"
    assert audit_cyclonedx_sbom(_sbom(_comp("nolic")), commercial_exceptions={"NoLic"}).passed is True


def test_spdx_or_expression_is_blocked_only_when_every_alternative_is() -> None:
    assert audit_cyclonedx_sbom(_sbom(_comp("a", [{"expression": "MIT OR AGPL-3.0-only"}]))).passed is True
    assert audit_cyclonedx_sbom(_sbom(_comp("b", [{"expression": "AGPL-3.0-only OR SSPL-1.0"}]))).passed is False
    assert audit_cyclonedx_sbom(_sbom(_comp("c", [{"expression": "MIT AND AGPL-3.0-only"}]))).passed is False
    assert audit_cyclonedx_sbom(_sbom(_comp("d", [{"expression": "(MIT OR Apache-2.0) AND (GPL-3.0-only OR AGPL-3.0-only)"}]))).passed is True
    assert audit_cyclonedx_sbom(_sbom(_comp("e", [{"expression": "GPL-2.0-only WITH Classpath-exception-2.0"}]))).warnings
    assert audit_cyclonedx_sbom(_sbom(_comp("f", [{"expression": "MIT OR (AGPL-3.0"}]))).passed is False  # malformed -> closed


def test_nested_components_are_scanned() -> None:
    nested = _comp("outer", [{"license": {"id": "MIT"}}], components=[
        _comp("inner", [{"license": {"id": "MIT"}}], components=[_comp("deep", [{"license": {"id": "AGPL-3.0-only"}}])])
    ])
    report = audit_cyclonedx_sbom(_sbom(nested))
    assert report.scanned_components == 3
    assert [v.package_name for v in report.blocking_violations] == ["deep"]


def test_banned_packages_are_matched_after_pep503_normalisation() -> None:
    for name in ("PyMuPDF", "py_mu_pdf".replace("py_mu_pdf", "PyMuPDF"), "pymupdf", "FITZ", "PyMuPDF4LLM"):
        assert audit_cyclonedx_sbom(_sbom(_comp(name, [{"license": {"id": "MIT"}}]))).passed is False
    assert audit_cyclonedx_sbom(_sbom(_comp("PyMuPDF", [{"license": {"id": "MIT"}}])), commercial_exceptions={"pymupdf"}).passed is True


def test_malformed_sbom_shapes_do_not_crash_and_non_dict_is_rejected() -> None:
    assert audit_cyclonedx_sbom({}).scanned_components == 0
    assert audit_cyclonedx_sbom({"components": "nope"}).passed is True
    report = audit_cyclonedx_sbom({"components": [None, 5, "x", {"version": "1"}, {"name": ""}]})
    assert report.scanned_components == 0 and report.passed is True
    for bad in (None, [], "sbom"):
        with pytest.raises(SbomAuditError):
            audit_cyclonedx_sbom(bad)  # type: ignore[arg-type]


def test_excessive_nesting_is_rejected() -> None:
    node: dict = _comp("leaf", [{"license": {"id": "MIT"}}])
    for _ in range(40):
        node = _comp("n", [{"license": {"id": "MIT"}}], components=[node])
    with pytest.raises(SbomAuditError):
        audit_cyclonedx_sbom(_sbom(node))


@pytest.mark.parametrize(
    "line",
    [
        "pymupdf>=1.24",
        "PyMuPDF==1.24.0",
        "PyMuPDF[extra]>=1 ; python_version >= '3.11'",
        "Py_Mu.PDF" if False else "pymupdf",
        "fitz",
        "pymupdf4llm>=0.0.5",
        "git+https://github.com/pymupdf/PyMuPDF.git#egg=pymupdf",
        "git+https://github.com/someone/fork.git@main#egg=PyMuPDF",
        "-e git+https://github.com/pymupdf/PyMuPDF.git#egg=pymupdf",
        "--editable=git+https://example.com/x.git#egg=fitz",
        "pymupdf @ https://files.example.com/pymupdf-1.24-py3-none-any.whl",
        "https://github.com/pymupdf/PyMuPDF/archive/refs/tags/1.24.0.zip",
        "git+https://github.com/pymupdf/PyMuPDF",
    ],
)
def test_requirements_scanner_catches_every_way_to_name_pymupdf(line: str) -> None:
    report = audit_requirements_lines(["fastapi>=0.1", line])
    assert report.passed is False, line
    assert report.scanned_components == 2


@pytest.mark.parametrize(
    "line",
    ["fastapi>=0.115", "# pymupdf is banned", "   ", "-r other.txt", "--index-url https://pypi.org/simple", "pypdfium2>=4", "requests  # not pymupdf"],
)
def test_requirements_scanner_ignores_clean_lines_and_comments(line: str) -> None:
    assert audit_requirements_lines([line]).passed is True


def test_requirements_commercial_exception_and_scanned_counts() -> None:
    lines = ["a==1", "# c", "", "b>=2", "-r x", "pymupdf"]
    assert audit_requirements_lines(lines).scanned_components == 3
    assert audit_requirements_lines(lines, commercial_exceptions={"PyMuPDF"}).passed is True


def test_url_credentials_never_appear_in_violations() -> None:
    line = "git+https://user:s3cr3t-token@github.com/pymupdf/PyMuPDF.git#egg=pymupdf"
    report = audit_requirements_lines([line])
    assert report.passed is False
    assert "s3cr3t-token" not in repr(report)


def test_non_string_requirement_lines_are_rejected() -> None:
    with pytest.raises(SbomAuditError):
        audit_requirements_lines([None])  # type: ignore[list-item]
