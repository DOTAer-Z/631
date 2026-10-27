"""
jsonl_ingest_router.py — 识别数据导入压缩包里的「带标签 JSONL」并分发给对应导入器。

背景:
    数据导入目前只走 `run_ingest_pipeline`(NuttX Test_* 目录 → training 平台)。
    现在要支持两类**已经标好标签**的 JSONL 压缩包,上传后直接进「文件选择」列表、
    可分析/查看/编辑/删除,但**不进入训练平台计数、不进入标注库**:

    - struct.jsonl    结构化日志(字段 id/round/log_type/text) → structured_log_import_service
    - notstruct.jsonl 非结构化语义段(segment_id/document_id/ground_truth/text)
                     → segment_import_service

识别方式(方案 B,内容探测):解压后扫描目录里的 .jsonl,按**前 N 行的字段 schema** 判定类型。
    结构化:  记录含 "id" 且 "round",并有 "text"(日志正文)
    非结构化:记录含 "segment_id" 且 "text",并(通常)有 "ground_truth"
    两者都不含 → 返回 None,由调用方回落到现有 NuttX 管线(仍报"未发现 Test_* 目录")。

设计:
    - 不解读 JSON 内容之外的东西;识别错 → 走既有错误分支,不会写脏数据。
    - 探测只读少量行,不整包读入内存。
    - 生命周期:调用方负责 db/mongo_db 的生命周期;本模块只做「解压目录 → 导入器」的调度。
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.jsonl_ingest_task import JsonlIngestTask
from app.services.segment_import_service import import_segment_file
from app.services.structured_log_import_service import import_jsonl_file

logger = logging.getLogger(__name__)

# 判定类型标识
FORMAT_STRUCTURED = "structured"
FORMAT_SEGMENT = "segment"

# 探测时最多读取的行数
_PROBE_LINES = 50

# 单个压缩包里只认「结构化」+「非结构化」各一个文件;超过则报错,避免歧义。
# 允许命名形如 struct.jsonl / notstruct.jsonl / *_struct.jsonl 等。
_STRUCTURED_MARKERS = ("struct",)
_SEGMENT_MARKERS = ("segment", "notstruct", "non_struct", "unstruct")


def _jsonl_files(root: Path) -> List[Path]:
    return sorted(
        p
        for p in root.rglob("*.jsonl")
        if p.is_file()
    )


def _probe_kind(path: Path) -> Optional[str]:
    """读前 _PROBE_LINES 行,按字段 schema 判定该 jsonl 的类型。

    返回 FORMAT_STRUCTURED / FORMAT_SEGMENT / None(无法判定)。"""
    has_id = False
    has_round = False
    has_segment_id = False
    seen_text = False
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line_no, raw in enumerate(fh, start=1):
            if line_no > _PROBE_LINES:
                break
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            keys = rec.keys()
            if "segment_id" in keys:
                has_segment_id = True
            if "id" in keys:
                has_id = True
            if "round" in keys:
                has_round = True
            if "text" in keys:
                seen_text = True
            # 判定收敛:两种特征都见到,足够定夺
            if has_segment_id and seen_text:
                return FORMAT_SEGMENT
            if has_id and has_round and seen_text:
                return FORMAT_STRUCTURED

    # 全文(实际是前 N 行)扫完仍未收敛,按已见特征兜底
    if has_segment_id and seen_text:
        return FORMAT_SEGMENT
    if has_id and has_round and seen_text:
        return FORMAT_STRUCTURED
    return None


class JsonlIngestError(RuntimeError):
    """JSONL 识别/导入失败时抛出(含可读 message,由调用方记录到 dataset_imports.ingest_error)。"""


def detect_jsonl_formats(work_dir: Path) -> Dict[str, Path]:
    """扫描 work_dir 下的 .jsonl,返回 {format: path}。

    规则:
        - 只允许 structured / segment 各至多一个文件;
        - 同名冲突(两个都命名为 struct.jsonl / notstruct.jsonl)时按名称优先,再按内容探测;
        - 无任何可识别 jsonl → 返回空 dict(调用方走 NuttX 管线)。
    """
    files = _jsonl_files(work_dir)
    if not files:
        return {}

    found: Dict[str, Path] = {}
    for path in files:
        kind = _probe_kind(path)
        if kind is None:
            continue
        if kind in found:
            raise JsonlIngestError(
                f"压缩包里存在多个 {kind} 类型的 jsonl:{found[kind].name} 与 {path.name};"
                "请每个压缩包只放一个该类型文件"
            )
        found[kind] = path

    return found


class IngestCancelled(RuntimeError):
    """用户主动取消 jsonl 导入;已导入部分保留(幂等,下次重导覆盖)。"""


def run_jsonl_ingest(
    db: Session,
    mongo_db: Any,
    work_dir: Path,
    formats: Dict[str, Path],
    *,
    window_size_s: int = 30,
    stride_s: int = 15,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """对识别到的 jsonl 执行导入,返回统一统计(供 dataset_imports 落库)。

    逐条导入器内部已做幂等(同 run_id 先删旧文档再写)。
    progress_cb: 进度回调 (processed, total),跨两个导入器累计调用。
    cancelled: 取消检测;返回 True 时抛 IngestCancelled,已导入部分保留。
    """
    summary: Dict[str, Any] = {
        "case_ids": [],
        "run_count": 0,
        "entry_count": 0,
        "window_count": 0,
        "new_run_count": 0,
        "updated_run_count": 0,
        "new_case_count": 0,
        "updated_case_count": 0,
        "imported_formats": [],
        "failures": [],
    }
    accumulated = {"processed": 0, "total": 0}

    def _emit_progress(processed: int, total: int) -> None:
        accumulated["processed"] = max(accumulated["processed"], processed)
        accumulated["total"] = max(accumulated["total"], total)
        if progress_cb is not None:
            try:
                progress_cb(accumulated["processed"], accumulated["total"])
            except Exception:
                pass

    if FORMAT_STRUCTURED in formats:
        path = formats[FORMAT_STRUCTURED]
        logger.info("[jsonl_ingest] 结构化 jsonl 导入: %s", path.name)
        res = import_jsonl_file(
            db, mongo_db, path,
            window_size_s=window_size_s, stride_s=stride_s,
            progress_cb=_emit_progress,
            cancelled=cancelled,
        )
        summary["imported_formats"].append(FORMAT_STRUCTURED)
        summary["run_count"] += res.get("ok", 0)
        summary["entry_count"] += res.get("entries", 0)
        summary["window_count"] += res.get("windows", 0)
        summary["new_run_count"] += res.get("ok", 0)
        summary["failures"].extend(res.get("failures", []))
        for s in res.get("successes", []):
            if s.get("case_id"):
                summary["case_ids"].append(s["case_id"])

    if FORMAT_SEGMENT in formats:
        path = formats[FORMAT_SEGMENT]
        logger.info("[jsonl_ingest] 非结构化段 jsonl 导入: %s", path.name)
        res = import_segment_file(db, mongo_db, path, progress_cb=_emit_progress, cancelled=cancelled)
        summary["imported_formats"].append(FORMAT_SEGMENT)
        summary["run_count"] += res.get("ok", 0)
        summary["entry_count"] += res.get("ok", 0)
        summary["window_count"] += 0
        summary["new_run_count"] += res.get("ok", 0)
        summary["failures"].extend(res.get("failures", []))
        for s in res.get("successes", []):
            if s.get("case_id"):
                summary["case_ids"].append(s["case_id"])

    summary["new_case_count"] = len(set(summary["case_ids"]))
    return summary


def create_jsonl_task(db: Session, *, import_id: str, format_label: str) -> JsonlIngestTask:
    """创建一个 jsonl 导入任务行(queued),返回 ORM 对象。"""
    task = JsonlIngestTask(
        task_id=uuid.uuid4().hex,
        import_id=import_id,
        format=format_label or None,
        state="queued",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def update_jsonl_task(
    db: Session,
    task: JsonlIngestTask,
    *,
    state: str,
    processed: Optional[int] = None,
    ok: Optional[int] = None,
    failed: Optional[int] = None,
    error: Optional[str] = None,
    total: Optional[int] = None,
) -> JsonlIngestTask:
    """更新任务状态与进度,落库。

    取消保护:任务已被用户取消(cancelled)时,进度回调不得把它改回 running;
    仅允许终态成功(cancelled/error/success)自行回落。
    """
    current = task.state
    # 用户已取消;进度回调想把 running 写回 → 保持 cancelled 不变,只更新计数
    if current == "cancelled" and state == "running":
        state = "cancelled"
    task.state = state
    if total is not None:
        task.total_records = total
    if processed is not None:
        task.processed_records = processed
    if ok is not None:
        task.ok_records = ok
    if failed is not None:
        task.failed_records = failed
    if error is not None:
        task.error = error
    if state in ("success", "cancelled", "error"):
        task.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(task)
    return task
