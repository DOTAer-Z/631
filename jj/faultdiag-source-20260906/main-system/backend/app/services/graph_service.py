import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set

from sqlalchemy.orm import Session

from app.config import settings
from app.models.fault_type import FaultType
from app.models.log_entry import LogEntry

logger = logging.getLogger(__name__)

# ── KG 文件名配置（实际文件名与此不同时只需改这两行） ─────────────────────────
_KG_NODES_FILE = "kg_nodes.jsonl"
_KG_EDGES_FILE = "kg_edges.jsonl"

# ── 节点类型 → (category索引, 颜色, symbolSize) ───────────────────────────────
_NODE_TYPE_STYLE = {
    "FaultType":    (0, "#5470c6", 45),
    "RootCause":    (1, "#91cc75", 35),
    "Case":         (2, "#ee6666", 20),
    "Component":    (3, "#fac858", 18),
    "System":       (4, "#73c0de", 30),
    "Subsystem":    (5, "#3ba272", 22),
    "LogWindow":    (6, "#fc8452", 15),
    "EventPattern": (7, "#9a60b4", 15),
}
_DEFAULT_NODE_STYLE = (8, "#ea7ccc", 12)

_KG_CATEGORIES = [
    {"name": "故障类型"},
    {"name": "根因"},
    {"name": "案例"},
    {"name": "组件"},
    {"name": "系统"},
    {"name": "子系统"},
    {"name": "日志窗口"},
    {"name": "事件模式"},
    {"name": "其他"},
]

# 节点 name 显示截断长度（超出则加 …）
_LABEL_MAX_LEN = 16


class GraphService:

    # =========================================================================
    # 公开入口
    # =========================================================================

    def build_graph(
        self,
        db: Session,
        view: str = "overview",
        focus: Optional[str] = None,
        center_id: Optional[str] = None,   # 向后兼容，非主路径
        max_nodes: int = 80,
        max_edges: int = 120,
    ) -> dict:
        """
        根据 view 参数分发到对应的模板构图函数。
        KG 文件不存在时回退到数据库逻辑。
        返回格式：{"nodes": [...], "edges": [...], "categories": [...]}
        """
        kg_index = self._load_kg_index()
        if kg_index is None:
            return self._build_from_db(db)

        # center_id 显式传入时走 1-跳展开（向后兼容，非默认路径）
        if center_id:
            return self._build_center_subgraph(kg_index, center_id, max_nodes, max_edges)

        # view 分发
        if view == "fault_type":
            result = self._build_fault_type_graph(kg_index, focus)
        elif view == "root_cause":
            result = self._build_root_cause_graph(kg_index, focus)
        elif view == "subsystem":
            result = self._build_subsystem_graph(kg_index, focus)
        else:
            result = self._build_overview_graph(kg_index)

        # 硬上限：view 构图函数已尽量控制在目标范围内，这里只做最终保护
        nodes = result["nodes"][:max_nodes]
        visible = {n["id"] for n in nodes}
        edges = [
            e for e in result["edges"]
            if e["source"] in visible and e["target"] in visible
        ][:max_edges]
        result["nodes"] = nodes
        result["edges"] = edges
        return result

    # =========================================================================
    # KG 索引加载
    # =========================================================================

    def _load_kg_index(self) -> Optional[dict]:
        """
        从 JSONL 文件读取 KG，建立内存索引供各视图函数查询。
        两个文件均不存在时返回 None（触发 DB fallback）。
        单行 JSON 解析失败时跳过并记录 warning。
        """
        kg_dir = Path(settings.KG_DIR)
        nodes_path = kg_dir / _KG_NODES_FILE
        edges_path = kg_dir / _KG_EDGES_FILE

        if not nodes_path.exists() and not edges_path.exists():
            logger.debug("[GraphService] KG files not found, falling back to DB")
            return None

        raw_nodes: Dict[str, dict] = {}
        nodes_by_type: Dict[str, List[dict]] = defaultdict(list)

        if nodes_path.exists():
            with nodes_path.open(encoding="utf-8") as f:
                for line_no, line in enumerate(f, 1):
                    raw = _parse_jsonl_line(line, nodes_path.name, line_no)
                    if raw is None:
                        continue
                    nid = raw.get("id")
                    if not nid or nid in raw_nodes:
                        continue
                    raw_nodes[nid] = raw
                    nodes_by_type[raw.get("type", "")].append(raw)

        all_edges: List[dict] = []
        out_edges: Dict[str, List[dict]] = defaultdict(list)   # source → edges
        in_edges: Dict[str, List[dict]] = defaultdict(list)    # target → edges
        seen_edge_keys: Set[tuple] = set()

        if edges_path.exists():
            with edges_path.open(encoding="utf-8") as f:
                for line_no, line in enumerate(f, 1):
                    raw = _parse_jsonl_line(line, edges_path.name, line_no)
                    if raw is None:
                        continue
                    src = raw.get("source")
                    tgt = raw.get("target")
                    if not src or not tgt or src not in raw_nodes or tgt not in raw_nodes:
                        continue
                    rel = raw.get("relation") or raw.get("label") or ""
                    key = (src, tgt, rel)
                    if key in seen_edge_keys:
                        continue
                    seen_edge_keys.add(key)
                    all_edges.append(raw)
                    out_edges[src].append(raw)
                    in_edges[tgt].append(raw)

        logger.info(
            "[GraphService] KG index: %d nodes, %d edges",
            len(raw_nodes), len(all_edges),
        )
        return {
            "raw_nodes": raw_nodes,
            "nodes_by_type": dict(nodes_by_type),
            "all_edges": all_edges,
            "out_edges": dict(out_edges),
            "in_edges": dict(in_edges),
        }

    # =========================================================================
    # 模板化构图函数
    # =========================================================================

    def _build_overview_graph(self, kg_index: dict) -> dict:
        """
        聚合总览图（默认视图）。目标节点数 ≤ 20，不展示实例层节点。
        策略：
          Step 1. FaultType 按总连接度降序，取 top 8
          Step 2. 与 top FaultType 直接相连的 RootCause，按共现频次取 top 8
          Step 3. 与已选节点直接相连的 System/Subsystem/Component，按共现取 top 4
        """
        raw_nodes = kg_index["raw_nodes"]
        out_edges = kg_index["out_edges"]
        in_edges  = kg_index["in_edges"]
        nodes_by_type = kg_index["nodes_by_type"]

        selected: Set[str] = set()

        # Step 1: top FaultType by degree
        ft_scored = sorted(
            nodes_by_type.get("FaultType", []),
            key=lambda n: len(out_edges.get(n["id"], [])) + len(in_edges.get(n["id"], [])),
            reverse=True,
        )[:8]
        ft_ids = [n["id"] for n in ft_scored]
        selected.update(ft_ids)

        # Step 2: RootCause co-occurrence with top FaultType
        rc_score: Dict[str, int] = defaultdict(int)
        for ft_id in ft_ids:
            for e in in_edges.get(ft_id, []):
                if raw_nodes.get(e["source"], {}).get("type") == "RootCause":
                    rc_score[e["source"]] += 1
        top_rc = sorted(rc_score, key=rc_score.__getitem__, reverse=True)[:8]
        selected.update(top_rc)

        # Step 3: structural nodes connected to already-selected set
        struct_score: Dict[str, int] = defaultdict(int)
        for nid in list(selected):
            for e in out_edges.get(nid, []) + in_edges.get(nid, []):
                other = e["target"] if e["source"] == nid else e["source"]
                if raw_nodes.get(other, {}).get("type") in ("System", "Subsystem", "Component"):
                    struct_score[other] += 1
        top_struct = sorted(struct_score, key=struct_score.__getitem__, reverse=True)[:4]
        selected.update(top_struct)

        result = self._assemble_graph(selected, kg_index)
        logger.info("[GraphService] overview: %d nodes, %d edges", len(result["nodes"]), len(result["edges"]))
        return result

    def _build_fault_type_graph(self, kg_index: dict, focus: Optional[str]) -> dict:
        """
        故障类型焦点图，目标节点数 ≤ 15。
        中心：1 个 FaultType（focus 指定，或自动取 degree 最高的）
        外围：RootCause (≤8) + Component (≤5) + 各 Component 的 Subsystem (≤3)
        """
        raw_nodes    = kg_index["raw_nodes"]
        out_edges    = kg_index["out_edges"]
        in_edges     = kg_index["in_edges"]
        nodes_by_type = kg_index["nodes_by_type"]

        focus_id = _find_node_id(focus, "FaultType", raw_nodes)
        if not focus_id:
            ft_nodes = nodes_by_type.get("FaultType", [])
            if not ft_nodes:
                return self._build_overview_graph(kg_index)
            focus_id = max(
                ft_nodes,
                key=lambda n: len(out_edges.get(n["id"], [])) + len(in_edges.get(n["id"], [])),
            )["id"]

        priority: List[str] = [focus_id]

        # RootCause → this FaultType（in_edges）
        rc_ids = [
            e["source"] for e in in_edges.get(focus_id, [])
            if raw_nodes.get(e["source"], {}).get("type") == "RootCause"
        ][:8]
        priority.extend(rc_ids)

        # this FaultType → Component（out_edges）
        comp_ids = [
            e["target"] for e in out_edges.get(focus_id, [])
            if raw_nodes.get(e["target"], {}).get("type") == "Component"
        ][:5]
        priority.extend(comp_ids)

        # Component → parent Subsystem（in_edges of Component, each max 1）
        sub_seen: Set[str] = set()
        for cid in comp_ids[:3]:
            for e in in_edges.get(cid, []):
                sid = e["source"]
                if raw_nodes.get(sid, {}).get("type") == "Subsystem" and sid not in sub_seen:
                    sub_seen.add(sid)
                    priority.append(sid)
                    break

        selected = set(priority[:15])
        result = self._assemble_graph(selected, kg_index)
        logger.info("[GraphService] fault_type view focus=%s: %d nodes", focus_id, len(result["nodes"]))
        return result

    def _build_root_cause_graph(self, kg_index: dict, focus: Optional[str]) -> dict:
        """
        根因焦点图，目标节点数 ≤ 15。
        中心：1 个 RootCause
        外围：它导致的 FaultType (≤6) + 这些 FaultType 影响的 Component (≤6)
        """
        raw_nodes    = kg_index["raw_nodes"]
        out_edges    = kg_index["out_edges"]
        in_edges     = kg_index["in_edges"]
        nodes_by_type = kg_index["nodes_by_type"]

        focus_id = _find_node_id(focus, "RootCause", raw_nodes)
        if not focus_id:
            rc_nodes = nodes_by_type.get("RootCause", [])
            if not rc_nodes:
                return self._build_overview_graph(kg_index)
            focus_id = max(
                rc_nodes,
                key=lambda n: len(out_edges.get(n["id"], [])) + len(in_edges.get(n["id"], [])),
            )["id"]

        priority: List[str] = [focus_id]

        # this RootCause → FaultType（CAUSES_FAULT）
        ft_ids = [
            e["target"] for e in out_edges.get(focus_id, [])
            if raw_nodes.get(e["target"], {}).get("type") == "FaultType"
        ][:6]
        priority.extend(ft_ids)

        # FaultType → Component（each FaultType max 2）
        comp_seen: Set[str] = set()
        for ft_id in ft_ids[:3]:
            for e in out_edges.get(ft_id, []):
                cid = e["target"]
                if raw_nodes.get(cid, {}).get("type") == "Component" and cid not in comp_seen:
                    comp_seen.add(cid)
                    priority.append(cid)
                    if len(comp_seen) >= 6:
                        break

        selected = set(priority[:15])
        result = self._assemble_graph(selected, kg_index)
        logger.info("[GraphService] root_cause view focus=%s: %d nodes", focus_id, len(result["nodes"]))
        return result

    def _build_subsystem_graph(self, kg_index: dict, focus: Optional[str]) -> dict:
        """
        子系统焦点图，目标节点数 ≤ 15。
        中心：1 个 Subsystem
        外围：父 System (≤1) + 子 Component (≤6) + 影响这些 Component 的 FaultType (≤6)
        """
        raw_nodes    = kg_index["raw_nodes"]
        out_edges    = kg_index["out_edges"]
        in_edges     = kg_index["in_edges"]
        nodes_by_type = kg_index["nodes_by_type"]

        focus_id = _find_node_id(focus, "Subsystem", raw_nodes)
        if not focus_id:
            sub_nodes = nodes_by_type.get("Subsystem", [])
            if not sub_nodes:
                return self._build_overview_graph(kg_index)
            focus_id = max(
                sub_nodes,
                key=lambda n: len(out_edges.get(n["id"], [])) + len(in_edges.get(n["id"], [])),
            )["id"]

        priority: List[str] = [focus_id]

        # parent System（in_edges of Subsystem, max 1）
        for e in in_edges.get(focus_id, []):
            if raw_nodes.get(e["source"], {}).get("type") == "System":
                priority.append(e["source"])
                break

        # child Component（out_edges of Subsystem）
        comp_ids = [
            e["target"] for e in out_edges.get(focus_id, [])
            if raw_nodes.get(e["target"], {}).get("type") == "Component"
        ][:6]
        priority.extend(comp_ids)

        # FaultType → these Component（in_edges of Component, each max 2）
        ft_seen: Set[str] = set()
        for cid in comp_ids[:3]:
            for e in in_edges.get(cid, []):
                fid = e["source"]
                if raw_nodes.get(fid, {}).get("type") == "FaultType" and fid not in ft_seen:
                    ft_seen.add(fid)
                    priority.append(fid)
                    if len(ft_seen) >= 6:
                        break

        selected = set(priority[:15])
        result = self._assemble_graph(selected, kg_index)
        logger.info("[GraphService] subsystem view focus=%s: %d nodes", focus_id, len(result["nodes"]))
        return result

    # =========================================================================
    # 图组装与节点/边映射
    # =========================================================================

    def _assemble_graph(self, selected_ids: Set[str], kg_index: dict) -> dict:
        """
        从 selected_ids 中取节点，保留两端均在集合内的边，组装为前端格式。
        """
        raw_nodes = kg_index["raw_nodes"]
        all_edges = kg_index["all_edges"]

        nodes = [self._map_node(raw_nodes[nid]) for nid in selected_ids if nid in raw_nodes]

        edges = []
        seen: Set[tuple] = set()
        for e in all_edges:
            src, tgt = e["source"], e["target"]
            if src not in selected_ids or tgt not in selected_ids:
                continue
            rel = e.get("relation") or e.get("label") or ""
            key = (src, tgt, rel)
            if key not in seen:
                seen.add(key)
                edges.append(self._map_edge(e))

        return {"nodes": nodes, "edges": edges, "categories": _KG_CATEGORIES}

    def _map_node(self, raw: dict) -> dict:
        node_type = raw.get("type", "")
        props = raw.get("properties") or {}
        name = (
            props.get("name")
            or props.get("label")
            or str(raw.get("id", "")).rsplit("::", 1)[-1]
        )
        cat, color, size = _NODE_TYPE_STYLE.get(node_type, _DEFAULT_NODE_STYLE)
        return {
            "id": raw["id"],
            "name": _truncate_label(name),   # 截断用于显示
            "label": name,                    # 完整 label
            "type": node_type,
            "category": cat,
            "symbolSize": size,
            "value": props.get("description") or name,
            "itemStyle": {"color": color},
            "data": props,                    # 完整属性
        }

    def _map_edge(self, raw: dict) -> dict:
        weight = raw.get("weight", 1)
        return {
            "source": raw["source"],
            "target": raw["target"],
            "label": raw.get("relation") or raw.get("label") or "",
            "lineStyle": {"width": min(1.0 + weight * 0.5, 5.0)},
        }

    # =========================================================================
    # 向后兼容：center_id 1-跳展开（非默认路径）
    # =========================================================================

    def _build_center_subgraph(
        self, kg_index: dict, center_id: str, max_nodes: int, max_edges: int
    ) -> dict:
        """center_id 路径：收集 1-跳邻居，再做 max 限流。"""
        raw_nodes = kg_index["raw_nodes"]
        out_edges = kg_index["out_edges"]
        in_edges  = kg_index["in_edges"]

        if center_id not in raw_nodes:
            logger.warning("[GraphService] center_id %r not found, returning overview", center_id)
            return self._build_overview_graph(kg_index)

        selected: Set[str] = {center_id}
        for e in out_edges.get(center_id, []) + in_edges.get(center_id, []):
            selected.add(e["source"])
            selected.add(e["target"])

        result = self._assemble_graph(selected, kg_index)
        nodes = sorted(result["nodes"], key=lambda n: n.get("symbolSize", 0), reverse=True)[:max_nodes]
        visible = {n["id"] for n in nodes}
        edges = [e for e in result["edges"] if e["source"] in visible and e["target"] in visible][:max_edges]
        result["nodes"] = nodes
        result["edges"] = edges
        return result

    # =========================================================================
    # 原有数据库逻辑（保留为 fallback，代码不做任何改动）
    # =========================================================================

    def _build_from_db(self, db: Session) -> dict:
        fault_types = db.query(FaultType).all()
        log_entries = db.query(LogEntry).all()

        nodes = []
        edges = []

        for ft in fault_types:
            nodes.append({
                "id": f"ft_{ft.id}",
                "name": ft.name,
                "category": 0,
                "symbolSize": 45,
                "value": ft.description or ft.name,
                "itemStyle": {"color": "#5470c6"},
            })

        for le in log_entries:
            nodes.append({
                "id": f"log_{le.id}",
                "name": le.filename,
                "category": 1,
                "symbolSize": 20,
                "value": le.summary or le.filename,
                "itemStyle": {"color": "#ee6666"},
            })
            if le.fault_type_id:
                edges.append({
                    "source": f"log_{le.id}",
                    "target": f"ft_{le.fault_type_id}",
                    "lineStyle": {"width": 1.5},
                })

        return {
            "nodes": nodes,
            "edges": edges,
            "categories": [
                {"name": "故障类型"},
                {"name": "日志案例"},
            ],
        }


# =============================================================================
# 模块级工具函数（与上一版本完全相同）
# =============================================================================

def _parse_jsonl_line(line: str, filename: str, line_no: int):
    """解析 JSONL 单行。空行静默跳过，JSON 错误记录 warning 并返回 None。"""
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        logger.warning(
            "[GraphService] %s line %d JSON parse failed (%s), skipping",
            filename, line_no, exc,
        )
        return None


def _find_node_id(
    focus: Optional[str], expected_type: str, raw_nodes: Dict[str, dict]
) -> Optional[str]:
    """
    按 focus 字符串定位节点 ID（三级匹配）：
      1. 完整 ID 直接命中（如 "FaultType::nova.compute"）
      2. 自动补类型前缀（如 "nova.compute" → "FaultType::nova.compute"）
      3. properties.name 大小写不敏感模糊匹配
    """
    if not focus:
        return None
    if focus in raw_nodes:
        return focus
    prefixed = f"{expected_type}::{focus}"
    if prefixed in raw_nodes:
        return prefixed
    focus_lower = focus.lower()
    for nid, n in raw_nodes.items():
        if n.get("type") != expected_type:
            continue
        props = n.get("properties") or {}
        name = (props.get("name") or props.get("label") or "").lower()
        if focus_lower == name or focus_lower in nid.lower():
            return nid
    return None


def _truncate_label(name: str, max_len: int = _LABEL_MAX_LEN) -> str:
    """截断节点显示标签，超出 max_len 时末位替换为省略号。"""
    if len(name) <= max_len:
        return name
    return name[: max_len - 1] + "…"
