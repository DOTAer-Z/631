"""
log_pipeline.py — Pydantic schemas for POST /log-pipeline/run

仅供调试接口使用，不对外暴露给前端。
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from pydantic import BaseModel


# =============================================================================
# 响应子模型
# =============================================================================


class PreprocessSummary(BaseModel):
    encoding: str
    detected_format: str          # "plain" | "structured" | "syslog"
    line_count: int
    char_count: int
    truncated: bool
    truncate_at: Optional[int]


class IngestSummary(BaseModel):
    total_lines: int
    parsed_ok: int
    parse_failed: int
    skipped: int
    inserted_count: int
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    level_distribution: Dict[str, int]


class WindowSummary(BaseModel):
    total_entries: int
    window_count: int
    inserted_count: int
    window_size_s: int
    stride_s: int


# =============================================================================
# 顶层响应
# =============================================================================


class PipelineRunOut(BaseModel):
    run_id: str
    preprocess: PreprocessSummary
    ingest: IngestSummary
    windows: WindowSummary
    elapsed_s: float
