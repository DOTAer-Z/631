from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DashboardSummaryResponse(BaseModel):
    total_packages: int
    total_slice_tasks: int
    total_windows: int
    annotated_windows: int
    pending_windows: int
    normal_count: int
    abnormal_count: int
    # 按数据种类拆三桶(向后兼容,默认 0)：semi_structured=日志(时间窗口)，unstructured=报告(语义段)，
    # structured=预留(监控/追踪等固定 schema 数据)，本期恒为 0。
    structured_packages: int = 0
    semi_structured_packages: int = 0
    unstructured_packages: int = 0
    structured_windows: int = 0
    semi_structured_windows: int = 0
    unstructured_windows: int = 0


class RecentPackageItem(BaseModel):
    id: int
    name: str
    import_status: str
    created_at: datetime


class RecentPackageListResponse(BaseModel):
    items: list[RecentPackageItem]


class RecentSliceTaskItem(BaseModel):
    id: int
    package_id: int
    name: str
    status: str
    created_at: datetime


class RecentSliceTaskListResponse(BaseModel):
    items: list[RecentSliceTaskItem]


class RecentAnnotationItem(BaseModel):
    id: int
    slice_window_id: int
    label: str
    anomaly_type: str | None
    updated_at: datetime


class RecentAnnotationListResponse(BaseModel):
    items: list[RecentAnnotationItem]
