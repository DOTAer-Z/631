from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.core.errors import SliceTaskNotAllowedError
from app.schemas.slice_task import (
    SliceTaskCreateRequest,
    SliceTaskDetailResponse,
    SliceTaskListResponse,
    SliceTaskSummaryResponse,
    SliceWindowSummary,
)
from app.services.slice_engine import WindowParseError
from app.services.slice_task_service import SliceTaskService

router = APIRouter(tags=["slice-tasks"])


@router.post("/packages/{package_id}/slice-tasks", response_model=SliceTaskDetailResponse, status_code=status.HTTP_201_CREATED)
def create_slice_task(
    package_id: int,
    payload: SliceTaskCreateRequest,
    db: Session = Depends(get_db),
) -> SliceTaskDetailResponse:
    service = SliceTaskService(db)
    try:
        task = service.create_and_run(
            package_id=package_id,
            name=payload.name,
            window_seconds=payload.window_seconds,
        )
    except WindowParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SliceTaskNotAllowedError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    if task is None:
        raise HTTPException(status_code=404, detail="Package not found")

    task = service.get_by_id(task_id=task.id)
    if task is None:
        raise HTTPException(status_code=404, detail="Slice task not found")

    return _to_detail_response(task)


@router.get("/packages/{package_id}/slice-tasks", response_model=SliceTaskListResponse)
def list_slice_tasks(package_id: int, db: Session = Depends(get_db)) -> SliceTaskListResponse:
    service = SliceTaskService(db)
    package, tasks = service.list_for_package(package_id=package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="Package not found")

    return SliceTaskListResponse(items=[_to_summary_response(task) for task in tasks], total=len(tasks))


@router.get("/slice-tasks/{task_id}", response_model=SliceTaskDetailResponse)
def get_slice_task(task_id: int, db: Session = Depends(get_db)) -> SliceTaskDetailResponse:
    service = SliceTaskService(db)
    task = service.get_by_id(task_id=task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Slice task not found")
    return _to_detail_response(task)


@router.delete("/slice-tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_slice_task(task_id: int, db: Session = Depends(get_db)) -> None:
    service = SliceTaskService(db)
    task = service.delete(task_id=task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Slice task not found")
    return None


def _to_summary_response(task) -> SliceTaskSummaryResponse:
    windows_count = len(getattr(task, "windows", []) or [])
    return SliceTaskSummaryResponse(
        id=task.id,
        package_id=task.package_id,
        name=task.name,
        window_seconds=task.window_seconds,
        status=task.status,
        total_files=task.total_files,
        total_lines=task.total_lines,
        total_windows=task.total_windows,
        error_message=task.error_message,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        windows_count=windows_count,
    )


def _to_detail_response(task) -> SliceTaskDetailResponse:
    windows = sorted(task.windows, key=lambda row: (row.window_start_ts, row.id))
    return SliceTaskDetailResponse(
        **_to_summary_response(task).model_dump(),
        windows=[
            SliceWindowSummary(
                id=row.id,
                window_start_ts=row.window_start_ts,
                window_end_ts=row.window_end_ts,
                segment_title=getattr(row, "segment_title", None),
                line_count=row.line_count,
                file_count=row.file_count,
                cpu_count=row.cpu_count,
                module_count=row.module_count,
                created_at=row.created_at,
            )
            for row in windows
        ],
    )
