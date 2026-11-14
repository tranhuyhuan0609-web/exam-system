"""Static PostgreSQL DDL & RLS hardening auditor (EV-V11-03 check).

The auditor reads DDL text and reports, per table, whether RLS is enabled, forced and backed by an
InitPlan tenant policy. Coverage is derived from what each statement or loop really does:

* explicit ``ALTER TABLE ... ENABLE/FORCE ROW LEVEL SECURITY`` and ``CREATE POLICY`` statements;
* ``DO`` blocks that loop ``FOREACH t IN ARRAY <name>``: only the tables of the array that the loop
  iterates are covered, and only by the statements that loop body actually executes;
* partitions (RLS of the parent does not apply to direct partition access): covered by their own
  statements, or by ``secure_partition('<name>')`` / a ``pg_inherits`` sweep calling
  ``secure_partition`` provided that function enables, forces and adds an InitPlan policy;
* tables without a ``tenant_id`` column are platform-global catalogs (no tenant policy is possible).
  They are listed in ``tenantless_tables`` so a reviewer can confirm each one.

Role checks cover the seven ``platform_manifest.yaml`` DB roles (all NOBYPASSRLS, none SUPERUSER).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.platform.rls import RLS_EXEMPT_TABLES

MANIFEST_DB_ROLE_NAMES: frozenset[str] = frozenset(
    {
        "ulp_migrator",
        "ulp_app_rw",
        "ulp_exam_session_rw",
        "ulp_worker_ingestion",
        "ulp_worker_grading",
        "ulp_system_scheduler",
        "ulp_analytics_ro",
    }
)
RUNTIME_ROLE_NAMES = MANIFEST_DB_ROLE_NAMES  # backwards-compatible alias

ANSWER_TABLES: tuple[str, ...] = ("question_version_answers", "assessment_answer_keys")
ANSWER_RESTRICTED_ROLES: tuple[str, ...] = ("ulp_exam_session_rw", "ulp_worker_ingestion")
GRADING_POLICIES: dict[str, str] = {
    "question_version_answers": "grading_read_question_answers",
    "assessment_answer_keys": "grading_read_answer_keys",
}

_IDENT = r'"?([a-zA-Z_][a-zA-Z0-9_]*)"?'
_TABLE_REF = r"(?:ONLY\s+)?(?:public\.)?" + _IDENT

_CREATE_TABLE_RE = re.compile(
    r"\bCREATE\s+(?:UNLOGGED\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?" + _IDENT + r"\b",
    re.IGNORECASE,
)
_PARTITION_OF_RE = re.compile(r"\s*PARTITION\s+OF\s+(?:public\.)?" + _IDENT, re.IGNORECASE)
_ENABLE_RLS_RE = re.compile(r"\bALTER\s+TABLE\s+" + _TABLE_REF + r"\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY\b", re.IGNORECASE)
_FORCE_RLS_RE = re.compile(r"\bALTER\s+TABLE\s+" + _TABLE_REF + r"\s+FORCE\s+ROW\s+LEVEL\s+SECURITY\b", re.IGNORECASE)
_CREATE_POLICY_RE = re.compile(
    r"\bCREATE\s+POLICY\s+" + _IDENT + r"\s+ON\s+(?:public\.)?" + _IDENT + r"\b([^;]*);",
    re.IGNORECASE | re.DOTALL,
)
_DOLLAR_BODY_RE = re.compile(r"\$([A-Za-z_]*)\$(.*?)\$\1\$", re.DOTALL)
_DO_PREFIX_RE = re.compile(r"\bDO\s+$", re.IGNORECASE)
_ARRAY_DECL_RE = re.compile(r"\b(\w+)\s+text\[\]\s*:=\s*ARRAY\[(.*?)\]\s*;", re.IGNORECASE | re.DOTALL)
_FOREACH_RE = re.compile(r"\bFOREACH\s+\w+\s+IN\s+ARRAY\s+(\w+)\s+LOOP(.*?)\bEND\s+LOOP\s*;", re.IGNORECASE | re.DOTALL)
_FORMAT_ENABLE_RE = re.compile(r"ALTER\s+TABLE\s+%I\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY", re.IGNORECASE)
_FORMAT_FORCE_RE = re.compile(r"ALTER\s+TABLE\s+%I\s+FORCE\s+ROW\s+LEVEL\s+SECURITY", re.IGNORECASE)
_FORMAT_POLICY_RE = re.compile(r"CREATE\s+POLICY\s+(\w+)\s+ON\s+%I(.*?);\s*'", re.IGNORECASE | re.DOTALL)
_ROLE_STMT_RE = re.compile(r"\b(CREATE|ALTER)\s+(?:ROLE|USER)\s+" + _IDENT + r"\b([^;]*);", re.IGNORECASE | re.DOTALL)
_FUNCTION_RE = re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+([a-zA-Z_][a-zA-Z0-9_.]*)\s*\(", re.IGNORECASE)
_GRANT_REVOKE_RE = re.compile(
    r"\b(GRANT|REVOKE)\s+(.*?)\s+ON\s+(.*?)\s+(TO|FROM)\s+([^;]*);", re.IGNORECASE | re.DOTALL
)
_INITPLAN_RE = re.compile(r"\(\s*SELECT\s+(?:current_app_tenant_id|app_system_job)\s*\(\s*\)\s*\)", re.IGNORECASE)
_TENANT_INITPLAN_RE = re.compile(r"\(\s*SELECT\s+current_app_tenant_id\s*\(\s*\)\s*\)", re.IGNORECASE)
_BARE_TENANT_FN_RE = re.compile(r"(?<!SELECT )(?<!SELECT\s)\bcurrent_app_tenant_id\s*\(\s*\)", re.IGNORECASE)
_USING_TRUE_RE = re.compile(r"\b(?:USING|WITH\s+CHECK)\s*\(\s*true\s*\)", re.IGNORECASE)

# Extra conditions the spec demands of specific hybrid_read_policy bodies (H-01).
_HYBRID_REQUIRED_TOKENS: dict[str, tuple[str, ...]] = {
    "content_items": ("publication_status='PUBLISHED'",),
    "questions": ("publication_status='PUBLISHED'",),
    "assessments": ("publication_status='PUBLISHED'",),
    "content_versions": ("status='PUBLISHED'", "is_immutableISTRUE"),
    "knowledge_edges": ("source_node_id", "target_node_id", "is_activeISTRUE"),
}


@dataclass(frozen=True)
class DdlAuditReport:
    discovered_tables: tuple[str, ...]
    missing_rls_enable: tuple[str, ...]
    missing_rls_force: tuple[str, ...]
    missing_rls_policy: tuple[str, ...]
    privileged_role_violations: tuple[str, ...]
    unsafe_security_definer_fns: tuple[str, ...]
    passed: bool
    tenantless_tables: tuple[str, ...] = ()
    policy_violations: tuple[str, ...] = ()
    grant_violations: tuple[str, ...] = ()
    roles_without_explicit_nobypassrls: tuple[str, ...] = ()  # advisory, never fails the audit


def _strip_sql_comments(sql: str) -> str:
    """Remove ``--`` and ``/* */`` comments while leaving single-quoted strings untouched."""
    out: list[str] = []
    i, n = 0, len(sql)
    in_string = False
    while i < n:
        ch = sql[i]
        if in_string:
            out.append(ch)
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    out.append("'")
                    i += 1
                else:
                    in_string = False
        elif ch == "'":
            in_string = True
            out.append(ch)
        elif sql.startswith("--", i):
            while i < n and sql[i] != "\n":
                i += 1
            continue
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _blank_bodies(sql: str) -> tuple[str, list[tuple[int, str, bool]]]:
    """Replace dollar-quoted bodies by spaces (positions preserved).

    Returns the blanked text and ``(offset, body, is_do_block)`` for every body.
    """
    bodies: list[tuple[int, str, bool]] = []
    chars = list(sql)
    for m in _DOLLAR_BODY_RE.finditer(sql):
        start, end = m.span(2)
        is_do = _DO_PREFIX_RE.search(sql[: m.start()]) is not None
        bodies.append((start, m.group(2), is_do))
        for k in range(start, end):
            if chars[k] != chr(10):
                chars[k] = " "
    return "".join(chars), bodies


def _normalise(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _split_top_level(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _table_body(clean: str, name_end: int) -> str | None:
    """Text between the parentheses opened right after a CREATE TABLE name, or None."""
    rest = clean[name_end:]
    m = re.match(r"\s*\(", rest)
    if m is None:
        return None
    depth = 0
    start = name_end + m.end()
    for idx in range(name_end + m.end() - 1, len(clean)):
        if clean[idx] == "(":
            depth += 1
        elif clean[idx] == ")":
            depth -= 1
            if depth == 0:
                return clean[start:idx]
    return None


def _has_tenant_column(body: str) -> bool:
    for item in _split_top_level(body):
        tokens = item.strip().split()
        if tokens and tokens[0].strip('"').lower() == "tenant_id":
            return True
    return False


def _function_flags(function_body: str) -> tuple[bool, bool, bool]:
    enable = bool(_FORMAT_ENABLE_RE.search(function_body))
    force = bool(_FORMAT_FORCE_RE.search(function_body))
    policy = any(_INITPLAN_RE.search(m.group(2)) for m in _FORMAT_POLICY_RE.finditer(function_body))
    return enable, force, policy


def _names(text: str) -> list[str]:
    return [t.strip().strip('"').split(".")[-1].lower() for t in text.split(",") if t.strip()]


def audit_ddl_rls_hardening(
    sql_text: str,
    *,
    exempt_tables: frozenset[str] = RLS_EXEMPT_TABLES,
    require_all_roles: bool = False,
) -> DdlAuditReport:
    """Audit PostgreSQL DDL text for RLS coverage, NOBYPASSRLS enforcement and safe SECURITY DEFINER."""
    clean = _strip_sql_comments(sql_text)
    outer, bodies = _blank_bodies(clean)
    do_bodies = [body for _, body, is_do in bodies if is_do]
    all_bodies = [body for _, body, _ in bodies]

    # --- tables -----------------------------------------------------------------------------
    discovered: set[str] = set()
    partitions: dict[str, str] = {}
    tenantless: set[str] = set()
    for m in _CREATE_TABLE_RE.finditer(outer):
        name = m.group(1).lower()
        discovered.add(name)
        part = _PARTITION_OF_RE.match(outer, m.end())
        if part is not None:
            partitions[name] = part.group(1).lower()
            continue
        body = _table_body(outer, m.end())
        if body is not None and not _has_tenant_column(body):
            tenantless.add(name)

    # --- explicit statements outside and inside DO blocks -------------------------------------
    statement_texts = [outer, *do_bodies]
    enabled: set[str] = set()
    forced: set[str] = set()
    policied: set[str] = set()
    policies: list[tuple[str, str, str]] = []  # (policy name, table, body)
    for text in statement_texts:
        enabled |= {m.group(1).lower() for m in _ENABLE_RLS_RE.finditer(text)}
        forced |= {m.group(1).lower() for m in _FORCE_RLS_RE.finditer(text)}
        for m in _CREATE_POLICY_RE.finditer(text):
            policies.append((m.group(1).lower(), m.group(2).lower(), m.group(3)))
            if _TENANT_INITPLAN_RE.search(m.group(3)):
                policied.add(m.group(2).lower())

    # --- DO-block loops: only the array a loop iterates, only what its body executes -----------
    policy_violations: list[str] = []
    for body in do_bodies:
        arrays = {
            m.group(1).lower(): [t.lower() for t in re.findall(r"'([a-zA-Z_][a-zA-Z0-9_]*)'", m.group(2))]
            for m in _ARRAY_DECL_RE.finditer(body)
        }
        for loop in _FOREACH_RE.finditer(body):
            tables = arrays.get(loop.group(1).lower(), [])
            loop_body = loop.group(2)
            if _FORMAT_ENABLE_RE.search(loop_body):
                enabled |= set(tables)
            if _FORMAT_FORCE_RE.search(loop_body):
                forced |= set(tables)
            for pm in _FORMAT_POLICY_RE.finditer(loop_body):
                policied |= set(tables)
                if not _INITPLAN_RE.search(pm.group(2)) or _BARE_TENANT_FN_RE.search(pm.group(2)):
                    policy_violations.append(
                        f"{pm.group(1).lower()} (loop over {loop.group(1)}): no InitPlan tenant expression"
                    )
                if _USING_TRUE_RE.search(pm.group(2)):
                    policy_violations.append(
                        f"{pm.group(1).lower()} (loop over {loop.group(1)}): permissive USING (true)"
                    )

    # --- partitions ----------------------------------------------------------------------------
    secure_fn_ok = False
    secure_fn_present = False
    for fm in _FUNCTION_RE.finditer(clean):
        if fm.group(1).lower().split(".")[-1] == "secure_partition":
            body_match = _DOLLAR_BODY_RE.search(clean, fm.end())
            if body_match is not None:
                secure_fn_present = True
                secure_fn_ok = all(_function_flags(body_match.group(2)))
    literal_secured = set()
    sweep = False
    for text in (outer, *all_bodies):
        literal_secured |= {
            m.group(1).lower()
            for m in re.finditer(r"secure_partition\s*\(\s*'([a-zA-Z_][a-zA-Z0-9_]*)'\s*\)", text, re.IGNORECASE)
        }
    for body in do_bodies:
        if re.search(r"pg_inherits", body, re.IGNORECASE) and re.search(r"secure_partition\s*\(", body, re.IGNORECASE):
            sweep = True
    for part in partitions:
        if secure_fn_present and secure_fn_ok and (sweep or part in literal_secured):
            enabled.add(part)
            forced.add(part)
            policied.add(part)

    exempt_lower = {t.lower() for t in exempt_tables}
    missing_enable: list[str] = []
    missing_force: list[str] = []
    missing_policy: list[str] = []
    for tbl in sorted(discovered):
        if tbl in exempt_lower or (tbl in tenantless and tbl not in partitions):
            continue
        if tbl not in enabled:
            missing_enable.append(tbl)
        if tbl not in forced:
            missing_force.append(tbl)
        if tbl not in policied:
            missing_policy.append(tbl)
    relies_on_secure_partition = sweep or bool(literal_secured & set(partitions))
    if (secure_fn_present and not secure_fn_ok) or (relies_on_secure_partition and not secure_fn_present):
        policy_violations.append("secure_partition(): missing or does not enable, force and add an InitPlan policy")

    # --- explicit policy bodies ---------------------------------------------------------------
    hybrid_policy_bodies: dict[str, str] = {}
    for name, table, body in policies:
        if not _INITPLAN_RE.search(body):
            policy_violations.append(f"{name} on {table}: no (SELECT current_app_tenant_id()) InitPlan expression")
        if _BARE_TENANT_FN_RE.search(body):
            policy_violations.append(f"{name} on {table}: bare current_app_tenant_id() is evaluated per row")
        if _USING_TRUE_RE.search(body):
            policy_violations.append(f"{name} on {table}: permissive USING (true)")
        if name == "hybrid_read_policy":
            hybrid_policy_bodies[table] = _normalise(body)

    for body in do_bodies:
        hybrid_arrays = [
            [t.lower() for t in re.findall(r"'([a-zA-Z_][a-zA-Z0-9_]*)'", m.group(2))]
            for m in _ARRAY_DECL_RE.finditer(body)
            if m.group(1).lower().startswith("hybrid")
        ]
        for tables in hybrid_arrays:
            for tbl in tables:
                if tbl not in hybrid_policy_bodies:
                    policy_violations.append(f"hybrid_read_policy missing on {tbl}")
    for tbl, tokens in _HYBRID_REQUIRED_TOKENS.items():
        body_norm = hybrid_policy_bodies.get(tbl)
        if body_norm is None:
            continue
        for token in tokens:
            if token.lower() not in body_norm.lower():
                policy_violations.append(f"hybrid_read_policy on {tbl}: missing condition {token}")

    # --- roles ---------------------------------------------------------------------------------
    role_violations: list[str] = []
    created_roles: set[str] = set()
    explicit_nobypass_missing: list[str] = []
    for text in (outer, *all_bodies):
        for m in _ROLE_STMT_RE.finditer(text):
            verb, role_name, attrs = m.group(1).upper(), m.group(2).lower(), m.group(3).upper()
            if role_name not in MANIFEST_DB_ROLE_NAMES and not role_name.startswith("ulp_"):
                continue
            if verb == "CREATE":
                created_roles.add(role_name)
                if "NOBYPASSRLS" not in attrs and role_name not in explicit_nobypass_missing:
                    explicit_nobypass_missing.append(role_name)
            if re.search(r"(?<!NO)BYPASSRLS\b", attrs):
                role_violations.append(f"{role_name}: granted BYPASSRLS")
            if re.search(r"(?<!NO)SUPERUSER\b", attrs):
                role_violations.append(f"{role_name}: granted SUPERUSER")
    if require_all_roles:
        for role in sorted(MANIFEST_DB_ROLE_NAMES - created_roles):
            role_violations.append(f"{role}: manifest role is never created")

    # --- SECURITY DEFINER -----------------------------------------------------------------------
    unsafe_sec_def: list[str] = []
    for fm in _FUNCTION_RE.finditer(clean):
        body_match = _DOLLAR_BODY_RE.search(clean, fm.end())
        if body_match is None:
            continue
        tail_end = clean.find(";", body_match.end())
        tail = clean[body_match.end() : tail_end if tail_end != -1 else len(clean)]
        header = clean[fm.end() : body_match.start()]
        attrs = f"{header} {tail}"
        if re.search(r"\bSECURITY\s+DEFINER\b", attrs, re.IGNORECASE) and not re.search(
            r"\bSET\s+search_path\b", attrs, re.IGNORECASE
        ):
            unsafe_sec_def.append(fm.group(1))

    # --- answer-key tables (H-03) ----------------------------------------------------------------
    grant_violations: list[str] = []
    defined_answer_tables = [t for t in ANSWER_TABLES if t in discovered]
    if defined_answer_tables:
        events: list[tuple[int, str, list[str], list[str], str]] = []
        for m in _GRANT_REVOKE_RE.finditer(outer):
            events.append(
                (
                    m.start(),
                    m.group(1).upper(),
                    _names(m.group(3).replace("TABLE ", "")),
                    _names(m.group(5)),
                    m.group(3),
                )
            )
        for table in defined_answer_tables:
            for role in ANSWER_RESTRICTED_ROLES:
                granted = False
                revoked_explicitly = False
                for _, verb, targets, grantees, raw_target in events:
                    all_tables = bool(re.search(r"ALL\s+TABLES\s+IN\s+SCHEMA", raw_target, re.IGNORECASE))
                    hits = all_tables or table in targets
                    if not hits or (role not in grantees and "public" not in grantees):
                        continue
                    if verb == "GRANT":
                        granted = True
                    else:
                        granted = False
                        if table in targets:
                            revoked_explicitly = True
                if not revoked_explicitly:
                    grant_violations.append(f"{role}: no explicit REVOKE ALL on {table}")
                if granted:
                    grant_violations.append(f"{role}: privileges on {table} are (re)granted after the REVOKE")
            if GRADING_POLICIES[table] not in {name for name, _, _ in policies}:
                grant_violations.append(f"missing grading policy {GRADING_POLICIES[table]}")

    passed = not (
        missing_enable
        or missing_force
        or missing_policy
        or role_violations
        or unsafe_sec_def
        or policy_violations
        or grant_violations
    )
    return DdlAuditReport(
        discovered_tables=tuple(sorted(discovered)),
        missing_rls_enable=tuple(missing_enable),
        missing_rls_force=tuple(missing_force),
        missing_rls_policy=tuple(missing_policy),
        privileged_role_violations=tuple(role_violations),
        unsafe_security_definer_fns=tuple(unsafe_sec_def),
        passed=passed,
        tenantless_tables=tuple(sorted(tenantless - {t.lower() for t in exempt_tables})),
        policy_violations=tuple(policy_violations),
        grant_violations=tuple(grant_violations),
        roles_without_explicit_nobypassrls=tuple(explicit_nobypass_missing),
    )
