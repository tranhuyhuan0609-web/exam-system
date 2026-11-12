from fastapi import APIRouter, Depends

from app.deps import require_admin
from app.routers.admin import ai_imports, audit_queue, categories, crawler, dashboard, data, exams, imports, library, questions, users

# Every admin route requires an admin; individual handlers still declare AdminUser when they need the user.
router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
# Registered directly: FastAPI rejects an empty path inside a prefix-less sub-router.
router.add_api_route("", dashboard.dashboard, methods=["GET"])
router.include_router(audit_queue.router)
router.include_router(categories.router)
router.include_router(imports.router)  # before questions: /questions/import* paths
router.include_router(questions.router)
router.include_router(exams.router)
router.include_router(ai_imports.router)
router.include_router(users.router)
router.include_router(crawler.router)
router.include_router(library.router)
router.include_router(data.router)
