from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app.infrastructure.storage import media_url
from app.models import Difficulty, SourceType
from app.platform.branding import (
    BRAND_HERO_SUBTITLE,
    BRAND_HERO_TITLE,
    BRAND_NAME,
    BRAND_SEO_DESCRIPTION,
    BRAND_SLOGAN,
    BRAND_TAB_TITLE,
)
from app.question_rules import TYPE_LABELS as _TYPE_LABELS
from app.security import get_or_create_csrf_token

_FLASH_KEY = "_flash"


def flash(request: Request, message: str, kind: str = "success") -> None:
    """Queue a one-time message shown on the next rendered page (kind: success | error)."""
    # Assign a new list: mutating the stored one in place is not seen as a change, so the cookie would
    # not be rewritten and a second queued message would be lost.
    request.session[_FLASH_KEY] = [*request.session.get(_FLASH_KEY, []), [kind, message]]


def _flash_context(request: Request) -> dict:
    flashes = request.session.pop(_FLASH_KEY, []) if "session" in request.scope else []
    return {"flashes": flashes}


def _csrf_input(request: Request) -> Markup:
    token = get_or_create_csrf_token(request)
    return Markup(f'<input type="hidden" name="csrf_token" value="{token}">')


templates = Jinja2Templates(directory=Path(__file__).parent / "templates", context_processors=[_flash_context])
templates.env.globals["BRAND_NAME"] = BRAND_NAME
templates.env.globals["BRAND_SLOGAN"] = BRAND_SLOGAN
templates.env.globals["BRAND_TAB_TITLE"] = BRAND_TAB_TITLE
templates.env.globals["BRAND_SEO_DESCRIPTION"] = BRAND_SEO_DESCRIPTION
templates.env.globals["BRAND_HERO_TITLE"] = BRAND_HERO_TITLE
templates.env.globals["BRAND_HERO_SUBTITLE"] = BRAND_HERO_SUBTITLE
templates.env.globals["csrf_token"] = get_or_create_csrf_token
templates.env.globals["csrf_input"] = _csrf_input
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
    Difficulty.ADVANCED_APPLICATION: "Vận dụng cao",
    Difficulty.HSG: "Học sinh giỏi (HSG)",
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


def _format_q_content(content: str) -> str:
    """Renders plain text and converts markdown pipe tables (| col1 | col2 |) into styled HTML tables."""
    if not content:
        return ""
    import html

    lines = content.splitlines()
    out = []
    table_lines = []

    def flush_table():
        nonlocal table_lines
        if not table_lines:
            return
        html_tbl = ['<div class="table-responsive" style="overflow-x:auto;"><table class="q-data-table"><tbody>']
        for tl in table_lines:
            cells = [c.strip() for c in tl.strip().strip("|").split("|")]
            html_tbl.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>")
        html_tbl.append("</tbody></table></div>")
        out.append("".join(html_tbl))
        table_lines = []

    for line in lines:
        stripped = line.strip()
        if "|" in stripped and stripped.count("|") >= 2:
            table_lines.append(stripped)
        else:
            flush_table()
            out.append(html.escape(line))
    flush_table()
    return "<br>".join(out)


templates.env.filters["local_dt"] = _local_dt
templates.env.filters["local_input"] = lambda v: _local_dt(v, "%Y-%m-%dT%H:%M")  # <input type=datetime-local>
templates.env.filters["format_q_content"] = _format_q_content
templates.env.globals["ROLE_LABELS"] = {"ADMIN": "Admin", "TEACHER": "Giáo viên", "STUDENT": "Học sinh"}
templates.env.globals["media_url"] = media_url
templates.env.filters["duration"] = _duration
