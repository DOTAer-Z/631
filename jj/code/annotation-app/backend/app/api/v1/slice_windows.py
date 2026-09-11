from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.core.errors import AppError, WindowNotFoundError
from app.schemas.slice_window import (
    SliceWindowDetailResponse,
    SliceWindowFullResponse,
    SliceWindowListItem,
    SliceWindowListResponse,
    SliceWindowLogsResponse,
    SliceWindowSummaryResponse,
    SliceWindowTreeResponse,
)
from app.services.slice_window_service import SliceWindowService
from app.services.source_log_query_service import SourceLogQueryService, WindowListFilters
from app.services.annotation_push_service import AnnotationPushService, WindowPushError

router = APIRouter(tags=["slice-windows"])


class SliceWindowSubdivideRequest(BaseModel):
    window_seconds: int = Field(..., ge=1, le=3600)


@router.get("/slice-tasks/{task_id}/windows", response_model=SliceWindowListResponse)
def list_windows(
    task_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    sort_by: str = Query(default="window_start_ts"),
    sort_order: str = Query(default="asc"),
    start_ts: float | None = Query(default=None),
    end_ts: float | None = Query(default=None),
    db: Session = Depends(get_db),
) -> SliceWindowListResponse:
    service = SourceLogQueryService(db)
    items, total = service.list_windows(
        task_id=task_id,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
        filters=WindowListFilters(start_ts=start_ts, end_ts=end_ts),
    )
    return SliceWindowListResponse(
        items=[
            SliceWindowListItem(
                window_id=row.id,
                window_start_ts=row.window_start_ts,
                window_end_ts=row.window_end_ts,
                segment_title=getattr(row, "segment_title", None),
                record_count=row.record_count,
                created_at=row.created_at,
            )
            for row in items
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/slice-windows/{window_id}", response_model=SliceWindowDetailResponse)
def get_window_detail(window_id: int, db: Session = Depends(get_db)) -> SliceWindowDetailResponse:
    return SourceLogQueryService(db).get_window_detail(window_id=window_id)


@router.get("/slice-windows/{window_id}/tree", response_model=SliceWindowTreeResponse)
def get_window_tree(window_id: int, db: Session = Depends(get_db)) -> SliceWindowTreeResponse:
    return SourceLogQueryService(db).get_window_tree(window_id=window_id)


@router.get("/slice-windows/{window_id}/full", response_model=SliceWindowFullResponse)
def get_window_full(window_id: int, db: Session = Depends(get_db)) -> SliceWindowFullResponse:
    return SourceLogQueryService(db).get_window_full(window_id=window_id)


@router.get("/slice-windows/{window_id}/logs", response_model=SliceWindowLogsResponse)
def get_window_logs(
    window_id: int,
    source_file_id: int = Query(..., ge=1),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    keyword: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> SliceWindowLogsResponse:
    return SourceLogQueryService(db).get_window_logs(
        window_id=window_id,
        source_file_id=source_file_id,
        cursor=cursor,
        limit=limit,
        keyword=keyword,
    )


@router.delete("/slice-windows/{window_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_slice_window(window_id: int, db: Session = Depends(get_db)) -> None:
    """Delete an unannotated slice window (typically used to discard empty / noise
    windows from the pending list). Refuses if the window already has an annotation."""
    SliceWindowService(db).delete(window_id=window_id)
    return None


@router.post(
    "/slice-windows/{window_id}/subdivide",
    response_model=list[SliceWindowSummaryResponse],
    status_code=status.HTTP_201_CREATED,
)
def subdivide_slice_window(
    window_id: int,
    payload: SliceWindowSubdivideRequest,
    db: Session = Depends(get_db),
) -> list[SliceWindowSummaryResponse]:
    """Subdivide a window into finer sub-windows; the original window and its
    annotations are preserved and the new children are returned."""
    children = SliceWindowService(db).subdivide(
        window_id=window_id, window_seconds=payload.window_seconds
    )
    return [
        SliceWindowSummaryResponse(
            id=child.id,
            slice_task_id=child.slice_task_id,
            window_start_ts=child.window_start_ts,
            window_end_ts=child.window_end_ts,
            line_count=child.line_count,
            file_count=child.file_count,
            cpu_count=child.cpu_count,
            module_count=child.module_count,
            created_at=child.created_at,
        )
        for child in children
    ]


@router.post("/slice-windows/{window_id}/push")
def push_window_to_main(
    window_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """把该窗口（含其标注）推送到主系统，落成一条 run + log_entries。

    Approach 1（两库分离 + HTTP 桥接）：推送后窗口按 run_id=annot_pkg{pid}_win{wid}
    出现在主系统「文件选择」，可被诊断 / 预测 / RAG 分析。重复推送 = 主系统侧覆盖（幂等）。
    主系统不可达 / 校验拒绝统一抛 WindowPushError（502）；窗口不存在抛 WindowNotFoundError（404）。
    """
    try:
        return AnnotationPushService(db).push_window_to_main(window_id=window_id)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise WindowPushError(f"推送标注窗口失败: {exc}") from exc
