#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03_ingest_logs_to_mongo_v2.py
目的：沿用旧版日志解析逻辑，但适配新结构：
- MongoDB: log_entries
- MySQL: runs.total_logs / error_logs / critical_logs / stats_json / start_time / end_time
- 可选：为每个 run 自动在 evidence_inputs 中登记一条原始日志输入记录
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterator, List, Optional, Tuple

from dateutil import parser as dtparser
import pymysql
from pymysql.cursors import DictCursor
from pymongo import ASCENDING, MongoClient
from pymongo.errors import BulkWriteError

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

COL_LOG_ENTRIES = 'log_entries'
COL_EVIDENCE_INPUTS = 'evidence_inputs'

LOG_RE_ISO_T = re.compile(
    r"""^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+
        (?P<host>[\w.-]+)\s+
        (?P<pid>\d+)\s+
        (?P<level>[A-Z]+)\s+
        (?P<module>[\w.]+)\s+
        \[(?P<reqinfo>[^\]]*)\]\s+
        (?P<msg>.*)$
    """,
    re.VERBOSE,
)
LOG_RE_SPACE_MS = re.compile(
    r"""^(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+
        (?P<pid>\d+)\s+
        (?P<level>[A-Z]+)\s+
        (?P<module>[\w.]+)\s+
        \[(?P<reqinfo>[^\]]*)\]\s+
        (?P<msg>.*)$
    """,
    re.VERBOSE,
)
LOG_RE_SPACE_HOST = re.compile(
    r"""^(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+
        (?P<host>[\w.-]+)\s+
        (?P<pid>\d+)\s+
        (?P<level>[A-Z]+)\s+
        (?P<module>[\w.]+)\s+
        \[(?P<reqinfo>[^\]]*)\]\s+
        (?P<msg>.*)$
    """,
    re.VERBOSE,
)


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


def mongo_connect():
    client = MongoClient(MONGO_URI)
    return client, client[MONGO_DB]


def load_manifest(path: str) -> List[RunRec]:
    out: List[RunRec] = []
    with open(path, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            for k in ('system', 'subsystem', 'test_name', 'case_id', 'round_no', 'run_id', 'is_fault', 'log_dir'):
                if k not in obj:
                    raise ValueError(f"manifest line {i} missing {k}")
            out.append(RunRec(
                system=str(obj['system']), subsystem=str(obj['subsystem']), test_name=str(obj['test_name']),
                case_id=str(obj['case_id']), round_no=int(obj['round_no']), run_id=str(obj['run_id']),
                is_fault=bool(obj['is_fault']), log_dir=str(obj['log_dir']),
                fip_path=str(obj['fip_path']) if obj.get('fip_path') else None,
            ))
    return out


def parse_reqinfo(reqinfo: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    parts = [p for p in reqinfo.split() if p]
    return (parts[0] if len(parts) >= 1 else None,
            parts[1] if len(parts) >= 2 else None,
            parts[2] if len(parts) >= 3 else None)


def parse_timestamp(ts_str: str) -> Optional[datetime]:
    try:
        dt = dtparser.parse(ts_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone(tz=None).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def parse_log_line(line: str) -> Optional[Dict]:
    line = line.rstrip('\n')
    if not line.strip():
        return None
    for regex in (LOG_RE_ISO_T, LOG_RE_SPACE_HOST, LOG_RE_SPACE_MS):
        m = regex.match(line)
        if not m:
            continue
        ts = parse_timestamp(m.group('ts'))
        if not ts:
            return None
        req_id, user_id, proj_id = parse_reqinfo(m.group('reqinfo'))
        return {
            'timestamp': ts,
            'hostname': m.groupdict().get('host'),
            'process_id': int(m.group('pid')),
            'level': m.group('level'),
            'module': m.group('module'),
            'request_id': req_id,
            'user_id': user_id,
            'project_id': proj_id,
            'message': m.group('msg').strip(),
        }
    return None


def iter_files(root_dir: str) -> Iterator[str]:
    for root, _, files in os.walk(root_dir):
        for fn in sorted(files):
            yield os.path.join(root, fn)


def ensure_indexes(col) -> None:
    col.create_index([('run_id', ASCENDING), ('timestamp', ASCENDING)], name='idx_run_time')
    col.create_index([('run_id', ASCENDING), ('level', ASCENDING)], name='idx_run_level')
    col.create_index([('run_id', ASCENDING), ('module', ASCENDING)], name='idx_run_module')


def update_run_stats(cur, run_id: str, start_time: Optional[datetime], end_time: Optional[datetime], stats: Dict) -> None:
    cur.execute(
        """
        UPDATE runs
        SET start_time = %s,
            end_time = %s,
            total_logs = %s,
            error_logs = %s,
            critical_logs = %s,
            stats_json = %s,
            status = 'ingested',
            updated_at = CURRENT_TIMESTAMP
        WHERE run_id = %s
        """,
        (
            start_time,
            end_time,
            int(stats.get('total_lines', 0)),
            int(stats.get('error_lines', 0)),
            int(stats.get('critical_lines', 0)),
            json.dumps(stats, ensure_ascii=False),
            run_id,
        ),
    )


def upsert_evidence_input(col, run: RunRec, stats: Dict) -> None:
    doc = {
        '_id': f'evidence::log::{run.run_id}',
        'task_id': f'task::{run.run_id}',
        'input_type': 'log_text',
        'source_type': 'dataset',
        'source_name': run.run_id,
        'content': {
            'log_dir': run.log_dir,
            'case_id': run.case_id,
            'run_id': run.run_id,
            'stats': stats,
        },
        'created_at': datetime.utcnow(),
        'meta': {
            'system_id': run.system,
            'subsystem': run.subsystem,
            'round_no': run.round_no,
            'is_fault': run.is_fault,
        },
    }
    col.replace_one({'_id': doc['_id']}, doc, upsert=True)


def ingest_one_run(col, run: RunRec, batch_size: int, drop_run_first: bool) -> Tuple[Dict, Optional[datetime], Optional[datetime]]:
    if drop_run_first:
        deleted = col.delete_many({'run_id': run.run_id}).deleted_count
        print(f"[INFO] deleted existing log_entries: run_id={run.run_id} deleted={deleted}")

    total_lines = 0
    parsed_lines = 0
    error_lines = 0
    critical_lines = 0
    level_dist = Counter()
    module_dist = Counter()
    module_error = Counter()
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    buffer: List[Dict] = []

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        try:
            col.insert_many(buffer, ordered=False)
        except BulkWriteError as e:
            print(f"[WARN] BulkWriteError inserted={e.details.get('nInserted')} errors={len(e.details.get('writeErrors', []))}")
        buffer = []

    for fp in iter_files(run.log_dir):
        try:
            with open(fp, 'r', encoding='utf-8', errors='replace') as f:
                for line_no, line in enumerate(f, 1):
                    total_lines += 1
                    raw_line = line.rstrip('\n')
                    parsed = parse_log_line(raw_line)
                    if not parsed:
                        continue
                    parsed_lines += 1
                    level = parsed.get('level') or 'UNKNOWN'
                    module = parsed.get('module') or 'UNKNOWN'
                    ts = parsed.get('timestamp')
                    level_dist[level] += 1
                    module_dist[module] += 1
                    if level in ('ERROR', 'CRITICAL'):
                        error_lines += 1
                        module_error[module] += 1
                    if level == 'CRITICAL':
                        critical_lines += 1
                    if isinstance(ts, datetime):
                        start_time = ts if start_time is None or ts < start_time else start_time
                        end_time = ts if end_time is None or ts > end_time else end_time

                    doc = {
                        'system_id': run.system,
                        'subsystem': run.subsystem,
                        'test_name': run.test_name,
                        'case_id': run.case_id,
                        'run_id': run.run_id,
                        'round_no': run.round_no,
                        'is_fault': run.is_fault,
                        'task_id': f'task::{run.run_id}',
                        'timestamp': parsed.get('timestamp'),
                        'hostname': parsed.get('hostname'),
                        'process_id': parsed.get('process_id'),
                        'level': parsed.get('level'),
                        'module': parsed.get('module'),
                        'request_id': parsed.get('request_id'),
                        'user_id': parsed.get('user_id'),
                        'project_id': parsed.get('project_id'),
                        'message': parsed.get('message'),
                        'raw_line': raw_line,
                        'line_no': line_no,
                        'file_path': fp,
                        'data_origin': 'dataset',
                        'ingested_at': datetime.utcnow(),
                    }
                    buffer.append(doc)
                    if len(buffer) >= batch_size:
                        flush()
        except Exception as e:
            print(f"[WARN] failed reading file {fp}: {e}")
    flush()

    stats = {
        'total_lines': total_lines,
        'parsed_lines': parsed_lines,
        'error_lines': error_lines,
        'critical_lines': critical_lines,
        'level_distribution': dict(level_dist),
        'top_modules': [{'module': m, 'count': c} for m, c in module_dist.most_common(10)],
        'top_error_modules': [{'module': m, 'error_count': c} for m, c in module_error.most_common(10)],
        'log_dir': run.log_dir,
        'notes': {'parser': '03_ingest_logs_to_mongo_v2.py'},
    }
    return stats, start_time, end_time


def main() -> None:
    ap = argparse.ArgumentParser(description='Ingest logs into MongoDB and update MySQL run stats.')
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--batch-size', type=int, default=2000)
    ap.add_argument('--limit-runs', type=int, default=0)
    ap.add_argument('--only-run-id', default='')
    ap.add_argument('--drop-run-first', action='store_true')
    ap.add_argument('--skip-evidence-input', action='store_true')
    args = ap.parse_args()

    runs = load_manifest(args.manifest)
    if args.only_run_id:
        runs = [r for r in runs if r.run_id == args.only_run_id]
    if args.limit_runs:
        runs = runs[: args.limit_runs]
    if not runs:
        print('[FATAL] no runs to process', file=sys.stderr)
        raise SystemExit(2)

    mongo_client, db = mongo_connect()
    col = db[COL_LOG_ENTRIES]
    evidence_col = db[COL_EVIDENCE_INPUTS]
    ensure_indexes(col)

    mysql_conn = mysql_connect()
    try:
        for i, run in enumerate(runs, 1):
            print(f"\n[INFO] ({i}/{len(runs)}) run_id={run.run_id}")
            if not os.path.isdir(run.log_dir):
                print(f"[WARN] log_dir not found: {run.log_dir}")
                continue
            stats, start_time, end_time = ingest_one_run(col, run, args.batch_size, args.drop_run_first)
            print(f"[INFO] total={stats['total_lines']} parsed={stats['parsed_lines']} error={stats['error_lines']}")
            with mysql_conn.cursor() as cur:
                update_run_stats(cur, run.run_id, start_time, end_time, stats)
            if not args.skip_evidence_input:
                upsert_evidence_input(evidence_col, run, stats)
            mysql_conn.commit()
        print('[OK] Log ingestion completed.')
    except Exception:
        mysql_conn.rollback()
        raise
    finally:
        mysql_conn.close()
        mongo_client.close()


if __name__ == '__main__':
    main()
