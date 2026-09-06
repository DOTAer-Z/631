from fastapi import APIRouter

from app.api.v1.annotations import router as annotations_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.fault_type_suggestions import router as fault_type_suggestions_router
from app.api.v1.fault_types import router as fault_types_router
from app.api.v1.health import router as health_router
from app.api.v1.import_tasks import router as import_tasks_router
from app.api.v1.packages import router as packages_router
from app.api.v1.recommendations import router as recommendations_router
from app.api.v1.slice_tasks import router as slice_tasks_router
from app.api.v1.slice_windows import router as slice_windows_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(dashboard_router)
api_router.include_router(packages_router)
api_router.include_router(import_tasks_router)
api_router.include_router(slice_tasks_router)
api_router.include_router(slice_windows_router)
api_router.include_router(annotations_router)
api_router.include_router(recommendations_router)
api_router.include_router(fault_types_router)
api_router.include_router(fault_type_suggestions_router)
