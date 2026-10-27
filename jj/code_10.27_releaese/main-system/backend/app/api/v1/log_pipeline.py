"""
log_pipeline.py

调试接口：POST /log-pipeline/run

完整跑通以下链路：
    raw log → preprocess → ingest (log_entries) → build-windows (log_windows)

不接 retrieval / KG / LLM，不对前端暴露。
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, UploadFile, File

from app.database import get_mongo_db
from app.schemas.log_pipeline import PipelineRunOut
from app.services.preprocessing_service import preprocessing_service
from app.services.log_ingest_service import RunMeta, log_ingest_service
from app.services.log_window_service import log_window_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/log-pipeline/run",
    response_model=PipelineRunOut,
    summary="[调试] 原始日志 → preprocess → ingest → build-windows",
    tags=["调试"],
)
async def run_log_pipeline(
    # ── 日志文件 ────────────────────────────────────────────────────────────
    file: UploadFile = File(..., description="原始日志文件"),

    # ── run_meta ─────────────────────────────────────────────────────────────
    run_id:      Optional[str]  = Form(default=None,     description="不传则自动生成"),
    source_type: str            = Form(default="upload", description="dataset|upload|api"),
    is_fault:    Optional[bool] = Form(default=None),
    task_id:     Optional[str]  = Form(default=None),
    system_id:   Optional[str]  = Form(default=None),
    subsystem:   Optional[str]  = Form(default=None),
    case_id:     Optional[str]  = Form(default=None),
    test_name:   Optional[str]  = Form(default=None),
    round_no:    Optional[int]  = Form(default=None),

    # ── preprocess 参数 ───────────────────────────────────────────────────────
    max_lines: Optional[int] = Form(default=None, description="截断行数，不传则不截断"),

    # ── window 参数 ───────────────────────────────────────────────────────────
    window_size_s:   int = Form(default=30,  ge=1,  description="窗口时长（秒）"),
    stride_s:        int = Form(default=15,  ge=1,  description="滑动步长（秒）"),
    min_entries:     int = Form(default=1,   ge=1,  description="窗口最少条目数"),
    key_events_topn: int = Form(default=10,  ge=1,  description="每窗口保留的关键事件数"),
) -> PipelineRunOut:
    """
    完整日志处理链路联调接口。

    依次执行：
    1. **Preprocess**：编码识别 → BOM 去除 → 换行统一 → splitlines → 格式检测
    2. **Ingest**：三级正则解析 → 批量写入 MongoDB `log_entries`
    3. **Build-Windows**：时间滑窗 → 写入 MongoDB `log_windows`

    所有步骤失败均返回 HTTP 500，并附带出错阶段说明。
    """
    t0 = time.time()

    # ── 生成 run_id ───────────────────────────────────────────────────────────
    effective_run_id = (run_id or "").strip() or uuid.uuid4().hex
    logger.info(
        "[pipeline] START run_id=%s file=%s source_type=%s",
        effective_run_id,
        file.filename,
        source_type,
    )

    # ── Step 1: Preprocess ───────────────────────────────────────────────────
    try:
        raw_bytes = await file.read()
        preprocessed = preprocessing_service.preprocess(
            raw_log=raw_bytes,
            source_type=source_type,
            filename=file.filename,
            max_lines=max_lines,
        )
    except Exception as exc:
        logger.error("[pipeline] preprocess FAILED run_id=%s: %s", effective_run_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"preprocess failed: {exc}") from exc

    logger.info(
        "[pipeline] preprocess OK lines=%d encoding=%s format=%s truncated=%s",
        preprocessed.line_count,
        preprocessed.encoding,
        preprocessed.detected_format,
        preprocessed.truncated,
    )

    # ── Step 2: Ingest ───────────────────────────────────────────────────────
    run_meta = RunMeta(
        run_id=effective_run_id,
        task_id=task_id,
        source_type=source_type,
        system_id=system_id,
        subsystem=subsystem,
        case_id=case_id,
        is_fault=is_fault,
        test_name=test_name,
        round_no=round_no,
    )

    try:
        mongo_db = get_mongo_db()
        ingest_result = log_ingest_service.ingest(
            preprocessed_log=preprocessed,
            run_meta=run_meta,
            mongo_db=mongo_db,
        )
    except Exception as exc:
        logger.error("[pipeline] ingest FAILED run_id=%s: %s", effective_run_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"ingest failed: {exc}") from exc

    logger.info(
        "[pipeline] ingest OK ok=%d failed=%d skipped=%d inserted=%d",
        ingest_result.parsed_ok,
        ingest_result.parse_failed,
        ingest_result.skipped,
        ingest_result.inserted_count,
    )

    # ── Step 3: Build Windows ─────────────────────────────────────────────────
    try:
        window_result = log_window_service.build_windows(
            run_meta=run_meta,
            mongo_db=mongo_db,
            window_size_s=window_size_s,
            stride_s=stride_s,
            min_entries=min_entries,
            key_events_topn=key_events_topn,
        )
    except Exception as exc:
        logger.error("[pipeline] build_windows FAILED run_id=%s: %s", effective_run_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"build_windows failed: {exc}") from exc

    logger.info(
        "[pipeline] windows OK entries=%d windows=%d inserted=%d",
        window_result.total_entries,
        window_result.window_count,
        window_result.inserted_count,
    )

    elapsed_s = round(time.time() - t0, 3)
    logger.info("[pipeline] DONE run_id=%s elapsed=%.3fs", effective_run_id, elapsed_s)

    # ── 组装响应 ──────────────────────────────────────────────────────────────
    return PipelineRunOut(
        run_id=effective_run_id,
        preprocess={
            "encoding":        preprocessed.encoding,
            "detected_format": preprocessed.detected_format,
            "line_count":      preprocessed.line_count,
            "char_count":      preprocessed.char_count,
            "truncated":       preprocessed.truncated,
            "truncate_at":     preprocessed.truncate_at,
        },
        ingest={
            "total_lines":        ingest_result.total_lines,
            "parsed_ok":          ingest_result.parsed_ok,
            "parse_failed":       ingest_result.parse_failed,
            "skipped":            ingest_result.skipped,
            "inserted_count":     ingest_result.inserted_count,
            "start_time":         ingest_result.start_time,
            "end_time":           ingest_result.end_time,
            "level_distribution": ingest_result.level_distribution,
        },
        windows={
            "total_entries":  window_result.total_entries,
            "window_count":   window_result.window_count,
            "inserted_count": window_result.inserted_count,
            "window_size_s":  window_size_s,
            "stride_s":       stride_s,
        },
        elapsed_s=elapsed_s,
    )
