#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
01_scan_dataset.py
目的：扫描 data/dataset/ 生成统一入口清单 manifest（JSONL），后续所有脚本都只读这个 manifest。

期望的数据目录结构（示例）：
data/dataset/
├── Nova/
│   ├── Test_1/
│   │   ├── fip_info.data
│   │   └── logs/
│   │       ├── round_1/
│   │       └── round_2/
│   └── ...
├── Cinder/
└── Neutron/

输出（JSON Lines）每行一个 run，至少字段：
- system
- subsystem
- test_name
- case_id
- round_no
- run_id
- is_fault
- log_dir
- fip_path

用法：
  python3 01_scan_dataset.py --dataset-root data/dataset --out artifacts/manifest.jsonl --system openstack

可选：
  --strict     开启严格校验：缺 fip 或缺 round 目录会报错退出
  --abs-path   输出绝对路径（log_dir / fip_path）
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple


ROUND_DIR_PATTERN = re.compile(r"^round[_-]?(\d+)$", re.IGNORECASE)


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def norm_subsystem(name: str) -> str:
    """保持你目录里的名字即可（Nova/Cinder/Neutron）；这里只做轻微清洗。"""
    return name.strip()


def list_dirs(path: str) -> List[str]:
    try:
        return sorted([d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))])
    except FileNotFoundError:
        return []


def find_fip_file(test_dir: str) -> Optional[str]:
    """
    在 Test_x/ 目录下寻找故障注入信息文件。
    目前按你给的固定命名：fip_info.data
    如果未来命名不固定，可以扩展成多个候选名。
    """
    candidate = os.path.join(test_dir, "fip_info.data")
    return candidate if os.path.isfile(candidate) else None


def find_logs_root(test_dir: str) -> Optional[str]:
    candidate = os.path.join(test_dir, "logs")
    return candidate if os.path.isdir(candidate) else None


def parse_round_no(round_dir_name: str) -> Optional[int]:
    m = ROUND_DIR_PATTERN.match(round_dir_name)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def is_fault_from_round(round_no: int) -> bool:
    """
    你的数据约定：
    - round_1 = 故障执行轮次 => is_fault = True
    - round_2 = 正常执行轮次 => is_fault = False
    如果未来 round_3/4 也有含义，可以在这里扩展。
    """
    if round_no == 1:
        return True
    if round_no == 2:
        return False
    # 默认：未知 round 先标 False（也可以改成 None 并在 strict 模式下报错）
    return False


def make_case_id(system: str, subsystem: str, test_name: str, include_system: bool) -> str:
    """
    你提的例子是 case_id: Nova_Test_1（不含 system）。
    但强烈建议 include_system=True，让全局唯一：openstack_Nova_Test_1
    这里提供开关。
    """
    if include_system:
        return f"{system}_{subsystem}_{test_name}"
    return f"{subsystem}_{test_name}"


def make_run_id(case_id: str, round_no: int) -> str:
    return f"{case_id}_round_{round_no}"


def validate_run(log_dir: str) -> Tuple[bool, str]:
    """
    简单校验：round 目录下至少有一个文件（不限定后缀）
    """
    if not os.path.isdir(log_dir):
        return False, "log_dir_not_found"
    for root, _, files in os.walk(log_dir):
        for f in files:
            # 只要有文件就算存在日志（后续 ingest 脚本再过滤具体后缀）
            return True, "ok"
    return False, "log_dir_empty"


def scan_dataset(
    dataset_root: str,
    system: str,
    include_system_in_ids: bool,
    abs_path: bool,
    strict: bool,
) -> List[Dict]:
    runs: List[Dict] = []

    subsystems = list_dirs(dataset_root)
    if not subsystems:
        msg = f"Dataset root has no subsystem directories: {dataset_root}"
        if strict:
            raise RuntimeError(msg)
        eprint("[WARN]", msg)
        return runs

    for subsystem_dir_name in subsystems:
        subsystem_path = os.path.join(dataset_root, subsystem_dir_name)
        subsystem = norm_subsystem(subsystem_dir_name)

        test_dirs = list_dirs(subsystem_path)
        if not test_dirs:
            eprint("[WARN]", f"No tests found under subsystem: {subsystem_path}")
            continue

        for test_name in test_dirs:
            test_dir = os.path.join(subsystem_path, test_name)

            fip_path = find_fip_file(test_dir)
            logs_root = find_logs_root(test_dir)

            if not logs_root:
                msg = f"Missing logs/ directory: {test_dir}"
                if strict:
                    raise RuntimeError(msg)
                eprint("[WARN]", msg)
                continue

            # case_id / run_id
            case_id = make_case_id(system, subsystem, test_name, include_system_in_ids)

            # round directories under logs/
            round_dirs = list_dirs(logs_root)
            parsed_rounds: List[Tuple[int, str]] = []
            for rd in round_dirs:
                rn = parse_round_no(rd)
                if rn is None:
                    # 忽略非 round_* 目录
                    continue
                parsed_rounds.append((rn, os.path.join(logs_root, rd)))

            if not parsed_rounds:
                msg = f"No round_* directories under: {logs_root}"
                if strict:
                    raise RuntimeError(msg)
                eprint("[WARN]", msg)
                continue

            # 如果缺 fip_info.data：是否允许？
            # round_2（正常轮）也许没有 fip，但你的目录结构里它在 Test_1/ 下通常都有。
            if not fip_path:
                msg = f"Missing fip_info.data: {test_dir}"
                if strict:
                    raise RuntimeError(msg)
                eprint("[WARN]", msg)

            for round_no, round_log_dir in sorted(parsed_rounds, key=lambda x: x[0]):
                ok, reason = validate_run(round_log_dir)
                if not ok:
                    msg = f"Skip run: {case_id} round_{round_no} ({reason}) dir={round_log_dir}"
                    if strict:
                        raise RuntimeError(msg)
                    eprint("[WARN]", msg)
                    continue

                run_id = make_run_id(case_id, round_no)
                is_fault = is_fault_from_round(round_no)

                rec = {
                    "system": system,
                    "subsystem": subsystem,
                    "test_name": test_name,
                    "case_id": case_id,
                    "round_no": round_no,
                    "run_id": run_id,
                    "is_fault": is_fault,
                    "log_dir": os.path.abspath(round_log_dir) if abs_path else round_log_dir,
                    "fip_path": (os.path.abspath(fip_path) if (abs_path and fip_path) else fip_path),
                    "scanned_at": datetime.now().isoformat(timespec="seconds"),
                }
                runs.append(rec)

    return runs


def write_jsonl(records: List[Dict], out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_summary(records: List[Dict], out_path: str) -> None:
    """
    输出一个小 summary，方便你一眼看对不对。
    """
    summary = {
        "total_runs": len(records),
        "by_subsystem": {},
        "by_round": {},
        "fault_vs_normal": {"fault": 0, "normal": 0},
    }
    for r in records:
        ss = r["subsystem"]
        summary["by_subsystem"][ss] = summary["by_subsystem"].get(ss, 0) + 1
        rn = str(r["round_no"])
        summary["by_round"][rn] = summary["by_round"].get(rn, 0) + 1
        if r["is_fault"]:
            summary["fault_vs_normal"]["fault"] += 1
        else:
            summary["fault_vs_normal"]["normal"] += 1

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser(description="Scan dataset and generate manifest.jsonl (one run per line).")
    ap.add_argument("--dataset-root", required=True, help="Root of dataset, e.g. data/dataset")
    ap.add_argument("--out", required=True, help="Output manifest JSONL path, e.g. artifacts/manifest.jsonl")
    ap.add_argument("--system", default="openstack", help="System id, e.g. openstack/k8s/mysql ...")
    ap.add_argument(
        "--include-system-in-ids",
        action="store_true",
        help="If set, case_id/run_id include system prefix (recommended for multi-system).",
    )
    ap.add_argument("--abs-path", action="store_true", help="Write absolute paths for log_dir/fip_path.")
    ap.add_argument("--strict", action="store_true", help="Strict validation: missing parts cause failure.")
    ap.add_argument("--summary-out", default="", help="Optional summary JSON output path.")

    args = ap.parse_args()

    dataset_root = args.dataset_root
    if not os.path.isdir(dataset_root):
        die(f"dataset-root not found or not a directory: {dataset_root}")

    records = scan_dataset(
        dataset_root=dataset_root,
        system=args.system,
        include_system_in_ids=args.include_system_in_ids,
        abs_path=args.abs_path,
        strict=args.strict,
    )

    if not records:
        die("No runs found. Check your dataset-root structure or use --strict to see errors.", 2)

    write_jsonl(records, args.out)
    info_path = os.path.abspath(args.out)
    print(f"[OK] Wrote manifest: {info_path} (runs={len(records)})")

    if args.summary_out:
        write_summary(records, args.summary_out)
        print(f"[OK] Wrote summary: {os.path.abspath(args.summary_out)}")


def die(msg: str, code: int = 1) -> None:
    eprint(f"[FATAL] {msg}")
    sys.exit(code)


if __name__ == "__main__":
    main()
