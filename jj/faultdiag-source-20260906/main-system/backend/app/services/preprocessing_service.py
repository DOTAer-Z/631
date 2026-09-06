"""
preprocessing_service.py

职责（唯一）：
    将原始日志（bytes 或 str）转换为结构化的 PreprocessedLog 数据对象。

包含：
    - 编码自动识别与解码（UTF-8-SIG → UTF-8 → GBK → latin-1 → 宽容替换）
    - BOM 去除（bytes 层 + str 层双重保险）
    - NUL 字符清除
    - 换行符统一（\\r\\n / \\r → \\n）
    - splitlines() 产出 lines: List[str]（整个预处理链路唯一 split 点）
    - 可选截断（max_lines 参数）
    - 日志格式检测（plain / structured / syslog）

不允许：
    - 写数据库
    - 调用任何其他 service
    - 做 log 行内容解析（那是 LogIngestService 的职责）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional


# ---------------------------------------------------------------------------
# 输出数据结构
# ---------------------------------------------------------------------------


@dataclass
class PreprocessedLog:
    """preprocessing_service 的唯一输出类型。

    lines 是整个 pipeline 的唯一 split 结果，
    LogIngestService 必须直接消费此字段，不允许再自行 split。
    """

    # 内容
    text: str               # "\n".join(lines)，与 lines 严格一致
    lines: List[str]        # splitlines() 结果，唯一 split 来源

    # 元信息
    encoding: str           # 实际使用的解码编码
    line_count: int         # len(lines)（截断后）
    char_count: int         # len(text)（截断后）

    # 格式
    detected_format: str    # "plain" | "structured" | "syslog"

    # 截断
    truncated: bool         # 是否触发截断
    truncate_at: Optional[int]  # 截断行数上限，truncated=False 时为 None

    # 透传字段
    source_type: str        # 来源标记，透传自调用方
    filename: Optional[str] # 文件名，透传自调用方


# ---------------------------------------------------------------------------
# 模块级常量
# ---------------------------------------------------------------------------

# 解码尝试顺序：
#   utf-8-sig  —— 自动处理 UTF-8 BOM，是 utf-8 的超集，优先尝试
#   utf-8      —— 标准 UTF-8，无 BOM 场景兜底
#   gbk        —— 中文 Windows 日志常见编码
#   latin-1    —— 单字节兜底，decode 永远不会抛异常
_DECODE_ORDER: tuple[str, ...] = ("utf-8-sig", "utf-8", "gbk", "latin-1")

# 格式检测：取前几行做样本
_SAMPLE_SIZE = 5
# 样本中至少有几行命中，才判定为该格式
_MATCH_THRESHOLD = 3

# syslog 时间格式：Jan  5 12:00:00 / Mar 15 09:14:27（必须在行首）
_RE_SYSLOG_TS = re.compile(
    r"^[A-Za-z]{3}\s{1,2}\d{1,2}\s+\d{2}:\d{2}:\d{2}\b"
)

# structured 时间格式：ISO8601（2024-03-15T14:22:31）或空格分隔（2024-03-15 14:22:31）
_RE_STRUCTURED_TS = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
)


# ---------------------------------------------------------------------------
# PreprocessingService
# ---------------------------------------------------------------------------


class PreprocessingService:
    """
    无状态的日志文本预处理器。

    所有方法均为静态或纯函数，可安全地多线程并发调用。
    """

    def preprocess(
        self,
        raw_log: bytes | str,
        source_type: str = "upload",
        filename: Optional[str] = None,
        max_lines: Optional[int] = None,
    ) -> PreprocessedLog:
        """
        主入口：将原始输入转换为 PreprocessedLog。

        参数
        ----
        raw_log     : 原始日志内容，bytes 或已解码的 str
        source_type : 来源标记（"dataset" | "upload" | "api"），透传到输出
        filename    : 来源文件名（可选），透传到输出
        max_lines   : 截断上限行数（None = 不截断）

        返回
        ----
        PreprocessedLog
        """
        # ── Step 1：解码 ────────────────────────────────────────────────────
        text, encoding = self._decode(raw_log)

        # ── Step 2：去除 BOM（str 层安全网，utf-8-sig 通常已处理） ───────────
        text = self._strip_bom(text)

        # ── Step 3：清除 NUL 字符，避免 MongoDB / 日志解析异常 ───────────────
        text = text.replace("\x00", "")

        # ── Step 4：统一换行符（\r\n 和孤立 \r 均转为 \n） ──────────────────
        text = self._normalize_newlines(text)

        # ── Step 5：splitlines()—— 整个 pipeline 唯一 split 点 ──────────────
        lines: List[str] = text.splitlines()

        # ── Step 6：可选截断 ─────────────────────────────────────────────────
        truncated = False
        truncate_at: Optional[int] = None

        if max_lines is not None and len(lines) > max_lines:
            lines = lines[:max_lines]
            truncated = True
            truncate_at = max_lines

        # ── Step 7：截断后重建 text（保证 text 与 lines 严格一致） ────────────
        text = "\n".join(lines)

        # ── Step 8：格式检测 ─────────────────────────────────────────────────
        detected_format = self._detect_format(lines)

        return PreprocessedLog(
            text=text,
            lines=lines,
            encoding=encoding,
            line_count=len(lines),
            char_count=len(text),
            detected_format=detected_format,
            truncated=truncated,
            truncate_at=truncate_at,
            source_type=source_type,
            filename=filename,
        )

    # ------------------------------------------------------------------
    # 内部静态方法
    # ------------------------------------------------------------------

    @staticmethod
    def _decode(raw: bytes | str) -> tuple[str, str]:
        """
        将 bytes 解码为 str，返回 (decoded_text, encoding_used)。

        若传入已是 str，直接返回，encoding 标记为 "str_input"。
        尝试顺序：utf-8-sig → utf-8 → gbk → latin-1 → utf-8(replace)
        """
        if isinstance(raw, str):
            return raw, "str_input"

        for enc in _DECODE_ORDER:
            try:
                return raw.decode(enc), enc
            except (UnicodeDecodeError, LookupError):
                continue

        # 终极兜底：UTF-8 宽容模式，用 \ufffd 替换无法解码的字节，永不抛异常
        return raw.decode("utf-8", errors="replace"), "utf-8(replace)"

    @staticmethod
    def _strip_bom(text: str) -> str:
        """
        移除字符串开头的 Unicode BOM（U+FEFF）。

        utf-8-sig 解码时通常已自动剥离，此步骤作为安全兜底（
        例如 str_input 场景下文本本身带有 BOM）。
        """
        if text.startswith("\ufeff"):
            return text[1:]
        return text

    @staticmethod
    def _normalize_newlines(text: str) -> str:
        """
        将 \\r\\n（Windows CRLF）和孤立 \\r（旧 Mac CR）统一替换为 \\n。

        注意替换顺序：必须先替换 \\r\\n，再替换剩余的 \\r，
        否则 \\r\\n 会被拆成 \\n\\n（双空行）。
        """
        text = text.replace("\r\n", "\n")
        text = text.replace("\r", "\n")
        return text

    @staticmethod
    def _detect_format(lines: List[str]) -> str:
        """
        根据前 _SAMPLE_SIZE 行的非空行判断日志格式。

        判断规则（优先级从高到低）：
          "syslog"     : ≥ _MATCH_THRESHOLD 行匹配 syslog 时间格式
                         示例：Mar 15 09:14:27 或 Jan  5 12:00:00
          "structured" : ≥ _MATCH_THRESHOLD 行匹配 ISO8601 / YYYY-MM-DD 时间格式
                         示例：2024-03-15T14:22:31 或 2024-03-15 14:22:31
          "plain"      : 其余情况

        只取非空行做样本，避免前几行是空行干扰结果。
        """
        # 取前 _SAMPLE_SIZE 行中的非空行
        sample = [line for line in lines[:_SAMPLE_SIZE] if line.strip()]

        if not sample:
            return "plain"

        syslog_hits = sum(
            1 for line in sample if _RE_SYSLOG_TS.match(line)
        )
        if syslog_hits >= _MATCH_THRESHOLD:
            return "syslog"

        structured_hits = sum(
            1 for line in sample if _RE_STRUCTURED_TS.match(line)
        )
        if structured_hits >= _MATCH_THRESHOLD:
            return "structured"

        return "plain"


# ---------------------------------------------------------------------------
# 模块级单例（懒加载友好，无初始化成本）
# ---------------------------------------------------------------------------

preprocessing_service = PreprocessingService()
