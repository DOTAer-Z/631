"""
annotated_run_service.py

职责（集成方案 A / Approach 1：两库分离 + HTTP 桥接）：
    接收标注子系统推来的「某一已标注窗口」，把它落成主系统的一条 runs 记录
    + 一批 log_entries 文档（mongo_compat / PG-JSONB），从而使该窗口能像
    nuttx_Test_2000_round_2 一样出现在「日志选择」，并被诊断 / 预测 / RAG 分析。

写入原则上复用既有链路的文档结构（log_ingest_service / ingest_dataset）：
    - runs 行：与 ingest_dataset.upsert_run 相同的字段集（run_id / case_id /
      test_name / run_name / is_fault / stats_json ...）
    - log_entries 文档：与 log_ingest_service 相同字段，但原始行已由标注侧解析好，
      故按「原文透传」落库（parse_status="raw_imported"），不再自行 split / 重解析。

约束：
    - 只写 fault_diagnosis（主库），不依赖运行中的 ingest / 导入链路。
    - 幂等：同一 run_id 重复推送 = 先清旧文档，再写新行（upsert run）。
    - 时间戳：标注侧给 UTC 秒级 epoch float，这里换算成 UTC naive datetime，
      与 log_ingest_service 存进 log_entries 的表达一致。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.run import Run
from app.models.case import Case
from app.models.system import System
from app.schemas.log_analysis import AnnotatedRunImportRequest

logger = logging.getLogger(__name__)

# MongoDB 集合名（与 log_ingest_service 一致）
_ENTRIES_COLLECTION = "log_entries"
_RESULTS_COLLECTION = "log_analysis_results"

# is_error_like 判定（透传行无 level，用关键词兜底；与 _is_error_like 同规则）
_ERROR_LIKE_RE = re.compile(
    r"exception|traceback|failed|timeout|refused|unavailable|panic",
    re.IGNORECASE,
)


def _epoch_to_utc(ts: Optional[float]) -> Optional[datetime]:
    """标注侧秒级 epoch float → UTC naive datetime（与 log_ingest_service 一致）。"""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def _is_error_like(raw_line: str) -> bool:
    return bool(_ERROR_LIKE_RE.search(raw_line or ""))


def _record_analysis_result(
    mongo_db: Any,
    *,
    run_id: str,
    source_type: str,
    filename: str,
    has_fault: bool,
    fault_score: float,
) -> None:
    """写入一条 log_analysis_results 文档（结构对齐既有 /log-analysis/parse/log 链路）。

    当 run 由标注子系统推送而来时，用人工标注的 has_fault 作为判定结果，使该 run
    在「日志分析结果」列表里按正常/异常分流到预测预警或故障诊断。幂等：先删后写。
    """
    doc = {
        "run_id": run_id,
        "source_type": source_type,
        "filename": filename,
        "api_url": None,
        "analyzed_at": datetime.now(),
        "has_fault": bool(has_fault),
        "fault_score": round(float(fault_score), 2),
        "result": {
            "run_id": run_id,
            "filename": filename,
            "has_fault": bool(has_fault),
            "fault_score": round(float(fault_score), 2),
            "source": "annotation",
        },
    }
    mongo_db[_RESULTS_COLLECTION].delete_many({"run_id": run_id})
    mongo_db[_RESULTS_COLLECTION].insert_one(doc)


def _ensure_system(db: Session, system_id: Optional[str]) -> Optional[str]:
    """给 system_id 一个可靠值：没有则回退到 test_name；存在则保证 systems 表有行。"""
    system_id = (system_id or "").strip() or None
    if not system_id:
        return None
    if not db.query(System).filter(System.system_id == system_id).first():
        db.add(System(system_id=system_id, name=system_id))
        db.commit()
    return system_id


def import_annotated_run(
    db: Session,
    mongo_db: Any,
    payload: AnnotatedRunImportRequest,
) -> Dict[str, Any]:
    """把一个已标注窗口导入主系统为一条 run。

    返回 {run_id, case_id, is_new, entries, message}。
    幂等：同 run_id 重复调用会覆盖（先清旧 log_entries，再 upsert run + 写新文档）。
    """
    run_id = payload.run_id.strip()
    if not run_id:
        raise ValueError("run_id 不能为空")

    # 汇总行 + 时间范围 + 失败/错误计数
    total_lines = 0
    error_like = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    # 需要写入 log_entries 的文档，先构建
    doc_lines: List[Dict[str, Any]] = []
    now_utc = datetime.now(tz=timezone.utc).replace(tzinfo=None)

    for file_block in payload.lines:
        file_path = (file_block.logical_path or "").strip() or run_id
        for line in file_block.lines:
            total_lines += 1
            ts = _epoch_to_utc(line.timestamp)
            if ts is not None:
                start_time = ts if start_time is None else min(start_time, ts)
                end_time = ts if end_time is None else max(end_time, ts)
            raw = line.content or ""
            if _is_error_like(raw):
                error_like += 1
            doc_lines.append({
                "run_id": run_id,
                "task_id": f"task::{run_id}",
                "source_type": payload.source_type or "annotation",
                "system_id": payload.system_id,
                "subsystem": payload.subsystem,
                "case_id": payload.case_id,
                "is_fault": payload.is_fault,
                "test_name": payload.test_name,
                "round_no": None,
                "file_path": file_path,
                "api_url": None,
                "local_path": None,
                "line_no": line.line_no,
                "timestamp": ts,
                "level": None,
                "module": None,
                "hostname": None,
                "process_id": None,
                "message": raw,
                "raw_line": raw,
                "parse_status": "raw_imported",
                "parse_error": None,
                "ingested_at": now_utc,
                "is_error_like": _is_error_like(raw),
                "_id": f"{run_id}::{file_path}::{line.line_no}",
            })

    # ── 幂等：清掉该 run 旧文档 ──
    mongo_db[_ENTRIES_COLLECTION].delete_many({"run_id": run_id})

    # ── case 级：upsert ──
    is_fault = bool(payload.is_fault)
    case_id = (payload.case_id or "").strip() or f"annot_{payload.test_name}"
    case = db.query(Case).filter(Case.case_id == case_id).first()
    is_new = case is None
    if case is None:
        case = Case(case_id=case_id, name=payload.test_name)
        db.add(case)
    case.case_name = payload.test_name
    case.test_name = payload.test_name
    case.is_fault = is_fault
    case.fault_type_label = payload.fault_type
    case.description = payload.description
    case.created_at = datetime.utcnow()
    db.commit()

    system_id = _ensure_system(db, payload.system_id)

    # ── run 级：upsert ──
    run = db.query(Run).filter(Run.run_id == run_id).first()
    is_new_run = run is None
    if run is None:
        run = Run(run_id=run_id, source_type="api")
        db.add(run)
    run.source_type = "api"
    run.case_id = case_id
    run.system_id = system_id
    run.subsystem = payload.subsystem
    run.is_fault = is_fault
    run.test_name = payload.test_name
    run.run_name = payload.run_name or payload.test_name
    run.round_no = 1
    run.line_count = total_lines
    run.error_logs = error_like
    run.critical_logs = 0
    run.window_count = 0
    run.log_type = "error" if error_like else "other"
    run.stats_json = {
        "parsed_lines": total_lines,
        "total_lines": total_lines,
        "level_distribution": {},
        "top_modules": [],
        "annotation_source": "annotation_subsystem",
    }
    run.start_time = start_time
    run.end_time = end_time
    run.created_at = datetime.utcnow()
    db.commit()

    # ── 写 log_entries ──
    if doc_lines:
        mongo_db[_ENTRIES_COLLECTION].insert_many(doc_lines)

    # ── 写 log_analysis_results ──
    # 让推送的 run 成为主系统「日志分析结果」的一等公民：按人工标注判定 has_fault，
    # 使它在「故障诊断」(verdict=abnormal) 与「预测预警」(verdict=normal) 的文件列表里
    # 只落入其一（与主系统的分析结果路由一致），而不是两端都出现。
    # 幂等：同 run_id 重复推送 = 先删旧分析结果再写。
    _record_analysis_result(
        mongo_db,
        run_id=run_id,
        source_type=payload.source_type or "annotation",
        filename=payload.run_name or payload.test_name or run_id,
        has_fault=is_fault,
        fault_score=(8.0 if is_fault else 0.0),
    )

    logger.info(
        "[annotated_run] imported run_id=%s case_id=%s lines=%d is_fault=%s new_run=%s",
        run_id, case_id, total_lines, is_fault, is_new_run,
    )

    return {
        "run_id": run_id,
        "case_id": case_id,
        "is_new": is_new_run or is_new,
        "entries": len(doc_lines),
        "message": f"已导入 {len(doc_lines)} 行日志到主系统 run {run_id}",
    }
