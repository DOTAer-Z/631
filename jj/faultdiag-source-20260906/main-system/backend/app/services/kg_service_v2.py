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

        return {"appended": True, "new_nodes": len(new_node_lines), "new_edges": len(new_edge_lines)}


# 全局单例
kg_service_v2 = KGServiceV2()
