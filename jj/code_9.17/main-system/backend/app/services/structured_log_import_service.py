"""
structured_log_import_service.py — 把 test.jsonl 结构化数据批量导入主系统为 runs。

来源：/home/junjiezuo/631-fault/test.jsonl（JSON Lines）。每行一条记录，结构：
    {
        "id": "Test_50005:round_1:qemu_console",
        "log_type": "qemu_console",
        "round": "round_1",
        "runtime_environment": "qemu-armv7a",
        "source_platform": "NuttX",
        "split": "test",
        "text": "<source_platform>...</source_platform>\\n<log>\\n<日志行>\\n...</log>",
    }

设计原则：
    - 完全复用既有链路（ingest_dataset.ingest_test_dir 同款 primitives）：
      preprocessing_service.preprocess → log_ingest_service.ingest →
      log_window_service.build_windows → cases/runs upsert。
      不重写日志解析逻辑。
    - 一条记录 = 一个 run。case 按 Test_<id> 归组（round_1 / round_2 同 case）。
    - 故障判定：round_1=故障轮(is_fault=True)、round_2=正常/基线轮(is_fault=False)。
      依据《单个Test数据介绍.md》：round_1 为故障轮，round_2 为正常基线轮。
    - 额外写一条 log_analysis_results 文档，使该 run 在「日志分析结果」按
      has_fault 分流到故障诊断(abnormal) / 预测预警(normal)。
    - 幂等：同 run_id 重复导入 = 先清旧 log_entries / log_windows /
      log_analysis_results，再 upsert case + run + 写新文档。

约束：
    - 只操作 fault_diagnosis 主库（mongo_compat / PG-JSONB）。
    - 不依赖运行中的 ingest / 上传链路；不透传 LLM。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.models.case import Case
from app.models.run import Run
from app.models.system import System
from app.services.log_ingest_service import RunMeta, log_ingest_service
from app.services.log_window_service import log_window_service
from app.services.preprocessing_service import preprocessing_service

logger = logging.getLogger(__name__)

SYSTEM_ID = "nuttx"
SYSTEM_NAME = "NuttX 嵌入式系统"

# MongoDB 集合名（与 log_ingest_service / ingest_dataset 一致）
_ENTRIES_COLLECTION = "log_entries"
_WINDOWS_COLLECTION = "log_windows"
_RESULTS_COLLECTION = "log_analysis_results"

# round_1 = 故障轮；其它（round_2） = 正常/基线轮
_FAULT_ROUND = 1

# 错误类级别（用于 runs.error_logs / critical_logs 统计，与 ingest_dataset 一致）
_ERROR_LEVELS = {"ERROR", "CRITICAL", "FATAL", "ALERT"}
_CRITICAL_LEVELS = {"CRITICAL", "FATAL"}

# 提取 <log>…</log> 包裹的日志内容
_RE_LOG_BLOCK = re.compile(r"<log>(.*?)</log>", re.DOTALL)
# 一条记录 id 形如 Test_52497:round_1:qemu_console
_RE_RECORD_ID = re.compile(
    r"^(?P<test>Test_[^:]+):round_(?P<round>\d+):(?P<log_type>[\w.-]+)$"
)


class StructuredImportError(RuntimeError):
    """结构化记录导入失败时抛出（含记录 id 上下文）。"""


def _ensure_system(db: Session) -> str:
    if not db.query(System).filter(System.system_id == SYSTEM_ID).first():
        db.add(System(system_id=SYSTEM_ID, name=SYSTEM_NAME))
    return SYSTEM_ID


def _extract_log_text(text: str) -> str:
    """从 <log>…</log> 中提取日志正文；无 <log> 标记时去除 XML 标签行兜底。"""
    m = _RE_LOG_BLOCK.search(text or "")
    if m:
        return m.group(1).strip("\n")
    # 兜底：去掉形如 <tag>…</tag> 的独立行
    lines = [
        ln
        for ln in (text or "").splitlines()
        if not (ln.strip().startswith("<") and ln.strip().endswith(">"))
    ]
    return "\n".join(lines)


def _parse_record_id(record_id: str) -> Dict[str, Any]:
    m = _RE_RECORD_ID.match(record_id or "")
    if not m:
        raise StructuredImportError(f"无法解析记录 id: {record_id!r}")
    return {
        "test_name": m.group("test"),
        "round_no": int(m.group("round")),
        "log_type": m.group("log_type"),
    }


def import_record(
    db: Session,
    mongo_db: Any,
    record: Dict[str, Any],
    *,
    window_size_s: int = 30,
    stride_s: int = 15,
) -> Dict[str, Any]:
    """导入一条 test.jsonl 记录，返回单条导入统计。

    幂等：同 run_id 重复调用会先清旧 log_entries / log_windows /
    log_analysis_results 文档，再 upsert case + run + 写新文档。
    """
    record_id = (record.get("id") or "").strip()
    if not record_id:
        raise StructuredImportError("记录缺少 id 字段")

    parsed = _parse_record_id(record_id)
    test_name = parsed["test_name"]
    round_no = parsed["round_no"]
    log_type_field = parsed["log_type"]

    case_id = f"{SYSTEM_ID}_{test_name}"
    run_id = record_id  # 用原始 id 作为 run_id，天然唯一且自描述

    # 故障判定：round_1 = 故障轮，其余 = 正常/基线轮
    is_fault = round_no == _FAULT_ROUND

    subsystem = (record.get("runtime_environment") or "").strip() or None
    split = (record.get("split") or "").strip()
    runtime_env = subsystem or ""
    source_platform = (record.get("source_platform") or "").strip()

    # ── 提取日志正文并走既有解析链路 ──
    raw_log = _extract_log_text(record.get("text") or "")
    filename = f"{run_id}.log"

    # 幂等：先清旧文档
    mongo_db[_ENTRIES_COLLECTION].delete_many({"run_id": run_id})
    mongo_db[_WINDOWS_COLLECTION].delete_many({"run_id": run_id})
    mongo_db[_RESULTS_COLLECTION].delete_many({"run_id": run_id})

    run_meta = RunMeta(
        run_id=run_id,
        task_id=f"task::{run_id}",
        source_type="dataset",
        system_id=SYSTEM_ID,
        subsystem=subsystem,
        case_id=case_id,
        is_fault=is_fault,
        test_name=test_name,
        round_no=round_no,
    )

    pre = preprocessing_service.preprocess(
        raw_log=raw_log, source_type="dataset", filename=filename
    )
    ing = log_ingest_service.ingest(
        preprocessed_log=pre, run_meta=run_meta, mongo_db=mongo_db
    )
    win = log_window_service.build_windows(
        run_meta=run_meta, mongo_db=mongo_db,
        window_size_s=window_size_s, stride_s=stride_s,
    )

    ld = ing.level_distribution or {}
    error_logs = sum(c for lvl, c in ld.items() if lvl.upper() in _ERROR_LEVELS)
    critical_logs = sum(c for lvl, c in ld.items() if lvl.upper() in _CRITICAL_LEVELS)

    # ── case 级：upsert（case.is_fault 用 OR，保留场景级故障标记）──
    case = db.query(Case).filter(Case.case_id == case_id).first()
    case_is_new = case is None
    if case is None:
        case = Case(case_id=case_id, name=test_name)
        db.add(case)
    case.case_name = test_name
    case.test_name = test_name
    case.fault_type_label = None  # test.jsonl 无故障类型字段
    case.is_fault = bool(case.is_fault) or is_fault
    case.description = f"从 test.jsonl 导入（{source_platform} / {runtime_env} / {split}）"
    case.created_at = datetime.utcnow()

    # ── run 级：upsert ──
    run = db.query(Run).filter(Run.run_id == run_id).first()
    run_is_new = run is None
    if run is None:
        run = Run(run_id=run_id, source_type="dataset")
        db.add(run)
    run.source_type = "dataset"
    run.case_id = case_id
    run.system_id = SYSTEM_ID
    run.subsystem = subsystem
    run.is_fault = is_fault
    run.test_name = test_name
    run.round_no = round_no
    run.run_name = f"{test_name} round {round_no}"
    run.line_count = ing.total_lines
    run.error_logs = error_logs
    run.critical_logs = critical_logs
    run.window_count = win.window_count
    # run 级 log_type：按故障场景语义（round_1=故障轮）而非按错误行计数
    run.log_type = "error" if is_fault else "other"
    run.stats_json = {
        "parsed_lines": ing.parsed_ok,
        "total_lines": ing.total_lines,
        "level_distribution": ld,
        "top_modules": [],
        "dataset_import_meta": {
            "display_name": f"{test_name} round {round_no}",
            "description": (
                f"来自 test.jsonl · {source_platform} · {runtime_env} · "
                f"{log_type_field} · 轮次 round_{round_no}"
            ),
            "tags": [t for t in (split, log_type_field, runtime_env) if t],
            "created_by": "structured_log_import",
            "original_filename": "test.jsonl",
            "import_id": "test_jsonl",
        },
    }
    run.start_time = ing.start_time
    run.end_time = ing.end_time
    run.created_at = datetime.utcnow()

    # (按需分析)导入时不再预写 log_analysis_results —— 只有用户点击「分析」才写。
    # run 的 is_fault / fault_type 已写在 runs/cases 表，文件选择列表仍可据此显示分类。

    # ── 登记为可训练样本（结构化 Test_xxx:round_x，round_1/round_2 合并进同一 Test）──
    from app.services.jsonl_training_registry import register_training_for_jsonl
    register_training_for_jsonl(
        db,
        test_name=test_name,
        platform=SYSTEM_ID,  # "nuttx"
        sample_class="fault" if is_fault else "normal",
        domain=log_type_field,
        fault_type=None,
        round_no=round_no,
        log_content=raw_log,
        import_id="test_jsonl" if run_id.startswith("Test_") else run_id,
    )

    db.commit()

    logger.info(
        "[structured_import] run_id=%s case=%s round=%d is_fault=%s "
        "lines=%d windows=%d errors=%d new_run=%s",
        run_id, case_id, round_no, is_fault, ing.total_lines,
        win.window_count, error_logs, run_is_new,
    )

    return {
        "run_id": run_id,
        "case_id": case_id,
        "test_name": test_name,
        "round_no": round_no,
        "is_fault": is_fault,
        "is_new": run_is_new or case_is_new,
        "entries": ing.inserted_count,
        "parsed_ok": ing.parsed_ok,
        "windows": win.window_count,
        "error_logs": error_logs,
        "critical_logs": critical_logs,
    }


def import_jsonl_file(
    db: Session,
    mongo_db: Any,
    jsonl_path: Path,
    *,
    window_size_s: int = 30,
    stride_s: int = 15,
    limit: Optional[int] = None,
    only: Optional[Iterable[str]] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """逐条流式导入 test.jsonl，返回累计统计与逐条（可选）结果。

    only: 只导入指定 list of run_id / record id，可空。
    limit: 最多导入前 N 条（调试用）。
    progress_cb: 进度回调 (processed, total)，每条成功后调用。
    cancelled: 取消检测；返回 True 时中断导入（已导入部分保留，抛 IngestCancelled）。
    """
    from app.services.jsonl_ingest_router import IngestCancelled

    import json

    records_attempted = 0
    records_ok = 0
    records_failed = 0
    entries_total = 0
    windows_total = 0
    fault_runs = 0
    normal_runs = 0
    failed: List[Dict[str, str]] = []
    successes: List[Dict[str, Any]] = []

    _ensure_system(db)
    only_set = set(only) if only else None

    # 预统计总行数（仅用于进度条分母；不整读入内存）
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
            # 取消检测：每条开始前检查，中断即抛出
            if cancelled is not None and cancelled():
                raise IngestCancelled(f"jsonl 导入被用户取消（{records_attempted}/{total_records}）")
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                records_failed += 1
                failed.append({"line": line_no, "error": f"json_decode: {exc}"})
                continue

            record_id = (record.get("id") or "").strip()
            if only_set is not None and record_id not in only_set:
                continue

            records_attempted += 1
            try:
                stats = import_record(
                    db, mongo_db, record,
                    window_size_s=window_size_s, stride_s=stride_s,
                )
            except Exception as exc:  # 单条失败不中断整体
                records_failed += 1
                failed.append({"line": line_no, "run_id": record_id, "error": str(exc)})
                logger.exception("[structured_import] 记录失败 line=%s run_id=%s", line_no, record_id)
                continue

            records_ok += 1
            entries_total += stats["entries"]
            windows_total += stats["windows"]
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
        "entries": entries_total,
        "windows": windows_total,
        "fault_runs": fault_runs,
        "normal_runs": normal_runs,
        "failures": failed,
        "successes": successes,
    }
