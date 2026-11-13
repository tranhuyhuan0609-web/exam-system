"""Static PostgreSQL DDL & RLS hardening auditor (EV-V11-03 check)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.platform.rls import RLS_EXEMPT_TABLES

RUNTIME_ROLE_NAMES: frozenset[str] = frozenset(
    {
        "ulp_api",
        "ulp_app_rw",
        "ulp_exam_session_rw",
        "ulp_worker_ingestion",
        "ulp_worker_grading",
        "ulp_system_scheduler",
        "ulp_analytics_ro",
    }
)

_CREATE_TABLE_RE = re.compile(
    r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?([a-zA-Z_][a-zA-Z0-9_]*)\b",
    re.IGNORECASE,
)
_ENABLE_RLS_RE = re.compile(
    r"\bALTER\s+TABLE\s+(?:ONLY\s+)?(?:public\.)?([a-zA-Z_][a-zA-Z0-9_]*)\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY\b",
    re.IGNORECASE,
)
_FORCE_RLS_RE = re.compile(
    r"\bALTER\s+TABLE\s+(?:ONLY\s+)?(?:public\.)?([a-zA-Z_][a-zA-Z0-9_]*)\s+FORCE\s+ROW\s+LEVEL\s+SECURITY\b",
    re.IGNORECASE,
)
_CREATE_POLICY_RE = re.compile(
    r"\bCREATE\s+POLICY\s+[a-zA-Z_][a-zA-Z0-9_]*\s+ON\s+(?:public\.)?([a-zA-Z_][a-zA-Z0-9_]*)\b",
    re.IGNORECASE,
)
_ARRAY_DECL_RE = re.compile(
    r"(?:strict_tenant_tables|hybrid_public_tables)\s+text\[\]\s*:=\s*ARRAY\[(.*?)\];",
    re.IGNORECASE | re.DOTALL,
)
_ROLE_DANGEROUS_ATTR_RE = re.compile(
    r"\b(?:CREATE|ALTER)\s+ROLE\s+([a-zA-Z_][a-zA-Z0-9_]*)\b([^;]*);",
    re.IGNORECASE,
)
_SECURITY_DEFINER_RE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+([a-zA-Z_][a-zA-Z0-9_.]*)\s*\([^)]*\).*?\$\$.*?\$\$\s*([^;]*);",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class DdlAuditReport:
    discovered_tables: tuple[str, ...]
    missing_rls_enable: tuple[str, ...]
    missing_rls_force: tuple[str, ...]
    missing_rls_policy: tuple[str, ...]
    privileged_role_violations: tuple[str, ...]
    unsafe_security_definer_fns: tuple[str, ...]
    passed: bool


def _strip_sql_line_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _extract_do_block_rls_tables(clean_sql: str) -> set[str]:
    """Recognize V11.3 DO-block table arrays only when paired with ENABLE/FORCE/CREATE POLICY in EXECUTE format."""
    has_loop_rls = bool(
        re.search(r"ENABLE\s+ROW\s+LEVEL\s+SECURITY", clean_sql, re.IGNORECASE)
        and re.search(r"FORCE\s+ROW\s+LEVEL\s+SECURITY", clean_sql, re.IGNORECASE)
        and re.search(r"CREATE\s+POLICY", clean_sql, re.IGNORECASE)
    )
    if not has_loop_rls:
        return set()

    tables: set[str] = set()
    for match in _ARRAY_DECL_RE.finditer(clean_sql):
        for quoted in re.findall(r"'([a-zA-Z_][a-zA-Z0-9_]*)'", match.group(1)):
            tables.add(quoted.lower())
    return tables


def audit_ddl_rls_hardening(
    sql_text: str,
    *,
    exempt_tables: frozenset[str] = RLS_EXEMPT_TABLES,
) -> DdlAuditReport:
    """Audit PostgreSQL DDL text for RLS coverage, NOBYPASSRLS enforcement, and safe SECURITY DEFINER functions."""
    clean = _strip_sql_line_comments(sql_text)

    discovered = sorted({m.group(1).lower() for m in _CREATE_TABLE_RE.finditer(clean)})
    loop_covered = _extract_do_block_rls_tables(clean)

    rls_enabled = {m.group(1).lower() for m in _ENABLE_RLS_RE.finditer(clean)} | loop_covered
    rls_forced = {m.group(1).lower() for m in _FORCE_RLS_RE.finditer(clean)} | loop_covered
    policy_tables = {m.group(1).lower() for m in _CREATE_POLICY_RE.finditer(clean)} | loop_covered

    exempt_lower = {t.lower() for t in exempt_tables}
    missing_enable: list[str] = []
    missing_force: list[str] = []
    missing_policy: list[str] = []

    for tbl in discovered:
        if tbl in exempt_lower:
            continue
        if tbl not in rls_enabled:
            missing_enable.append(tbl)
        if tbl not in rls_forced:
            missing_force.append(tbl)
        if tbl not in policy_tables:
            missing_policy.append(tbl)

    role_violations: list[str] = []
    for match in _ROLE_DANGEROUS_ATTR_RE.finditer(clean):
        role_name = match.group(1).lower()
        attrs = match.group(2).upper()
        if role_name in RUNTIME_ROLE_NAMES:
            if re.search(r"(?<!NO)BYPASSRLS\b", attrs):
                role_violations.append(f"{role_name}: granted BYPASSRLS")
            if re.search(r"(?<!NO)SUPERUSER\b", attrs):
                role_violations.append(f"{role_name}: granted SUPERUSER")

    unsafe_sec_def: list[str] = []
    for match in _SECURITY_DEFINER_RE.finditer(clean):
        fn_name = match.group(1)
        tail = match.group(2)
        if re.search(r"\bSECURITY\s+DEFINER\b", tail, re.IGNORECASE) and not re.search(
            r"\bSET\s+search_path\b", tail, re.IGNORECASE
        ):
            unsafe_sec_def.append(fn_name)

    passed = not (missing_enable or missing_force or missing_policy or role_violations or unsafe_sec_def)
    return DdlAuditReport(
        discovered_tables=tuple(discovered),
        missing_rls_enable=tuple(missing_enable),
        missing_rls_force=tuple(missing_force),
        missing_rls_policy=tuple(missing_policy),
        privileged_role_violations=tuple(role_violations),
        unsafe_security_definer_fns=tuple(unsafe_sec_def),
        passed=passed,
    )
