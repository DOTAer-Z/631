from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.services.slice_engine import (
    DEFAULT_WINDOW_SECONDS,
    MAX_WINDOW_SECONDS,
    MIN_WINDOW_SECONDS,
)


class SliceTaskCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    window_seconds: int = Field(
        default=DEFAULT_WINDOW_SECONDS,
        ge=MIN_WINDOW_SECONDS,
        le=MAX_WINDOW_SECONDS,
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("name cannot be empty")
        return trimmed


class SliceWindowSummary(BaseModel):
    id: int
    window_start_ts: float
    window_end_ts: float
    # 非结构化语义段标题;非空时前端按段标题展示而非时间范围。
    segment_title: str | None = None
    line_count: int
    file_count: int
    cpu_count: int
    module_count: int
    created_at: datetime


class SliceTaskSummaryResponse(BaseModel):
    id: int
    package_id: int
    name: str
    window_seconds: int
    status: str
    total_files: int
    total_lines: int
    total_windows: int
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    windows_count: int


class SliceTaskDetailResponse(SliceTaskSummaryResponse):
    windows: list[SliceWindowSummary] = Field(default_factory=list)


class SliceTaskListResponse(BaseModel):
    items: list[SliceTaskSummaryResponse]
    total: int
