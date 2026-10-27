#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
02_parse_fip_info_to_mysql.py
目的：读取 manifest.jsonl，解析每个 case 的 fip_info.data，并写入 MySQL：
- systems
- subsystems
- components
- cases
- runs
- labels / object_labels
- diagnosis_tasks / diagnosis_results（可选）

说明：
- manifest 格式沿用旧脚本；因此 01_scan_dataset.py 可直接复用。 
- 新结构中 labels 是字典表，具体对象绑定写入 object_labels。
- 脚本默认会为每个 run 生成一个 diagnosis_task，并生成一条黄金结果 diagnosis_result。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pymysql
from pymysql.cursors import DictCursor


from config import (
    MYSQL_HOST,
    MYSQL_PORT,
    MYSQL_USER,
    MYSQL_PASSWORD,
    MYSQL_DB,
    MYSQL_CHARSET, 
    MONGO_URI,
    MONGO_DB,
    MANIFEST_PATH,
    VECTOR_OUT_DIR,
    KG_OUT_DIR,
    ensure_dirs,
    print_config,
)

ensure_dirs()
print_config()

FIP_LINE_RE = re.compile(r"^\s*([A-Z0-9 _/-]+?)\s*:\s*(.*?)\s*$")
FUNC_DEF_RE = re.compile(r"^\s*(?P<func>[A-Za-z_][A-Za-z0-9_]*)\s*\+\s*(?P<line>\d+)\s*;\s*(?P<col>\d+)\s*$")


@dataclass
class RunRec:
    system: str
    subsystem: str
    test_name: str
    case_id: str
    round_no: int
    run_id: str
    is_fault: bool
    log_dir: str
    fip_path: Optional[str]


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


def load_manifest(path: str) -> List[RunRec]:
    runs: List[RunRec] = []
    with open(path, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            for k in ('system', 'subsystem', 'test_name', 'case_id', 'round_no', 'run_id', 'is_fault', 'log_dir'):
                if k not in obj:
                    raise ValueError(f"manifest line {i} missing {k}")
            runs.append(
                RunRec(
                    system=str(obj['system']),
                    subsystem=str(obj['subsystem']),
                    test_name=str(obj['test_name']),
                    case_id=str(obj['case_id']),
                    round_no=int(obj['round_no']),
                    run_id=str(obj['run_id']),
                    is_fault=bool(obj['is_fault']),
                    log_dir=str(obj['log_dir']),
                    fip_path=str(obj['fip_path']) if obj.get('fip_path') else None,
                )
            )
    return runs


def group_by_case(runs: List[RunRec]) -> Dict[str, List[RunRec]]:
    out: Dict[str, List[RunRec]] = {}
    for r in runs:
        out.setdefault(r.case_id, []).append(r)
    return out


def read_text_file(path: str) -> str:
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        return f.read()


def parse_fip_text(raw: str) -> Dict:
    kv: Dict[str, str] = {}
    for line in raw.splitlines():
        m = FIP_LINE_RE.match(line)
        if not m:
            continue
        key = re.sub(r"\s+", "_", m.group(1).strip()).upper()
        kv[key] = m.group(2).strip()

    func_def_raw = kv.get('TARGET_FUNCTION_DEF')
    target_function = None
    if func_def_raw:
        m = FUNC_DEF_RE.match(func_def_raw)
        if m:
            target_function = {
                'name': m.group('func'),
                'line': int(m.group('line')),
                'col': int(m.group('col')),
                'raw': func_def_raw,
            }
        else:
            target_function = {'raw': func_def_raw}

    parsed = {
        'fault_type': kv.get('FAULT_TYPE'),
        'target_component': kv.get('TARGET_COMPONENT'),
        'target_class': None if (kv.get('TARGET_CLASS') or '').lower() == 'none' else kv.get('TARGET_CLASS'),
        'target_function': target_function,
        'fault_point': kv.get('FAULT_POINT'),
        'raw_kv': kv,
    }
    parsed['_parse_warnings'] = [
        f'missing_{name}'
        for name in ('fault_type', 'target_component', 'fault_point')
        if not parsed.get(name)
    ]
    return parsed


def normalize_component_code(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    s = path.strip().replace('\\', '/')
    return s.lstrip('/')


def upsert_system(cur, system_id: str) -> None:
    cur.execute(
        """
        INSERT INTO systems(system_id, system_code, system_name)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
          system_name = VALUES(system_name),
          updated_at = CURRENT_TIMESTAMP;
        """,
        (system_id, system_id, system_id),
    )


def upsert_subsystem(cur, system_id: str, subsystem_name: str) -> int:
    subsystem_code = subsystem_name
    cur.execute(
        """
        INSERT INTO subsystems(system_id, subsystem_code, subsystem_name)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
          subsystem_name = VALUES(subsystem_name),
          updated_at = CURRENT_TIMESTAMP,
          subsystem_id = LAST_INSERT_ID(subsystem_id);
        """,
        (system_id, subsystem_code, subsystem_name),
    )
    return int(cur.lastrowid)


def upsert_component(cur, system_id: str, subsystem_id: Optional[int], target_component: Optional[str]) -> Optional[int]:
    component_code = normalize_component_code(target_component)
    if not component_code:
        return None
    component_name = component_code.split('/')[-1]
    cur.execute(
        """
        INSERT INTO components(system_id, subsystem_id, component_code, component_name, component_path)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          subsystem_id = VALUES(subsystem_id),
          component_name = VALUES(component_name),
          component_path = VALUES(component_path),
          updated_at = CURRENT_TIMESTAMP,
          component_id = LAST_INSERT_ID(component_id);
        """,
        (system_id, subsystem_id, component_code, component_name, target_component),
    )
    return int(cur.lastrowid)


def upsert_case(cur, run0: RunRec, subsystem_id: Optional[int], component_id: Optional[int], fip_info: Dict, root_cause: Dict) -> None:
    target_component = root_cause.get('target_component')
    target_function = (root_cause.get('target_function') or {}).get('name') if isinstance(root_cause.get('target_function'), dict) else None
    cur.execute(
        """
        INSERT INTO cases(
          case_id, system_id, subsystem_id, case_code, case_name, test_name,
          is_fault, fault_type, root_cause_type, target_component_id,
          target_component_path, target_function, injection_point,
          fip_info_json, root_cause_json
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          system_id = VALUES(system_id),
          subsystem_id = VALUES(subsystem_id),
          case_code = VALUES(case_code),
          case_name = VALUES(case_name),
          test_name = VALUES(test_name),
          is_fault = VALUES(is_fault),
          fault_type = VALUES(fault_type),
          root_cause_type = VALUES(root_cause_type),
          target_component_id = VALUES(target_component_id),
          target_component_path = VALUES(target_component_path),
          target_function = VALUES(target_function),
          injection_point = VALUES(injection_point),
          fip_info_json = VALUES(fip_info_json),
          root_cause_json = VALUES(root_cause_json),
          updated_at = CURRENT_TIMESTAMP;
        """,
        (
            run0.case_id,
            run0.system,
            subsystem_id,
            run0.case_id,
            run0.case_id,
            run0.test_name,
            1,
            root_cause.get('fault_type'),
            root_cause.get('fault_type'),
            component_id,
            target_component,
            target_function,
            root_cause.get('fault_point'),
            json.dumps(fip_info, ensure_ascii=False),
            json.dumps(root_cause, ensure_ascii=False),
        ),
    )


def upsert_run(cur, run: RunRec) -> None:
    cur.execute(
        """
        INSERT INTO runs(run_id, case_id, system_id, round_no, run_name, status, is_fault, log_dir)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          case_id = VALUES(case_id),
          system_id = VALUES(system_id),
          round_no = VALUES(round_no),
          run_name = VALUES(run_name),
          status = VALUES(status),
          is_fault = VALUES(is_fault),
          log_dir = VALUES(log_dir),
          updated_at = CURRENT_TIMESTAMP;
        """,
        (run.run_id, run.case_id, run.system, run.round_no, run.run_id, 'ready', int(run.is_fault), run.log_dir),
    )


def ensure_label(cur, label_type: str, label_code: str, label_name: Optional[str] = None, label_desc: Optional[str] = None) -> int:
    cur.execute(
        """
        INSERT INTO labels(label_type, label_code, label_name, label_desc)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          label_name = VALUES(label_name),
          label_desc = VALUES(label_desc),
          updated_at = CURRENT_TIMESTAMP,
          label_id = LAST_INSERT_ID(label_id);
        """,
        (label_type, label_code, label_name or label_code, label_desc),
    )
    return int(cur.lastrowid)


def bind_label(cur, object_type: str, object_id: str, label_id: int, source: str, payload: Optional[Dict] = None, confidence: Optional[float] = None) -> None:
    cur.execute(
        """
        INSERT INTO object_labels(object_type, object_id, label_id, source, confidence, payload_json)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          confidence = VALUES(confidence),
          payload_json = VALUES(payload_json);
        """,
        (object_type, object_id, label_id, source, confidence, json.dumps(payload, ensure_ascii=False) if payload else None),
    )


def upsert_task(cur, run: RunRec) -> str:
    task_id = f"task::{run.run_id}"
    cur.execute(
        """
        INSERT INTO diagnosis_tasks(task_id, task_code, task_type, source_mode, system_id, case_id, run_id, status, trigger_source, task_desc)
        VALUES (%s, %s, 'fault_diagnosis', 'log', %s, %s, %s, 'ready', 'dataset', %s)
        ON DUPLICATE KEY UPDATE
          status = VALUES(status),
          task_desc = VALUES(task_desc),
          run_id = VALUES(run_id),
          case_id = VALUES(case_id);
        """,
        (task_id, task_id, run.system, run.case_id, run.run_id, f"Dataset task for {run.run_id}"),
    )
    return task_id


def upsert_result(cur, task_id: str, component_id: Optional[int], root_cause: Dict, run: RunRec, version: int = 1) -> None:
    target_function = (root_cause.get('target_function') or {}).get('name') if isinstance(root_cause.get('target_function'), dict) else None
    cur.execute(
        """
        INSERT INTO diagnosis_results(
          task_id, result_version, is_fault, fault_type, root_cause, root_cause_type,
          target_component_id, target_function, severity, confidence, recommendation,
          explanation_text, decision_path, result_status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          is_fault = VALUES(is_fault),
          fault_type = VALUES(fault_type),
          root_cause = VALUES(root_cause),
          root_cause_type = VALUES(root_cause_type),
          target_component_id = VALUES(target_component_id),
          target_function = VALUES(target_function),
          severity = VALUES(severity),
          confidence = VALUES(confidence),
          recommendation = VALUES(recommendation),
          explanation_text = VALUES(explanation_text),
          decision_path = VALUES(decision_path),
          result_status = VALUES(result_status),
          updated_at = CURRENT_TIMESTAMP;
        """,
        (
            task_id,
            version,
            int(run.is_fault),
            root_cause.get('fault_type'),
            root_cause.get('fault_point'),
            root_cause.get('fault_type'),
            component_id,
            target_function,
            'high' if run.is_fault else 'normal',
            1.0,
            'Use as golden label from fip_info.data',
            json.dumps(root_cause, ensure_ascii=False),
            'fip',
            'golden',
        ),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description='Parse fip_info.data and write new-structure records into MySQL.')
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--skip-tasks', action='store_true', help='Do not create diagnosis_tasks / diagnosis_results.')
    ap.add_argument('--label-source', default='fip')
    args = ap.parse_args()

    if not os.path.isfile(args.manifest):
        print(f"[FATAL] manifest not found: {args.manifest}", file=sys.stderr)
        raise SystemExit(2)

    runs = load_manifest(args.manifest)
    by_case = group_by_case(runs)
    print(f"[INFO] runs={len(runs)} unique_cases={len(by_case)}")

    if args.dry_run:
        for case_id, case_runs in by_case.items():
            fip_path = next((r.fip_path for r in case_runs if r.fip_path), None)
            print(f"[DRY] case_id={case_id} fip_path={fip_path}")
        return

    conn = mysql_connect()
    try:
        with conn.cursor() as cur:
            for case_id, case_runs in by_case.items():
                run0 = case_runs[0]
                fip_path = next((r.fip_path for r in case_runs if r.fip_path and os.path.isfile(r.fip_path)), None)
                if not fip_path:
                    print(f"[WARN] case_id={case_id} missing usable fip_path; skip")
                    continue

                raw = read_text_file(fip_path)
                parsed = parse_fip_text(raw)
                fip_info = {'raw_text': raw, 'parsed': parsed, 'fip_path': fip_path}
                root_cause = {
                    'fault_type': parsed.get('fault_type'),
                    'target_component': parsed.get('target_component'),
                    'target_class': parsed.get('target_class'),
                    'target_function': parsed.get('target_function'),
                    'fault_point': parsed.get('fault_point'),
                    'source': 'fip',
                }

                upsert_system(cur, run0.system)
                subsystem_id = upsert_subsystem(cur, run0.system, run0.subsystem)
                component_id = upsert_component(cur, run0.system, subsystem_id, root_cause.get('target_component'))
                upsert_case(cur, run0, subsystem_id, component_id, fip_info, root_cause)

                fault_type = root_cause.get('fault_type')
                label_fault = ensure_label(cur, 'fault_type', fault_type or 'UNKNOWN_FAULT_TYPE')
                label_root = ensure_label(cur, 'root_cause', fault_type or 'UNKNOWN_ROOT_CAUSE')

                for run in case_runs:
                    upsert_run(cur, run)
                    bind_label(cur, 'run', run.run_id, label_fault, args.label_source, {'value': fault_type})
                    bind_label(cur, 'run', run.run_id, label_root, args.label_source, root_cause)
                    bind_label(cur, 'case', run.case_id, label_fault, args.label_source, {'value': fault_type})
                    bind_label(cur, 'case', run.case_id, label_root, args.label_source, root_cause)

                    if not args.skip_tasks:
                        task_id = upsert_task(cur, run)
                        upsert_result(cur, task_id, component_id, root_cause, run)

            conn.commit()
            print('[OK] FIP metadata written to MySQL.')
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
