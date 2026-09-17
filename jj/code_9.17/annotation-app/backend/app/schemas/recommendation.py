from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class RecommendationResponse(BaseModel):
    window_id: int
    status: Literal["pending", "success", "failed"]
    recommended_label: Literal["normal", "abnormal"] | None
    recommended_anomaly_type: str | None
    reason: str | None
    model: str | None
    error_message: str | None
    pending_suggestion_id: int | None = None
    created_at: datetime
    updated_at: datetime


class RecommendationBatchProgressResponse(BaseModel):
    task_id: int
    total_windows: int
    success_count: int
    failed_count: int
    pending_count: int
    not_started_count: int


class WindowMultiAnalysisFileItem(BaseModel):
    source_log_file_id: int
    logical_path: str
    label: Literal["normal", "abnormal"]
    anomaly_type: str | None = None
    reason: str | None = None


class WindowMultiAnalysisResponse(BaseModel):
    window_id: int
    status: Literal["success", "failed"]
    multiple_faults: bool
    suggest_subdivide: bool
    suggested_window_seconds: int | None = None
    files: list[WindowMultiAnalysisFileItem]
    model: str | None = None
    error_message: str | None = None
