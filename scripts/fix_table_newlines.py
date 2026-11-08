import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.db import SessionLocal
from app.models import Question

def collapse_table_pipes(text: str) -> str:
    if not text or not isinstance(text, str):
        return text
    # Replace \n|\n or \r\n|\r\n (with optional surrounding whitespace) with " | "
    return re.sub(r"\r?\n\s*\|\s*\r?\n", " | ", text)

async def main():
    async with SessionLocal() as s:
        qs = (await s.scalars(select(Question))).all()
        updated = 0
        for q in qs:
            modified = False
            if q.content and re.search(r"\r?\n\s*\|\s*\r?\n", q.content):
                q.content = collapse_table_pipes(q.content)
                modified = True

            if q.explanation and re.search(r"\r?\n\s*\|\s*\r?\n", q.explanation):
                q.explanation = collapse_table_pipes(q.explanation)
                modified = True

            if q.choices:
                new_choices = []
                for c in q.choices:
                    c_dict = dict(c)
                    c_content = c_dict.get("content", "")
                    if c_content and re.search(r"\r?\n\s*\|\s*\r?\n", c_content):
                        c_dict["content"] = collapse_table_pipes(c_content)
                        modified = True
                    new_choices.append(c_dict)
                if modified:
                    q.choices = new_choices

            if modified:
                updated += 1

        await s.commit()
        print(f"Successfully cleaned up table formatting in {updated} questions.")

if __name__ == "__main__":
    asyncio.run(main())

