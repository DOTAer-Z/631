"""
ingest_dataset.py — 将 data/Test_*** 数据集导入当前 PostgreSQL 主库（含 JSONB 文档层）。

最小分析单位 = 一个 Test_*** 文件夹（= 一个 case）；其下每个 logs/round_N = 一个 run。

复用后端既有「分析」代码，不另造解析逻辑：
    preprocessing_service.preprocess   原始日志 → 规范化行
    log_ingest_service.ingest          行 → 结构化 log_entries（三级正则解析）
    log_window_service.build_windows   log_entries → 时间滑窗 log_windows

并把 case / run 元信息写入关系表 cases / runs，使前端「数据处理 / 日志列表」可查询、
可展示（run 列表、故障类型、error/critical/窗口计数、单 run 的 entries/windows）。

用法：
    cd backend && set -a && . ./run.env && set +a
    PYTHONPATH=. .venv/bin/python ingest_dataset.py --data-root ../data
    # 可选：--only Test_1001 --only Test_1002   仅导入指定 Test
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# 允许 `from app...` 导入
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.database import SessionLocal, get_mongo_db
from app.models import Case, Run, System
from app.services.preprocessing_service import preprocessing_service
from app.services.log_ingest_service import RunMeta, log_ingest_service
from app.services.log_window_service import log_window_service

SYSTEM_ID = "nuttx"
SYSTEM_NAME = "NuttX 嵌入式系统"

# 错误类级别（用于 runs.error_logs / critical_logs 统计）
_ERROR_LEVELS = {"ERROR", "CRITICAL", "FATAL", "ALERT"}
_CRITICAL_LEVELS = {"CRITICAL", "FATAL"}

# round_N 目录名解析
_ROUND_RE = re.compile(r"round[_-]?(\d+)", re.IGNORECASE)

# 该 round 下要纳入分析的日志文件后缀
_LOG_SUFFIXES = (".log", ".out", ".txt")


# ---------------------------------------------------------------------------
# 解析 Test 目录的故障元信息
# ---------------------------------------------------------------------------
def parse_fip_info(path: Path) -> Dict[str, str]:
    """解析 fip_info.data（每行 'KEY: value'）。"""
    info: Dict[str, str] = {}
    if not path.exists():
        return info
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            info[k.strip()] = v.strip()
    return info


def parse_ground_truth(path: Path) -> Dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}


def collect_round_logs(round_dir: Path) -> bytes:
    """把一个 round 下所有日志文件按路径排序拼接为一个字节流。"""
    chunks: List[bytes] = []
    for fp in sorted(round_dir.rglob("*")):
        if fp.is_file() and fp.suffix.lower() in _LOG_SUFFIXES and "Zone.Identifier" not in fp.name:
            data = fp.read_bytes()
            if not data.endswith(b"\n"):
                data += b"\n"
            chunks.append(data)
    return b"".join(chunks)


def determine_round_is_fault(round_dir: Path, round_no: int) -> bool:
    """
    判断某一轮是「故障轮」还是「正常/基线轮」。

    依据《单个Test数据介绍.md》§3/§10：
        round_1 = 故障轮（fault_events.log 记录 run_start/step_*）
        round_2 = 正常基线轮（fault_events.log 记录 baseline_run）

    优先按 fault_events.log 的实际内容判断（数据驱动），
    无法判断时回退到轮次启发式（round 1 = 故障，其余 = 正常）。
    """
    faults_dir = round_dir / "faults"
    text = ""
    if faults_dir.is_dir():
        for fp in faults_dir.glob("*.log"):
            if "Zone.Identifier" in fp.name:
                continue
            text += fp.read_text(encoding="utf-8", errors="ignore")
    low = text.lower()
    if "baseline_run" in low:
        return False
    if "run_start" in low or "step_start" in low or "step_end" in low:
        return True
    # 回退：第 1 轮为故障轮，其余为基线轮
    return round_no == 1


# ---------------------------------------------------------------------------
# 关系表 upsert
# ---------------------------------------------------------------------------
def ensure_system(db) -> None:
    if not db.query(System).filter(System.system_id == SYSTEM_ID).first():
        db.add(System(system_id=SYSTEM_ID, name=SYSTEM_NAME))
        db.commit()


def upsert_case(db, *, case_id: str, test_name: str, fault_type: Optional[str], is_fault: bool,
                description: Optional[str], test_version_id: int | None = None) -> bool:
    """upsert 一条 case，返回 True 表示新增、False 表示更新已有行。"""
    case = db.query(Case).filter(Case.case_id == case_id).first()
    is_new = case is None
    if case is None:
        case = Case(case_id=case_id, name=test_name)
        db.add(case)
    case.case_name = test_name
    case.test_name = test_name
    case.fault_type_label = fault_type
    case.is_fault = is_fault
    case.description = description
    case.test_version_id = test_version_id
    # 刷新 created_at，让重新上传的数据集排序靠前
    case.created_at = datetime.utcnow()
    db.commit()
    return is_new


def upsert_run(db, *, run_id: str, case_id: str, system_id: str, subsystem: Optional[str],
               is_fault: bool, test_name: str, round_no: int, run_name: str,
               error_logs: int, critical_logs: int, window_count: int,
               start_time: Optional[datetime], end_time: Optional[datetime],
               stats_json: Dict, test_version_id: int | None = None) -> bool:
    """upsert 一条 run，返回 True 表示新增、False 表示更新已有行。"""
    run = db.query(Run).filter(Run.run_id == run_id).first()
    is_new = run is None
    if run is None:
        run = Run(run_id=run_id, source_type="dataset")
        db.add(run)
    run.source_type = "dataset"
    run.case_id = case_id
    run.system_id = system_id
    run.subsystem = subsystem
    run.is_fault = is_fault
    run.test_name = test_name
    run.test_version_id = test_version_id
    run.round_no = round_no
    run.run_name = run_name
    run.error_logs = error_logs
    run.critical_logs = critical_logs
    run.window_count = window_count
    run.line_count = int(stats_json.get("total_lines") or 0)
    run.start_time = start_time
    run.end_time = end_time
    run.stats_json = stats_json
    # 刷新 created_at，让「日志列表」按 created_at desc 排序时，新摄入的数据集排在最前
    run.created_at = datetime.utcnow()
    db.commit()
    return is_new


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def ingest_test_dir(db, mongo_db, test_dir: Path,
                    window_size_s: int, stride_s: int,
                    test_version_id: int | None = None) -> Dict:
    test_name = test_dir.name
    case_id = f"{SYSTEM_ID}_{test_name}"

    fip = parse_fip_info(test_dir / "fip_info.data")
    gt = parse_ground_truth(test_dir / "ground_truth.json")

    fault_type = gt.get("fault_type") or fip.get("FAULT_TYPE")
    # Test 级标签：这条用例是不是一个故障场景（用于 case.is_fault / 故障类型展示）
    case_is_fault = (gt.get("sample_class") or "").lower() == "fault" or bool(fault_type)
    target_component = fip.get("TARGET_COMPONENT") or ""
    subsystem = target_component.split("/")[-1] if target_component else gt.get("domain")
    description = gt.get("root_cause_text")

    case_is_new = upsert_case(db, case_id=case_id, test_name=test_name, fault_type=fault_type,
                              is_fault=case_is_fault, description=description,
                              test_version_id=test_version_id)

    logs_dir = test_dir / "logs"
    round_dirs = sorted([d for d in logs_dir.iterdir() if d.is_dir() and _ROUND_RE.search(d.name)]) \
        if logs_dir.is_dir() else []

    summary = {"test": test_name, "case_id": case_id, "fault_type": fault_type,
               "case_is_new": case_is_new,
               "runs": [], "rounds": len(round_dirs)}

    for rd in round_dirs:
        m = _ROUND_RE.search(rd.name)
        round_no = int(m.group(1)) if m else 0
        run_id = f"{case_id}_round_{round_no}"

        # 每一轮单独判定故障/正常（故障轮 vs 基线轮），不再一刀切沿用 Test 级标签
        run_is_fault = determine_round_is_fault(rd, round_no)

        raw = collect_round_logs(rd)
        if not raw.strip():
            summary["runs"].append({"run_id": run_id, "skipped": "no_logs"})
            continue

        # 幂等：先清掉该 run 旧的文档
        mongo_db["log_entries"].delete_many({"run_id": run_id})
        mongo_db["log_windows"].delete_many({"run_id": run_id})

        run_meta = RunMeta(
            run_id=run_id, task_id=f"task::{run_id}", source_type="dataset",
            system_id=SYSTEM_ID, subsystem=subsystem, case_id=case_id,
            is_fault=run_is_fault, test_name=test_name, round_no=round_no,
        )

        pre = preprocessing_service.preprocess(raw_log=raw, source_type="dataset",
                                               filename=f"{run_id}.log")
        ing = log_ingest_service.ingest(preprocessed_log=pre, run_meta=run_meta, mongo_db=mongo_db)
        win = log_window_service.build_windows(run_meta=run_meta, mongo_db=mongo_db,
                                               window_size_s=window_size_s, stride_s=stride_s)

        ld = ing.level_distribution or {}
        error_logs = sum(c for lvl, c in ld.items() if lvl.upper() in _ERROR_LEVELS)
        critical_logs = sum(c for lvl, c in ld.items() if lvl.upper() in _CRITICAL_LEVELS)
        stats_json = {
            "parsed_lines": ing.parsed_ok,
            "total_lines": ing.total_lines,
            "level_distribution": ld,
            "top_modules": [],
        }
        run_is_new = upsert_run(db, run_id=run_id, case_id=case_id, system_id=SYSTEM_ID, subsystem=subsystem,
                                is_fault=run_is_fault, test_name=test_name, round_no=round_no,
                                run_name=f"{test_name} round {round_no}",
                                error_logs=error_logs, critical_logs=critical_logs,
                                window_count=win.window_count,
                                start_time=ing.start_time, end_time=ing.end_time, stats_json=stats_json,
                                test_version_id=test_version_id)

        summary["runs"].append({
            "run_id": run_id, "entries": ing.inserted_count, "parsed_ok": ing.parsed_ok,
            "windows": win.window_count, "errors": error_logs, "criticals": critical_logs,
            "is_fault": run_is_fault,
            "is_new": run_is_new,
        })

    return summary


def _default_data_root() -> str:
    """优先用脚本同级 data/（容器内 /app/data），否则用上级 data/（本地 dev ../data）。"""
    here = Path(__file__).resolve().parent
    for cand in (here / "data", here.parent / "data"):
        if cand.is_dir():
            return str(cand)
    return str(here / "data")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=_default_data_root())
    ap.add_argument("--only", action="append", default=None, help="只导入指定 Test 目录名，可多次")
    ap.add_argument("--window-size-s", type=int, default=30)
    ap.add_argument("--stride-s", type=int, default=15)
    ap.add_argument("--skip-if-present", action="store_true",
                    help="若 runs 表已有数据则跳过（供后台非阻塞启动，避免每次重启重复解析内置数据集）")
    args = ap.parse_args()

    # 已有数据则跳过（后台启动场景）：runs 由 pgdata 持久，重启即时可用
    if args.skip_if_present:
        _db = SessionLocal()
        try:
            if _db.query(Run).count() > 0:
                print("[ingest] runs 表已有数据，跳过内置数据集导入")
                return 0
        finally:
            _db.close()

    data_root = Path(args.data_root).resolve()
    if not data_root.is_dir():
        print(f"[ingest] 数据根目录不存在: {data_root}", file=sys.stderr)
        return 2

    test_dirs = sorted([d for d in data_root.iterdir()
                        if d.is_dir() and d.name.startswith("Test_")])
    if args.only:
        keep = set(args.only)
        test_dirs = [d for d in test_dirs if d.name in keep]

    print(f"[ingest] data_root={data_root}  发现 {len(test_dirs)} 个 Test_* 目录")

    db = SessionLocal()
    mongo_db = get_mongo_db()
    ensure_system(db)

    total_runs = 0
    total_entries = 0
    total_windows = 0
    fault_runs = 0
    normal_runs = 0
    try:
        for i, td in enumerate(test_dirs, 1):
            s = ingest_test_dir(db, mongo_db, td,
                                window_size_s=args.window_size_s, stride_s=args.stride_s)
            runs = [r for r in s["runs"] if "skipped" not in r]
            ents = sum(r.get("entries", 0) for r in runs)
            wins = sum(r.get("windows", 0) for r in runs)
            f_runs = sum(1 for r in runs if r.get("is_fault"))
            n_runs = len(runs) - f_runs
            total_runs += len(runs)
            total_entries += ents
            total_windows += wins
            fault_runs += f_runs
            normal_runs += n_runs
            print(f"[{i:>3}/{len(test_dirs)}] {s['test']:<10} "
                  f"fault_type={str(s['fault_type'])[:26]:<26} "
                  f"runs={len(runs)} (fault={f_runs} normal={n_runs}) "
                  f"entries={ents} windows={wins}")
    finally:
        db.close()

    print(f"\n[ingest] 完成：cases={len(test_dirs)} runs={total_runs} "
          f"(故障轮={fault_runs} 正常轮={normal_runs}) "
          f"log_entries={total_entries} log_windows={total_windows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
