"""app.ops.seed_pilot: per-account random passwords in an owner-only CSV, idempotent, and everything signs in."""

from __future__ import annotations

import asyncio
import csv
import json
import os

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app
from app.ops.seed_pilot import PAPERS, main, new_password, plan_accounts, seed_pilot


def run(coro):
    return asyncio.run(coro)


async def _count(sql: str) -> int:
    async with SessionLocal() as session:
        return (await session.execute(text(sql))).scalar_one()


def _read(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_generated_passwords_are_random_typeable_and_distinct():
    passwords = {new_password() for _ in range(200)}
    assert len(passwords) == 200
    for p in list(passwords)[:50]:
        assert len(p) == 12 and not set(p) & set("0O1lI") and any(c.isdigit() for c in p) and p != p.lower()


def test_the_plan_names_one_admin_the_teachers_and_the_learners_and_rejects_duplicates():
    plan = plan_accounts(3, 2, "school.vn")
    assert [(a["key"], a["roles"]) for a in plan] == [
        ("admin", ["TENANT_ADMIN"]),
        ("gv01", ["TEACHER"]),
        ("gv02", ["TEACHER"]),
        ("gv03", ["TEACHER"]),
        ("hs01", ["LEARNER"]),
        ("hs02", ["LEARNER"]),
    ]
    named = plan_accounts(0, 0, "x.vn", ["Lan@School.vn", " minh@school.vn "])
    assert [a["email"] for a in named if a["roles"] == ["TEACHER"]] == ["lan@school.vn", "minh@school.vn"]
    try:
        plan_accounts(0, 0, "x.vn", ["a@x.vn", "A@x.vn"])
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate e-mails must be rejected")


def test_pilot_seed_builds_accounts_papers_and_classes_once(tmp_path):
    out = tmp_path / "logins.csv"
    plan = plan_accounts(2, 1, "pilot.test")
    first = run(seed_pilot(tenant_slug="pilot-t", tenant_name="Pilot T", plan=plan, out=out))
    assert first["teachers"] == 2 and first["questions_created"] == 22 and first["assignments_created"] == 2
    assert first["papers"] == sorted(t for t, _ in PAPERS)
    rows = _read(out)
    assert len(rows) == 4 and all(r["password"] != "(unchanged)" for r in rows)
    assert len({r["password"] for r in rows}) == 4, "every account has its own password"
    assert "password" not in json.dumps(first).lower().replace("credentials", ""), "the report never carries passwords"
    if os.name == "posix":
        assert oct(out.stat().st_mode & 0o777) == "0o600"

    second_out = tmp_path / "again.csv"
    second = run(seed_pilot(tenant_slug="pilot-t", tenant_name="Pilot T", plan=plan, out=second_out))
    assert second["questions_created"] == 0 and second["assignments_created"] == 0
    assert all(r["password"] == "(unchanged)" for r in _read(second_out)), "an existing account keeps its password"
    assert run(_count("SELECT count(*) FROM questions WHERE stable_code LIKE 'DEMO-BAYES-%'")) == 22
    assert run(_count("SELECT count(*) FROM assessments WHERE title LIKE 'Đề mẫu THPTQG 2025+%'")) == 3
    assert run(_count("SELECT count(*) FROM classes WHERE invite_code LIKE 'PILOT-GV%'")) == 2
    assert (
        run(
            _count(
                "SELECT count(*) FROM class_members cm JOIN classes c ON c.id = cm.class_id "
                "WHERE c.invite_code LIKE 'PILOT-GV%'"
            )
        )
        == 2
    ), "the learner sits in both classes"
    assert run(_count("SELECT count(*) FROM assessment_assignments")) == 2

    client = TestClient(app, base_url="http://127.0.0.1:8000")
    for row in rows:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": row["email"], "password": row["password"], "tenant_slug": "pilot-t"},
        )
        assert response.status_code == 200, (row["email"], response.text)
        assert response.json()["roles"] == row["role"].split(",")
    wrong = client.post(
        "/api/v1/auth/login", json={"email": rows[1]["email"], "password": "Demo@12345", "tenant_slug": "pilot-t"}
    )
    assert wrong.status_code in (401, 403), "the public demo password does not work here"

    teacher = next(r for r in rows if r["role"] == "TEACHER")
    token = client.post(
        "/api/v1/auth/login",
        json={"email": teacher["email"], "password": teacher["password"], "tenant_slug": "pilot-t"},
    ).json()["access_token"]
    papers = client.get("/api/v1/assessments", headers={"Authorization": f"Bearer {token}"}).json()
    assert len([p for p in papers if p["title"].startswith("Đề mẫu THPTQG 2025+")]) == 3


def test_the_cli_writes_the_file_and_prints_no_password(tmp_path, capsys):
    out = tmp_path / "cli.csv"
    code = main(
        [
            "--out",
            str(out),
            "--tenant",
            "pilot-cli",
            "--teachers",
            "1",
            "--learners",
            "1",
            "--no-papers",
            "--domain",
            "cli.test",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    passwords = [r["password"] for r in _read(out)]
    assert len(passwords) == 3 and all(p not in captured.out and p not in captured.err for p in passwords)
