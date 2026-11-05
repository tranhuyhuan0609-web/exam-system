from fastapi import APIRouter, Request

from app.deps import CurrentUser
from app.templating import templates

router = APIRouter()


@router.get("/")
async def home(request: Request, user: CurrentUser):
    return templates.TemplateResponse(request, "index.html", {"user": user})
