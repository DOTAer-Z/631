"""
run_indexing_service.py

职责：把「文件选择」里的 run（Postgres 里的 runs / cases / log_windows /
log_entries）批量写入两个下游：

    1. 知识库（Chroma collection `fault_logs`）—— 供故障诊断的 Step 3 检索
    2. 知识图谱（KG_DIR/kg_nodes.jsonl + kg_edges.jsonl）—— 供诊断的 KG 重排
       与「知识图谱」页展示

为什么需要这个服务
------------------
9.10 及之前，向量库/知识图谱只有三条「推」入口，且都只吃 SQL `log_entries`
表（11 条 demo）：上传日志 / 新增知识案例 / 从标注典型案例导入。用户在
「数据导入」灌进 Postgres 的三类数据（结构化 Test_xxx、非结构化 seg_xxx、
半结构化 nuttx_Test_xxx）没有任何路径参与检索 —— 诊断的相似度因此恒为
「-」。本服务补上这条路径，并且是可重复执行、用户可控的。

设计要点
--------
- **单位是 window，不是 run**：诊断检索的 query 是窗口文本
  （`fault_location_service._retrieve_and_aggregate` 逐窗口查 Chroma），
  粒度不一致会系统性拉低相似度。run 没有窗口时才退化为整条 entry。
- **doc_id 稳定**：`faultwin::{window_id}` / `faulthlog::{log_entry_id}`，
  重复入库走 Chroma upsert 覆盖，不产生重复向量。
- **必须有故障类型**：没有故障类型的 run 直接 skip —— 检索候选落到
  "__unknown__" 会拉低诊断置信度。故障类型来源按优先级取
  case.fault_type_id → case.fault_type_label → str(run.fault_type)
  （后者 name 不存在时自动建 FaultType 行，与标注导入的做法一致）。
- **KG 只建实例层**：`Case::<run_id> -HAS_RESULT_FAULT-> FaultType::<name>`，
  外加根因/子系统（有才建）。抽象的 FaultType/RootCause 关系由
  `append_curated_case` 负责，两边共用同一批 jsonl 文件。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Chroma 单次 add/upsert 的最大条数（嵌入一次算一批，太大爆内存）
_BATCH_SIZE = 32

# 单个窗口/条目进入向量的最大字符数。窗口文本理论上无上限，
# 这里截断是为了控制嵌入耗时，同时避免超长文本稀释语义。
_MAX_TEXT_CHARS = 4000

_ENTRIES_COLLECTION = "log_entries"
_WINDOWS_COLLECTION = "log_windows"

# doc_id 前缀：便于按前缀统计 / 清理，不与旧 log_entry_{id} 命名冲突
_DOC_PREFIX_WINDOW = "faultwin::"
_DOC_PREFIX_ENTRY = "faulthlog::"


class IndexingError(RuntimeError):
    """入知识库整体失败（例如向量库初始化不了）时抛出。"""


# ════════════════════════════════════════════════════════════════════════════
# 故障类型解析
# ════════════════════════════════════════════════════════════════════════════

def _get_or_create_fault_type(db: Session, name: str) -> Tuple[Any, bool]:
    """按名称取故障类型，不存在则创建。返回 (ft, created)。"""
    from app.models.fault_type import FaultType

    name = (name or "").strip()
    if not name:
        raise ValueError("故障类型名称为空")
    ft = db.query(FaultType).filter(FaultType.name == name).first()
    if ft:
        return ft, False
    ft = FaultType(name=name, color_tag="red")
    db.add(ft)
    db.commit()
    db.refresh(ft)
    return ft, True


def _resolve_fault_type(
    db: Session,
    *,
    case: Optional[Any],
    run: Any,
) -> Tuple[Optional[Any], bool, str]:
    """推导一条 run 的故障类型。

    返回 (fault_type 或 None, 是否新建, 来源说明)。

    优先级：
        1. case.fault_type_id  → FK 命中 fault_types 表（用户在「文件选择」里选的）
        2. case.fault_type_label → 自由文本名（segment 导入自带 ground_truth.fault_type）
        3. run.fault_type（shim 里透传的同名列）
    都没有则视为「未标注」，调用方 skip。
    """
    from app.models.fault_type import FaultType

    if case is not None and getattr(case, "fault_type_id", None):
        ft = db.query(FaultType).filter(FaultType.id == case.fault_type_id).first()
        if ft:
            return ft, False, "case.fault_type_id"

    raw = ""
    if case is not None:
        raw = (getattr(case, "fault_type_label", None) or "").strip()
    if not raw:
        raw = (getattr(run, "fault_type", None) or "").strip()
    if not raw:
        return None, False, ""

    ft, created = _get_or_create_fault_type(db, raw)
    return ft, created, "case.fault_type_label"


def _resolve_root_cause(case: Optional[Any], ft: Any) -> str:
    """根因取 case.description；没有则退化为故障类型自身描述。

    两者都空时返回空串 —— 调用方据此决定要不要建 RootCause 节点。
    """
    desc = (getattr(case, "description", None) or "").strip() if case else ""
    if desc:
        # case.description 通常是导入来源说明（"从 test.jsonl 导入（...）"），
        # 对检索仍有价值（能区分数据来源），不额外过滤。
        return desc
    return (getattr(ft, "description", None) or "").strip()


# ════════════════════════════════════════════════════════════════════════════
# 文本组装
# ════════════════════════════════════════════════════════════════════════════

def _as_int(value: Any) -> int:
    """把窗口 id / 条目 id 安全转 int（Chroma metadata 不接受 None 与任意字符串）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _compose_text(
    *,
    ft_name: str,
    run_id: str,
    body: str,
    start_time: Any = None,
    end_time: Any = None,
) -> str:
    """把「故障类型 + 来源 run + 正文」拼成入库文本。

    加头部上下文（而不是只存裸日志）是为了让检索文本自带标签信息：
    诊断查询的是疑似窗口的原始日志，加上「故障类型：xxx」后，同类型样本
    在 embedding 空间更聚拢，且命中后大模型能从候选文本里直接读到类型。
    """
    head = [f"故障类型：{ft_name}", f"来源 Run：{run_id}"]
    if start_time is not None or end_time is not None:
        head.append(f"时间范围：{start_time} ~ {end_time}")
    body = (body or "").strip()
    if not body:
        return ""
    return "\n".join(head) + "\n日志内容：\n" + body


# ════════════════════════════════════════════════════════════════════════════
# 取数：按 run 收集「可入库单元」
# ════════════════════════════════════════════════════════════════════════════

def _collect_units(
    mongo_db: Any,
    *,
    run_id: str,
    window_scope: str,
    max_units_per_run: int,
) -> List[Dict[str, Any]]:
    """收集一条 run 的入库单元（优先窗口，无窗口退化到条目）。

    window_scope:
        "suspicious"（默认）— stats.error_events > 0 的窗口；若一个都没有，
                              退化为取 entry_count 最多的前 N 个（与诊断的
                              `_select_suspicious_windows` 兜底逻辑一致）
        "all"              — 全部窗口，按 start_time 升序
    """
    units: List[Dict[str, Any]] = []

    windows = list(
        mongo_db[_WINDOWS_COLLECTION].find(
            {"run_id": run_id},
            {
                "_id": 0,
                "window_id": 1,
                "start_time": 1,
                "end_time": 1,
                "stats": 1,
                "text": 1,
            },
        )
    )

    if windows:
        if window_scope == "all":
            ordered = sorted(
                windows,
                key=lambda w: (w.get("start_time") is None, w.get("start_time"), w.get("window_id")),
            )
        else:
            suspicious = [
                w for w in windows
                if int((w.get("stats") or {}).get("error_events") or 0) > 0
            ]
            if suspicious:
                ordered = sorted(
                    suspicious,
                    key=lambda w: (
                        -int((w.get("stats") or {}).get("error_events") or 0),
                        w.get("window_id"),
                    ),
                )
            else:
                ordered = sorted(
                    windows,
                    key=lambda w: -int((w.get("stats") or {}).get("n_entries") or 0),
                )

        for w in ordered[:max_units_per_run]:
            units.append({
                "kind": "window",
                "key": w.get("window_id"),
                "body": w.get("text") or "",
                "start_time": w.get("start_time"),
                "end_time": w.get("end_time"),
                "n_entries": int((w.get("stats") or {}).get("n_entries") or 0),
                "error_events": int((w.get("stats") or {}).get("error_events") or 0),
            })
        return units

    # ── 无窗口（segment 非结构化导入走的是这条）──
    entries = list(
        mongo_db[_ENTRIES_COLLECTION].find(
            {"run_id": run_id},
            {"_id": 0, "log_id": 1, "_id": 1, "raw_line": 1, "message": 1, "timestamp": 1},
        ).limit(max_units_per_run)
    )
    for e in entries:
        body = (e.get("raw_line") or e.get("message") or "").strip()
        units.append({
            "kind": "entry",
            "key": e.get("log_id") or e.get("_id"),
            "body": body,
            "start_time": e.get("timestamp"),
            "end_time": e.get("timestamp"),
            "n_entries": 1,
            "error_events": 0,
        })
    return units


# ════════════════════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════════════════════

def index_runs(
    db: Session,
    mongo_db: Any,
    *,
    run_ids: Sequence[str],
    window_scope: str = "suspicious",
    max_units_per_run: int = 60,
    force: bool = False,
    skip_kg: bool = False,
    create_fault_types: bool = True,
) -> Dict[str, Any]:
    """把若干 run 入知识库 + 知识图谱。

    参数
    ----
    run_ids            : 要入库的 run_id 列表（必须先在 runs 表里存在）
    window_scope       : "suspicious" | "all"（见 _collect_units）
    max_units_per_run  : 每条 run 最多入多少个窗口/条目（防一条巨型 run 灌爆库）
    force              : False 时跳过「已有向量」的单元；True 则全部 upsert 覆盖
    skip_kg            : True 时只动向量库，不写 KG
    create_fault_types : 遇到 case.fault_type_label 里没见过的类型名时是否自动建行

    返回
    ----
    {
      indexed, skipped, failed,           # 向量库维度（单位 = 窗口/条目）
      runs_indexed, runs_skipped,         # run 维度
      kg_cases, kg_new_nodes, kg_new_edges,
      created_fault_types,
      details: [ {run_id, status, reason?, indexed, skipped, doc_kind, fault_type, kg} ],
      errors: [ ... ],
    }
    """
    from app.models.case import Case
    from app.models.run import Run
    from app.services.vector_store import get_vector_store

    if not run_ids:
        return _empty_result()

    try:
        vs = get_vector_store()
    except Exception as exc:  # noqa: BLE001
        raise IndexingError(f"向量库初始化失败: {exc}") from exc

    details: List[Dict[str, Any]] = []
    errors: List[str] = []
    kg_instances: List[Dict[str, Any]] = []

    indexed = skipped = failed = 0
    runs_indexed = runs_skipped = 0
    created_fault_types = 0

    for run_id in run_ids:
        run = db.query(Run).filter(Run.run_id == run_id).first()
        if run is None:
            details.append({"run_id": run_id, "status": "not_found"})
            errors.append(f"{run_id}: run 不存在")
            continue

        case: Optional[Case] = None
        if run.case_id:
            case = db.query(Case).filter(Case.case_id == run.case_id).first()

        ft, ft_created, ft_source = _resolve_fault_type(db, case=case, run=run)
        if ft_created:
            created_fault_types += 1

        if ft is None:
            _remove_existing_vectors(vs, run_id)
            details.append({
                "run_id": run_id,
                "status": "skipped",
                "reason": "未标注故障类型（请先在「文件选择」编辑该 run 选择故障类型）",
            })
            runs_skipped += 1
            continue

        units = _collect_units(
            mongo_db,
            run_id=run_id,
            window_scope=window_scope,
            max_units_per_run=max_units_per_run,
        )
        units = [u for u in units if (u.get("body") or "").strip()]
        if not units:
            _remove_existing_vectors(vs, run_id)
            details.append({
                "run_id": run_id,
                "status": "skipped",
                "reason": "没有可入库的窗口/日志内容（该 run 没有 log_windows，也没有 log_entries）",
                "fault_type": ft.name,
            })
            runs_skipped += 1
            continue

        root_cause = _resolve_root_cause(case, ft)

        # ── 组装 (doc_id, text, metadata) ──
        doc_ids: List[str] = []
        texts: List[str] = []
        metadatas: List[Dict[str, Any]] = []
        for u in units:
            body = (u["body"] or "")[:_MAX_TEXT_CHARS]
            text = _compose_text(
                ft_name=ft.name,
                run_id=run_id,
                body=body,
                start_time=u.get("start_time"),
                end_time=u.get("end_time"),
            )
            if not text:
                continue
            uid = u.get("key")
            if u["kind"] == "window":
                doc_id = f"{_DOC_PREFIX_WINDOW}{uid}"
            else:
                doc_id = f"{_DOC_PREFIX_ENTRY}{uid}"
            meta: Dict[str, Any] = {
                # 诊断侧 _query_knowledge_base 读的键（不可改名）
                "fault_type_name": ft.name,
                "fault_type_id": ft.id,
                "log_entry_id": 0 if u["kind"] == "window" else _as_int(uid),
                # 供 delete_run 按 run_id 反查清理 / 供前端统计
                "run_id": run_id,
                "case_id": run.case_id or "",
                "source": "run_import",
                "doc_kind": u["kind"],
                "window_id": str(uid) if u["kind"] == "window" else "",
                "window_total": len(units),
            }
            if u.get("start_time") is not None:
                meta["start_time"] = str(u["start_time"])
            if root_cause:
                meta["root_cause"] = root_cause[:500]
            # Chroma 只接受 str/int/float/bool —— None 会让整批 add 报错，
            # 统一在这里兜底（新增字段时忘了判空也不会炸）。
            meta = {k: v for k, v in meta.items() if v is not None}
            doc_ids.append(doc_id)
            texts.append(text)
            metadatas.append(meta)

        # ── 已有向量判定（force=False 时跳过）──
        total_built = len(doc_ids)
        if not force:
            existing = vs.get_metadatas(doc_ids)
            keep = [i for i, m in enumerate(existing) if m is None]
            run_skipped = total_built - len(keep)
            doc_ids = [doc_ids[i] for i in keep]
            texts = [texts[i] for i in keep]
            metadatas = [metadatas[i] for i in keep]
        else:
            run_skipped = 0

        run_indexed = _upsert_batched(vs, doc_ids, texts, metadatas, errors, run_id)
        run_failed = len(doc_ids) - run_indexed

        indexed += run_indexed
        skipped += run_skipped
        failed += run_failed
        runs_indexed += 1

        kg_info: Dict[str, Any] = {}
        if not skip_kg:
            kg_instances.append({
                "run_id": run_id,
                "case_id": run.case_id,
                "display_name": run.run_name or run.run_id,
                "fault_type_name": ft.name,
                "root_cause": root_cause,
                "subsystem": run.subsystem,
                "window_total": len(units),
                "log_entry_id": metadatas[0].get("log_entry_id") if metadatas else None,
            })

        details.append({
            "run_id": run_id,
            "status": "indexed",
            "fault_type": ft.name,
            "fault_type_source": ft_source,
            "doc_kind": units[0]["kind"] if units else None,
            "indexed": run_indexed,
            "skipped": run_skipped,
            "failed": run_failed,
            "kg": kg_info,
        })

    # ── KG 批量落盘 ──
    kg_res: Dict[str, Any] = {"cases": 0, "new_nodes": 0, "new_edges": 0}
    if kg_instances:
        try:
            from app.services.kg_service_v2 import kg_service_v2

            kg_res = kg_service_v2.append_run_cases(instances=kg_instances)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"知识图谱写入失败：{exc}")
            logger.warning("KG append_run_cases 失败", exc_info=True)

    # ── 入库后失效图谱缓存（节点数变了） ──
    try:
        from app.utils.cache import cache_delete, GRAPH_CACHE_KEY

        cache_delete(GRAPH_CACHE_KEY)
    except Exception:  # noqa: BLE001
        pass

    return {
        "indexed": indexed,
        "skipped": skipped,
        "failed": failed,
        "runs_indexed": runs_indexed,
        "runs_skipped": runs_skipped,
        "kg_cases": kg_res.get("cases", 0),
        "kg_new_nodes": kg_res.get("new_nodes", 0),
        "kg_new_edges": kg_res.get("new_edges", 0),
        "created_fault_types": created_fault_types,
        "details": details,
        "errors": errors,
    }


def _upsert_batched(
    vs: Any,
    doc_ids: List[str],
    texts: List[str],
    metadatas: List[Dict[str, Any]],
    errors: List[str],
    run_id: str,
) -> int:
    """分批 upsert，返回成功条数；失败逐批记录不中断整体。"""
    ok = 0
    for start in range(0, len(doc_ids), _BATCH_SIZE):
        batch_ids = doc_ids[start:start + _BATCH_SIZE]
        batch_texts = texts[start:start + _BATCH_SIZE]
        batch_metas = metadatas[start:start + _BATCH_SIZE]
        try:
            vs.upsert_documents(
                doc_ids=batch_ids,
                texts=batch_texts,
                metadatas=batch_metas,
            )
            ok += len(batch_ids)
        except Exception as exc:  # noqa: BLE001
            logger.warning("run %s 入向量库第 %d 批失败: %s", run_id, start // _BATCH_SIZE, exc)
            errors.append(f"{run_id}: 入库批次 {start // _BATCH_SIZE} 失败（{exc}）")
    return ok


def _remove_existing_vectors(vs: Any, run_id: str) -> int:
    """清掉某 run 已有的向量（run 变成「未标注」或内容被清空时保持一致性）。"""
    try:
        got = vs.collection.get(where={"run_id": run_id}, include=[])
        ids = got.get("ids") or []
        if ids:
            vs.delete_documents(ids)
        return len(ids)
    except Exception as exc:  # noqa: BLE001
        logger.warning("清理 run %s 旧向量失败: %s", run_id, exc)
        return 0


def _empty_result() -> Dict[str, Any]:
    return {
        "indexed": 0,
        "skipped": 0,
        "failed": 0,
        "runs_indexed": 0,
        "runs_skipped": 0,
        "kg_cases": 0,
        "kg_new_nodes": 0,
        "kg_new_edges": 0,
        "created_fault_types": 0,
        "details": [],
        "errors": [],
    }


# ════════════════════════════════════════════════════════════════════════════
# 状态查询：给「文件选择」列表标「已入知识库」
# ════════════════════════════════════════════════════════════════════════════

def get_index_status(run_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """返回 {run_id: {indexed_windows: n, in_kg: bool}}。

    - indexed_windows：Chroma 里 metadata.run_id == run_id 的向量数
    - in_kg：KG 文件里存在 `Case::<run_id>` 节点

    向量数一次 get 全量 id+metadata 后本地聚合，避免按 run 逐次查询；
    Chroma collection 是本机 PersistentClient，量级（万级）可接受。
    """
    ids = [r for r in run_ids if r]
    out: Dict[str, Dict[str, Any]] = {r: {"indexed_windows": 0, "in_kg": False} for r in ids}
    if not ids:
        return out

    try:
        from app.services.vector_store import get_vector_store

        vs = get_vector_store()
        res = vs.collection.get(include=["metadatas"])
        metas = res.get("metadatas") or []
        wanted = set(ids)
        for m in metas:
            if not m:
                continue
            rid = m.get("run_id")
            if rid in wanted:
                out[rid]["indexed_windows"] += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("查询向量入库状态失败: %s", exc)

    try:
        from app.services.kg_service_v2 import kg_service_v2

        kg = kg_service_v2
        if not kg._loaded:
            kg._load_files()
            kg._loaded = True
        for r in ids:
            out[r]["in_kg"] = f"Case::{r}" in kg._nodes
    except Exception as exc:  # noqa: BLE001
        logger.warning("查询 KG 入库状态失败: %s", exc)

    return out
