"""Chapter 7.2 Gate 6 & GATE-09 OSS license, CycloneDX SBOM, and ADR-010 dependency auditor."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

BLOCKED_LICENSE_IDS: frozenset[str] = frozenset(
    {
        "AGPL-3.0",
        "AGPL-3.0-ONLY",
        "AGPL-3.0-OR-LATER",
        "SSPL-1.0",
    }
)

RESTRICTED_LICENSE_WARNING_IDS: frozenset[str] = frozenset(
    {
        "GPL-2.0",
        "GPL-3.0",
        "GPL-3.0-ONLY",
        "GPL-3.0-OR-LATER",
    }
)

# ADR-010: pymupdf/fitz is AGPLv3 and banned from final release builds.
BANNED_PACKAGE_NAMES: frozenset[str] = frozenset({"pymupdf", "fitz"})

_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)")


@dataclass(frozen=True)
class LicenseViolation:
    package_name: str
    version: str
    license_id: str
    severity: str  # "BLOCKING" | "WARNING"
    reason: str


@dataclass(frozen=True)
class SbomAuditReport:
    scanned_components: int
    blocking_violations: tuple[LicenseViolation, ...]
    warnings: tuple[LicenseViolation, ...]
    passed: bool


def _normalize_pkg(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _extract_component_license_ids(component: dict[str, Any]) -> list[str]:
    licenses = component.get("licenses") or []
    ids: list[str] = []
    for entry in licenses:
        if not isinstance(entry, dict):
            continue
        if "expression" in entry and entry["expression"]:
            ids.append(str(entry["expression"]).strip())
            continue
        lic = entry.get("license")
        if isinstance(lic, dict):
            if lic.get("id"):
                ids.append(str(lic["id"]).strip())
            elif lic.get("name"):
                ids.append(str(lic["name"]).strip())
    return ids


def audit_cyclonedx_sbom(
    sbom: dict[str, Any],
    *,
    commercial_exceptions: set[str] | frozenset[str] = frozenset(),
) -> SbomAuditReport:
    """Audit a CycloneDX JSON document (`sbom.cdx.json`) for AGPLv3/SSPL violations."""
    allowed = {_normalize_pkg(p) for p in commercial_exceptions}
    components = sbom.get("components") or []
    blocking: list[LicenseViolation] = []
    warnings: list[LicenseViolation] = []

    for comp in components:
        if not isinstance(comp, dict):
            continue
        name = str(comp.get("name", "")).strip()
        norm_name = _normalize_pkg(name)
        version = str(comp.get("version", "")).strip()
        if not name:
            continue

        if norm_name in BANNED_PACKAGE_NAMES and norm_name not in allowed:
            blocking.append(
                LicenseViolation(
                    package_name=name,
                    version=version,
                    license_id="AGPL-3.0",
                    severity="BLOCKING",
                    reason="ADR-010 banned AGPLv3 package present in release SBOM",
                )
            )
            continue

        for lic_id in _extract_component_license_ids(comp):
            upper_lic = lic_id.upper()
            if any(bad in upper_lic for bad in BLOCKED_LICENSE_IDS) and norm_name not in allowed:
                blocking.append(
                    LicenseViolation(
                        package_name=name,
                        version=version,
                        license_id=lic_id,
                        severity="BLOCKING",
                        reason="Strong-copyleft AGPL/SSPL license requires commercial waiver",
                    )
                )
            elif any(warn in upper_lic for warn in RESTRICTED_LICENSE_WARNING_IDS) and norm_name not in allowed:
                warnings.append(
                    LicenseViolation(
                        package_name=name,
                        version=version,
                        license_id=lic_id,
                        severity="WARNING",
                        reason="GPL license requires legal review before shipping",
                    )
                )

    return SbomAuditReport(
        scanned_components=len(components),
        blocking_violations=tuple(blocking),
        warnings=tuple(warnings),
        passed=len(blocking) == 0,
    )


def audit_requirements_lines(
    lines: list[str] | tuple[str, ...],
    *,
    commercial_exceptions: set[str] | frozenset[str] = frozenset(),
) -> SbomAuditReport:
    """Scan pip requirements lines for ADR-010 banned AGPLv3 dependencies."""
    allowed = {_normalize_pkg(p) for p in commercial_exceptions}
    scanned = 0
    blocking: list[LicenseViolation] = []

    for raw in lines:
        stripped = raw.split("#", 1)[0].strip()
        if not stripped or stripped.startswith("-"):
            continue
        match = _REQ_NAME_RE.match(stripped)
        if not match:
            continue
        pkg = match.group(1)
        norm = _normalize_pkg(pkg)
        scanned += 1
        if norm in BANNED_PACKAGE_NAMES and norm not in allowed:
            blocking.append(
                LicenseViolation(
                    package_name=pkg,
                    version=stripped,
                    license_id="AGPL-3.0",
                    severity="BLOCKING",
                    reason="ADR-010 requires removing pymupdf/fitz before release gate",
                )
            )

    return SbomAuditReport(
        scanned_components=scanned,
        blocking_violations=tuple(blocking),
        warnings=(),
        passed=len(blocking) == 0,
    )
