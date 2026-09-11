"""
log_window_service.py

职责（唯一）：
    从 MongoDB log_entries 读取指定 run_id 的全部有效日志条目
    （仅 timestamp 为 datetime 类型的条目），按时间滑动窗口切分，
    写入 MongoDB log_windows 集合。

对齐目标：
    与离线 04_build_log_windows_v2.py（strategy="time"）保持结构一致，
    确保在线构建的 log_windows 可被 05_build_vector_index_faiss.py 正确读取。

包含：
    - 时间滑窗（window_size_s + stride_s）
    - window_id = f"{run_id}_w_{sha1(key)[:16]}"（与离线格式完全一致）
    - _id 显式设置为 window_id（支持按 _id 分页拉取 + upsert）
    - text 重组为 "ts_iso LEVEL MODULE message" 格式（与 FAISS 索引训练格式一致）
    - key_events（前 topn 条 ERROR/CRITICAL 事件，按时间序）
    - stats 子文档（n_entries / level_distribution / top_modules / error 计数）
    - metadata 子文档（05_build_vector_index_faiss get_label_field() 兼容层）
    - is_fault_label / is_fault_candidate 语义分离

不允许：
    - 写 MySQL
    - 调用任何其他 service
    - 修改已有 log_entries 文档
    - 填写 fault_type / root_cause / root_cause_type / label_source
      （由 LabelEnrichmentService 负责）
"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from pymongo import ASCENDING
from pymongo.database import Database

from app.services.log_ingest_service import RunMeta

logger = logging.getLogger(__name__)

# MongoDB 集合名
_ENTRIES_COL = "log_entries"
_WINDOWS_COL = "log_windows"

# 窗口参数默认值（与离线脚本默认值一致）
_DEFAULT_WINDOW_SIZE_S = 30
_DEFAULT_STRIDE_S = 15
_DEFAULT_MIN_ENTRIES = 1
_DEFAULT_KEY_EVENTS_TOPN = 10

# 级别集合
_ERROR_LEVELS = frozenset({"ERROR", "CRITICAL"})     # 与离线 ERROR_LEVELS 完全一致
_CRITICAL_LEVELS = frozenset({"CRITICAL", "FATAL"})  # 额外细化，不影响 error_events 计数
_WARN_LEVELS = frozenset({"WARNING", "WARN"})


# =============================================================================
# 输出数据结构
# =============================================================================


@dataclass
class WindowResult:
    """build_windows() 的统计输出。"""

    run_id: str
    task_id: Optional[str]
    total_entries: int    # 从 log_entries 读取的有效条目数（仅含有效 timestamp 的）
    window_count: int     # 构建的窗口总数（含 min_entries 过滤后）
    inserted_count: int   # 实际写入 MongoDB 的窗口数（upsert 每条成功计 1）


# =============================================================================
# 内部工具函数
# =============================================================================


def _make_window_id(
    run_id: str,
    start_time: datetime,
    end_time: datetime,
    salt: str,
) -> str:
    """
    window_id = f"{run_id}_w_{sha1(key)[:16]}"

    与离线 04_build_log_windows_v2.py mk_window_id() 格式完全一致。
    salt 携带策略参数（如 "time|ws=30|st=15"），防止不同参数的窗口 ID 碰撞。
    """
    key = f"{run_id}|{start_time.isoformat()}|{end_time.isoformat()}|{salt}"
    return f"{run_id}_w_{hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]}"


def _reconstruct_text(entries: List[dict]) -> str:
    """
    将条目列表重组为 FAISS 索引兼容的文本格式。

    格式：每行 = "{ts.isoformat()} {LEVEL} {MODULE} {message}"
    与离线 build_window_doc() 中 text 生成逻辑完全一致。

    - timestamp 为 None 的条目跳过（不纳入 text）
    - level / module 缺失时用 "UNKNOWN" 占位
    """
    lines = []
    for e in entries:
        ts = e.get("timestamp")
        if not isinstance(ts, datetime):
            continue
        level = (e.get("level") or "UNKNOWN").upper()
        module = e.get("module") or "UNKNOWN"
        message = (e.get("message") or "").strip()
        lines.append(f"{ts.isoformat()} {level} {module} {message}")
    return "\n".join(lines)


def _build_window_doc(
    entries: List[dict],
    run_meta: RunMeta,
    start_time: datetime,
    end_time: datetime,
    strategy: str,
    salt: str,
    key_events_topn: int,
    now_utc: datetime,
) -> dict:
    """
    将一批 log_entries 文档构建为一个 log_window 文档。

    entries 已按 timestamp 升序排序，且均含有效 datetime timestamp
    （由 build_windows 的 MongoDB 查询过滤保证）。
    """
    # ── window_id / _id ──────────────────────────────────────────────────────
    window_id = _make_window_id(run_meta.run_id, start_time, end_time, salt)

    # ── task_id fallback（不允许写 None，与离线一致） ─────────────────────────
    task_id: str = run_meta.task_id or f"task::{run_meta.run_id}"

    # ── text 重组（FAISS 索引兼容格式） ───────────────────────────────────────
    text = _reconstruct_text(entries)

    # ── 统计 ─────────────────────────────────────────────────────────────────
    level_dist: Dict[str, int] = {}
    module_dist: Counter = Counter()
    error_events = 0
    warning_events = 0
    critical_events = 0
    key_events: List[dict] = []

    for e in entries:
        lvl = (e.get("level") or "UNKNOWN").upper()
        mod = e.get("module") or "UNKNOWN"
        msg = (e.get("message") or "").strip()
        ts = e.get("timestamp")

        level_dist[lvl] = level_dist.get(lvl, 0) + 1
        module_dist[mod] += 1

        if lvl in _ERROR_LEVELS:
            error_events += 1
            if len(key_events) < key_events_topn:
                key_events.append({
                    "timestamp": ts,
                    "level": lvl,
                    "module": mod,
                    "message": msg,
                    "line_no": e.get("line_no"),
                })

        if lvl in _CRITICAL_LEVELS:
            critical_events += 1

        if lvl in _WARN_LEVELS:
            warning_events += 1

    top_modules = [
        {"module": m, "count": c}
        for m, c in module_dist.most_common(10)
    ]

    # ── 故障标记（语义分离） ──────────────────────────────────────────────────
    is_fault_label: Optional[bool] = run_meta.is_fault
    is_fault_candidate: bool = error_events > 0
    # compat：有确定标签时用标签，否则用推断值
    is_fault: bool = is_fault_label if is_fault_label is not None else is_fault_candidate

    return {
        # ── 标识（_id 显式设置，与离线 _id 策略一致） ────────────────────────
        "_id":       window_id,
        "window_id": window_id,

        # ── run_meta 透传 ─────────────────────────────────────────────────────
        "run_id":      run_meta.run_id,
        "task_id":     task_id,
        "case_id":     run_meta.case_id,
        "system_id":   run_meta.system_id,
        "subsystem":   run_meta.subsystem,
        "source_type": run_meta.source_type,
        "test_name":   run_meta.test_name,   # 数据集场景有值；upload 为 None
        "round_no":    run_meta.round_no,    # 数据集场景有值；upload 为 None

        # ── 时间范围 ──────────────────────────────────────────────────────────
        "start_time":  start_time,   # UTC naive，窗口左边界（含）
        "end_time":    end_time,     # UTC naive，窗口右边界（含）
        "anchor_time": None,         # time 策略固定为 None；error 策略预留

        # ── 窗口策略 ──────────────────────────────────────────────────────────
        "strategy": strategy,        # "time"（本服务当前仅支持时间滑窗）

        # ── 文本（FAISS embedding 对象） ──────────────────────────────────────
        # 格式："{ts} {LEVEL} {MODULE} {message}\n..."，与离线格式一致
        "text": text,

        # ── 关键事件（前 key_events_topn 条 ERROR/CRITICAL，按时间序） ─────────
        "key_events": key_events,

        # ── 聚合统计子文档 ────────────────────────────────────────────────────
        "stats": {
            "n_entries":          len(entries),
            "error_events":       error_events,
            "warning_events":     warning_events,
            "critical_events":    critical_events,
            "level_distribution": level_dist,
            "top_modules":        top_modules,
            "strategy":           strategy,
        },

        # ── 故障标记 ──────────────────────────────────────────────────────────
        "is_fault":           is_fault,           # compat 字段（离线 & API 兼容）
        "is_fault_label":     is_fault_label,     # ground truth，来自 run_meta.is_fault
        "is_fault_candidate": is_fault_candidate, # 推断：error_events > 0

        # ── 标签（待 LabelEnrichmentService 填充） ────────────────────────────
        "fault_type":      None,
        "root_cause":      None,
        "root_cause_type": None,
        "label_source":    None,

        # ── embedding 状态 ────────────────────────────────────────────────────
        "embedding_status": "pending",

        # ── FAISS 检索兼容层 ──────────────────────────────────────────────────
        # 05_build_vector_index_faiss.py 的 get_label_field() 先读顶层，
        # 再 fallback 到 metadata 子文档，此处保持字段同步。
        "metadata": {
            "fault_type":      None,
            "root_cause":      None,
            "root_cause_type": None,
            "task_id":         task_id,
            "case_id":         run_meta.case_id,
            "run_id":          run_meta.run_id,
            "system_id":       run_meta.system_id,
            "subsystem":       run_meta.subsystem,
            "is_fault":        is_fault,
            "source_type":     run_meta.source_type,
            "round_no":        run_meta.round_no,
        },

        "created_at": now_utc,
    }


# =============================================================================
# LogWindowService
# =============================================================================


class LogWindowService:
    """
    无状态的时间滑窗构建服务。

    build_windows() 是唯一公开方法，所有 I/O 资源由调用方传入。
    同一 run_id 可安全重复调用（upsert=True 时幂等）。

    窗口算法与离线 04_build_log_windows_v2.py（strategy="time"）完全一致：
      - 只处理 timestamp 为有效 datetime 类型的条目（无 timestamp 的条目不入窗）
      - 按 [t, t + window_size_s] 滑动，步长 stride_s
      - 条目数 < min_entries 的窗口丢弃
      - 内层扫描使用"起始索引前移"优化：O(n × window_size/stride) 而非 O(n × windows)
    """

    def build_windows(
        self,
        run_meta: RunMeta,
        mongo_db: Database,
        window_size_s: int = _DEFAULT_WINDOW_SIZE_S,
        stride_s: int = _DEFAULT_STRIDE_S,
        min_entries: int = _DEFAULT_MIN_ENTRIES,
        key_events_topn: int = _DEFAULT_KEY_EVENTS_TOPN,
        upsert: bool = True,
    ) -> WindowResult:
        """
        从 log_entries 读取 run_id 对应的全部有效条目，构建时间滑动窗口。

        参数
        ----
        run_meta        : 注入任务元信息（与 LogIngestService 使用相同的 RunMeta）
        mongo_db        : pymongo Database 对象（调用方管理连接生命周期）
        window_size_s   : 窗口时长（秒，默认 30）
        stride_s        : 滑动步长（秒，默认 15）；stride < window_size 时产生重叠窗口
        min_entries     : 每窗口最少条目数，低于此则丢弃（默认 1）
        key_events_topn : 每窗口保留的 ERROR/CRITICAL 事件数上限（默认 10）
        upsert          : True  = replace_one/upsert，重复调用幂等
                          False = insert_many，首次批量写入速度快

        返回
        ----
        WindowResult
        """
        entries_col = mongo_db[_ENTRIES_COL]
        windows_col = mongo_db[_WINDOWS_COL]
        now_utc = datetime.now(tz=timezone.utc).replace(tzinfo=None)

        # ── 只读取 timestamp 为有效 datetime 的条目 ────────────────────────────
        # 与离线 fetch_entries_for_run 的过滤规则完全一致：
        #   {"run_id": run_id, "timestamp": {"$type": "date"}}
        # 不含有效 timestamp 的条目（parse_status="failed" 等）不纳入任何窗口。
        cursor = entries_col.find(
            {"run_id": run_meta.run_id, "timestamp": {"$type": "date"}},
            projection={
                "_id":       0,
                "timestamp": 1,
                "level":     1,
                "module":    1,
                "message":   1,
                "line_no":   1,
            },
            sort=[("timestamp", ASCENDING)],
        )
        entries: List[dict] = list(cursor)
        total_entries = len(entries)

        if total_entries == 0:
            logger.warning(
                "[window] run_id=%s: no timestamped entries found; nothing to window",
                run_meta.run_id,
            )
            return WindowResult(
                run_id=run_meta.run_id,
                task_id=run_meta.task_id,
                total_entries=0,
                window_count=0,
                inserted_count=0,
            )

        # ── 时间滑窗 ──────────────────────────────────────────────────────────
        first_ts: datetime = entries[0]["timestamp"]
        last_ts: datetime = entries[-1]["timestamp"]
        window_delta = timedelta(seconds=window_size_s)
        stride_delta = timedelta(seconds=stride_s)

        # salt 携带策略参数，与离线脚本 build_windows_time() 调用格式一致
        salt = f"time|ws={window_size_s}|st={stride_s}"
        strategy = "time"

        window_docs: List[dict] = []
        t = first_ts
        # win_start_idx：当前窗口起始位置的下界索引（单调前进，均摊 O(n) 扫描）
        win_start_idx = 0

        while t <= last_ts:
            t_end = t + window_delta

            # 将 win_start_idx 前移到第一个 timestamp >= t 的位置
            while win_start_idx < total_entries and entries[win_start_idx]["timestamp"] < t:
                win_start_idx += 1

            # ── 空隙快进（修复：数据时间轴存在大跨度间隔时避免百万级空转） ──────
            # 若 [t, t_end] 内无任何条目，按 stride 网格快进到下一条目附近，
            # 保持窗口起点仍落在 first_ts + k*stride 网格上（与离线输出对齐）。
            # 对无间隔的密集数据，next_ts <= t_end，分支不触发，行为与原实现完全一致。
            if win_start_idx >= total_entries:
                break
            next_ts = entries[win_start_idx]["timestamp"]
            if next_ts > t_end:
                gap_s = (next_ts - t).total_seconds()
                skip = int(gap_s // stride_s)
                t = t + (stride_delta * skip if skip > 0 else stride_delta)
                continue

            # 收集 [t, t_end] 内的全部条目（entries 已升序排列，遇到超出 t_end 即可 break）
            chunk: List[dict] = []
            for j in range(win_start_idx, total_entries):
                if entries[j]["timestamp"] > t_end:
                    break
                chunk.append(entries[j])

            if len(chunk) >= min_entries:
                doc = _build_window_doc(
                    chunk, run_meta,
                    t, t_end,
                    strategy, salt,
                    key_events_topn, now_utc,
                )
                window_docs.append(doc)

            t += stride_delta

        # ── 写入 MongoDB ─────────────────────────────────────────────────────
        inserted_count = 0

        if upsert:
            # 逐条 replace_one/upsert：单条失败不影响其余，重复调用安全
            for doc in window_docs:
                try:
                    windows_col.replace_one(
                        {"_id": doc["_id"]},
                        doc,
                        upsert=True,
                    )
                    inserted_count += 1
                except Exception as exc:
                    logger.warning(
                        "[window] upsert failed window_id=%s run_id=%s: %s",
                        doc.get("window_id"),
                        run_meta.run_id,
                        exc,
                    )
        else:
            # 批量插入：速度快，但若 window_id 已存在会抛重复键异常
            try:
                result = windows_col.insert_many(window_docs, ordered=False)
                inserted_count = len(result.inserted_ids)
            except Exception as exc:
                logger.warning(
                    "[window] insert_many partial failure run_id=%s: %s",
                    run_meta.run_id,
                    exc,
                )

        logger.info(
            "[window] run_id=%s total_entries=%d windows=%d inserted=%d "
            "window_size_s=%d stride_s=%d min_entries=%d",
            run_meta.run_id,
            total_entries,
            len(window_docs),
            inserted_count,
            window_size_s,
            stride_s,
            min_entries,
        )

        return WindowResult(
            run_id=run_meta.run_id,
            task_id=run_meta.task_id,
            total_entries=total_entries,
            window_count=len(window_docs),
            inserted_count=inserted_count,
        )


# =============================================================================
# 模块级单例（懒加载友好，无初始化成本）
# =============================================================================

log_window_service = LogWindowService()
