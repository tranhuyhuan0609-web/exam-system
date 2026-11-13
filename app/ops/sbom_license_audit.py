"""Chapter 7.2 Gate 6 & GATE-09 OSS license, CycloneDX SBOM, and ADR-010 dependency auditor.

Fail-closed rules: a component with no declared licence blocks (unless waived), SPDX expressions are
evaluated (``A OR B`` is blocked only when every alternative is blocked), free-text licence names such
as "GNU Affero General Public License v3" are recognised, nested CycloneDX components are scanned and
pip requirements are checked by distribution name, ``#egg=`` fragment and VCS/URL basename.
"""

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

# ADR-010: pymupdf/fitz is AGPLv3 and banned from final release builds (pymupdf4llm is AGPLv3 too).
BANNED_PACKAGE_NAMES: frozenset[str] = frozenset({"pymupdf", "fitz", "pymupdf4llm"})

MAX_COMPONENT_DEPTH = 32
MAX_COMPONENTS = 200_000

_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)")
_EGG_RE = re.compile(r"[#&]egg=([A-Za-z0-9][A-Za-z0-9_.-]*)")
_URL_CREDENTIALS_RE = re.compile(r"(://)[^/@\s]+@")
_OK, _WARN, _BLOCK = 0, 1, 2
_TOKEN_RE = re.compile(r"\(|\)|[^\s()]+")


class SbomAuditError(ValueError):
    """Raised when the SBOM or requirements input is structurally invalid."""


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
    """PEP 503 normalisation: lower case, runs of ``-``, ``_`` and ``.`` become a single ``-``."""
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _classify_term(term: str) -> int:
    compact = re.sub(r"[^A-Z0-9]", "", term.upper())
    if not compact:
        return _OK
    if "AGPL" in compact or "AFFERO" in compact or "SSPL" in compact or "SERVERSIDEPUBLICLICENSE" in compact:
        return _BLOCK
    if "GPL" in compact or "GENERALPUBLICLICENSE" in compact:
        return _WARN  # GPL and LGPL: legal review before shipping
    return _OK


def _evaluate_expression(expression: str) -> int:
    """Evaluate an SPDX-like expression: AND/WITH raise the severity, OR picks the best alternative."""
    tokens = _TOKEN_RE.findall(expression)
    pos = 0

    def parse_or(depth: int) -> int:
        nonlocal pos
        result = parse_and(depth)
        while pos < len(tokens) and tokens[pos].upper() == "OR":
            pos += 1
            result = min(result, parse_and(depth))
        return result

    def parse_and(depth: int) -> int:
        nonlocal pos
        result = parse_atom(depth)
        while pos < len(tokens) and tokens[pos].upper() in {"AND", "WITH"}:
            keyword = tokens[pos].upper()
            pos += 1
            right = parse_atom(depth)
            if keyword == "AND":
                result = max(result, right)
        return result

    def parse_atom(depth: int) -> int:
        nonlocal pos
        if depth > 16 or pos >= len(tokens):
            return _BLOCK  # malformed or absurdly nested: fail closed
        token = tokens[pos]
        if token == "(":
            pos += 1
            value = parse_or(depth + 1)
            if pos < len(tokens) and tokens[pos] == ")":
                pos += 1
                return value
            return _BLOCK  # unclosed parenthesis: fail closed
        pos += 1
        words = [token]
        # free-text names ("GNU Affero General Public License v3") span several words
        while pos < len(tokens) and tokens[pos] not in {"(", ")"} and tokens[pos].upper() not in {"AND", "OR", "WITH"}:
            words.append(tokens[pos])
            pos += 1
        return _classify_term(" ".join(words))

    try:
        value = parse_or(0)
    except RecursionError:
        return _BLOCK
    return _BLOCK if pos < len(tokens) else value  # leftover tokens (stray parenthesis): fail closed


def _extract_component_license_ids(component: dict[str, Any]) -> list[str]:
    licenses = component.get("licenses")
    if not isinstance(licenses, list):
        return []
    ids: list[str] = []
    for entry in licenses:
        if not isinstance(entry, dict):
            continue
        expression = entry.get("expression")
        if isinstance(expression, str) and expression.strip():
            ids.append(expression.strip())
            continue
        lic = entry.get("license")
        if isinstance(lic, dict):
            for key in ("id", "name"):
                value = lic.get(key)
                if isinstance(value, str) and value.strip():
                    ids.append(value.strip())
                    break
    return ids


def _walk_components(components: object, depth: int = 0) -> list[dict[str, Any]]:
    if not isinstance(components, list):
        return []
    if depth > MAX_COMPONENT_DEPTH:
        raise SbomAuditError("SBOM components are nested too deeply")
    found: list[dict[str, Any]] = []
    for comp in components:
        if not isinstance(comp, dict):
            continue
        found.append(comp)
        if len(found) > MAX_COMPONENTS:
            raise SbomAuditError("SBOM has too many components")
        found.extend(_walk_components(comp.get("components"), depth + 1))
    return found


def audit_cyclonedx_sbom(
    sbom: dict[str, Any],
    *,
    commercial_exceptions: set[str] | frozenset[str] = frozenset(),
) -> SbomAuditReport:
    """Audit a CycloneDX JSON document (`sbom.cdx.json`) for AGPLv3/SSPL violations and missing licences."""
    if not isinstance(sbom, dict):
        raise SbomAuditError("SBOM must be a JSON object")
    allowed = {_normalize_pkg(p) for p in commercial_exceptions}
    components = _walk_components(sbom.get("components"))
    blocking: list[LicenseViolation] = []
    warnings: list[LicenseViolation] = []
    scanned = 0

    for comp in components:
        name = str(comp.get("name", "")).strip()
        norm_name = _normalize_pkg(name)
        version = str(comp.get("version", "")).strip()
        if not name:
            continue
        scanned += 1
        waived = norm_name in allowed

        if norm_name in BANNED_PACKAGE_NAMES and not waived:
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

        license_ids = _extract_component_license_ids(comp)
        if not license_ids:
            if not waived:
                blocking.append(
                    LicenseViolation(
                        package_name=name,
                        version=version,
                        license_id="UNDECLARED",
                        severity="BLOCKING",
                        reason="No license declared: cannot prove the component is shippable",
                    )
                )
            continue

        for lic_id in license_ids:
            level = _evaluate_expression(lic_id)
            if level == _BLOCK and not waived:
                blocking.append(
                    LicenseViolation(
                        package_name=name,
                        version=version,
                        license_id=lic_id,
                        severity="BLOCKING",
                        reason="Strong-copyleft AGPL/SSPL license requires commercial waiver",
                    )
                )
            elif level == _WARN and not waived:
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
        scanned_components=scanned,
        blocking_violations=tuple(blocking),
        warnings=tuple(warnings),
        passed=len(blocking) == 0,
    )


def _requirement_candidates(stripped: str) -> tuple[str | None, set[str]]:
    """Return (display name, normalised distribution-name candidates) for one requirement line."""
    candidates: set[str] = set()
    egg = _EGG_RE.search(stripped)
    if egg:
        candidates.add(_normalize_pkg(egg.group(1)))
    url_part = stripped.split(" @ ", 1)[1] if " @ " in stripped else stripped
    display: str | None = None
    if "://" in url_part or url_part.startswith(("git+", "hg+", "svn+", "bzr+")):
        path = re.sub(r"[#?].*$", "", url_part.split("://", 1)[-1]).rstrip("/")
        basename = path.rsplit("/", 1)[-1]
        basename = re.sub(r"\.(git|zip|tar\.gz|tgz|whl)$", "", basename, flags=re.IGNORECASE)
        basename = re.sub(r"@.*$", "", basename)
        if basename:
            candidates.add(_normalize_pkg(basename))
            display = basename
        # "https://github.com/pymupdf/PyMuPDF" also names the owner organisation
        segments = [s for s in path.split("/")[1:] if s]
        candidates.update(_normalize_pkg(s) for s in segments[:-1])
    match = _REQ_NAME_RE.match(stripped) if "://" not in stripped.split(" @ ", 1)[0] else None
    if match:
        candidates.add(_normalize_pkg(match.group(1)))
        display = match.group(1)
    return display or (egg.group(1) if egg else None), candidates


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
        if not isinstance(raw, str):
            raise SbomAuditError("requirements lines must be strings")
        # a comment starts with "#" at the line start or after whitespace; "#egg=" is a URL fragment
        stripped = re.split(r"(?:^|\s)#", raw, maxsplit=1)[0].strip()
        for prefix in ("--editable", "-e"):
            if stripped.startswith(prefix + " ") or stripped.startswith(prefix + "="):
                stripped = stripped[len(prefix) :].lstrip(" =")
                break
        if not stripped or stripped.startswith("-"):
            continue
        display, candidates = _requirement_candidates(stripped)
        if display is None and not candidates:
            continue
        scanned += 1
        hit = sorted(c for c in candidates if c in BANNED_PACKAGE_NAMES and c not in allowed)
        if hit:
            blocking.append(
                LicenseViolation(
                    package_name=display or hit[0],
                    version=_URL_CREDENTIALS_RE.sub(r"\1[REDACTED]@", stripped),
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
