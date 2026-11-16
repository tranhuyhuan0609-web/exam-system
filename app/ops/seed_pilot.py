"""Accounts and sample data for a teacher pilot: ``python -m app.ops.seed_pilot --out pilot_accounts.csv``.

Idempotent (every step looks before it writes), safe on a production database, and unlike `seed_demo` it never uses a
public password: each account gets its own random password, written ONLY to the CSV given in ``--out`` (mode 0600,
never printed, never logged). An account that already exists keeps its password and shows ``(unchanged)``.

It builds, in the migrated database,

* the tenant (``--tenant``, default ``pilot``), one ``TENANT_ADMIN`` and ``--teachers`` teachers (or the e-mails of
  ``--emails-file``), and ``--learners`` learner accounts so a teacher can try the exam room at once;
* the question bank for the sample papers: 22 published, verified questions (12 single choice, 4 true/false clusters,
  6 short numeric: the THPTQG 2025+ shape) under the shared MATH > BAYES topic;
* three sample papers "Đề mẫu THPTQG 2025+ · Toán · Mã đề 101/102/103" (the same 22 questions in three different
  orders), assembled and published through the real API so every rule applies;
* one class per teacher (``PILOT-GV01`` ...) with the learners in it and the first sample paper already assigned.

Run it as the database owner (the role migrations use), after ``alembic upgrade head``.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import secrets
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal, engine
from app.ops import seed_demo
from app.platform.tenancy import tenant_transaction
from app.security import hash_password

DEFAULT_TENANT = "pilot"
DEFAULT_DOMAIN = "pilot.example.com"
PAPERS: tuple[tuple[str, int], ...] = (
    ("Đề mẫu THPTQG 2025+ · Toán · Mã đề 101", 101),
    ("Đề mẫu THPTQG 2025+ · Toán · Mã đề 102", 102),
    ("Đề mẫu THPTQG 2025+ · Toán · Mã đề 103", 103),
)
_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/l/I


def new_password(length: int = 12) -> str:
    """A random password a person can type: no look-alike characters, at least one letter and one digit."""
    while True:
        candidate = "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))
        if (
            any(c.isdigit() for c in candidate)
            and any(c.islower() for c in candidate)
            and any(c.isupper() for c in candidate)
        ):
            return candidate


def plan_accounts(teachers: int, learners: int, domain: str, emails: list[str] | None = None) -> list[dict[str, Any]]:
    """The accounts to create: key, e-mail, display name, roles, entitlement tier."""
    plan: list[dict[str, Any]] = [
        {
            "key": "admin",
            "email": f"admin@{domain}",
            "name": "Quản trị trường",
            "roles": ["TENANT_ADMIN"],
            "tier": "SCHOOL_ENTERPRISE",
        }
    ]
    for n in range(1, (len(emails) if emails else teachers) + 1):
        plan.append(
            {
                "key": f"gv{n:02d}",
                "email": emails[n - 1] if emails else f"gv{n:02d}@{domain}",
                "name": f"Giáo viên {n:02d}",
                "roles": ["TEACHER"],
                "tier": "TEACHER_PRO",
            }
        )
    for n in range(1, learners + 1):
        plan.append(
            {
                "key": f"hs{n:02d}",
                "email": f"hs{n:02d}@{domain}",
                "name": f"Học sinh thử {n:02d}",
                "roles": ["LEARNER"],
                "tier": "STUDENT_PRO",
            }
        )
    seen: set[str] = set()
    for item in plan:
        item["email"] = item["email"].strip().lower()
        if item["email"] in seen:
            raise ValueError(f"duplicate e-mail {item['email']}")
        seen.add(item["email"])
    return plan


async def _ensure_tenant(slug: str, name: str) -> uuid.UUID:
    async with SessionLocal() as session, session.begin():
        await session.execute(
            text("INSERT INTO tenants (slug, name) VALUES (:s, :n) ON CONFLICT (slug) DO NOTHING"),
            {"s": slug, "n": name},
        )
        return (await session.execute(text("SELECT id FROM tenants WHERE slug = :s"), {"s": slug})).scalar_one()


async def _ensure_accounts(
    tenant_id: uuid.UUID, plan: list[dict[str, Any]], *, reset_passwords: bool = False
) -> dict[str, dict[str, Any]]:
    """Register every planned account; `password` is set only on the ones created now (None: already there).

    `reset_passwords` gives the accounts that already exist a new password too (a run that failed before the CSV was
    written, or a password that was lost).
    """
    settings = get_settings()
    accounts: dict[str, dict[str, Any]] = {}
    async with tenant_transaction(tenant_id) as session:
        for item in plan:
            password = new_password()
            password_hash = hash_password(password)
            blind = seed_demo.email_blind_index(item["email"], settings.secret_key, settings.blind_index_pepper)
            row = (
                await session.execute(
                    text("SELECT user_id, created FROM f04_register_user(:b, :h, NULL, :p, NULL, 'ACTIVE')"),
                    {"b": blind, "h": password_hash, "p": item["name"]},
                )
            ).one()
            # registered earlier without a password: it gets this one; an existing password changes only on request
            await session.execute(
                text(
                    "UPDATE users SET password_hash = :h "
                    "WHERE id = :u AND (password_hash IS NULL OR CAST(:r AS boolean))"
                ),
                {"h": password_hash, "u": row.user_id, "r": reset_passwords},
            )
            await session.execute(
                text(
                    "INSERT INTO memberships (tenant_id, user_id, roles, entitlement_tier) "
                    "VALUES (:t, :u, CAST(:r AS varchar[]), :tier) ON CONFLICT (tenant_id, user_id) DO NOTHING"
                ),
                {"t": tenant_id, "u": row.user_id, "r": item["roles"], "tier": item["tier"]},
            )
            membership = (
                await session.execute(
                    text("SELECT id FROM memberships WHERE tenant_id = :t AND user_id = :u"),
                    {"t": tenant_id, "u": row.user_id},
                )
            ).scalar_one()
            stored = (
                await session.execute(text("SELECT password_hash FROM users WHERE id = :u"), {"u": row.user_id})
            ).scalar_one()
            fresh = stored == password_hash  # the password just generated is the one stored: created now (or had none)
            accounts[item["key"]] = {
                **item,
                "user_id": row.user_id,
                "membership_id": membership,
                "password": password if fresh else None,
            }
    return accounts


async def _ensure_class(
    tenant_id: uuid.UUID, teacher: uuid.UUID, code: str, name: str, learners: list[uuid.UUID]
) -> uuid.UUID:
    async with tenant_transaction(tenant_id) as session:
        row = (
            await session.execute(
                text("SELECT id FROM classes WHERE tenant_id = :t AND invite_code = :c"), {"t": tenant_id, "c": code}
            )
        ).first()
        if row is None:
            row = (
                await session.execute(
                    text(
                        "INSERT INTO classes (tenant_id, teacher_membership_id, name, academic_year, invite_code) "
                        "VALUES (:t, :m, :n, '2025-2026', :c) RETURNING id"
                    ),
                    {"t": tenant_id, "m": teacher, "n": name, "c": code},
                )
            ).one()
        for member in learners:
            await session.execute(
                text(
                    "INSERT INTO class_members (tenant_id, class_id, membership_id) VALUES (:t, :c, :m) "
                    "ON CONFLICT (class_id, membership_id) DO NOTHING"
                ),
                {"t": tenant_id, "c": row.id, "m": member},
            )
        return row.id


async def _ensure_papers(tenant_id: uuid.UUID, owner: dict[str, Any]) -> dict[str, str]:
    """The sample papers (by title), made through the HTTP API by `owner`; returns title -> published version id."""
    headers = seed_demo._headers(tenant_id, owner)
    async with tenant_transaction(tenant_id) as session:
        have = {
            r.title
            for r in (
                await session.execute(
                    text("SELECT title FROM assessments WHERE tenant_id = :t AND title = ANY(:titles)"),
                    {"t": tenant_id, "titles": [t for t, _ in PAPERS]},
                )
            )
        }

    def make(client: Any, title: str, seed: int) -> None:
        made = seed_demo._ok(
            client.post(
                "/api/v1/assessments",
                headers=headers,
                json={
                    "title": title,
                    "subject_code": "math",
                    "grade_band": "GRADE_12",
                    "exam_type_code": "THPTQG",
                    "rights_status": "OWNED",
                },
            )
        )
        assessment_id = made.json()["id"]
        seed_demo._ok(
            client.post(f"/api/v1/assessments/{assessment_id}/assemble", headers=headers, json={"random_seed": seed})
        )
        seed_demo._ok(client.post(f"/api/v1/assessments/{assessment_id}/publish", headers=headers))

    for title, seed in PAPERS:
        if title not in have:

            def make_this(client: Any, title: str = title, seed: int = seed) -> None:
                make(client, title, seed)

            await seed_demo._through_api(make_this)

    def listing(client: Any) -> list[dict[str, Any]]:
        return seed_demo._ok(client.get("/api/v1/assessments", headers=headers)).json()

    rows = await seed_demo._through_api(listing)
    wanted = {t for t, _ in PAPERS}
    return {r["title"]: r["current_version_id"] for r in rows if r["title"] in wanted and r.get("current_version_id")}


async def _ensure_assignments(
    tenant_id: uuid.UUID, teachers: list[dict[str, Any]], classes: dict[str, uuid.UUID], version_id: str
) -> int:
    """Hand the first sample paper to every teacher's class (once)."""
    async with tenant_transaction(tenant_id) as session:
        done = {
            r.class_id
            for r in (
                await session.execute(
                    text(
                        "SELECT class_id FROM assessment_assignments "
                        "WHERE tenant_id = :t AND assessment_version_id = :v"
                    ),
                    {"t": tenant_id, "v": uuid.UUID(version_id)},
                )
            )
        }
    created = 0
    for teacher in teachers:
        class_id = classes[teacher["key"]]
        if class_id in done:
            continue
        headers = seed_demo._headers(tenant_id, teacher)

        def assign(client: Any, headers: dict[str, str] = headers, class_id: uuid.UUID = class_id) -> None:
            seed_demo._ok(
                client.post(
                    "/api/v1/assignments",
                    headers=headers,
                    json={"class_id": str(class_id), "assessment_version_id": version_id},
                )
            )

        await seed_demo._through_api(assign)
        created += 1
    return created


def write_credentials(path: Path, tenant_slug: str, accounts: dict[str, dict[str, Any]]) -> int:
    """Write the logins (CSV, owner-only). Returns how many rows carry a fresh password."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    fresh = 0
    with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["tenant_slug", "email", "role", "password", "name"])
        for account in accounts.values():
            password = account["password"]
            fresh += password is not None
            writer.writerow(
                [tenant_slug, account["email"], ",".join(account["roles"]), password or "(unchanged)", account["name"]]
            )
    return fresh


async def seed_pilot(
    *,
    tenant_slug: str,
    tenant_name: str,
    plan: list[dict[str, Any]],
    out: Path,
    papers: bool = True,
    reset_passwords: bool = False,
) -> dict[str, Any]:
    await seed_demo._ensure_catalogues()
    tenant_id = await _ensure_tenant(tenant_slug, tenant_name)
    accounts = await _ensure_accounts(tenant_id, plan, reset_passwords=reset_passwords)
    # written at once: if a later step fails, the passwords just created are not lost
    credentials_written = write_credentials(out, tenant_slug, accounts)
    teachers = [a for a in accounts.values() if "TEACHER" in a["roles"]]
    learners = [a["membership_id"] for a in accounts.values() if "LEARNER" in a["roles"]]
    report: dict[str, Any] = {"tenant": {"slug": tenant_slug, "id": str(tenant_id)}, "teachers": len(teachers)}
    if papers and teachers:
        taxonomy_id = await seed_demo._ensure_taxonomy()
        report["questions_created"] = await seed_demo._ensure_questions(tenant_id, taxonomy_id)
        versions = await _ensure_papers(tenant_id, teachers[0])
        report["papers"] = sorted(versions)
        classes: dict[str, uuid.UUID] = {}
        for teacher in teachers:
            number = teacher["key"].removeprefix("gv")
            classes[teacher["key"]] = await _ensure_class(
                tenant_id,
                teacher["membership_id"],
                f"PILOT-GV{number}",
                f"Lớp thử nghiệm {teacher['name']}",
                learners,
            )
        first = versions.get(PAPERS[0][0])
        report["assignments_created"] = await _ensure_assignments(tenant_id, teachers, classes, first) if first else 0
    report["credentials_written"] = credentials_written
    report["finished_at"] = datetime.now(UTC).isoformat()
    await engine.dispose()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create pilot accounts, sample papers and one class per teacher.")
    parser.add_argument("--out", required=True, help="CSV file for the logins (created owner-only; contains passwords)")
    parser.add_argument("--tenant", default=DEFAULT_TENANT, help="tenant slug (default: pilot)")
    parser.add_argument("--tenant-name", default="Pilot giáo viên H2Edu")
    parser.add_argument("--domain", default=DEFAULT_DOMAIN, help="e-mail domain of the generated accounts")
    parser.add_argument("--teachers", type=int, default=15)
    parser.add_argument("--learners", type=int, default=3)
    parser.add_argument("--emails-file", help="one teacher e-mail per line: used instead of gv01..gvNN@domain")
    parser.add_argument(
        "--no-papers", action="store_true", help="accounts only: no sample questions, papers or classes"
    )
    parser.add_argument(
        "--reset-passwords", action="store_true", help="also give the accounts that already exist a new password"
    )
    args = parser.parse_args(argv)
    emails = None
    if args.emails_file:
        lines = Path(args.emails_file).read_text(encoding="utf-8").splitlines()
        emails = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    plan = plan_accounts(args.teachers, args.learners, args.domain, emails)
    report = asyncio.run(
        seed_pilot(
            tenant_slug=args.tenant,
            tenant_name=args.tenant_name,
            plan=plan,
            out=Path(args.out),
            papers=not args.no_papers,
            reset_passwords=args.reset_passwords,
        )
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"logins written to {args.out}: hand each teacher their own row, then delete the file.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
