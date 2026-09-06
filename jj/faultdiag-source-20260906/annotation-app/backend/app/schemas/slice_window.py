from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.annotation import AnnotationResponse


class SliceWindowListItem(BaseModel):
    window_id: int
    window_start_ts: float
    window_end_ts: float
    # 非结构化语义段标题;非空时前端按段标题展示而非时间范围。
    segment_title: str | None = None
    record_count: int
    created_at: datetime


class SliceWindowListResponse(BaseModel):
    items: list[SliceWindowListItem]
    total: int
    page: int
    page_size: int


class SliceWindowSummaryResponse(BaseModel):
    id: int
    slice_task_id: int
    window_start_ts: float
    window_end_ts: float
    # 非结构化语义段标题;非空即表示该窗口是语义段(window_*_ts 仅为段序号)。
    segment_title: str | None = None
    line_count: int
    file_count: int
    cpu_count: int
    module_count: int
    created_at: datetime


class SliceWindowDetailResponse(SliceWindowSummaryResponse):
    pass


class SliceWindowTreeNode(BaseModel):
    name: str
    type: Literal["cpu", "module", "file"]
    path: str | None = None
    source_file_id: int | None = None
    children: list["SliceWindowTreeNode"] = []


class SliceWindowTreeResponse(BaseModel):
    window_id: int
    root: list[SliceWindowTreeNode]


class SliceWindowNavigationResponse(BaseModel):
    current_window_id: int
    prev_window_id: int | None
    next_window_id: int | None


class SliceWindowFullResponse(BaseModel):
    window: SliceWindowSummaryResponse
    tree: SliceWindowTreeResponse
    annotations: list[AnnotationResponse]
    navigation: SliceWindowNavigationResponse


class SliceWindowLogItem(BaseModel):
    id: int
    source_file_id: int
    line_no: int
    timestamp: float | None
    content: str


class SliceWindowLogsResponse(BaseModel):
    items: list[SliceWindowLogItem]
    next_cursor: str | None
    has_more: bool
