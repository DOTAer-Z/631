from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.schemas.import_task import ImportTaskResponse
from app.services.import_service import ImportService

router = APIRouter(tags=["import-tasks"])


@router.get("/import-tasks/{task_id}", response_model=ImportTaskResponse)
def get_import_task(task_id: int, db: Session = Depends(get_db)) -> ImportTaskResponse:
    service = ImportService(db)
    task = service.get_task(task_id=task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Import task not found")

    return ImportTaskResponse(
        id=task.id,
        package_id=task.package_id,
        status=task.status,
        error_message=task.error_message,
        started_at=task.started_at,
        finished_at=task.finished_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )
