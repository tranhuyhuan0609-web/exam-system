"""Management commands.

python -m app.cli create-admin --email admin@example.com --name "Admin"
python -m app.cli seed-demo
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import func, select

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


async def migrate_files() -> int:
    """Copy files from the old on-disk layout into the configured storage (local or MinIO).

    data/uploads/<f>         -> raw/<f>            (and import_jobs.stored_path is updated)
    data/jobs/<id>/pages/<f> -> pages/<id>/<f>
    data/media/figures/<f>   -> figures/<f>
    """
    from pathlib import Path

    from sqlalchemy import update

    from app.config import get_settings
    from app.infrastructure.storage import LocalStorage, get_storage
    from app.models import ImportJob

    data_dir = Path(get_settings().data_dir)
    storage = get_storage()
    copied = 0

    async def copy(path: Path, key: str) -> None:
        nonlocal copied
        if isinstance(storage, LocalStorage) and storage.path(key) == path:
            return  # already in place
        if not await storage.exists(key):
            await storage.put(
                key, path.read_bytes(), "image/png" if path.suffix == ".png" else "application/octet-stream"
            )
            copied += 1

    for path in sorted((data_dir / "uploads").glob("*")):
        await copy(path, f"raw/{path.name}")
    for path in sorted((data_dir / "jobs").glob("*/pages/*.png")):
        await copy(path, f"pages/{path.parent.parent.name}/{path.name}")
    for path in sorted((data_dir / "media" / "figures").glob("*")):
        await copy(path, f"figures/{path.name}")
    async with SessionLocal() as session:
        result = await session.execute(
            update(ImportJob)
            .where(ImportJob.stored_path.like("uploads/%"))
            .values(stored_path=func.concat("raw/", func.substr(ImportJob.stored_path, 9)))
        )
        await session.commit()
    print(f"Copied {copied} file(s) to {type(storage).__name__}; updated {result.rowcount} job path(s).")
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

    sub.add_parser("migrate-files", help="Copy files from the old data/ layout into the configured storage")
    sub.add_parser("seed-demo", help="Insert demo subjects, chapters, lessons, questions and an exam")

    args = parser.parse_args()
    if args.command == "create-admin":
        password = args.password or getpass.getpass("Password: ")
        return asyncio.run(create_admin(args.email, args.name, password))
    if args.command == "migrate-files":
        return asyncio.run(migrate_files())
    asyncio.run(seed_demo())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
