"""
log_analysis_service.py

职责：
    对已完成 ingest + build_windows 的日志数据进行故障评分，
    输出 ScoreResult（has_fault / confidence / summary / evidence / score_breakdown）。

约束：
    - 纯计算，不访问 MongoDB / MySQL / 任何 I/O
    - error_windows 由调用方从 MongoDB 查询后传入（只含 stats / key_events 字段）
    - evidence 最多返回 5 条，按 score 降序
    - score_breakdown 结构固定：level_score / keyword_score / stack_trace_score / window_score / details
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from app.schemas.log_analysis import EvidenceItem
from app.services.log_ingest_service import IngestResult

# ── 关键词分类（case-insensitive，每类命中一次计分）────────────────────────────

_STRONG_KWS: List[str] = [
    "exception", "traceback", "panic", "segmentation fault",
    "fatal", "assertion failed",
    # 中文高危关键词（日志多为中文，避免命中不到导致评分恒 0）
    "异常", "堆栈", "崩溃", "宕机", "致命", "段错误", "内核错误",
]
_MEDIUM_KWS: List[str] = [
    "failed", "refused", "unavailable", "crash", "oom",
    "out of memory", "disk full",
    # 中文中危关键词
    "失败", "错误", "无法连接", "连接被拒", "拒绝", "内存溢出",
    "磁盘已满", "不可用", "超载", "中断",
]
_WEAK_KWS: List[str] = [
    "timeout", "retry", "latency", "slow", "throttle", "503", "429",
    # 中文低危关键词
    "超时", "重试", "延迟", "缓慢", "告警", "警告", "丢包",
]

# ── 级别分组 ──────────────────────────────────────────────────────────────────

_CRITICAL_LEVELS = frozenset({"CRITICAL", "FATAL", "ALERT", "EMERGENCY"})
_WARN_LEVELS = frozenset({"WARNING", "WARN"})

# ── 判断阈值 ──────────────────────────────────────────────────────────────────

_THRESHOLD_FAULT = 6.0


# =============================================================================
# 输出数据结构
# =============================================================================

@dataclass
class ScoreResult:
    """FaultScorer.score() 的完整输出。"""
    has_fault: bool
    fault_score: float
    confidence: float
    summary: str
    evidence: List[EvidenceItem]    # 最多 5 条，按 score 降序
    next_action: str                # "fault_location" | "warning_forecast"
    score_breakdown: Dict[str, Any] # 固定结构，见 FaultScorer.score()


# =============================================================================
# FaultScorer
# =============================================================================

class FaultScorer:
    """
    多证据故障评分器。

    score() 是唯一公开方法：
        ingest_result  — log_ingest_service.ingest() 的输出
        raw_text       — 原始日志文本（用于关键词扫描）
        stack_traces   — 已提取的异常堆栈列表（由路由层从原文提取）
        error_windows  — MongoDB log_windows 中 stats.error_events > 0 的文档列表
                         调用方只需传 {"stats": ..., "key_events": ...} 字段，不传 text

    返回 ScoreResult。
    """

    def score(
        self,
        ingest_result: IngestResult,
        raw_text: str,
        stack_traces: List[str],
        error_windows: List[dict],
    ) -> ScoreResult:

        # ── 各证据独立计分 ─────────────────────────────────────────────────────
        level_score, level_ev, level_det = self._score_levels(
            ingest_result.level_distribution
        )
        kw_score, kw_ev, kw_det = self._score_keywords(raw_text)
        stack_score, stack_ev = self._score_stack_traces(stack_traces)
        win_score, win_ev, win_det = self._score_windows(error_windows)

        # ── 汇总 ──────────────────────────────────────────────────────────────
        total = level_score + kw_score + stack_score + win_score
        fault_score = round(min(total, 10.0), 2)
        has_fault = fault_score >= _THRESHOLD_FAULT
        # 置信度：在「评分/12」之外叠加「级别占比基线」，使得即便关键词没命中，
        # 只要日志里有 ERROR/CRITICAL/WARNING 行也能给出非 0 置信度（修复恒为 0 的问题）。
        total_lines = max(getattr(ingest_result, "total_lines", 0) or 0, 1)
        err_ratio = (level_det["critical_n"] + level_det["error_n"]) / total_lines
        warn_ratio = level_det["warning_n"] / total_lines
        baseline = min(err_ratio * 0.6 + warn_ratio * 0.2, 1.0)
        confidence = round(min(max(fault_score / 10.0, baseline), 1.0), 3)
        next_action: str = "fault_location" if has_fault else "warning_forecast"

        # top-5 证据（按 score 降序）
        all_evidence = level_ev + kw_ev + stack_ev + win_ev
        top_evidence = sorted(all_evidence, key=lambda e: e.score, reverse=True)[:5]

        # summary 规则拼装
        summary = self._build_summary(
            line_count=ingest_result.total_lines,
            has_fault=has_fault,
            fault_score=fault_score,
            confidence=confidence,
            level_det=level_det,
            kw_det=kw_det,
            n_traces=len(stack_traces),
            win_det=win_det,
        )

        score_breakdown: Dict[str, Any] = {
            "level_score":       round(level_score, 2),
            "keyword_score":     round(kw_score, 2),
            "stack_trace_score": round(stack_score, 2),
            "window_score":      round(win_score, 2),
            "details": {
                "critical_n":        level_det["critical_n"],
                "error_n":           level_det["error_n"],
                "warning_n":         level_det["warning_n"],
                "strong_hits":       kw_det["strong_hits"],
                "medium_hits":       kw_det["medium_hits"],
                "weak_hits":         kw_det["weak_hits"],
                "n_traces":          len(stack_traces),
                "error_window_count": win_det["error_window_count"],
                "has_key_events":    win_det["has_key_events"],
            },
        }

        return ScoreResult(
            has_fault=has_fault,
            fault_score=fault_score,
            confidence=confidence,
            summary=summary,
            evidence=top_evidence,
            next_action=next_action,
            score_breakdown=score_breakdown,
        )

    # ── 证据1：级别分布 ────────────────────────────────────────────────────────

    def _score_levels(
        self,
        level_dist: Dict[str, int],
    ) -> Tuple[float, List[EvidenceItem], dict]:
        critical_n = sum(level_dist.get(k, 0) for k in _CRITICAL_LEVELS)
        error_n = level_dist.get("ERROR", 0)
        warning_n = sum(level_dist.get(k, 0) for k in _WARN_LEVELS)

        c_score = min(critical_n * 3.0, 6.0)
        e_score = min(error_n * 2.0, 6.0)
        w_score = min(warning_n * 0.5, 2.0)
        total = c_score + e_score + w_score

        evidence: List[EvidenceItem] = []
        if c_score > 0:
            evidence.append(EvidenceItem(
                source="level_stats",
                detail=f"发现 {critical_n} 条 CRITICAL/FATAL 级日志",
                score=c_score,
            ))
        if e_score > 0:
            evidence.append(EvidenceItem(
                source="level_stats",
                detail=f"发现 {error_n} 条 ERROR 级日志",
                score=e_score,
            ))
        if w_score > 0:
            evidence.append(EvidenceItem(
                source="level_stats",
                detail=f"发现 {warning_n} 条 WARNING 级日志",
                score=w_score,
            ))

        details = {
            "critical_n": critical_n,
            "error_n": error_n,
            "warning_n": warning_n,
        }
        return total, evidence, details

    # ── 证据2：关键词 ──────────────────────────────────────────────────────────

    def _score_keywords(
        self,
        raw_text: str,
    ) -> Tuple[float, List[EvidenceItem], dict]:
        text_lower = raw_text.lower()

        strong_hits = [kw for kw in _STRONG_KWS if kw in text_lower]
        medium_hits = [kw for kw in _MEDIUM_KWS if kw in text_lower]
        weak_hits = [kw for kw in _WEAK_KWS if kw in text_lower]

        s_score = min(len(strong_hits) * 2.0, 6.0)
        m_score = min(len(medium_hits) * 1.0, 4.0)
        w_score = min(len(weak_hits) * 0.5, 2.0)
        total = s_score + m_score + w_score

        evidence: List[EvidenceItem] = []
        if s_score > 0:
            evidence.append(EvidenceItem(
                source="keyword",
                detail=f"命中高危关键词：{', '.join(strong_hits[:4])}",
                score=s_score,
            ))
        if m_score > 0:
            evidence.append(EvidenceItem(
                source="keyword",
                detail=f"命中中危关键词：{', '.join(medium_hits[:4])}",
                score=m_score,
            ))
        if w_score > 0:
            evidence.append(EvidenceItem(
                source="keyword",
                detail=f"命中低危关键词：{', '.join(weak_hits[:4])}",
                score=w_score,
            ))

        details = {
            "strong_hits": strong_hits,
            "medium_hits": medium_hits,
            "weak_hits": weak_hits,
        }
        return total, evidence, details

    # ── 证据3：Stack Trace ─────────────────────────────────────────────────────

    def _score_stack_traces(
        self,
        stack_traces: List[str],
    ) -> Tuple[float, List[EvidenceItem]]:
        n = len(stack_traces)
        if n >= 2:
            score = 5.0
        elif n == 1:
            score = 3.0
        else:
            score = 0.0

        evidence: List[EvidenceItem] = []
        if score > 0:
            evidence.append(EvidenceItem(
                source="stack_trace",
                detail=f"检测到 {n} 处异常堆栈（Traceback/Exception）",
                score=score,
            ))
        return score, evidence

    # ── 证据4：窗口聚集 ────────────────────────────────────────────────────────

    def _score_windows(
        self,
        error_windows: List[dict],
    ) -> Tuple[float, List[EvidenceItem], dict]:
        n = len(error_windows)
        score = 0.0
        if n >= 1:
            score += 1.0
        if n >= 2:
            score += 1.0  # 累加，不是替换
        has_key_events = any(bool(w.get("key_events")) for w in error_windows)
        if has_key_events:
            score += 1.0

        evidence: List[EvidenceItem] = []
        if score > 0:
            kw_note = "，含关键事件" if has_key_events else ""
            evidence.append(EvidenceItem(
                source="window",
                detail=f"发现 {n} 个错误聚集窗口{kw_note}",
                score=score,
            ))

        details = {
            "error_window_count": n,
            "has_key_events": has_key_events,
        }
        return score, evidence, details

    # ── summary 规则拼装（三档） ───────────────────────────────────────────────

    def _build_summary(
        self,
        line_count: int,
        has_fault: bool,
        fault_score: float,
        confidence: float,
        level_det: dict,
        kw_det: dict,
        n_traces: int,
        win_det: dict,
    ) -> str:
        score_str = f"{fault_score:.1f}"
        pct = int(confidence * 100)
        critical_n = level_det["critical_n"]
        error_n = level_det["error_n"]
        warning_n = level_det["warning_n"]
        error_window_count = win_det["error_window_count"]

        if has_fault:
            # 档1：确认故障（score >= 6）
            header = (
                f"共 {line_count} 行日志，"
                f"故障评分 {score_str}/10（置信度 {pct}%）"
            )
            details: List[str] = []
            if critical_n:
                details.append(f"{critical_n} 条 CRITICAL/FATAL")
            if error_n:
                details.append(f"{error_n} 条 ERROR")
            if n_traces:
                details.append(f"{n_traces} 处异常堆栈")
            if error_window_count:
                details.append(f"{error_window_count} 个错误聚集窗口")
            if details:
                return f"{header}。检测到：{'、'.join(details)}。"
            return f"{header}。"

        elif fault_score >= 3.0:
            # 档2：有风险信号（3 <= score < 6）
            kws = (kw_det["strong_hits"] + kw_det["medium_hits"])[:3]
            signals: List[str] = []
            if warning_n:
                signals.append(f"{warning_n} 条 WARN")
            if kws:
                signals.append(f"命中关键词 [{', '.join(kws)}]")
            signal_text = "，".join(signals) if signals else "存在轻微异常"
            return (
                f"共 {line_count} 行日志，"
                f"未确认故障（评分 {score_str}/10），"
                f"但存在风险信号：{signal_text}。"
            )

        else:
            # 档3：无明显异常（score < 3）
            return (
                f"共 {line_count} 行日志，"
                f"未发现明显故障信号（评分 {score_str}/10）。"
            )


# =============================================================================
# 模块级单例
# =============================================================================

fault_scorer = FaultScorer()
