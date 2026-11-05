from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.infrastructure.storage import media_url
from app.models import Difficulty, SourceType
from app.question_rules import TYPE_LABELS as _TYPE_LABELS

_FLASH_KEY = "_flash"


def flash(request: Request, message: str, kind: str = "success") -> None:
    """Queue a one-time message shown on the next rendered page (kind: success | error)."""
    request.session.setdefault(_FLASH_KEY, []).append([kind, message])


def _flash_context(request: Request) -> dict:
    flashes = request.session.pop(_FLASH_KEY, []) if "session" in request.scope else []
    return {"flashes": flashes}


templates = Jinja2Templates(directory=Path(__file__).parent / "templates", context_processors=[_flash_context])
templates.env.globals["TYPE_LABELS"] = {t.value: label for t, label in _TYPE_LABELS.items()}
templates.env.globals["SOURCE_LABELS"] = {
    SourceType.MANUAL: "Soạn tay",
    SourceType.FILE_IMPORT: "Nhập file",
    SourceType.AI_UPLOAD: "AI",
    SourceType.CRAWLER: "Thu thập",
}
templates.env.globals["DIFFICULTY_LABELS"] = {
    Difficulty.RECOGNITION: "Nhận biết",
    Difficulty.UNDERSTANDING: "Thông hiểu",
    Difficulty.APPLICATION: "Vận dụng",
}

# Vietnam has no DST, so a fixed UTC+7 offset is exact.
DISPLAY_TZ = timezone(timedelta(hours=7))


def _local_dt(value: datetime | None, fmt: str = "%H:%M %d/%m/%Y") -> str:
    return value.astimezone(DISPLAY_TZ).strftime(fmt) if value else ""


def _duration(seconds: int) -> str:
    minutes, secs = divmod(int(seconds), 60)
    if not minutes:
        return f"{secs} giây"
    return f"{minutes} phút" + (f" {secs} giây" if secs else "")


templates.env.filters["local_dt"] = _local_dt
templates.env.globals["media_url"] = media_url
templates.env.filters["duration"] = _duration
