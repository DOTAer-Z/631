#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
06_build_knowledge_graph_v3.py

目的：
从 MySQL + MongoDB 构建一份可落盘、可支持基础推理的知识图谱产物，
并为后续 graph_service.py 提供更稳定的数据基础。

输出：
  <out-dir>/
    kg_nodes.jsonl
    kg_edges.jsonl
    kg_summary.json

相对 V2 的主要增强：
1. EventPattern / FaultType / RootCause 支持额外元数据（严重度、预警等级、说明等）
2. EventPattern -> FaultType 的边不再只是“有就连”，而是先做聚合统计，再写入加权边
3. 过滤过泛 pattern，减少 pattern -> fault 关系污染
4. normalize_event_text / extract_event_patterns 可直接复用于在线诊断
5. 支持可选 JSON 元数据文件，无需强依赖新增 MySQL 表
6. 输出 summary 时增加 pattern 统计，方便后续 graph_service 做健康检查
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Set, Tuple

import pymysql
from pymysql.cursors import DictCursor
from pymongo import MongoClient


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

def mysql_connect():
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        charset=MYSQL_CHARSET,
        autocommit=True,
        cursorclass=DictCursor,
    )


def mongo_connect():
    client = MongoClient(MONGO_URI)
    return client, client[MONGO_DB]


def mysql_table_exists(cur, table_name: str) -> bool:
    sql = """
        SELECT COUNT(*) AS cnt
        FROM information_schema.tables
        WHERE table_schema = %s AND table_name = %s
    """
    cur.execute(sql, (MYSQL_DB, table_name))
    row = cur.fetchone()
    return bool(row and row.get("cnt", 0) > 0)


def load_json_file(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"JSON metadata file not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def add_node(
    nodes: Dict[str, Dict[str, Any]],
    node_id: str,
    node_type: str,
    props: Dict[str, Any],
) -> None:
    clean_props = {k: v for k, v in props.items() if v is not None}
    if node_id not in nodes:
        nodes[node_id] = {
            "id": node_id,
            "type": node_type,
            "properties": clean_props,
        }
    else:
        nodes[node_id]["properties"].update(clean_props)


def add_edge(
    edges: Dict[Tuple[str, str, str], Dict[str, Any]],
    src: str,
    rel: str,
    dst: str,
    evidence: Optional[str] = None,
    weight: int = 1,
    extra_props: Optional[Dict[str, Any]] = None,
) -> None:
    key = (src, rel, dst)
    if key not in edges:
        edges[key] = {
            "source": src,
            "relation": rel,
            "target": dst,
            "weight": 0,
            "evidence_count": 0,
            "evidences": [],
        }

    edges[key]["weight"] += weight
    if evidence:
        edges[key]["evidence_count"] += 1
        if evidence not in edges[key]["evidences"]:
            edges[key]["evidences"].append(evidence)

    if extra_props:
        for k, v in extra_props.items():
            if v is not None:
                edges[key][k] = v


def safe_str(x: Any) -> str:
    return "" if x is None else str(x)


def normalize_event_text(text: str) -> str:
    """
    对日志事件做简单归一化，提取可复用的事件模式。
    目标不是 NLP 精准抽取，而是构造一个稳定的 EventPattern。
    这个函数建议与在线诊断逻辑共用。
    """
    s = safe_str(text).lower().strip()

    # 去 request_id / uuid / 长数字 / IP / 十六进制地址
    s = re.sub(r"\breq-[a-z0-9-]+\b", "REQ_ID", s)
    s = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", "UUID", s)
    s = re.sub(r"\b0x[0-9a-f]+\b", "HEX", s)
    s = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "IP", s)
    s = re.sub(r"\b\d+\b", "NUM", s)

    # 去多余符号
    s = re.sub(r"[\[\]\(\)\{\}<>\'\"`]", " ", s)
    s = re.sub(r"[:;,=|/\\]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    # 只保留前若干词，避免 pattern 过长
    tokens = s.split()
    if len(tokens) > 12:
        tokens = tokens[:12]
    s = " ".join(tokens)

    return s if s else "unknown_event"


def is_generic_pattern(pattern: str) -> bool:
    """
    过滤过泛 pattern，避免图谱被“无信息症状”污染。
    这里保持保守，只过滤明显无区分度的文本。
    """
    if not pattern:
        return True

    generic_set = {
        "error",
        "warning",
        "failed",
        "exception",
        "unexpected error",
        "operation failed",
        "request failed",
        "unknown error",
        "unknown_event",
    }
    if pattern in generic_set:
        return True

    tokens = pattern.split()
    if len(tokens) <= 1:
        return True

    # 太短、信息量太少
    if len(pattern) < 8:
        return True

    return False


def extract_event_patterns(key_events: Any) -> List[str]:
    """
    从 log_windows.key_events 中尽量提取事件模式。
    支持：
    - ["text1", "text2"]
    - [{"message": "..."}]
    - [{"text": "..."}]
    - 其他可转字符串对象
    """
    patterns: List[str] = []

    if not key_events:
        return patterns

    if isinstance(key_events, list):
        for item in key_events:
            if isinstance(item, str):
                p = normalize_event_text(item)
            elif isinstance(item, dict):
                raw = (
                    item.get("message")
                    or item.get("text")
                    or item.get("event")
                    or item.get("content")
                    or json.dumps(item, ensure_ascii=False, default=str)
                )
                p = normalize_event_text(raw)
            else:
                p = normalize_event_text(str(item))

            if p and not is_generic_pattern(p):
                patterns.append(p)
    else:
        p = normalize_event_text(str(key_events))
        if p and not is_generic_pattern(p):
            patterns.append(p)

    # 去重但保序
    uniq: List[str] = []
    seen = set()
    for p in patterns:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def build_case_fault_map(nodes: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for node in nodes.values():
        if node["type"] == "Case":
            props = node["properties"]
            case_id = props.get("case_id")
            fault_type = props.get("fault_type")
            if case_id is not None and fault_type:
                mapping[str(case_id)] = str(fault_type)
    return mapping


def build_task_fault_map(
    edges: Dict[Tuple[str, str, str], Dict[str, Any]]
) -> Dict[str, List[str]]:
    mapping: Dict[str, List[str]] = defaultdict(list)
    for (src, rel, dst), _edge_obj in edges.items():
        if rel == "HAS_RESULT_FAULT" and src.startswith("Task::") and dst.startswith("FaultType::"):
            fault_type = dst.split("FaultType::", 1)[1]
            mapping[src].append(fault_type)
    return dict(mapping)


def build_fault_to_component_map(
    edges: Dict[Tuple[str, str, str], Dict[str, Any]]
) -> Dict[str, Set[str]]:
    """
    从 Task/Case -> FaultType 与 Task/Case -> Component 共同抽取 fault -> component 候选。
    便于 graph_service 后续快速补全组件候选。
    """
    owner_faults: DefaultDict[str, Set[str]] = defaultdict(set)
    owner_components: DefaultDict[str, Set[str]] = defaultdict(set)
    fault_to_components: DefaultDict[str, Set[str]] = defaultdict(set)

    for (src, rel, dst), _edge_obj in edges.items():
        if rel == "HAS_RESULT_FAULT" and (src.startswith("Task::") or src.startswith("Case::")):
            owner_faults[src].add(dst)
        elif rel == "TARGETS_COMPONENT" and (src.startswith("Task::") or src.startswith("Case::")):
            owner_components[src].add(dst)

    for owner, faults in owner_faults.items():
        comps = owner_components.get(owner, set())
        for fault_nid in faults:
            for comp_nid in comps:
                fault_to_components[fault_nid].add(comp_nid)

    return fault_to_components


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def enrich_fault_node_from_meta(
    fault_type: str,
    fault_meta: Dict[str, Any],
) -> Dict[str, Any]:
    meta = fault_meta.get(fault_type, {}) if fault_meta else {}
    return {
        "fault_type": fault_type,
        "severity": meta.get("severity"),
        "warning_level": meta.get("warning_level"),
        "description": meta.get("description"),
        "category": meta.get("category"),
    }


def enrich_root_cause_node_from_meta(
    root_cause_type: str,
    rc_meta: Dict[str, Any],
) -> Dict[str, Any]:
    meta = rc_meta.get(root_cause_type, {}) if rc_meta else {}
    return {
        "root_cause_type": root_cause_type,
        "severity": meta.get("severity"),
        "recovery_hint": meta.get("recovery_hint"),
        "description": meta.get("description"),
        "category": meta.get("category"),
    }


def enrich_pattern_node_from_meta(
    pattern: str,
    pattern_meta: Dict[str, Any],
) -> Dict[str, Any]:
    meta = pattern_meta.get(pattern, {}) if pattern_meta else {}
    return {
        "pattern": pattern,
        "risk_score": meta.get("risk_score"),
        "pattern_category": meta.get("pattern_category"),
        "description": meta.get("description"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build knowledge graph JSONL from MySQL + MongoDB.")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--window-limit", type=int, default=0, help="Max log_windows to attach (0 = all).")
    ap.add_argument("--fault-meta-json", default="", help="Optional JSON file for FaultType metadata.")
    ap.add_argument("--root-cause-meta-json", default="", help="Optional JSON file for RootCause metadata.")
    ap.add_argument("--pattern-meta-json", default="", help="Optional JSON file for EventPattern metadata.")
    ap.add_argument(
        "--min-pattern-fault-support",
        type=int,
        default=2,
        help="Minimum support count required before writing EventPattern -> FaultType.",
    )
    ap.add_argument(
        "--max-pattern-fault-fanout",
        type=int,
        default=5,
        help="If one pattern points to too many fault types, keep only top-N by support.",
    )
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    nodes_path = out_dir / "kg_nodes.jsonl"
    edges_path = out_dir / "kg_edges.jsonl"
    summary_path = out_dir / "kg_summary.json"

    fault_meta = load_json_file(args.fault_meta_json)
    rc_meta = load_json_file(args.root_cause_meta_json)
    pattern_meta = load_json_file(args.pattern_meta_json)

    nodes: Dict[str, Dict[str, Any]] = {}
    edges: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    # pattern -> fault 的聚合统计，避免边过于噪声
    pattern_fault_counter: DefaultDict[str, Counter] = defaultdict(Counter)
    # pattern 出现次数
    pattern_counter: Counter = Counter()

    mysql_conn = mysql_connect()
    mongo_client, mongo_db = mongo_connect()

    try:
        with mysql_conn.cursor() as cur:
            # ---------------------------
            # systems
            # ---------------------------
            cur.execute("SELECT * FROM systems")
            for s in cur.fetchall():
                sid = f"System::{s['system_id']}"
                add_node(
                    nodes,
                    sid,
                    "System",
                    {
                        "system_id": s["system_id"],
                        "name": s.get("system_name"),
                        "code": s.get("system_code"),
                    },
                )

            # ---------------------------
            # subsystems
            # ---------------------------
            cur.execute("SELECT * FROM subsystems")
            for ss in cur.fetchall():
                nid = f"Subsystem::{ss['subsystem_id']}"
                add_node(
                    nodes,
                    nid,
                    "Subsystem",
                    {
                        "subsystem_id": ss["subsystem_id"],
                        "name": ss.get("subsystem_name"),
                        "code": ss.get("subsystem_code"),
                    },
                )
                if ss.get("system_id") is not None:
                    add_edge(edges, f"System::{ss['system_id']}", "HAS_SUBSYSTEM", nid, evidence="subsystems")

            # ---------------------------
            # components
            # ---------------------------
            cur.execute("SELECT * FROM components")
            for c in cur.fetchall():
                nid = f"Component::{c['component_id']}"
                add_node(
                    nodes,
                    nid,
                    "Component",
                    {
                        "component_id": c["component_id"],
                        "name": c.get("component_name"),
                        "path": c.get("component_path"),
                        "code": c.get("component_code"),
                    },
                )
                if c.get("subsystem_id") is not None:
                    add_edge(edges, f"Subsystem::{c['subsystem_id']}", "HAS_COMPONENT", nid, evidence="components")

            # ---------------------------
            # optional component_dependencies
            # ---------------------------
            if mysql_table_exists(cur, "component_dependencies"):
                cur.execute("SELECT * FROM component_dependencies")
                for d in cur.fetchall():
                    src_id = d.get("component_id") or d.get("source_component_id")
                    dst_id = d.get("depends_on_component_id") or d.get("target_component_id")
                    if src_id is not None and dst_id is not None:
                        add_edge(
                            edges,
                            f"Component::{src_id}",
                            "DEPENDS_ON",
                            f"Component::{dst_id}",
                            evidence="component_dependencies",
                        )

            # ---------------------------
            # cases
            # ---------------------------
            cur.execute("SELECT * FROM cases")
            for c in cur.fetchall():
                nid = f"Case::{c['case_id']}"
                add_node(
                    nodes,
                    nid,
                    "Case",
                    {
                        "case_id": c["case_id"],
                        "case_code": c.get("case_code"),
                        "fault_type": c.get("fault_type"),
                        "root_cause_type": c.get("root_cause_type"),
                        "target_function": c.get("target_function"),
                    },
                )

                if c.get("subsystem_id") is not None:
                    add_edge(edges, f"Subsystem::{c['subsystem_id']}", "HAS_CASE", nid, evidence="cases")

                if c.get("target_component_id") is not None:
                    add_edge(
                        edges,
                        nid,
                        "TARGETS_COMPONENT",
                        f"Component::{c['target_component_id']}",
                        evidence="cases",
                    )

                fault_nid: Optional[str] = None
                rc_nid: Optional[str] = None

                if c.get("fault_type"):
                    fault_type = c["fault_type"]
                    fault_nid = f"FaultType::{fault_type}"
                    add_node(nodes, fault_nid, "FaultType", enrich_fault_node_from_meta(fault_type, fault_meta))
                    add_edge(edges, nid, "HAS_RESULT_FAULT", fault_nid, evidence="cases")

                if c.get("root_cause_type"):
                    rc_type = c["root_cause_type"]
                    rc_nid = f"RootCause::{rc_type}"
                    props = enrich_root_cause_node_from_meta(rc_type, rc_meta)
                    add_node(nodes, rc_nid, "RootCause", props)
                    add_edge(edges, nid, "HAS_ROOT_CAUSE", rc_nid, evidence="cases")

                if fault_nid and rc_nid:
                    add_edge(edges, rc_nid, "CAUSES_FAULT", fault_nid, evidence="cases")
                    add_edge(edges, fault_nid, "CAUSED_BY", rc_nid, evidence="cases")

            # ---------------------------
            # runs
            # ---------------------------
            cur.execute("SELECT * FROM runs")
            for r in cur.fetchall():
                nid = f"Run::{r['run_id']}"
                add_node(
                    nodes,
                    nid,
                    "Run",
                    {
                        "run_id": r["run_id"],
                        "round_no": r.get("round_no"),
                        "is_fault": bool(r.get("is_fault")),
                    },
                )
                if r.get("case_id") is not None:
                    add_edge(edges, f"Case::{r['case_id']}", "HAS_RUN", nid, evidence="runs")

            # ---------------------------
            # diagnosis_tasks
            # ---------------------------
            cur.execute("SELECT * FROM diagnosis_tasks")
            for t in cur.fetchall():
                nid = f"Task::{t['task_id']}"
                add_node(
                    nodes,
                    nid,
                    "Task",
                    {
                        "task_id": t["task_id"],
                        "task_type": t.get("task_type"),
                        "source_mode": t.get("source_mode"),
                        "status": t.get("status"),
                    },
                )
                if t.get("run_id"):
                    add_edge(edges, f"Run::{t['run_id']}", "HAS_TASK", nid, evidence="diagnosis_tasks")
                if t.get("component_id"):
                    add_edge(edges, nid, "TARGETS_COMPONENT", f"Component::{t['component_id']}", evidence="diagnosis_tasks")

            # ---------------------------
            # diagnosis_results
            # ---------------------------
            cur.execute("SELECT * FROM diagnosis_results")
            for r in cur.fetchall():
                task_nid = f"Task::{r['task_id']}"
                fault_nid: Optional[str] = None
                rc_nid: Optional[str] = None

                if r.get("fault_type"):
                    fault_type = r["fault_type"]
                    fault_nid = f"FaultType::{fault_type}"
                    add_node(nodes, fault_nid, "FaultType", enrich_fault_node_from_meta(fault_type, fault_meta))
                    add_edge(edges, task_nid, "HAS_RESULT_FAULT", fault_nid, evidence="diagnosis_results")

                if r.get("root_cause_type"):
                    rc_type = r["root_cause_type"]
                    rc_nid = f"RootCause::{rc_type}"
                    props = enrich_root_cause_node_from_meta(rc_type, rc_meta)
                    props["root_cause"] = r.get("root_cause")
                    add_node(nodes, rc_nid, "RootCause", props)
                    add_edge(edges, task_nid, "HAS_ROOT_CAUSE", rc_nid, evidence="diagnosis_results")

                if r.get("target_component_id"):
                    add_edge(
                        edges,
                        task_nid,
                        "TARGETS_COMPONENT",
                        f"Component::{r['target_component_id']}",
                        evidence="diagnosis_results",
                    )

                if fault_nid and rc_nid:
                    add_edge(edges, rc_nid, "CAUSES_FAULT", fault_nid, evidence="diagnosis_results")
                    add_edge(edges, fault_nid, "CAUSED_BY", rc_nid, evidence="diagnosis_results")

        case_fault_map = build_case_fault_map(nodes)
        task_fault_map = build_task_fault_map(edges)

        # ---------------------------
        # Mongo log_windows
        # ---------------------------
        projection = {
            "window_id": 1,
            "run_id": 1,
            "case_id": 1,
            "task_id": 1,
            "system_id": 1,
            "subsystem": 1,
            "strategy": 1,
            "stats": 1,
            "key_events": 1,
        }
        cursor = mongo_db["log_windows"].find({}, projection=projection)
        if args.window_limit > 0:
            cursor = cursor.limit(args.window_limit)

        for w in cursor:
            window_id = w.get("window_id")
            if window_id is None:
                continue

            nid = f"LogWindow::{window_id}"
            stats = w.get("stats") or {}

            add_node(
                nodes,
                nid,
                "LogWindow",
                {
                    "window_id": window_id,
                    "strategy": w.get("strategy"),
                    "n_entries": stats.get("n_entries"),
                    "error_events": stats.get("error_events"),
                    "critical_events": stats.get("critical_events"),
                    "case_id": w.get("case_id"),
                    "task_id": w.get("task_id"),
                    "run_id": w.get("run_id"),
                },
            )

            if w.get("run_id"):
                add_edge(edges, f"Run::{w['run_id']}", "HAS_WINDOW", nid, evidence="log_windows")
            if w.get("task_id"):
                add_edge(edges, f"Task::{w['task_id']}", "USES_WINDOW", nid, evidence="log_windows")

            patterns = extract_event_patterns(w.get("key_events"))
            for pattern in patterns:
                pattern_counter[pattern] += 1
                pattern_nid = f"EventPattern::{pattern}"
                add_node(nodes, pattern_nid, "EventPattern", enrich_pattern_node_from_meta(pattern, pattern_meta))
                add_edge(edges, nid, "HAS_KEY_EVENT", pattern_nid, evidence="log_windows")

            candidate_faults: List[str] = []

            task_id = w.get("task_id")
            if task_id:
                candidate_faults.extend(task_fault_map.get(f"Task::{task_id}", []))

            case_id = w.get("case_id")
            if case_id is not None and str(case_id) in case_fault_map:
                candidate_faults.append(case_fault_map[str(case_id)])

            candidate_faults = list(dict.fromkeys(candidate_faults))

            if stats.get("error_events", 0) > 0 or stats.get("critical_events", 0) > 0:
                for fault_type in candidate_faults:
                    fault_nid = f"FaultType::{fault_type}"
                    add_node(nodes, fault_nid, "FaultType", enrich_fault_node_from_meta(fault_type, fault_meta))
                    add_edge(edges, nid, "SUPPORTS_FAULT", fault_nid, evidence="log_windows")

                    # 这里只累计统计，后面统一写 INDICATES_FAULT
                    for pattern in patterns:
                        pattern_fault_counter[pattern][fault_type] += 1

        # ---------------------------
        # 由 Task/Case 推出 FaultType -> Component
        # ---------------------------
        fault_to_components = build_fault_to_component_map(edges)
        for fault_nid, component_nids in fault_to_components.items():
            for comp_nid in component_nids:
                add_edge(edges, fault_nid, "AFFECTS_COMPONENT", comp_nid, evidence="derived")

        # ---------------------------
        # 统一写 EventPattern -> FaultType
        # 带支持度和占比，方便 graph_service 后续打分
        # ---------------------------
        kept_pattern_fault_edges = 0
        for pattern, fault_counter in pattern_fault_counter.items():
            if not fault_counter:
                continue

            total_support = sum(fault_counter.values())
            top_items = fault_counter.most_common(args.max_pattern_fault_fanout)

            for fault_type, support in top_items:
                if support < args.min_pattern_fault_support:
                    continue

                ratio = round(support / total_support, 6) if total_support > 0 else 0.0
                pattern_nid = f"EventPattern::{pattern}"
                fault_nid = f"FaultType::{fault_type}"

                add_edge(
                    edges,
                    pattern_nid,
                    "INDICATES_FAULT",
                    fault_nid,
                    evidence="log_windows_aggregated",
                    weight=support,
                    extra_props={
                        "support_count": support,
                        "support_ratio": ratio,
                        "pattern_total_support": total_support,
                    },
                )
                kept_pattern_fault_edges += 1

        # ---------------------------
        # 输出 summary 前，给 pattern 节点补频次
        # ---------------------------
        for pattern, cnt in pattern_counter.items():
            pattern_nid = f"EventPattern::{pattern}"
            if pattern_nid in nodes:
                nodes[pattern_nid]["properties"]["support_count"] = cnt

        write_jsonl(nodes_path, nodes.values())
        write_jsonl(edges_path, [edges[k] for k in sorted(edges.keys())])

        summary: Dict[str, Any] = {
            "built_at": datetime.utcnow().isoformat() + "Z",
            "node_count": len(nodes),
            "edge_count": len(edges),
            "node_types": defaultdict(int),
            "edge_types": defaultdict(int),
            "pattern_stats": {
                "unique_patterns": len(pattern_counter),
                "pattern_fault_edges_written": kept_pattern_fault_edges,
                "min_pattern_fault_support": args.min_pattern_fault_support,
                "max_pattern_fault_fanout": args.max_pattern_fault_fanout,
            },
            "files": {
                "nodes": str(nodes_path),
                "edges": str(edges_path),
            },
        }

        for n in nodes.values():
            summary["node_types"][n["type"]] += 1
        for edge_obj in edges.values():
            summary["edge_types"][edge_obj["relation"]] += 1

        summary["node_types"] = dict(summary["node_types"])
        summary["edge_types"] = dict(summary["edge_types"])

        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(f"[OK] knowledge graph exported to {out_dir}")
        print(f"[INFO] nodes={len(nodes)}, edges={len(edges)}")
        print(f"[INFO] files: {nodes_path.name}, {edges_path.name}, {summary_path.name}")

    finally:
        mysql_conn.close()
        mongo_client.close()


if __name__ == "__main__":
    main()
