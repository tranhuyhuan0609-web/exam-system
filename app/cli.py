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


async def repair_latex_command() -> int:
    """Fix LaTeX damaged by JSON escaping (TAB instead of \t, ...) in the bank, AI drafts and attempt snapshots."""
    from app.domain.latex import repair_latex
    from app.domain.question_hash import hash_of_choices
    from app.models import ExamAttempt, ImportJob, Question

    def fix_choices(choices):
        return [{**c, "content": repair_latex(c.get("content", ""))} for c in choices or []]

    counts = {"questions": 0, "drafts": 0, "attempts": 0}
    async with SessionLocal() as session:
        for q in (await session.scalars(select(Question))).all():
            content, choices, explanation = repair_latex(q.content), fix_choices(q.choices), repair_latex(q.explanation)
            if (content, choices, explanation) != (q.content, q.choices, q.explanation):
                q.content, q.choices, q.explanation = content, choices, explanation
                q.content_hash = hash_of_choices(q.question_type.value, content, choices)
                counts["questions"] += 1
        for job in (await session.scalars(select(ImportJob))).all():
            drafts, changed = [], False
            for d in job.drafts or []:
                fixed = {
                    **d,
                    "content": repair_latex(d.get("content", "")),
                    "choices": [repair_latex(c) for c in d.get("choices", [])],
                    "explanation": repair_latex(d.get("explanation", "")),
                    "final_value": repair_latex(d.get("final_value")),
                    "short_answer": repair_latex(d.get("short_answer")),
                }
                changed |= fixed != d
                counts["drafts"] += fixed != d
                drafts.append(fixed)
            if changed:
                job.drafts = drafts
        for attempt in (await session.scalars(select(ExamAttempt))).all():
            snapshot = attempt.paper_snapshot or {}
            questions = [
                {
                    **q,
                    "content": repair_latex(q.get("content", "")),
                    "choices": fix_choices(q.get("choices")),
                    "explanation": repair_latex(q.get("explanation")),
                }
                for q in snapshot.get("questions", [])
            ]
            if questions != snapshot.get("questions", []):
                attempt.paper_snapshot = {**snapshot, "questions": questions}
                counts["attempts"] += 1
        await session.commit()
    print("Repaired: {questions} question(s), {drafts} AI draft(s), {attempts} attempt snapshot(s).".format(**counts))
    return 0


async def recrop_figures() -> int:
    """Re-cut stored figures so they contain only the figure, not neighbouring question text.

    For AI imports of PDFs, the old crop is located on the stored page image and snapped to the exact
    vector graphics of the original PDF. Otherwise, text lines cut by the crop border are trimmed.
    """
    import io

    import pymupdf
    from PIL import Image

    from app.ai.figures import SNAP_PAD_PX, cut, locate_crop, page_geometry, snap_to_graphics, trim_cut_text
    from app.ai.pipeline import page_key
    from app.ai.schemas import BoundingBox
    from app.infrastructure.storage import StorageError, get_storage
    from app.models import ImportJob, Question

    storage = get_storage()
    done: dict[str, str] = {}  # key -> how it was fixed

    async def save(key: str, image: Image.Image) -> None:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        await storage.put(key, buffer.getvalue(), "image/png")

    async with SessionLocal() as session:
        for job in (await session.scalars(select(ImportJob).order_by(ImportJob.id))).all():
            geometries = None
            if job.stored_path.lower().endswith(".pdf"):
                try:
                    with pymupdf.open(stream=await storage.get(job.stored_path), filetype="pdf") as doc:
                        geometries = [page_geometry(page) for page in doc]
                except (StorageError, RuntimeError):
                    geometries = None
            drafts = [dict(d) for d in job.drafts or []]
            for d in drafts:
                key = d.get("figure")
                if not key or key in done:
                    continue
                try:
                    page = Image.open(io.BytesIO(await storage.get(page_key(job.id, d.get("page") or 1))))
                    crop = Image.open(io.BytesIO(await storage.get(key)))
                except StorageError:
                    continue
                stored = d.get("figure_bbox")
                position = locate_crop(page, crop) if geometries and not stored else None
                if geometries and stored:
                    snapped = snap_to_graphics(BoundingBox(**stored), geometries[(d.get("page") or 1) - 1])
                    if snapped and (figure := cut(page, snapped, SNAP_PAD_PX, SNAP_PAD_PX)) is not None:
                        await save(key, figure)
                        done[key] = f"snapped from the stored box, {crop.size} -> {figure.size}"
                        continue
                if position:
                    width, height = page.size
                    x, y = position
                    region = BoundingBox(
                        ymin=y * 1000 // height,
                        xmin=x * 1000 // width,
                        ymax=(y + crop.height) * 1000 // height,
                        xmax=(x + crop.width) * 1000 // width,
                    )
                    snapped = snap_to_graphics(region, geometries[(d.get("page") or 1) - 1])
                    if snapped and (figure := cut(page, snapped, SNAP_PAD_PX, SNAP_PAD_PX)) is not None:
                        await save(key, figure)
                        d["figure_bbox"] = region.model_dump()
                        done[key] = f"snapped to vector graphics, {crop.size} -> {figure.size}"
                        continue
                trimmed = trim_cut_text(crop.convert("RGB"))
                if trimmed.size != crop.size:
                    await save(key, trimmed)
                done[key] = f"trimmed, {crop.size} -> {trimmed.size}"
            job.drafts = drafts
        # Figures of questions that did not come from an AI job (or whose job was deleted).
        for key in (await session.scalars(select(Question.image_path).where(Question.image_path.is_not(None)))).all():
            if key in done:
                continue
            try:
                crop = Image.open(io.BytesIO(await storage.get(key)))
            except StorageError:
                continue
            trimmed = trim_cut_text(crop.convert("RGB"))
            if trimmed.size != crop.size:
                await save(key, trimmed)
            done[key] = f"trimmed, {crop.size} -> {trimmed.size}"
        await session.commit()
    for key, how in sorted(done.items()):
        print(f"{key}: {how}")
    print(f"Processed {len(done)} figure(s).")
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

    sub.add_parser("recrop-figures", help="Re-cut stored figures so they contain only the figure")
    sub.add_parser("repair-latex", help="Fix LaTeX damaged by JSON escaping in stored questions and drafts")
    sub.add_parser("migrate-files", help="Copy files from the old data/ layout into the configured storage")
    sub.add_parser("seed-demo", help="Insert demo subjects, chapters, lessons, questions and an exam")

    args = parser.parse_args()
    if args.command == "create-admin":
        password = args.password or getpass.getpass("Password: ")
        return asyncio.run(create_admin(args.email, args.name, password))
    if args.command == "recrop-figures":
        return asyncio.run(recrop_figures())
    if args.command == "repair-latex":
        return asyncio.run(repair_latex_command())
    if args.command == "migrate-files":
        return asyncio.run(migrate_files())
    asyncio.run(seed_demo())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
