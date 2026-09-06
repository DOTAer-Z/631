"""
log_ingest_service.py

职责：
    消费 PreprocessedLog.lines，逐行解析为结构化文档，
    批量写入 MongoDB log_entries 集合，输出 IngestResult 统计。

解析策略（三级降级）：
    Tier 1  全结构化  — timestamp + hostname + pid + level + module + message
    Tier 2  部分解析  — timestamp + level + message（无 host/pid）
    Tier 3  仅时间戳  — timestamp 可提取，其余字段为 None，message = raw_line
    失败    —         parse_status="failed"，raw_line 原样保留

约束：
    - 必须直接消费 preprocessed_log.lines，禁止自行 split
    - 不做窗口构建
    - 不写 MySQL
    - 不调用任何其他 service
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from pymongo.database import Database

from app.services.preprocessing_service import PreprocessedLog

logger = logging.getLogger(__name__)

# MongoDB 集合名
_COLLECTION = "log_entries"
# 每批写入文档数
_BATCH_SIZE = 500

# is_error_like 判断规则
_ERROR_LIKE_LEVELS = frozenset({"ERROR", "CRITICAL", "FATAL", "ALERT"})
_ERROR_LIKE_RE = re.compile(
    r"exception|traceback|failed|timeout|refused|unavailable|panic",
    re.IGNORECASE,
)


def _is_error_like(level: Optional[str], raw_line: str) -> bool:
    """
    判断一条日志是否具有"错误特征"。

    规则（OR 关系）：
    1. level 属于 ERROR / CRITICAL / FATAL / ALERT
    2. raw_line 中包含关键词（不区分大小写）：
       exception、traceback、failed、timeout、refused、unavailable、panic
    """
    if level and level.upper() in _ERROR_LIKE_LEVELS:
        return True
    return bool(_ERROR_LIKE_RE.search(raw_line))


# =============================================================================
# 公开数据结构
# =============================================================================


@dataclass
class RunMeta:
    """
    一次注入任务的元信息。
    全部字段都会透传到每一条 MongoDB log_entries 文档。
    """

    run_id: str
    task_id: Optional[str] = None
    source_type: str = "upload"       # "dataset" | "upload" | "api"
    system_id: Optional[str] = None
    subsystem: Optional[str] = None
    case_id: Optional[str] = None
    is_fault: Optional[bool] = None
    test_name: Optional[str] = None   # 数据集场景透传；upload/api 为 None
    round_no: Optional[int] = None    # 数据集场景透传；upload/api 为 None
    api_url: Optional[str] = None     # 接口导入时的URL
    local_path: Optional[str] = None  # 本地文件路径


@dataclass
class IngestResult:
    """ingest() 的统计输出。"""

    run_id: str
    task_id: Optional[str]
    total_lines: int            # preprocessed_log.lines 总行数
    parsed_ok: int              # parse_status == "ok"
    parse_failed: int           # parse_status == "failed"（非空但解析失败）
    skipped: int                # parse_status == "skipped"（空行，不写 MongoDB）
    inserted_count: int         # 实际写入 MongoDB 的文档数（ok + failed）
    start_time: Optional[datetime]        # 最早有效 timestamp（UTC naive）
    end_time: Optional[datetime]          # 最晚有效 timestamp（UTC naive）
    level_distribution: Dict[str, int]    # {"ERROR": N, "WARN": M, ...}


# =============================================================================
# 时间戳解析
# =============================================================================

# syslog 月日时间捕获（无年份）：Mar 15 09:14:27 / Jan  5 12:00:00
_RE_SYSLOG_TS = re.compile(
    r"^(?P<mon>[A-Za-z]{3})\s{1,2}(?P<day>\d{1,2})\s+(?P<time>\d{2}:\d{2}:\d{2})"
)


def _to_utc_naive(dt: datetime) -> datetime:
    """将 aware datetime 转为 UTC naive datetime；naive datetime 直接返回（视为 UTC）。"""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_timestamp(ts_str: str) -> Tuple[Optional[datetime], Optional[str]]:
    """
    解析时间戳字符串，返回 (UTC naive datetime, error_str)。
    失败时返回 (None, 原因字符串)。

    解析顺序
    --------
    1. datetime.fromisoformat（Python 3.11 支持所有 ISO 8601 变体）
       覆盖：YYYY-MM-DDTHH:MM:SS[.f][Z|±HH:MM]
             YYYY-MM-DD HH:MM:SS[.f]（空格代替 T）
    2. syslog 格式（无年份：Mar 15 09:14:27）
       补当前年；若结果超出当前时间 > 1 天则退为上一年。

    所有输出均为 UTC naive datetime（无 tzinfo）。
    """
    ts_str = ts_str.strip()

    # ── 方案 1：fromisoformat ─────────────────────────────────────────────────
    try:
        # Python < 3.11 的 fromisoformat 不识别 "Z"，统一替换为 "+00:00"
        normalized = ts_str.replace("Z", "+00:00")
        return _to_utc_naive(datetime.fromisoformat(normalized)), None
    except ValueError:
        pass

    # ── 方案 2：syslog（无年份） ──────────────────────────────────────────────
    m = _RE_SYSLOG_TS.match(ts_str)
    if m:
        now_utc = datetime.now(tz=timezone.utc)
        year = now_utc.year
        day_padded = f"{int(m.group('day')):02d}"
        date_str = f"{year} {m.group('mon')} {day_padded} {m.group('time')}"
        try:
            dt = datetime.strptime(date_str, "%Y %b %d %H:%M:%S")
            # 若解析结果超出当前 UTC 时间 > 1 天，说明月份在本年还未到，应为上一年
            if dt.replace(tzinfo=timezone.utc) > now_utc + timedelta(days=1):
                dt = dt.replace(year=year - 1)
            return dt, None
        except ValueError as exc:
            return None, f"syslog_strptime_error: {exc}"

    return None, f"unrecognized_timestamp_format: {ts_str!r}"


# =============================================================================
# 日志行解析正则（按优先级排列）
# =============================================================================

# ── Tier 1 全结构化（OpenStack / server 风格，含 host + pid + module）────────

# ISO-T 变体：2024-03-15T14:22:31.456 hostname 12345 ERROR nova.compute [req] msg
_RE_T1_ISO_T = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)"
    r"\s+(?P<host>[\w.-]+)"
    r"\s+(?P<pid>\d+)"
    r"\s+(?P<level>[A-Z]+)"
    r"\s+(?P<module>[\w./-]+)"
    r"(?:\s+\[(?P<reqinfo>[^\]]*)\])?"
    r"\s+(?P<msg>.+)$"
)

# 空格变体：2024-03-15 14:22:31.456 hostname 12345 ERROR nova.compute [req] msg
_RE_T1_SPACE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)"
    r"\s+(?P<host>[\w.-]+)"
    r"\s+(?P<pid>\d+)"
    r"\s+(?P<level>[A-Z]+)"
    r"\s+(?P<module>[\w./-]+)"
    r"(?:\s+\[(?P<reqinfo>[^\]]*)\])?"
    r"\s+(?P<msg>.+)$"
)

# OpenStack 无 Hostname 格式（Tier 1b）：
#   ts PID LEVEL module [reqinfo] message
#   示例：2018-09-29 16:12:38.043 19513 DEBUG glance.api.middleware.version_negotiation [-] msg
#   与上面两条 Tier 1 的区别：空格分隔时间戳、无 hostname、PID 紧跟在时间戳后
_RE_T1_OS_NHOST = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)"
    r"\s+(?P<pid>\d+)"
    r"\s+(?P<level>[A-Z]+)"
    r"\s+(?P<module>[\w./_-]+)"
    r"(?:\s+\[(?P<reqinfo>[^\]]*)\])?"
    r"\s+(?P<msg>.+)$"
)

# ── Tier 2 部分解析（含 level，不含 host/pid）────────────────────────────────

# Java-style：2024-03-15 14:22:31.456 ERROR [thread-name] --- message
_RE_T2_JAVA = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)"
    r"\s+(?P<level>DEBUG|INFO|NOTICE|WARNING|WARN|ERROR|CRITICAL|FATAL|ALERT)"
    r"(?:\s+\[(?P<module>[^\]]*)\])?"
    r"(?:\s+-+)?"
    r"\s*(?P<msg>.*)$"
)

# Simple：2024-03-15 14:22:31 ERROR some message
_RE_T2_SIMPLE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)"
    r"\s+(?P<level>DEBUG|INFO|NOTICE|WARNING|WARN|ERROR|CRITICAL|FATAL|ALERT)"
    r"\s+(?P<msg>.+)$"
)

# ── Syslog（含 host + process/pid，独立处理时间戳）────────────────────────────

# Mar 15 09:14:27 prod-server kernel: Out of memory...
# Jan  5 12:00:00 hostname app[1234]: message
_RE_T2_SYSLOG = re.compile(
    r"^(?P<ts>[A-Za-z]{3}\s{1,2}\d{1,2}\s+\d{2}:\d{2}:\d{2})"
    r"\s+(?P<host>[\w.-]+)"
    r"\s+(?P<proc>[\w.-]+?)(?:\[(?P<pid>\d+)\])?:"
    r"\s*(?P<msg>.*)$"
)

# ── Tier 3 仅时间戳（最后防线，message = 整行）───────────────────────────────

_RE_T3_ISO_TS = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)"
)
_RE_T3_SYSLOG_TS = re.compile(
    r"^(?P<ts>[A-Za-z]{3}\s{1,2}\d{1,2}\s+\d{2}:\d{2}:\d{2})"
)


def _parse_reqinfo(reqinfo: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """将 '[req-xxx user-xxx proj-xxx]' 格式的 reqinfo 分解为三元组。"""
    parts = reqinfo.split() if reqinfo else []
    return (
        parts[0] if len(parts) > 0 else None,
        parts[1] if len(parts) > 1 else None,
        parts[2] if len(parts) > 2 else None,
    )


def _try_parse_line(raw: str) -> Tuple[Optional[dict], str]:
    """
    尝试按优先级解析一行日志。

    返回 (parsed_fields_dict | None, parse_error_str)。
    parsed_fields_dict 中不含 run_meta 字段，也不含 raw_line / line_no / ingested_at。

    字段说明
    --------
    parse_status  "ok"     : 完整解析（timestamp + level + message 均提取）
                  "failed" : 提取到时间戳但其余字段不完整，或完全无法解析
    """
    # ── Tier 1：全结构化 ──────────────────────────────────────────────────────
    for regex in (_RE_T1_ISO_T, _RE_T1_SPACE):
        m = regex.match(raw)
        if not m:
            continue
        ts, ts_err = _parse_timestamp(m.group("ts"))
        if ts is None:
            continue
        req_id, user_id, proj_id = _parse_reqinfo(m.group("reqinfo") or "")
        return {
            "timestamp": ts,
            "level": m.group("level"),
            "module": m.group("module"),
            "hostname": m.group("host"),
            "process_id": int(m.group("pid")),
            "request_id": req_id,
            "user_id": user_id,
            "project_id": proj_id,
            "message": (m.group("msg") or "").strip(),
            "parse_status": "ok",
            "parse_error": None,
        }, ""

    # ── Tier 1b：OpenStack 无 Hostname（ts PID LEVEL module [reqinfo] msg） ────
    m = _RE_T1_OS_NHOST.match(raw)
    if m:
        ts, ts_err = _parse_timestamp(m.group("ts"))
        if ts is not None:
            req_id, user_id, proj_id = _parse_reqinfo(m.group("reqinfo") or "")
            # reqinfo="-" 是 OpenStack 的"无上下文"占位符，语义上等同于 None
            if req_id == "-":
                req_id = None
            return {
                "timestamp": ts,
                "level": m.group("level"),
                "module": m.group("module"),
                "hostname": None,
                "process_id": int(m.group("pid")),
                "request_id": req_id,
                "user_id": user_id,
                "project_id": proj_id,
                "message": (m.group("msg") or "").strip(),
                "parse_status": "ok",
                "parse_error": None,
            }, ""

    # ── Tier 2a：Java-style ───────────────────────────────────────────────────
    m = _RE_T2_JAVA.match(raw)
    if m:
        ts, ts_err = _parse_timestamp(m.group("ts"))
        if ts is not None:
            return {
                "timestamp": ts,
                "level": m.group("level"),
                "module": (m.group("module") or "").strip() or None,
                "hostname": None,
                "process_id": None,
                "message": (m.group("msg") or "").strip(),
                "parse_status": "ok",
                "parse_error": None,
            }, ""

    # ── Tier 2b：Simple ───────────────────────────────────────────────────────
    m = _RE_T2_SIMPLE.match(raw)
    if m:
        ts, ts_err = _parse_timestamp(m.group("ts"))
        if ts is not None:
            return {
                "timestamp": ts,
                "level": m.group("level"),
                "module": None,
                "hostname": None,
                "process_id": None,
                "message": (m.group("msg") or "").strip(),
                "parse_status": "ok",
                "parse_error": None,
            }, ""

    # ── Tier 2c：Syslog（含 host） ────────────────────────────────────────────
    m = _RE_T2_SYSLOG.match(raw)
    if m:
        ts, ts_err = _parse_timestamp(m.group("ts"))
        if ts is not None:
            pid_str = m.group("pid")
            return {
                "timestamp": ts,
                "level": None,
                "module": m.group("proc"),
                "hostname": m.group("host"),
                "process_id": int(pid_str) if pid_str else None,
                "message": (m.group("msg") or "").strip(),
                "parse_status": "ok",
                "parse_error": None,
            }, ""

    # ── Tier 3：仅提取时间戳 ──────────────────────────────────────────────────
    for ts_regex in (_RE_T3_ISO_TS, _RE_T3_SYSLOG_TS):
        m = ts_regex.match(raw)
        if not m:
            continue
        ts, ts_err = _parse_timestamp(m.group("ts"))
        if ts is not None:
            return {
                "timestamp": ts,
                "level": None,
                "module": None,
                "hostname": None,
                "process_id": None,
                "message": raw.strip(),
                "parse_status": "failed",
                "parse_error": "tier3_ts_only: no level/module extracted",
            }, "tier3_ts_only"

    # ── 完全失败 ──────────────────────────────────────────────────────────────
    return None, "no_pattern_matched"


# =============================================================================
# LogIngestService
# =============================================================================


class LogIngestService:
    """
    无状态的日志注入服务。

    ingest() 是唯一公开方法，不持有任何连接，
    所有 I/O 资源由调用方传入。
    """

    def ingest(
        self,
        preprocessed_log: PreprocessedLog,
        run_meta: RunMeta,
        mongo_db: Database,
        batch_size: int = _BATCH_SIZE,
    ) -> IngestResult:
        """
        主入口：解析 PreprocessedLog.lines，写入 MongoDB log_entries。

        参数
        ----
        preprocessed_log : PreprocessingService.preprocess() 的输出
        run_meta         : 本次注入任务的元信息
        mongo_db         : pymongo Database 对象（由调用方管理连接生命周期）
        batch_size       : 每次批量写入的文档数

        返回
        ----
        IngestResult
        """
        try:
            # 检查 MongoDB 连接
            mongo_db.command('ping')
        except Exception as exc:
            logger.error(
                "[ingest] MongoDB连接异常: %s",
                exc,
            )
            raise Exception("MongoDB连接异常，日志存储失败")
        
        col = mongo_db[_COLLECTION]
        now_utc = datetime.now(tz=timezone.utc).replace(tzinfo=None)

        # run_meta 中透传到每条文档的公共字段（不可变，提前构建）
        meta_base: dict = {
            "run_id": run_meta.run_id,
            "task_id": run_meta.task_id,
            "source_type": run_meta.source_type,
            "system_id": run_meta.system_id,
            "subsystem": run_meta.subsystem,
            "case_id": run_meta.case_id,
            "is_fault": run_meta.is_fault,
            "test_name": run_meta.test_name,
            "round_no": run_meta.round_no,
            "file_path": preprocessed_log.filename,
            "api_url": run_meta.api_url,
            "local_path": run_meta.local_path,
        }

        # 统计变量
        parsed_ok = 0
        parse_failed = 0
        skipped = 0
        inserted_count = 0
        start_time: Optional[datetime] = None
        end_time: Optional[datetime] = None
        level_dist: Dict[str, int] = {}

        buffer: List[dict] = []

        def flush() -> None:
            nonlocal inserted_count
            if not buffer:
                return
            try:
                # 检查存储空间是否充足（跨平台兼容）
                import os
                if os.name == 'posix':
                    # Unix/Linux 系统
                    stat = os.statvfs('/')
                    free_space = stat.f_bavail * stat.f_frsize
                    if free_space < 1024 * 1024 * 100:  # 至少需要100MB空闲空间
                        raise Exception("存储空间不足")
                elif os.name == 'nt':
                    # Windows 系统
                    import ctypes
                    free_bytes = ctypes.c_ulonglong(0)
                    total_bytes = ctypes.c_ulonglong(0)
                    ctypes.windll.kernel32.GetDiskFreeSpaceExW(ctypes.c_wchar_p('C:'), ctypes.byref(free_bytes), ctypes.byref(total_bytes), None)
                    if free_bytes.value < 1024 * 1024 * 100:  # 至少需要100MB空闲空间
                        raise Exception("存储空间不足")
                
                col.insert_many(buffer, ordered=False)
                inserted_count += len(buffer)
            except Exception as exc:
                if "存储空间不足" in str(exc):
                    logger.error(
                        "[ingest] storage space不足 run_id=%s: %s",
                        run_meta.run_id,
                        exc,
                    )
                    raise Exception("存储目录空间不足，无法完成日志上传，请清理空间后重试")
                elif "connection" in str(exc).lower() or "timeout" in str(exc).lower():
                    logger.error(
                        "[ingest] MongoDB连接异常 run_id=%s: %s",
                        run_meta.run_id,
                        exc,
                    )
                    raise Exception("MongoDB连接异常，日志存储失败")
                else:
                    # 局部失败不中断整体流程，记录警告
                    logger.warning(
                        "[ingest] insert_many partial failure run_id=%s: %s",
                        run_meta.run_id,
                        exc,
                    )
            buffer.clear()

        # ── 逐行处理 ─────────────────────────────────────────────────────────
        # 直接消费 preprocessed_log.lines，禁止再 split
        for line_no, raw_line in enumerate(preprocessed_log.lines, start=1):

            # ── 空行 skip ────────────────────────────────────────────────────
            if not raw_line.strip():
                skipped += 1
                continue

            # ── 尝试解析 ─────────────────────────────────────────────────────
            parsed_fields, parse_error = _try_parse_line(raw_line)

            if parsed_fields is not None:
                # Tier 1 / 2 / 3（已有 parse_status 在 parsed_fields 中）
                doc = {
                    **meta_base,
                    **parsed_fields,
                    "raw_line": raw_line,
                    "line_no": line_no,
                    "ingested_at": now_utc,
                    "is_error_like": _is_error_like(
                        parsed_fields.get("level"), raw_line
                    ),
                }
                if parsed_fields["parse_status"] == "ok":
                    parsed_ok += 1
                else:
                    # Tier 3：timestamp only，parse_status 已是 "failed"
                    parse_failed += 1
            else:
                # 完全失败：保留 raw_line，其余字段为 None
                doc = {
                    **meta_base,
                    "timestamp": None,
                    "level": None,
                    "module": None,
                    "hostname": None,
                    "process_id": None,
                    "message": None,
                    "raw_line": raw_line,
                    "line_no": line_no,
                    "parse_status": "failed",
                    "parse_error": parse_error or "no_pattern_matched",
                    "ingested_at": now_utc,
                    "is_error_like": _is_error_like(None, raw_line),
                }
                parse_failed += 1

            # ── 更新时间范围和级别分布 ────────────────────────────────────────
            ts: Optional[datetime] = doc.get("timestamp")
            if ts is not None:
                start_time = ts if start_time is None else min(start_time, ts)
                end_time = ts if end_time is None else max(end_time, ts)

            lvl: Optional[str] = doc.get("level")
            if lvl:
                level_dist[lvl] = level_dist.get(lvl, 0) + 1

            buffer.append(doc)

            if len(buffer) >= batch_size:
                flush()

        # 最后一批
        flush()

        total_lines = len(preprocessed_log.lines)
        logger.info(
            "[ingest] run_id=%s total=%d ok=%d failed=%d skipped=%d inserted=%d",
            run_meta.run_id,
            total_lines,
            parsed_ok,
            parse_failed,
            skipped,
            inserted_count,
        )

        return IngestResult(
            run_id=run_meta.run_id,
            task_id=run_meta.task_id,
            total_lines=total_lines,
            parsed_ok=parsed_ok,
            parse_failed=parse_failed,
            skipped=skipped,
            inserted_count=inserted_count,
            start_time=start_time,
            end_time=end_time,
            level_distribution=level_dist,
        )


# =============================================================================
# 模块级单例
# =============================================================================

log_ingest_service = LogIngestService()
