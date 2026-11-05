from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.models import Difficulty

_FLASH_KEY = "_flash"


def flash(request: Request, message: str, kind: str = "success") -> None:
    """Queue a one-time message shown on the next rendered page (kind: success | error)."""
    request.session.setdefault(_FLASH_KEY, []).append([kind, message])


def _flash_context(request: Request) -> dict:
    flashes = request.session.pop(_FLASH_KEY, []) if "session" in request.scope else []
    return {"flashes": flashes}


templates = Jinja2Templates(directory=Path(__file__).parent / "templates", context_processors=[_flash_context])
templates.env.globals["DIFFICULTY_LABELS"] = {
    Difficulty.RECOGNITION: "Nhận biết",
    Difficulty.UNDERSTANDING: "Thông hiểu",
    Difficulty.APPLICATION: "Vận dụng",
}
