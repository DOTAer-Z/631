from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PackageResponse(BaseModel):
    id: int
    name: str
    archive_type: str
    stored_path: str
    file_size: int
    sha256: str
    description: str | None
    import_status: str
    import_error_message: str | None
    # 数据种类(三值):'semi_structured'=日志(时间窗切片)| 'unstructured'=非结构化报告(按 `## ` 分段)
    # | 'structured'=预留(监控/追踪等固定 schema 数据)。
    data_kind: str = "semi_structured"
    source_file_count: int
    source_line_count: int
    cpu_count: int
    module_count: int
    earliest_timestamp: float | None
    latest_timestamp: float | None
    created_at: datetime
    updated_at: datetime
    slice_task_count: int = 0
    has_abnormal: bool = False


class PackageUploadAcceptedResponse(PackageResponse):
    import_task_id: int


class PackageListResponse(BaseModel):
    items: list[PackageResponse]
    total: int
    page: int
    page_size: int


class PackageUpdateRequest(BaseModel):
    description: str | None = Field(default=None, max_length=10000)
