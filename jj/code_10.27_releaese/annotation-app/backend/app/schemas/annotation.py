from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

LabelType = Literal["normal", "abnormal"]
ExportScope = Literal["current_filter", "all"]
ExportMode = Literal["light", "full"]


class AnnotationCreateRequest(BaseModel):
    label: LabelType
    anomaly_type: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=20000)
    # None => whole-window annotation; a source file id => file-level annotation.
    source_log_file_id: int | None = None

    @field_validator("anomaly_type")
    @classmethod
    def _normalize_anomaly_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("note")
    @classmethod
    def _normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AnnotationUpdateRequest(BaseModel):
    label: LabelType
    anomaly_type: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=20000)

    @field_validator("anomaly_type")
    @classmethod
    def _normalize_anomaly_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("note")
    @classmethod
    def _normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AnnotationResponse(BaseModel):
    id: int
    slice_window_id: int
    source_log_file_id: int | None = None
    # Human-readable binding target: the file's logical_path, or None for whole-window.
    binding_label: str | None = None
    label: LabelType
    anomaly_type: str | None
    note: str | None
    created_at: datetime
    updated_at: datetime


class AnnotationListItem(BaseModel):
    id: int
    package_id: int
    task_id: int
    window_id: int
    window_start_ts: float
    window_end_ts: float
    label: LabelType
    anomaly_type: str | None
    note: str | None
    created_at: datetime
    updated_at: datetime


class AnnotationListResponse(BaseModel):
    items: list[AnnotationListItem]
    total: int
    page: int
    page_size: int


class AnnotationStatsResponse(BaseModel):
    total_annotations: int
    normal_count: int
    abnormal_count: int


class PendingAnnotationItem(BaseModel):
    package_id: int
    package_name: str | None = None
    task_id: int
    task_name: str | None = None
    window_id: int
    window_start_ts: float
    window_end_ts: float
    line_count: int = 0
    file_count: int = 0
    cpu_count: int = 0
    module_count: int = 0


class PendingAnnotationListResponse(BaseModel):
    items: list[PendingAnnotationItem]
    total: int
    page: int
    page_size: int


class AnnotationWorkbenchItem(BaseModel):
    annotation_id: int
    package_id: int
    task_id: int
    window_id: int
    window_start_ts: float
    window_end_ts: float
    label: LabelType
    anomaly_type: str | None
    note: str | None
    updated_at: datetime


class AnnotationWorkbenchResponse(BaseModel):
    items: list[AnnotationWorkbenchItem]
    total: int
    page: int
    page_size: int


class AnnotationExportResponse(BaseModel):
    format: Literal["csv", "json"]
    scope: ExportScope
    mode: ExportMode
    file_path: str
    file_name: str
    download_url: str
    item_count: int


class AnnotationExportLightItem(BaseModel):
    package_id: int
    package_name: str
    task_id: int
    task_name: str
    window_id: int
    window_start_at: str
    window_end_at: str
    line_count: int
    file_count: int
    cpu_count: int
    module_count: int
    annotation: dict[str, Any]
    window_context: dict[str, Any]
