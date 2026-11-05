"""Management commands.

python -m app.cli create-admin --email admin@example.com --name "Admin"
python -m app.cli seed-demo
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.demo_data import seed_demo
from app.models import User, UserRole
from app.security import hash_password, normalize_email, password_problem


async def create_admin(email: str, name: str, password: str) -> int:
    email = normalize_email(email)
    if problem := password_problem(password):
        print(f"Error: {problem}", file=sys.stderr)
        return 1
    async with SessionLocal() as session:
        if await session.scalar(select(User.id).where(User.email == email)):
            print(f"Error: {email} already exists.", file=sys.stderr)
            return 1
        session.add(User(email=email, full_name=name, password_hash=hash_password(password), role=UserRole.ADMIN))
        await session.commit()
    print(f"Admin {email} created.")
    return 0


def main() -> int:
    # Windows consoles default to a legacy code page that cannot print Vietnamese.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_admin = sub.add_parser("create-admin", help="Create an admin account")
    p_admin.add_argument("--email", required=True)
    p_admin.add_argument("--name", default="Admin")
    p_admin.add_argument("--password", help="Prompted if omitted")

    sub.add_parser("seed-demo", help="Insert demo subjects, chapters, lessons, questions and an exam")

    args = parser.parse_args()
    if args.command == "create-admin":
        password = args.password or getpass.getpass("Password: ")
        return asyncio.run(create_admin(args.email, args.name, password))
    asyncio.run(seed_demo())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
