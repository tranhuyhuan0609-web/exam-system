from typing import Any, Protocol

from fastapi import HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession


class _Positioned(Protocol):
    position: int


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status.HTTP_303_SEE_OTHER)


async def get_or_404[T](session: AsyncSession, model: type[T], ident: Any) -> T:
    obj = await session.get(model, ident)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy.")
    return obj


def move[P: _Positioned](items: list[P], item: P, direction: str) -> None:
    """Move `item` one step up/down inside the ordered `items`, then renumber positions 1..n."""
    i = items.index(item)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(items):
        items[i], items[j] = items[j], items[i]
    for pos, it in enumerate(items, start=1):
        it.position = pos


def clean_text(value: str, max_len: int) -> str | None:
    value = value.strip()
    return value if value and len(value) <= max_len else None
