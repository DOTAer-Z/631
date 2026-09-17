"""
kg_service_v2.py

职责：
- 从 KG_DIR 目录读取 kg_nodes.jsonl / kg_edges.jsonl（由 06_build_knowledge_graph.py 生成）
- 在内存中构建节点/边索引，支持高效查询
- 提供两类核心查询接口：
    1. get_root_cause_by_fault_type(fault_type)  → 给定故障类型，返回关联的根因列表
    2. get_root_cause_by_case_id(case_id)        → 给定案例 ID，返回根因信息

KG 数据结构（来自 06_build_knowledge_graph.py）：

节点（kg_nodes.jsonl）每行：
    {"id": "FaultType::xxx", "type": "FaultType", "properties": {...}}
    {"id": "RootCause::xxx", "type": "RootCause", "properties": {...}}
    {"id": "Case::1",        "type": "Case",      "properties": {...}}
    ...
    节点类型：System, Subsystem, Component, Case, FaultType, RootCause,
              Run, Task, LogWindow, EventPattern

边（kg_edges.jsonl）每行：
    {"source": "RootCause::xxx", "relation": "CAUSES_FAULT", "target": "FaultType::yyy",
     "weight": 3, "evidence_count": 2, "evidences": [...]}
    关键关系：
      Case      -> HAS_RESULT_FAULT -> FaultType
      Case      -> HAS_ROOT_CAUSE   -> RootCause
      RootCause -> CAUSES_FAULT     -> FaultType
      FaultType -> CAUSED_BY        -> RootCause
      FaultType -> AFFECTS_COMPONENT-> Component
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings


class KGServiceV2:
    """
    懒加载知识图谱。首次查询时读取 JSONL 文件并建立内存索引。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded = False

        # 节点索引：node_id -> node dict
        self._nodes: Dict[str, Dict[str, Any]] = {}

        # 边索引（有向）：source_id -> list of edge dicts
        self._out_edges: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

        # 边索引（反向）：target_id -> list of edge dicts
        self._in_edges: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    # ------------------------------------------------------------------
    # 内部：加载
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._load_files()
            self._loaded = True

    def _load_files(self) -> None:
        kg_dir = Path(settings.KG_DIR)
        nodes_path = kg_dir / "kg_nodes.jsonl"
        edges_path = kg_dir / "kg_edges.jsonl"

        # 容错：KG 文件缺失时按空图加载（新系统未跑离线构建脚本、或仅靠运行时策展追加），
        # 不再抛 FileNotFoundError，避免 /graph 直接 500，也让首次策展导入能创建文件。
        if nodes_path.exists():
            with nodes_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    node = json.loads(line)
                    self._nodes[node["id"]] = node

        if edges_path.exists():
            with edges_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    edge = json.loads(line)
                    self._out_edges[edge["source"]].append(edge)
                    self._in_edges[edge["target"]].append(edge)

    # ------------------------------------------------------------------
    # 内部：辅助
    # ------------------------------------------------------------------

    def _node_props(self, node_id: str) -> Dict[str, Any]:
        node = self._nodes.get(node_id)
        return node["properties"] if node else {}

    def _neighbors(
        self,
        node_id: str,
        relation: str,
        direction: str = "out",
    ) -> List[str]:
        """返回满足指定关系的邻居节点 ID 列表。"""
        edges = self._out_edges[node_id] if direction == "out" else self._in_edges[node_id]
        return [e["target"] if direction == "out" else e["source"]
                for e in edges if e["relation"] == relation]

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def get_root_cause_by_fault_type(
        self, fault_type: str
    ) -> List[Dict[str, Any]]:
        """
        给定故障类型名称，返回所有关联的根因信息。

        查询路径：FaultType::{fault_type} -[CAUSED_BY]-> RootCause::*

        返回
        ----
        List[dict]，每个元素包含：
            root_cause_type : str
            root_cause      : str | None（具体描述，来自 diagnosis_results）
            severity        : str | None
            recovery_hint   : str | None
            description     : str | None
            evidence_weight : int（边的累计权重）
        """
        self._load()

        fault_node_id = f"FaultType::{fault_type}"
        if fault_node_id not in self._nodes:
            return []

        results = []
        seen = set()
        for edge in self._out_edges.get(fault_node_id, []):
            if edge["relation"] != "CAUSED_BY":
                continue
            rc_id = edge["target"]
            if rc_id in seen:
                continue
            seen.add(rc_id)
            props = self._node_props(rc_id)
            results.append({
                "root_cause_type": props.get("root_cause_type", rc_id.split("::", 1)[-1]),
                "root_cause": props.get("root_cause"),
                "severity": props.get("severity"),
                "recovery_hint": props.get("recovery_hint"),
                "description": props.get("description"),
                "evidence_weight": edge.get("weight", 1),
            })

        # 按证据权重降序排列
        results.sort(key=lambda x: x["evidence_weight"], reverse=True)
        return results

    def get_root_cause_by_case_id(
        self, case_id: Any
    ) -> Optional[Dict[str, Any]]:
        """
        给定案例 ID，返回该案例的根因信息。

        查询路径：Case::{case_id} -[HAS_ROOT_CAUSE]-> RootCause::*
                  Case::{case_id} -[HAS_RESULT_FAULT]-> FaultType::*

        返回
        ----
        dict | None，包含：
            case_id         : any
            fault_type      : str | None
            root_cause_type : str | None
            root_cause      : str | None
            severity        : str | None
            recovery_hint   : str | None
        """
        self._load()

        case_node_id = f"Case::{case_id}"
        if case_node_id not in self._nodes:
            return None

        case_props = self._node_props(case_node_id)

        # 取 fault_type
        fault_type: Optional[str] = case_props.get("fault_type")
        fault_neighbors = self._neighbors(case_node_id, "HAS_RESULT_FAULT")
        if fault_neighbors and not fault_type:
            fault_type = fault_neighbors[0].split("::", 1)[-1]

        # 取 root_cause
        rc_neighbors = self._neighbors(case_node_id, "HAS_ROOT_CAUSE")
        if not rc_neighbors:
            return {
                "case_id": case_id,
                "fault_type": fault_type,
                "root_cause_type": case_props.get("root_cause_type"),
                "root_cause": None,
                "severity": None,
                "recovery_hint": None,
            }

        rc_id = rc_neighbors[0]
        rc_props = self._node_props(rc_id)
        return {
            "case_id": case_id,
            "fault_type": fault_type,
            "root_cause_type": rc_props.get("root_cause_type", rc_id.split("::", 1)[-1]),
            "root_cause": rc_props.get("root_cause"),
            "severity": rc_props.get("severity"),
            "recovery_hint": rc_props.get("recovery_hint"),
        }

    def get_fault_type_info(self, fault_type: str) -> Optional[Dict[str, Any]]:
        """
        返回故障类型节点的完整属性（severity、warning_level、description 等）。
        """
        self._load()
        node_id = f"FaultType::{fault_type}"
        node = self._nodes.get(node_id)
        return node["properties"] if node else None

    def get_affected_components(self, fault_type: str) -> List[str]:
        """
        给定故障类型，返回受影响的组件名称列表。
        查询路径：FaultType::{fault_type} -[AFFECTS_COMPONENT]-> Component::*
        """
        self._load()
        fault_node_id = f"FaultType::{fault_type}"
        comp_ids = self._neighbors(fault_node_id, "AFFECTS_COMPONENT")
        result = []
        for cid in comp_ids:
            props = self._node_props(cid)
            name = props.get("name") or props.get("code") or cid.split("::", 1)[-1]
            result.append(name)
        return result

    def health_check(self) -> Dict[str, Any]:
        """返回图谱基本统计，用于调试。"""
        try:
            self._load()
            type_counts: Dict[str, int] = defaultdict(int)
            for node in self._nodes.values():
                type_counts[node["type"]] += 1
            relation_counts: Dict[str, int] = defaultdict(int)
            for edges in self._out_edges.values():
                for e in edges:
                    relation_counts[e["relation"]] += 1
            return {
                "status": "ok",
                "node_count": len(self._nodes),
                "edge_count": sum(len(v) for v in self._out_edges.values()),
                "node_types": dict(type_counts),
                "relation_types": dict(relation_counts),
                "kg_dir": settings.KG_DIR,
            }
        except Exception as exc:
            return {"status": "error", "detail": str(exc)}


    def append_curated_case(
        self,
        *,
        fault_type_name: str,
        root_cause: str,
        component: str | None = None,
        evidence: str | None = None,
    ) -> dict:
        """运行时把一条策展案例追加进 KG（节点+边），同步更新内存索引 + JSONL 文件。

        追加 FaultType::<name> / RootCause::<rc> 节点（缺则加）+ 边
        RootCause -CAUSES_FAULT-> FaultType、FaultType -CAUSED_BY-> RootCause（去重），
        使 /graph 与 get_root_cause_by_fault_type 立即反映新案例（无需离线重建）。
        """
        name = (fault_type_name or "").strip()
        rc = (root_cause or "").strip()
        if not name or not rc:
            return {"appended": False, "reason": "fault_type_name/root_cause 为空"}
        rc_key = rc[:200]

        ft_id = f"FaultType::{name}"
        rc_id = f"RootCause::{rc_key}"

        new_node_lines: list[str] = []
        new_edge_lines: list[str] = []

        with self._lock:
            # 未加载则在持锁状态下直接读文件（避免 _load 二次加锁死锁）
            if not self._loaded:
                self._load_files()
                self._loaded = True

            if ft_id not in self._nodes:
                node = {"id": ft_id, "type": "FaultType", "properties": {"name": name}}
                self._nodes[ft_id] = node
                new_node_lines.append(json.dumps(node, ensure_ascii=False))
            if rc_id not in self._nodes:
                node = {"id": rc_id, "type": "RootCause", "properties": {"name": rc_key, "text": rc}}
                self._nodes[rc_id] = node
                new_node_lines.append(json.dumps(node, ensure_ascii=False))

            def _edge_exists(source: str, relation: str, target: str) -> bool:
                return any(
                    e["relation"] == relation and e["target"] == target
                    for e in self._out_edges.get(source, [])
                )

            for source, relation, target in (
                (rc_id, "CAUSES_FAULT", ft_id),
                (ft_id, "CAUSED_BY", rc_id),
            ):
                if not _edge_exists(source, relation, target):
                    edge = {
                        "source": source,
                        "relation": relation,
                        "target": target,
                        "weight": 1,
                        "evidence_count": 1,
                        "evidences": [evidence] if evidence else ["curated:annotation"],
                    }
                    self._out_edges[source].append(edge)
                    self._in_edges[target].append(edge)
                    new_edge_lines.append(json.dumps(edge, ensure_ascii=False))

            kg_dir = Path(settings.KG_DIR)
            kg_dir.mkdir(parents=True, exist_ok=True)
            if new_node_lines:
                with (kg_dir / "kg_nodes.jsonl").open("a", encoding="utf-8") as f:
                    for ln in new_node_lines:
                        f.write(ln + "\n")
            if new_edge_lines:
                with (kg_dir / "kg_edges.jsonl").open("a", encoding="utf-8") as f:
                    for ln in new_edge_lines:
                        f.write(ln + "\n")

    def append_run_cases(
        self,
        *,
        instances: List[dict],
    ) -> dict:
        """把一批「run 实例」写入 KG（节点 + 边），同步内存索引 + JSONL 文件。

        instances 每项：
            {
              "run_id": str,            必填
              "case_id": str | None,
              "display_name": str | None,
              "fault_type_name": str,   必填（无故障类型不建 Case 节点）
              "root_cause": str | None,
              "subsystem": str | None,
              "window_total": int,
              "log_entry_id": int | None,
            }

        生成的节点：
            Case::<run_id>        type=Case
            FaultType::<name>     （缺则建）
            RootCause::<text>     （有根因才建）
            Subsystem::<name>     （有 subsystem 才建）
        生成的边（去重）：
            Case     -HAS_RESULT_FAULT->  FaultType      （即 kg 文档里的实例关系）
            Case     -HAS_ROOT_CAUSE   -> RootCause
            Case     -OCCURRED_IN      -> Subsystem
            RootCause- CAUSES_FAULT    -> FaultType
            FaultType- CAUSED_BY       -> RootCause

        与 append_curated_case 的差异：那条路径只产 FaultType/RootCause 抽象的
        两种节点，看不出「哪条 run 是这个故障的实例」；本方法补齐实例层。
        同一 run_id 重复调用是幂等的：节点已存在则跳过，边已存在则跳过。
        """
        new_node_lines: List[str] = []
        new_edge_lines: List[str] = []

        with self._lock:
            if not self._loaded:
                self._load_files()
                self._loaded = True

            def _ensure_node(node_id: str, ntype: str, props: dict) -> bool:
                if node_id in self._nodes:
                    return False
                node = {"id": node_id, "type": ntype, "properties": props}
                self._nodes[node_id] = node
                new_node_lines.append(json.dumps(node, ensure_ascii=False))
                return True

            def _edge_exists(source: str, relation: str, target: str) -> bool:
                return any(
                    e["relation"] == relation and e["target"] == target
                    for e in self._out_edges.get(source, [])
                )

            def _ensure_edge(
                source: str,
                relation: str,
                target: str,
                evidence: Optional[str],
                evidences: Optional[List[str]] = None,
            ) -> bool:
                if _edge_exists(source, relation, target):
                    return False
                ev = [e for e in (evidences or []) if e]
                if evidence:
                    ev.append(evidence)
                edge = {
                    "source": source,
                    "relation": relation,
                    "target": target,
                    "weight": 1,
                    "evidence_count": max(1, len(ev)),
                    "evidences": ev or ["imported:run"],
                }
                self._out_edges[source].append(edge)
                self._in_edges[target].append(edge)
                new_edge_lines.append(json.dumps(edge, ensure_ascii=False))
                return True

            cases_added = 0
            for inst in instances:
                run_id = (inst.get("run_id") or "").strip()
                ft_name = (inst.get("fault_type_name") or "").strip()
                if not run_id or not ft_name:
                    continue

                case_id = f"Case::{run_id}"
                ft_id = f"FaultType::{ft_name}"

                if _ensure_node(
                    case_id,
                    "Case",
                    {
                        "name": inst.get("display_name") or run_id,
                        "run_id": run_id,
                        "case_id": inst.get("case_id"),
                        "window_total": int(inst.get("window_total") or 0),
                        "log_entry_id": inst.get("log_entry_id"),
                    },
                ):
                    cases_added += 1

                _ensure_node(ft_id, "FaultType", {"name": ft_name})
                _ensure_edge(case_id, "HAS_RESULT_FAULT", ft_id, run_id)

                rc = (inst.get("root_cause") or "").strip()
                if rc:
                    rc_key = rc[:200]
                    rc_id = f"RootCause::{rc_key}"
                    _ensure_node(rc_id, "RootCause", {"name": rc_key, "text": rc})
                    _ensure_edge(case_id, "HAS_ROOT_CAUSE", rc_id, run_id)
                    _ensure_edge(rc_id, "CAUSES_FAULT", ft_id, run_id)
                    _ensure_edge(ft_id, "CAUSED_BY", rc_id, run_id)

                sub = (inst.get("subsystem") or "").strip()
                if sub:
                    sub_id = f"Subsystem::{sub}"
                    _ensure_node(sub_id, "Subsystem", {"name": sub})
                    _ensure_edge(case_id, "OCCURRED_IN", sub_id, run_id)

            kg_dir = Path(settings.KG_DIR)
            kg_dir.mkdir(parents=True, exist_ok=True)
            if new_node_lines:
                with (kg_dir / "kg_nodes.jsonl").open("a", encoding="utf-8") as f:
                    for ln in new_node_lines:
                        f.write(ln + "\n")
            if new_edge_lines:
                with (kg_dir / "kg_edges.jsonl").open("a", encoding="utf-8") as f:
                    for ln in new_edge_lines:
                        f.write(ln + "\n")

        # 已经加载过索引的进程：文件与内存已一致；未加载的下次 _load 直接读文件。
        return {
            "appended": True,
            "cases": cases_added,
            "new_nodes": len(new_node_lines),
            "new_edges": len(new_edge_lines),
        }

    def remove_run_cases(self, run_ids: List[str]) -> dict:
        """从 KG 里删除指定 run 的 Case 节点及其边（run 被删除时清理用）。

        注意：只删 Case 节点自身与它的边；由它顺带创建的 FaultType /
        RootCause / Subsystem 节点保留（可能被其它案例共享，无法安全判断孤儿）。
        返回 {removed_nodes, removed_edges, rewritten}。
        """
        targets = {f"Case::{r}" for r in run_ids if r}
        if not targets:
            return {"removed_nodes": 0, "removed_edges": 0, "rewritten": False}

        kg_dir = Path(settings.KG_DIR)
        nodes_path = kg_dir / "kg_nodes.jsonl"
        edges_path = kg_dir / "kg_edges.jsonl"

        with self._lock:
            if not self._loaded:
                self._load_files()
                self._loaded = True

            removed_nodes = sum(1 for t in targets if t in self._nodes)
            for t in targets:
                self._nodes.pop(t, None)

            removed_edges = 0
            for src in list(self._out_edges.keys()):
                kept = []
                for e in self._out_edges[src]:
                    if e["source"] in targets or e["target"] in targets:
                        removed_edges += 1
                        self._in_edges[e["target"]] = [
                            x for x in self._in_edges.get(e["target"], [])
                            if not (x["source"] == e["source"] and x["relation"] == e["relation"])
                        ]
                    else:
                        kept.append(e)
                self._out_edges[src] = kept
            for t in targets:
                self._out_edges.pop(t, None)
                self._in_edges.pop(t, None)

            if not nodes_path.exists() and not edges_path.exists():
                return {"removed_nodes": removed_nodes, "removed_edges": removed_edges, "rewritten": False}

            # 重写两个文件（KG 体量小，全量重写比逐行删除简单且安全）
            def _rewrite(path: Path, drop):
                if not path.exists():
                    return
                kept_lines = []
                with path.open(encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if drop(obj):
                            continue
                        kept_lines.append(line if line.endswith("\n") else line + "\n")
                with path.open("w", encoding="utf-8") as f:
                    f.writelines(kept_lines)

            _rewrite(nodes_path, lambda o: o.get("id") in targets)
            _rewrite(
                edges_path,
                lambda o: o.get("source") in targets or o.get("target") in targets,
            )

        return {
            "removed_nodes": removed_nodes,
            "removed_edges": removed_edges,
            "rewritten": True,
        }


# 全局单例
kg_service_v2 = KGServiceV2()
