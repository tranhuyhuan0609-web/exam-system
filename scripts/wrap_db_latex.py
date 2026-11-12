import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Question


def should_wrap(txt: str) -> bool:
    if not txt or not isinstance(txt, str):
        return False
    if '$' in txt or '\\(' in txt or '\\[' in txt:
        return False
    # If it contains LaTeX backslash commands
    if re.search(r'\\[a-zA-Z]+', txt):
        return True
    return False

async def main():
    async with SessionLocal() as s:
        qs = (await s.scalars(select(Question))).all()
        updated_count = 0
        for q in qs:
            modified = False
            # Check content
            if should_wrap(q.content):
                q.content = f"${q.content.strip()}$"
                modified = True
            
            # Check choices
            if q.choices:
                new_choices = []
                for c in q.choices:
                    c_dict = dict(c)
                    c_txt = c_dict.get('content', '')
                    if should_wrap(c_txt):
                        c_dict['content'] = f"${c_txt.strip()}$"
                        modified = True
                    new_choices.append(c_dict)
                if modified:
                    q.choices = new_choices
            
            if modified:
                updated_count += 1
        
        await s.commit()
        print(f"Successfully wrapped naked LaTeX in {updated_count} questions.")

if __name__ == '__main__':
    asyncio.run(main())
