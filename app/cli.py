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
from app.models import Chapter, Lesson, Subject, User, UserRole
from app.security import hash_password, normalize_email, password_problem

DEMO_SUBJECTS = [
    {
        "name": "Vật lí 11",
        "slug": "vat-li-11",
        "chapters": [
            ("Chương 1: Dao động", ["Bài 1: Dao động điều hoà", "Bài 2: Mô tả dao động điều hoà",
                                    "Bài 3: Vận tốc, gia tốc trong dao động điều hoà"]),
            ("Chương 2: Sóng", ["Bài 8: Mô tả sóng", "Bài 9: Sóng ngang. Sóng dọc"]),
        ],
    },
    {
        "name": "Toán 11",
        "slug": "toan-11",
        "chapters": [
            ("Chương 1: Hàm số lượng giác và phương trình lượng giác",
             ["Bài 1: Giá trị lượng giác của góc lượng giác", "Bài 2: Công thức lượng giác"]),
        ],
    },
]


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


async def seed_demo() -> int:
    async with SessionLocal() as session:
        for data in DEMO_SUBJECTS:
            if await session.scalar(select(Subject.id).where(Subject.slug == data["slug"])):
                print(f"Skip {data['name']} (already exists).")
                continue
            subject = Subject(name=data["name"], slug=data["slug"])
            for ci, (chapter_name, lessons) in enumerate(data["chapters"], start=1):
                chapter = Chapter(name=chapter_name, position=ci)
                chapter.lessons = [Lesson(name=n, position=li) for li, n in enumerate(lessons, start=1)]
                subject.chapters.append(chapter)
            session.add(subject)
            print(f"Created {data['name']}.")
        await session.commit()
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

    sub.add_parser("seed-demo", help="Insert demo subjects, chapters and lessons")

    args = parser.parse_args()
    if args.command == "create-admin":
        password = args.password or getpass.getpass("Password: ")
        return asyncio.run(create_admin(args.email, args.name, password))
    return asyncio.run(seed_demo())


if __name__ == "__main__":
    raise SystemExit(main())
