from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.schemas.annotation import (
    AnnotationCreateRequest,
    AnnotationExportResponse,
    AnnotationListItem,
    AnnotationListResponse,
    AnnotationResponse,
    AnnotationStatsResponse,
    AnnotationUpdateRequest,
    AnnotationWorkbenchItem,
    AnnotationWorkbenchResponse,
    PendingAnnotationItem,
    PendingAnnotationListResponse,
)
from app.services.annotation_service import AnnotationQueryFilters, AnnotationService

router = APIRouter(tags=["annotations"])


def _to_response(row) -> AnnotationResponse:
    return AnnotationResponse(
        id=row.id,
        slice_window_id=row.slice_window_id,
        source_log_file_id=row.source_log_file_id,
        label=row.label,
        anomaly_type=row.anomaly_type,
        note=row.note,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _normalize_export_mode(mode: str | object = "light") -> str:
    if isinstance(mode, str):
        return mode
    return "light"


@router.post("/slice-windows/{window_id}/annotation", response_model=AnnotationResponse, status_code=status.HTTP_201_CREATED)
def create_annotation(
    window_id: int,
    payload: AnnotationCreateRequest,
    db: Session = Depends(get_db),
) -> AnnotationResponse:
    service = AnnotationService(db)
    row = service.save_for_window(window_id=window_id, payload=payload)
    return _to_response(row)


@router.get("/slice-windows/{window_id}/annotation", response_model=AnnotationResponse)
def get_annotation_for_window(window_id: int, db: Session = Depends(get_db)) -> AnnotationResponse:
    service = AnnotationService(db)
    row = service.get_by_window(window_id=window_id)
    return _to_response(row)


@router.patch("/annotations/{annotation_id}", response_model=AnnotationResponse)
def update_annotation(
    annotation_id: int,
    payload: AnnotationUpdateRequest,
    db: Session = Depends(get_db),
) -> AnnotationResponse:
    service = AnnotationService(db)
    row = service.update(annotation_id=annotation_id, payload=payload)
    return _to_response(row)


@router.delete("/annotations/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_annotation(annotation_id: int, db: Session = Depends(get_db)) -> None:
    service = AnnotationService(db)
    service.delete(annotation_id=annotation_id)
    return None


@router.get("/annotations", response_model=AnnotationListResponse)
def list_annotations(
    package_id: int | None = Query(default=None),
    task_id: int | None = Query(default=None),
    label: str | None = Query(default=None),
    anomaly_type: str | None = Query(default=None),
    start_ts: float | None = Query(default=None),
    end_ts: float | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    sort_by: str = "updated_at",
    sort_order: str = "desc",
    db: Session = Depends(get_db),
) -> AnnotationListResponse:
    service = AnnotationService(db)
    items, total = service.query(
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
        filters=AnnotationQueryFilters(
            package_id=package_id,
            task_id=task_id,
            label=label,
            anomaly_type=anomaly_type,
            start_ts=start_ts,
            end_ts=end_ts,
        ),
    )
    return AnnotationListResponse(
        items=[AnnotationListItem(**item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/annotations/export-cases")
def export_annotation_cases(
    anomaly_type: str | None = Query(default=None),
    package_id: int | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """导出典型异常标注案例（供主系统「从标注典型案例导入」拉取入知识库/图谱）。"""
    items = AnnotationService(db).export_cases(
        anomaly_type=anomaly_type, package_id=package_id, limit=limit
    )
    return {"items": items, "total": len(items)}


@router.get("/annotations/stats", response_model=AnnotationStatsResponse)
def get_annotation_stats(
    package_id: int | None = Query(default=None),
    task_id: int | None = Query(default=None),
    label: str | None = Query(default=None),
    anomaly_type: str | None = Query(default=None),
    start_ts: float | None = Query(default=None),
    end_ts: float | None = Query(default=None),
    db: Session = Depends(get_db),
) -> AnnotationStatsResponse:
    return AnnotationService(db).stats(
        filters=AnnotationQueryFilters(
            package_id=package_id,
            task_id=task_id,
            label=label,
            anomaly_type=anomaly_type,
            start_ts=start_ts,
            end_ts=end_ts,
        )
    )


@router.get("/annotations/pending", response_model=PendingAnnotationListResponse)
def get_annotation_pending(
    package_id: int | None = None,
    task_id: int | None = None,
    start_ts: float | None = None,
    end_ts: float | None = None,
    min_line_count: int | None = None,
    max_line_count: int | None = None,
    keyword: str | None = None,
    sort_by: str = "window_start_ts",
    sort_order: str = "asc",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> PendingAnnotationListResponse:
    items, total = AnnotationService(db).pending(
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
        filters=AnnotationQueryFilters(
            package_id=package_id,
            task_id=task_id,
            start_ts=start_ts,
            end_ts=end_ts,
            min_line_count=min_line_count,
            max_line_count=max_line_count,
            keyword=keyword.strip() if keyword else None,
        ),
    )
    return PendingAnnotationListResponse(
        items=[PendingAnnotationItem(**item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/annotations/workbench", response_model=AnnotationWorkbenchResponse)
def get_annotation_workbench(
    package_id: int | None = None,
    task_id: int | None = None,
    label: str | None = None,
    anomaly_type: str | None = None,
    start_ts: float | None = None,
    end_ts: float | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> AnnotationWorkbenchResponse:
    items, total = AnnotationService(db).workbench(
        page=page,
        page_size=page_size,
        filters=AnnotationQueryFilters(
            package_id=package_id,
            task_id=task_id,
            label=label,
            anomaly_type=anomaly_type,
            start_ts=start_ts,
            end_ts=end_ts,
        ),
    )
    return AnnotationWorkbenchResponse(
        items=[AnnotationWorkbenchItem(**item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/annotations/export", response_model=AnnotationExportResponse)
def export_annotations(
    format: str = Query(..., pattern="^(csv|json)$"),
    scope: str = Query(default="current_filter", pattern="^(current_filter|all)$"),
    mode: str = Query(default="light", pattern="^(light|full)$"),
    package_id: int | None = Query(default=None),
    task_id: int | None = Query(default=None),
    label: str | None = Query(default=None),
    anomaly_type: str | None = Query(default=None),
    start_ts: float | None = Query(default=None),
    end_ts: float | None = Query(default=None),
    db: Session = Depends(get_db),
) -> AnnotationExportResponse:
    normalized_mode = _normalize_export_mode(mode)
    result = AnnotationService(db).export(
        export_format=format,
        scope=scope,  # type: ignore[arg-type]
        export_mode=normalized_mode,  # type: ignore[arg-type]
        filters=AnnotationQueryFilters(
            package_id=package_id,
            task_id=task_id,
            label=label,
            anomaly_type=anomaly_type,
            start_ts=start_ts,
            end_ts=end_ts,
        ),
    )
    # download_url is intentionally a relative path (e.g. "/api/v1/annotations/export/download?file=…").
    # The frontend resolves it against window.location.origin via resolveApiUrl, so it works behind
    # any reverse proxy (Docker compose, K8s gateway/ingress) without leaking cluster-internal hosts.
    return AnnotationExportResponse(
        format=result.format,
        scope=result.scope,
        mode=result.mode,  # type: ignore[arg-type]
        file_path=result.file_path,
        file_name=result.file_name,
        download_url=result.download_url,
        item_count=result.item_count,
    )


@router.get("/annotations/export/download")
def download_annotation_export(file: str, db: Session = Depends(get_db)) -> FileResponse:
    path = AnnotationService(db).get_export_file_path(file_name=file)
    return FileResponse(
        path=path,
        filename=path.name,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )
