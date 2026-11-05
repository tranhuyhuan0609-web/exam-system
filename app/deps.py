from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import User, UserRole

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class LoginRequired(Exception):
    """Raised by page dependencies; turned into a redirect to /login by an exception handler."""

    def __init__(self, next_url: str):
        self.next_url = next_url


async def get_current_user(request: Request, session: SessionDep) -> User | None:
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        request.session.clear()
        return None
    return user


CurrentUser = Annotated[User | None, Depends(get_current_user)]


async def require_user(request: Request, user: CurrentUser) -> User:
    if user is None:
        raise LoginRequired(request.url.path)
    return user


async def require_admin(user: Annotated[User, Depends(require_user)]) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bạn không có quyền truy cập trang này.")
    return user


LoggedInUser = Annotated[User, Depends(require_user)]
AdminUser = Annotated[User, Depends(require_admin)]
