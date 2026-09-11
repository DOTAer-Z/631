"""
import_test_jsonl.py — 将 test.jsonl 结构化数据全量导入主系统 runs。

用法（在 backend 目录、容器内）：
    cd /app
    PYTHONPATH=. python import_test_jsonl.py /app/data/test.jsonl
    # 可选：
    PYTHONPATH=. python import_test_jsonl.py /app/data/test.jsonl --limit 20
    PYTHONPATH=. python import_test_jsonl.py /app/data/test.jsonl \
        --only Test_50005:round_1:qemu_console --only Test_50005:round_2:qemu_console
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 允许 `from app...` 导入
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.database import SessionLocal, get_mongo_db
from app.services.structured_log_import_service import import_jsonl_file


def main() -> int:
    ap = argparse.ArgumentParser(description="导入 test.jsonl 结构化数据到主系统 runs")
    ap.add_argument("jsonl_path", type=Path, help="test.jsonl 文件路径")
    ap.add_argument("--window-size-s", type=int, default=30)
    ap.add_argument("--stride-s", type=int, default=15)
    ap.add_argument("--limit", type=int, default=None, help="只导入前 N 条（调试）")
    ap.add_argument("--only", action="append", default=None,
                    help="只导入指定 run_id（可多次）")
    ap.add_argument("--out", type=Path, default=None,
                    help="把逐条结果写入 jsonl 文件（调试/审计用）")
    args = ap.parse_args()

    path = args.jsonl_path.resolve()
    if not path.is_file():
        print(f"[import_test_jsonl] 文件不存在: {path}", file=sys.stderr)
        return 2

    print(f"[import_test_jsonl] 导入 {path} (limit={args.limit})")
    db = SessionLocal()
    mongo_db = get_mongo_db()
    try:
        result = import_jsonl_file(
            db, mongo_db, path,
            window_size_s=args.window_size_s, stride_s=args.stride_s,
            limit=args.limit, only=args.only,
        )
    finally:
        db.close()

    print(
        f"[import_test_jsonl] 完成：attempted={result['attempted']} "
        f"ok={result['ok']} failed={result['failed']} "
        f"fault_runs={result['fault_runs']} normal_runs={result['normal_runs']} "
        f"log_entries={result['entries']} log_windows={result['windows']}"
    )

    if result["failures"]:
        print("[import_test_jsonl] 失败明细：")
        for f in result["failures"]:
            print(f"  {json.dumps(f, ensure_ascii=False)}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            for s in result["successes"]:
                fh.write(json.dumps(s, ensure_ascii=False) + "\n")
        print(f"[import_test_jsonl] 逐条结果已写入 {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
