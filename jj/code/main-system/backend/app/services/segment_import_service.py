"""
segment_import_service.py — 把 segment.jsonl 非结构化标注段批量导入主系统为 runs。

来源：/home/junjiezuo/631-fault/segments.jsonl（JSON Lines）。每行一条语义段，结构：
    {
        "segment_id": "seg_5c4bd5dbd727607d6c8a_00001_c8c7f38d9fd7fc23",
        "document_id": "doc_5c4bd5dbd727607d6c8a",
        "source_kind": "synthetic",
        "source_type": "maintenance_report",
        "text": "## 基本信息\n报告编号：SYN-631-000001\n...",
        "ground_truth": {
            "label": "abnormal",          # abnormal / normal
            "fault_type": "存储空间满",     # 故障类型（多数段非空）
            "severity": "low",
            "root_cause_class": "resource",
            "resolution_status": "fixed",
        },
        "annotation": {"label": null, "fault_type": null, "note": null},
        "annotation_status": "unreviewed",
        "source_locator": {"path": "reports/SYN-631-000001.txt", "segment_index": 1, ...},
    }

设计原则：
    - 一条段 = 一个 run，run_id = segment_id（天然唯一、自描述）；case 按 document_id 归组。
    - 非结构化纯文本段无逐行时间戳：每段写 1 条 log_entries（timestamp=None），
      run.window_count = 0（与 annotated_run 处理无时间戳内容一致）。
    - fault_type 取 ground_truth.fault_type 写入 Case.fault_type_label —— 即
      「文件选择」列表 Fault Type 列直接显示该值。
    - has_fault 取 ground_truth.label == "abnormal"；额外写一条 log_analysis_results
      使该 run 按 has_fault 分流到故障诊断(abnormal) / 预测预警(normal)。
    - 幂等：同 run_id 重复导入 = 先清旧 log_entries / log_windows /
      log_analysis_results，再 upsert case + run + 写新文档。

约束：
    - 只操作 fault_diagnosis 主库（mongo_compat / PG-JSONB）。
    - 不依赖运行中的 ingest / 上传链路；不透传 LLM。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.models.case import Case
from app.models.run import Run
from app.models.system import System

logger = logging.getLogger(__name__)

SYSTEM_ID = "synthetic"
SYSTEM_NAME = "合成维护报告"

# MongoDB 集合名（与 log_ingest_service / ingest_dataset 一致）
_ENTRIES_COLLECTION = "log_entries"
_WINDOWS_COLLECTION = "log_windows"
_RESULTS_COLLECTION = "log_analysis_results"

# ground_truth.label → has_fault；"abnormal" 视为故障段
_FAULT_LABEL = "abnormal"


class SegmentImportError(RuntimeError):
    """segment 导入失败时抛出（含 segment_id 上下文）。"""


def _ensure_system(db: Session) -> str:
    if not db.query(System).filter(System.system_id == SYSTEM_ID).first():
        db.add(System(system_id=SYSTEM_ID, name=SYSTEM_NAME))
    return SYSTEM_ID


def import_segment(
    db: Session,
    mongo_db: Any,
    record: Dict[str, Any],
) -> Dict[str, Any]:
    """导入一条 segment.jsonl 段，返回单条导入统计。

    幂等：同 run_id 重复调用会先清旧 log_entries / log_windows /
    log_analysis_results 文档，再 upsert case + run + 写新文档。
    """
    segment_id = (record.get("segment_id") or "").strip()
    if not segment_id:
        raise SegmentImportError("记录缺少 segment_id 字段")

    document_id = (record.get("document_id") or "").strip()
    if not document_id:
        raise SegmentImportError(f"记录缺少 document_id 字段: {segment_id}")

    text = record.get("text") or ""
    ground_truth = record.get("ground_truth") or {}
    if not isinstance(ground_truth, dict):
        ground_truth = {}

    run_id = segment_id  # 用原始 segment_id 作为 run_id，天然唯一且自描述
    case_id = f"seg_{document_id}"

    # 故障判定：ground_truth.label == "abnormal"
    gt_label = (ground_truth.get("label") or "").strip()
    is_fault = gt_label == _FAULT_LABEL
    fault_type = (ground_truth.get("fault_type") or "").strip() or None

    source_type = (record.get("source_type") or "").strip()
    source_kind = (record.get("source_kind") or "").strip()
    locator = record.get("source_locator") or {}
    if not isinstance(locator, dict):
        locator = {}
    src_path = (locator.get("path") or "").strip()
    segment_index = locator.get("segment_index")
    raw_text = (record.get("annotation") or {}).get("note") or None

    run_name = f"seg {segment_id}"
    test_name = document_id

    # ── 幂等：先清旧文档 ──
    mongo_db[_ENTRIES_COLLECTION].delete_many({"run_id": run_id})
    mongo_db[_WINDOWS_COLLECTION].delete_many({"run_id": run_id})
    mongo_db[_RESULTS_COLLECTION].delete_many({"run_id": run_id})

    now_utc = datetime.utcnow()

    # 非结构化纯文本：整段作为 1 条 log_entry（无逐行时间戳）
    entry_doc = {
        "run_id": run_id,
        "task_id": f"task::{run_id}",
        "source_type": "dataset",
        "system_id": SYSTEM_ID,
        "subsystem": None,
        "case_id": case_id,
        "is_fault": is_fault,
        "test_name": test_name,
        "round_no": None,
        "file_path": src_path or run_id,
        "api_url": None,
        "local_path": None,
        "line_no": 1,
        "timestamp": None,
        "level": None,
        "module": None,
        "hostname": None,
        "process_id": None,
        "message": text,
        "raw_line": text,
        "parse_status": "raw_imported",
        "parse_error": None,
        "ingested_at": now_utc,
        "is_error_like": is_fault,
        "_id": f"{run_id}::1",
    }
    mongo_db[_ENTRIES_COLLECTION].insert_one(entry_doc)

    # ── case 级：upsert（fault_type 写入 fault_type_label → 文件选择 Fault Type 列）──
    case = db.query(Case).filter(Case.case_id == case_id).first()
    case_is_new = case is None
    if case is None:
        case = Case(case_id=case_id, name=test_name)
        db.add(case)
    case.case_name = test_name
    case.test_name = test_name
    case.fault_type_label = fault_type
    case.is_fault = bool(case.is_fault) or is_fault
    case.description = (
        f"从 segment.jsonl 导入（{source_type} / {source_kind}）"
        f"{f' · {src_path}' if src_path else ''}"
        f"{f' · 段 {segment_index}' if segment_index is not None else ''}"
    )
    case.created_at = now_utc

    # ── run 级：upsert ──
    run = db.query(Run).filter(Run.run_id == run_id).first()
    run_is_new = run is None
    if run is None:
        run = Run(run_id=run_id, source_type="dataset")
        db.add(run)
    run.source_type = "dataset"
    run.case_id = case_id
    run.system_id = SYSTEM_ID
    run.subsystem = None
    run.is_fault = is_fault
    run.test_name = test_name
    run.round_no = None
    run.run_name = run_name
    run.line_count = 1
    run.error_logs = 1 if is_fault else 0
    run.critical_logs = 0
    run.window_count = 0
    run.log_type = "error" if is_fault else "other"
    run.stats_json = {
        "parsed_lines": 1,
        "total_lines": 1,
        "level_distribution": {},
        "top_modules": [],
        "dataset_import_meta": {
            "display_name": run_name,
            "description": (
                f"来自 segment.jsonl · {source_type} · {source_kind}"
                f"{f' · {src_path}' if src_path else ''}"
            ),
            "tags": [t for t in (source_type, source_kind, "synthetic") if t],
            "created_by": "segment_import",
            "original_filename": "segments.jsonl",
            "import_id": "segment_jsonl",
        },
    }
    run.start_time = None
    run.end_time = None
    run.created_at = now_utc

    # (按需分析)导入时不再预写 log_analysis_results —— 只有用户点击「分析」才写。
    # run 的 is_fault / fault_type 已写在 runs/cases 表，文件选择列表仍可据此显示分类。

    # ── 登记为可训练样本（非结构化 seg_xxx，round=1，ground_truth 自带标签）──
    from app.services.jsonl_training_registry import register_training_for_jsonl
    register_training_for_jsonl(
        db,
        test_name=segment_id,
        platform=SYSTEM_ID,  # "synthetic"
        sample_class="fault" if is_fault else "normal",
        domain=source_type,
        fault_type=fault_type,
        round_no=1,
        log_content=text,
        import_id=run_id,
    )

    db.commit()

    logger.info(
        "[segment_import] run_id=%s case=%s is_fault=%s fault_type=%s new_run=%s",
        run_id, case_id, is_fault, fault_type, run_is_new,
    )

    return {
        "run_id": run_id,
        "case_id": case_id,
        "document_id": document_id,
        "is_fault": is_fault,
        "fault_type": fault_type,
        "is_new": run_is_new or case_is_new,
        "entries": 1,
        "windows": 0,
    }


def import_segment_file(
    db: Session,
    mongo_db: Any,
    jsonl_path: Path,
    *,
    limit: Optional[int] = None,
    only: Optional[Iterable[str]] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """逐条流式导入 segment.jsonl，返回累计统计与逐条（可选）结果。

    only: 只导入指定 list of segment_id / run_id，可空。
    limit: 最多导入前 N 条（调试用）。
    progress_cb: 进度回调 (processed, total)，每条成功后调用。
    cancelled: 取消检测；返回 True 时中断导入（已导入部分保留，抛 IngestCancelled）。
    """
    from app.services.jsonl_ingest_router import IngestCancelled

    import json

    records_attempted = 0
    records_ok = 0
    records_failed = 0
    fault_runs = 0
    normal_runs = 0
    failed: List[Dict[str, str]] = []
    successes: List[Dict[str, Any]] = []

    _ensure_system(db)
    only_set = set(only) if only else None

    total_records = 0
    try:
        with open(jsonl_path, "r", encoding="utf-8") as _probe:
            total_records = sum(1 for _ in _probe)
    except OSError:
        total_records = 0

    with open(jsonl_path, "r", encoding="utf-8") as fh:
        for line_no, raw_line in enumerate(fh, start=1):
            if not raw_line.strip():
                continue
            if limit is not None and records_attempted >= limit:
                break
            if cancelled is not None and cancelled():
                raise IngestCancelled(f"jsonl 导入被用户取消（{records_attempted}/{total_records}）")
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                records_failed += 1
                failed.append({"line": line_no, "error": f"json_decode: {exc}"})
                continue

            segment_id = (record.get("segment_id") or "").strip()
            if only_set is not None and segment_id not in only_set:
                continue

            records_attempted += 1
            try:
                stats = import_segment(db, mongo_db, record)
            except Exception as exc:  # 单条失败不中断整体
                records_failed += 1
                failed.append({"line": line_no, "run_id": segment_id, "error": str(exc)})
                logger.exception("[segment_import] 段失败 line=%s segment=%s", line_no, segment_id)
                continue

            records_ok += 1
            if stats["is_fault"]:
                fault_runs += 1
            else:
                normal_runs += 1
            successes.append(stats)

            if progress_cb is not None:
                try:
                    progress_cb(records_attempted, total_records)
                except Exception:
                    pass

    return {
        "attempted": records_attempted,
        "ok": records_ok,
        "failed": records_failed,
        "fault_runs": fault_runs,
        "normal_runs": normal_runs,
        "failures": failed,
        "successes": successes,
    }
