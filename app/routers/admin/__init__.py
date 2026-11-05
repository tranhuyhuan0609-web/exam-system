from fastapi import APIRouter, Depends

from app.deps import require_admin
from app.routers.admin import dashboard, exams, questions, subjects

# Every admin route requires an admin; individual handlers still declare AdminUser when they need the user.
router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
# Registered directly: FastAPI rejects an empty path inside a prefix-less sub-router.
router.add_api_route("", dashboard.dashboard, methods=["GET"])
router.include_router(subjects.router)
router.include_router(questions.router)
router.include_router(exams.router)
