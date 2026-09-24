from fastapi import APIRouter

from app.core.config import get_settings
from app.db.health import check_database
from app.db.session import engine

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, object]:
    settings = get_settings()
    db_ok, db_error = check_database(engine)
    return {
        "status": "ok" if db_ok else "degraded",
        "version": settings.app_version,
        "database": {
            "connected": db_ok,
            "error": db_error,
        },
    }
