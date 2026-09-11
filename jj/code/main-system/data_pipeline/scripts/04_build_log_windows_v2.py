#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
04_build_log_windows_v2.py
目的：从 MongoDB log_entries 生成 log_windows，并同步回写：
- MongoDB log_windows
- MySQL runs.window_count
- MySQL task_feature_summaries（可选，写入窗口条数/错误窗口条数摘要）

本次修改重点：
1. 从 MySQL 补查 fault_type / root_cause / root_cause_type
2. 将标签字段写入 log_windows 顶层
3. 同时写入 metadata 子字段，方便后续 vector index / retrieval 统一读取
"""

from __future__ import annotations

import argparse
import json
import hashlib
import sys
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pymysql
from pymysql.cursors import DictCursor
from pymongo import ASCENDING, MongoClient

from config import (
    MYSQL_HOST,
    MYSQL_PORT,
    MYSQL_USER,
    MYSQL_PASSWORD,
    MYSQL_DB,
    MYSQL_CHARSET,
    MONGO_URI,
    MONGO_DB,
    ensure_dirs,
    print_config,
)

ensure_dirs()
print_config()

COL_LOG_ENTRIES = "log_entries"
COL_LOG_WINDOWS = "log_windows"
ERROR_LEVELS = {"ERROR", "CRITICAL"}


def mysql_connect():
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        charset=MYSQL_CHARSET,
        autocommit=False,
        cursorclass=DictCursor,
    )


def connect_mongo():
    client = MongoClient(MONGO_URI)
    return client, client[MONGO_DB]


def mk_window_id(run_id: str, start_time: datetime, end_time: datetime, salt: str = "") -> str:
    key = f"{run_id}|{start_time.isoformat()}|{end_time.isoformat()}|{salt}"
    return f"{run_id}_w_{hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]}"


def ensure_indexes(db) -> None:
    db[COL_LOG_ENTRIES].create_index(
        [("run_id", ASCENDING), ("timestamp", ASCENDING)],
        name="idx_run_time",
    )
    db[COL_LOG_WINDOWS].create_index(
        [("run_id", ASCENDING), ("start_time", ASCENDING)],
        name="idx_windows_run_start",
    )
    db[COL_LOG_WINDOWS].create_index(
        [("case_id", ASCENDING)],
        name="idx_windows_case",
    )
    db[COL_LOG_WINDOWS].create_index(
        [("task_id", ASCENDING)],
        name="idx_windows_task",
    )


def list_run_ids(db, only_run_id: str = "") -> List[str]:
    if only_run_id:
        return [only_run_id]
    return sorted(db[COL_LOG_ENTRIES].distinct("run_id"))


def fetch_mysql_labels(cur, case_id: Optional[str], task_id: Optional[str]) -> Dict:
    result = {
        "fault_type": None,
        "root_cause": None,
        "root_cause_type": None,
    }

    # ✅ 修复1：diagnosis_results 用 result_id
    if task_id:
        try:
            cur.execute(
                """
                SELECT
                    fault_type,
                    root_cause,
                    root_cause_type
                FROM diagnosis_results
                WHERE task_id = %s
                ORDER BY result_id DESC
                LIMIT 1
                """,
                (task_id,),
            )
            row = cur.fetchone()
            if row:
                result["fault_type"] = row.get("fault_type")
                result["root_cause"] = row.get("root_cause")
                result["root_cause_type"] = row.get("root_cause_type")
        except Exception as e:
            print(f"[WARN] query diagnosis_results failed for task_id={task_id}: {e}")

    
    if case_id and result["fault_type"] is None and result["root_cause"] is None:
        try:
            cur.execute(
                """
                SELECT
                    fault_type,
                    root_cause_type,
                    root_cause_json
                FROM cases
                WHERE case_id = %s
                LIMIT 1
                """,
                (case_id,),
            )
            row = cur.fetchone()
            if row:
                if result["fault_type"] is None:
                    result["fault_type"] = row.get("fault_type")

                if result["root_cause_type"] is None:
                    result["root_cause_type"] = row.get("root_cause_type")

                if result["root_cause"] is None:
                    result["root_cause"] = normalize_root_cause_from_json(
                        row.get("root_cause_json")
                    )

        except Exception as e:
            print(f"[WARN] query cases failed for case_id={case_id}: {e}")

    return result


def get_run_meta(db, mysql_conn, run_id: str) -> Optional[Dict]:
    """
    先从 Mongo log_entries 取基础运行信息，
    再从 MySQL 补 fault_type / root_cause / root_cause_type。
    """
    base = db[COL_LOG_ENTRIES].find_one(
        {"run_id": run_id},
        projection={
            "system_id": 1,
            "subsystem": 1,
            "case_id": 1,
            "test_name": 1,
            "round_no": 1,
            "is_fault": 1,
            "task_id": 1,
        },
    )
    if not base:
        return None

    base["_id"] = str(base.get("_id")) if base.get("_id") is not None else None

    labels = {
        "fault_type": None,
        "root_cause": None,
        "root_cause_type": None,
    }

    if mysql_conn:
        with mysql_conn.cursor() as cur:
            labels = fetch_mysql_labels(
                cur,
                case_id=base.get("case_id"),
                task_id=base.get("task_id"),
            )

    merged = dict(base)
    merged.update(labels)
    return merged


def fetch_entries_for_run(db, run_id: str) -> List[Dict]:
    return list(
        db[COL_LOG_ENTRIES].find(
            {"run_id": run_id, "timestamp": {"$type": "date"}},
            projection={
                "_id": 0,
                "timestamp": 1,
                "level": 1,
                "module": 1,
                "message": 1,
                "line_no": 1,
                "file_path": 1,
            },
            sort=[("timestamp", ASCENDING)],
        )
    )


def build_window_doc(
    run_meta: Dict,
    run_id: str,
    entries: List[Dict],
    start_time: datetime,
    end_time: datetime,
    anchor_time: Optional[datetime],
    strategy: str,
    key_events_topn: int,
    salt: str = "",
) -> Dict:
    sliced: List[Dict] = []
    level_dist = Counter()
    module_dist = Counter()
    key_events: List[Dict] = []

    for e in entries:
        ts = e.get("timestamp")
        if not isinstance(ts, datetime):
            continue
        if ts < start_time:
            continue
        if ts > end_time:
            break

        lvl = (e.get("level") or "UNKNOWN").upper()
        mod = e.get("module") or "UNKNOWN"
        msg = (e.get("message") or "").strip()

        sliced.append(e)
        level_dist[lvl] += 1
        module_dist[mod] += 1

        if lvl in ERROR_LEVELS and len(key_events) < key_events_topn:
            key_events.append(
                {
                    "timestamp": ts,
                    "level": lvl,
                    "module": mod,
                    "message": msg,
                    "line_no": e.get("line_no"),
                    "file_path": e.get("file_path"),
                }
            )

    text = "\n".join(
        f"{e['timestamp'].isoformat()} "
        f"{(e.get('level') or 'UNKNOWN').upper()} "
        f"{e.get('module') or 'UNKNOWN'} "
        f"{(e.get('message') or '').strip()}"
        for e in sliced
    )

    window_id = mk_window_id(run_id, start_time, end_time, salt=salt)

    fault_type = run_meta.get("fault_type")
    root_cause = run_meta.get("root_cause")
    root_cause_type = run_meta.get("root_cause_type")

    return {
        "_id": window_id,
        "window_id": window_id,
        "task_id": run_meta.get("task_id") or f"task::{run_id}",
        "strategy": strategy,
        "run_id": run_id,
        "case_id": run_meta.get("case_id"),
        "system_id": run_meta.get("system_id"),
        "subsystem": run_meta.get("subsystem"),
        "test_name": run_meta.get("test_name"),
        "round_no": run_meta.get("round_no"),
        "is_fault": run_meta.get("is_fault"),
        # 新增：顶层标签字段
        "fault_type": fault_type,
        "root_cause": root_cause,
        "root_cause_type": root_cause_type,
        "start_time": start_time,
        "end_time": end_time,
        "anchor_time": anchor_time,
        "text": text,
        "key_events": key_events,
        "stats": {
            "n_entries": len(sliced),
            "level_distribution": dict(level_dist),
            "top_modules": [
                {"module": m, "count": c}
                for m, c in module_dist.most_common(10)
            ],
            "strategy": strategy,
            "error_events": sum(1 for k in key_events if k["level"] in ERROR_LEVELS),
        },
        # 新增：metadata 子字段，便于后续检索/向量库统一取值
        "metadata": {
            "fault_type": fault_type,
            "root_cause": root_cause,
            "root_cause_type": root_cause_type,
            "task_id": run_meta.get("task_id") or f"task::{run_id}",
            "case_id": run_meta.get("case_id"),
            "run_id": run_id,
            "system_id": run_meta.get("system_id"),
            "subsystem": run_meta.get("subsystem"),
            "is_fault": run_meta.get("is_fault"),
        },
        "built_at": datetime.utcnow(),
    }


def build_windows_time(
    run_meta: Dict,
    run_id: str,
    entries: List[Dict],
    window_size_s: int,
    stride_s: int,
    min_entries: int,
    key_events_topn: int,
    max_windows: int,
) -> List[Dict]:
    if not entries:
        return []

    first_ts = entries[0]["timestamp"]
    last_ts = entries[-1]["timestamp"]
    windows: List[Dict] = []
    t = first_ts

    while t <= last_ts:
        doc = build_window_doc(
            run_meta,
            run_id,
            entries,
            t,
            t + timedelta(seconds=window_size_s),
            None,
            "time",
            key_events_topn,
            f"time|ws={window_size_s}|st={stride_s}",
        )
        if doc["stats"]["n_entries"] >= min_entries:
            windows.append(doc)
            if max_windows and len(windows) >= max_windows:
                break
        t += timedelta(seconds=stride_s)

    return windows


def iou(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> float:
    inter_start = max(a_start, b_start)
    inter_end = min(a_end, b_end)
    if inter_end <= inter_start:
        return 0.0
    inter = (inter_end - inter_start).total_seconds()
    union = (max(a_end, b_end) - min(a_start, b_start)).total_seconds()
    return inter / union if union > 0 else 0.0


def build_windows_error(
    run_meta: Dict,
    run_id: str,
    entries: List[Dict],
    pre_s: int,
    post_s: int,
    min_entries: int,
    key_events_topn: int,
    max_windows: int,
    dedup_iou_threshold: float,
) -> List[Dict]:
    windows: List[Dict] = []
    last_kept: Optional[Tuple[datetime, datetime]] = None
    pre = timedelta(seconds=pre_s)
    post = timedelta(seconds=post_s)

    for e in entries:
        ts = e.get("timestamp")
        lvl = (e.get("level") or "").upper()
        if not isinstance(ts, datetime) or lvl not in ERROR_LEVELS:
            continue

        start_time = ts - pre
        end_time = ts + post

        if last_kept and iou(start_time, end_time, last_kept[0], last_kept[1]) >= dedup_iou_threshold:
            continue

        doc = build_window_doc(
            run_meta,
            run_id,
            entries,
            start_time,
            end_time,
            ts,
            "error",
            key_events_topn,
            f"error|pre={pre_s}|post={post_s}",
        )
        if doc["stats"]["n_entries"] >= min_entries:
            windows.append(doc)
            last_kept = (start_time, end_time)
            if max_windows and len(windows) >= max_windows:
                break

    return windows


def upsert_windows(db, windows: List[Dict]) -> Tuple[int, int]:
    col = db[COL_LOG_WINDOWS]
    upserts = 0
    updates = 0
    for doc in windows:
        res = col.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        if res.upserted_id is not None:
            upserts += 1
        else:
            updates += 1
    return upserts, updates


def update_mysql_run_summary(cur, run_id: str, window_count: int) -> None:
    cur.execute(
        "UPDATE runs SET window_count=%s, updated_at=CURRENT_TIMESTAMP WHERE run_id=%s",
        (window_count, run_id),
    )


def upsert_feature(cur, task_id: str, feature_name: str, value: str, value_type: str = "int") -> None:
    cur.execute(
        """
        INSERT INTO task_feature_summaries(task_id, feature_type, feature_name, feature_value, value_type)
        VALUES (%s, 'log_window', %s, %s, %s)
        ON DUPLICATE KEY UPDATE feature_value=VALUES(feature_value)
        """,
        (task_id, feature_name, value, value_type),
    )


def normalize_root_cause_from_json(root_cause_json):
    if root_cause_json is None:
        return None

    if isinstance(root_cause_json, dict):
        if "root_cause" in root_cause_json:
            return root_cause_json["root_cause"]
        return json.dumps(root_cause_json, ensure_ascii=False)

    if isinstance(root_cause_json, str):
        try:
            obj = json.loads(root_cause_json)
            if isinstance(obj, dict) and "root_cause" in obj:
                return obj["root_cause"]
            return json.dumps(obj, ensure_ascii=False)
        except Exception:
            return root_cause_json

    return str(root_cause_json)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build log_windows from log_entries.")
    ap.add_argument("--strategy", choices=["time", "error"], default="time")
    ap.add_argument("--only-run-id", default="")
    ap.add_argument("--limit-runs", type=int, default=0)
    ap.add_argument("--drop-run-windows", action="store_true")
    ap.add_argument("--window-size", type=int, default=30)
    ap.add_argument("--stride", type=int, default=15)
    ap.add_argument("--pre", type=int, default=60)
    ap.add_argument("--post", type=int, default=30)
    ap.add_argument("--dedup-iou", type=float, default=0.7)
    ap.add_argument("--min-entries", type=int, default=5)
    ap.add_argument("--key-events-topn", type=int, default=10)
    ap.add_argument("--max-windows-per-run", type=int, default=0)
    ap.add_argument("--skip-mysql-summary", action="store_true")
    args = ap.parse_args()

    mongo_client, db = connect_mongo()
    ensure_indexes(db)

    run_ids = list_run_ids(db, args.only_run_id)
    if args.limit_runs:
        run_ids = run_ids[: args.limit_runs]

    if not run_ids:
        print("[FATAL] no run_id found in log_entries", file=sys.stderr)
        raise SystemExit(2)

    # 注意：这里不仅用于 summary，也用于补 fault_type/root_cause 标签
    mysql_conn = mysql_connect()

    try:
        for i, run_id in enumerate(run_ids, 1):
            print(f"[INFO] ({i}/{len(run_ids)}) run_id={run_id}")

            run_meta = get_run_meta(db, mysql_conn, run_id)
            if not run_meta:
                print(f"[WARN] run_meta not found for run_id={run_id}")
                continue

            print(
                "[INFO] labels:",
                f"fault_type={run_meta.get('fault_type')}, "
                f"root_cause={run_meta.get('root_cause')}, "
                f"root_cause_type={run_meta.get('root_cause_type')}"
            )

            entries = fetch_entries_for_run(db, run_id)
            if not entries:
                print(f"[WARN] no entries for run_id={run_id}")
                continue

            if args.drop_run_windows:
                deleted = db[COL_LOG_WINDOWS].delete_many({"run_id": run_id}).deleted_count
                print(f"[INFO] deleted windows={deleted}")

            if args.strategy == "time":
                windows = build_windows_time(
                    run_meta,
                    run_id,
                    entries,
                    args.window_size,
                    args.stride,
                    args.min_entries,
                    args.key_events_topn,
                    args.max_windows_per_run,
                )
            else:
                windows = build_windows_error(
                    run_meta,
                    run_id,
                    entries,
                    args.pre,
                    args.post,
                    args.min_entries,
                    args.key_events_topn,
                    args.max_windows_per_run,
                    args.dedup_iou,
                )

            upserts, updates = upsert_windows(db, windows)
            print(f"[INFO] windows={len(windows)} upserts={upserts} updates={updates}")

            # summary 写回仍然保留；如果你真的不想写 summary，可以再单独加开关
            if not args.skip_mysql_summary:
                with mysql_conn.cursor() as cur:
                    update_mysql_run_summary(cur, run_id, len(windows))
                    task_id = run_meta.get("task_id") or f"task::{run_id}"
                    upsert_feature(cur, task_id, "window_count", str(len(windows)))
                    upsert_feature(cur, task_id, "window_strategy", args.strategy, "string")
                mysql_conn.commit()

        print("[OK] log_windows build completed.")

    except Exception:
        mysql_conn.rollback()
        raise
    finally:
        mysql_conn.close()
        mongo_client.close()


if __name__ == "__main__":
    main()