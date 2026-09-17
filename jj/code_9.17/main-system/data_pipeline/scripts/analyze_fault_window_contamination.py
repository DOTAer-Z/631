#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_fault_window_contamination.py

做两个统计：
1) 统计故障窗口库污染度：
   在 log_windows 中，统计 is_fault=True 的 windows 里：
   - stats.error_events == 0
   - len(key_events) == 0
   的数量和比例

2) 抽样检查同一 fault run(round_1) 的 time windows：
   将窗口按“故障前 / 故障附近 / 故障后”分区，观察哪些窗口虽然来自故障 run，
   但文本和统计特征其实更像正常流量。

依赖：
  pip install pymongo python-dateutil

用法示例：
  python analyze_fault_window_contamination.py \
      --mongo-uri mongodb://localhost:27019 \
      --mongo-db fault_diagnosis \
      --out-json artifacts/window_contamination_report.json

可选：
  --limit-runs 20
  --sample-per-zone 2
  --preview-chars 180
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pymongo import MongoClient


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Analyze contamination in fault log_windows.")
    ap.add_argument("--mongo-uri", required=True, help="MongoDB URI, e.g. mongodb://localhost:27019")
    ap.add_argument("--mongo-db", required=True, help="MongoDB database name")
    ap.add_argument("--collection", default="log_windows", help="MongoDB collection name (default: log_windows)")
    ap.add_argument("--limit-runs", type=int, default=10, help="How many fault runs to sample for statistic 2")
    ap.add_argument("--sample-per-zone", type=int, default=2, help="How many sample windows to keep per zone")
    ap.add_argument("--preview-chars", type=int, default=180, help="Preview chars for sampled text")
    ap.add_argument("--out-json", default="", help="Optional path to save JSON report")
    return ap.parse_args()


def connect(uri: str, db_name: str):
    client = MongoClient(uri)
    db = client[db_name]
    return client, db


def safe_len(x: Any) -> int:
    if isinstance(x, list):
        return len(x)
    return 0


def text_preview(text: str, limit: int) -> str:
    s = " ".join((text or "").split())
    return s[:limit]


def iso(v: Any) -> Optional[str]:
    if isinstance(v, datetime):
        return v.isoformat()
    if v is None:
        return None
    return str(v)


def fetch_fault_windows(col) -> Iterable[Dict[str, Any]]:
    return col.find(
        {"is_fault": True},
        {
            "_id": 0,
            "window_id": 1,
            "run_id": 1,
            "case_id": 1,
            "round_no": 1,
            "strategy": 1,
            "start_time": 1,
            "end_time": 1,
            "anchor_time": 1,
            "text": 1,
            "key_events": 1,
            "stats": 1,
            "fault_type": 1,
            "metadata": 1,
        },
    )


def statistic_1(col) -> Dict[str, Any]:
    total_fault = 0
    quiet_fault = 0
    by_strategy = Counter()
    quiet_by_strategy = Counter()
    by_run = Counter()
    quiet_by_run = Counter()

    for w in fetch_fault_windows(col):
        total_fault += 1
        strategy = w.get("strategy") or w.get("stats", {}).get("strategy") or "unknown"
        by_strategy[strategy] += 1

        run_id = w.get("run_id") or w.get("metadata", {}).get("run_id") or "unknown"
        by_run[run_id] += 1

        error_events = int(w.get("stats", {}).get("error_events", 0) or 0)
        key_event_count = safe_len(w.get("key_events"))
        if error_events == 0 and key_event_count == 0:
            quiet_fault += 1
            quiet_by_strategy[strategy] += 1
            quiet_by_run[run_id] += 1

    strategy_rows = []
    for strategy, total in sorted(by_strategy.items()):
        quiet = quiet_by_strategy[strategy]
        strategy_rows.append(
            {
                "strategy": strategy,
                "total_fault_windows": total,
                "quiet_fault_windows": quiet,
                "quiet_ratio": round(quiet / total, 4) if total else 0.0,
            }
        )

    run_rows = []
    for run_id, total in by_run.most_common():
        quiet = quiet_by_run[run_id]
        if quiet == 0:
            continue
        run_rows.append(
            {
                "run_id": run_id,
                "total_fault_windows": total,
                "quiet_fault_windows": quiet,
                "quiet_ratio": round(quiet / total, 4) if total else 0.0,
            }
        )

    run_rows.sort(key=lambda x: (-x["quiet_ratio"], -x["quiet_fault_windows"], x["run_id"]))

    return {
        "definition": {
            "population": "log_windows where is_fault=True",
            "quiet_fault_window": "stats.error_events == 0 AND len(key_events) == 0",
        },
        "overall": {
            "total_fault_windows": total_fault,
            "quiet_fault_windows": quiet_fault,
            "quiet_ratio": round(quiet_fault / total_fault, 4) if total_fault else 0.0,
        },
        "by_strategy": strategy_rows,
        "top_contaminated_runs": run_rows[:20],
    }


@dataclass
class WindowSample:
    zone: str
    window_id: str
    start_time: Optional[str]
    end_time: Optional[str]
    error_events: int
    key_event_count: int
    n_entries: int
    preview: str


def classify_zone(
    center_ts: datetime,
    first_anchor: datetime,
    last_anchor: datetime,
    span_seconds: float,
) -> str:
    # 给“故障附近”一个缓冲带：总跨度的 10%，至少 30 秒
    margin = max(30.0, span_seconds * 0.10)
    if center_ts < first_anchor:
        return "pre_fault"
    if center_ts > last_anchor:
        return "post_recovery"
    # 落在 anchor 区间内，或者紧邻 anchor 区间，都算 fault_nearby
    if first_anchor.timestamp() - margin <= center_ts.timestamp() <= last_anchor.timestamp() + margin:
        return "fault_nearby"
    return "fault_nearby"


def statistic_2(
    col,
    limit_runs: int,
    sample_per_zone: int,
    preview_chars: int,
) -> Dict[str, Any]:
    """
    仅看 round_1 + strategy=time + is_fault=True 的窗口。
    以“含异常信号的时间窗”作为故障锚点：
      abnormal_window := error_events > 0 OR len(key_events) > 0
    然后把同一 run 的 time windows 分成：
      - pre_fault
      - fault_nearby
      - post_recovery
    """
    pipeline = [
        {"$match": {"is_fault": True, "round_no": 1, "strategy": "time"}},
        {"$group": {"_id": "$run_id", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
        {"$limit": int(limit_runs)},
    ]
    run_ids = [x["_id"] for x in col.aggregate(pipeline)]

    runs_report: List[Dict[str, Any]] = []

    for run_id in run_ids:
        windows = list(
            col.find(
                {"run_id": run_id, "is_fault": True, "round_no": 1, "strategy": "time"},
                {
                    "_id": 0,
                    "window_id": 1,
                    "run_id": 1,
                    "case_id": 1,
                    "fault_type": 1,
                    "start_time": 1,
                    "end_time": 1,
                    "anchor_time": 1,
                    "text": 1,
                    "key_events": 1,
                    "stats": 1,
                },
            ).sort("start_time", 1)
        )
        if not windows:
            continue

        abnormal = []
        for w in windows:
            error_events = int(w.get("stats", {}).get("error_events", 0) or 0)
            key_event_count = safe_len(w.get("key_events"))
            if error_events > 0 or key_event_count > 0:
                # 优先用 anchor_time，其次窗口中心
                anchor = w.get("anchor_time")
                if not isinstance(anchor, datetime):
                    st = w.get("start_time")
                    et = w.get("end_time")
                    if isinstance(st, datetime) and isinstance(et, datetime):
                        anchor = st + (et - st) / 2
                if isinstance(anchor, datetime):
                    abnormal.append((anchor, w))

        run_summary: Dict[str, Any] = {
            "run_id": run_id,
            "case_id": windows[0].get("case_id"),
            "fault_type": windows[0].get("fault_type"),
            "n_time_windows": len(windows),
            "n_abnormal_windows": len(abnormal),
            "note": "",
            "zones": {
                "pre_fault": {"count": 0, "quiet_count": 0, "quiet_ratio": 0.0, "samples": []},
                "fault_nearby": {"count": 0, "quiet_count": 0, "quiet_ratio": 0.0, "samples": []},
                "post_recovery": {"count": 0, "quiet_count": 0, "quiet_ratio": 0.0, "samples": []},
            },
        }

        if not abnormal:
            run_summary["note"] = "No abnormal windows found in this fault run; likely fully quiet under current window signals."
            # 这类 run 仍然很可疑，抽最前几个窗口供人工看
            for w in windows[:sample_per_zone]:
                run_summary["zones"]["pre_fault"]["samples"].append(
                    asdict(
                        WindowSample(
                            zone="pre_fault",
                            window_id=w.get("window_id", ""),
                            start_time=iso(w.get("start_time")),
                            end_time=iso(w.get("end_time")),
                            error_events=int(w.get("stats", {}).get("error_events", 0) or 0),
                            key_event_count=safe_len(w.get("key_events")),
                            n_entries=int(w.get("stats", {}).get("n_entries", 0) or 0),
                            preview=text_preview(w.get("text", ""), preview_chars),
                        )
                    )
                )
            runs_report.append(run_summary)
            continue

        anchors = [a for a, _ in abnormal]
        first_anchor = min(anchors)
        last_anchor = max(anchors)

        first_start = windows[0].get("start_time")
        last_end = windows[-1].get("end_time")
        if isinstance(first_start, datetime) and isinstance(last_end, datetime):
            span_seconds = max((last_end - first_start).total_seconds(), 1.0)
        else:
            span_seconds = 1.0

        # 逐窗分类
        for w in windows:
            st = w.get("start_time")
            et = w.get("end_time")
            if not isinstance(st, datetime) or not isinstance(et, datetime):
                continue
            center = st + (et - st) / 2
            zone = classify_zone(center, first_anchor, last_anchor, span_seconds)

            error_events = int(w.get("stats", {}).get("error_events", 0) or 0)
            key_event_count = safe_len(w.get("key_events"))
            quiet = error_events == 0 and key_event_count == 0

            z = run_summary["zones"][zone]
            z["count"] += 1
            if quiet:
                z["quiet_count"] += 1
                if len(z["samples"]) < sample_per_zone:
                    z["samples"].append(
                        asdict(
                            WindowSample(
                                zone=zone,
                                window_id=w.get("window_id", ""),
                                start_time=iso(st),
                                end_time=iso(et),
                                error_events=error_events,
                                key_event_count=key_event_count,
                                n_entries=int(w.get("stats", {}).get("n_entries", 0) or 0),
                                preview=text_preview(w.get("text", ""), preview_chars),
                            )
                        )
                    )

        for zone in ("pre_fault", "fault_nearby", "post_recovery"):
            z = run_summary["zones"][zone]
            z["quiet_ratio"] = round(z["quiet_count"] / z["count"], 4) if z["count"] else 0.0

        run_summary["anchor_range"] = {
            "first_anchor": iso(first_anchor),
            "last_anchor": iso(last_anchor),
        }
        runs_report.append(run_summary)

    return {
        "definition": {
            "population": "log_windows where is_fault=True, round_no=1, strategy='time'",
            "abnormal_window": "stats.error_events > 0 OR len(key_events) > 0",
            "quiet_window": "stats.error_events == 0 AND len(key_events) == 0",
            "zones": ["pre_fault", "fault_nearby", "post_recovery"],
        },
        "sampled_runs": runs_report,
    }


def main() -> None:
    args = parse_args()
    client, db = connect(args.mongo_uri, args.mongo_db)
    try:
        col = db[args.collection]

        report = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "mongo_db": args.mongo_db,
            "collection": args.collection,
            "statistic_1_fault_window_contamination": statistic_1(col),
            "statistic_2_fault_run_time_window_sampling": statistic_2(
                col,
                limit_runs=args.limit_runs,
                sample_per_zone=args.sample_per_zone,
                preview_chars=args.preview_chars,
            ),
        }

        pretty = json.dumps(report, ensure_ascii=False, indent=2, default=str)
        print(pretty)

        if args.out_json:
            out = Path(args.out_json)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(pretty, encoding="utf-8")
            print(f"\n[OK] report saved to: {out}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
