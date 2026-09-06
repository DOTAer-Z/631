from __future__ import annotations

from app.services.unstructured.segmenter import (
    LineSegment,
    split_line_segments,
    split_segments,
    split_text,
)


# ── split_text 语义(移植自 test_build_segments.py 的三个断言) ──────────────────

def test_split_text_keeps_complete_heading_blocks() -> None:
    text = """## 现象
飞控计算机连续三次未收到心跳响应，通信链路进入降级状态。

## 原因
接收队列被异常报文占满，心跳消息无法及时处理。

## 处理
清理队列并调整通信任务优先级后恢复正常。
"""
    blocks = split_text(text, min_chars=20, max_chars=200)
    assert len(blocks) == 3
    assert blocks[0].startswith("## 现象")
    assert "接收队列" in blocks[1]
    assert "恢复正常" in blocks[2]


def test_split_text_drops_empty_template_sections_and_attaches_orphan_heading() -> None:
    text = """### Flight Log
_No response_

## Root cause

The receive queue remained full and blocked heartbeat processing.
"""
    blocks = split_text(text, min_chars=20, max_chars=200)
    assert blocks == [
        "## Root cause\nThe receive queue remained full and blocked heartbeat processing."
    ]


def test_split_text_merges_short_numbered_fragments_into_neighboring_text() -> None:
    text = """1 0
C (3) = 1 T

The software voting mechanism compares redundant processor outputs before control is applied.
"""
    blocks = split_text(text, min_chars=40, max_chars=200)
    assert blocks == [
        "1 0\nC (3) = 1 T\n\n"
        "The software voting mechanism compares redundant processor outputs before control is applied."
    ]


# ── split_segments (文本→Segment) ──────────────────────────────────────────────

SAMPLE_REPORT = """# 嵌入式机载软件维护/故障报告

## 基本信息
报告编号: R-0001，机型 X-100，发生日期 2026-01-01，责任单位航电部。

## 故障现象与现场证据
现象: 系统在起飞阶段连续三次自动重启，机组观察到主控黑屏约两秒。

## 分析与根本原因
根本原因: 调度任务因资源争用进入死锁，喂狗任务被阻塞导致看门狗复位。

## 处理与恢复结果
结果: 修复调度优先级与资源锁顺序后连续运行 72 小时无复现，恢复正常。
"""


def test_split_segments_yields_four_sections_and_drops_h1() -> None:
    segments = split_segments(SAMPLE_REPORT)
    assert [seg.title for seg in segments] == [
        "基本信息",
        "故障现象与现场证据",
        "分析与根本原因",
        "处理与恢复结果",
    ]
    # H1 前言被丢弃。
    assert all("嵌入式机载软件维护" not in line for seg in segments for line in seg.lines)


# ── split_line_segments (行级映射,切片引擎实际使用) ────────────────────────────

def test_split_line_segments_maps_line_ids_and_drops_prelude() -> None:
    lines = [
        (10, "# H1 前言"),
        (11, "## 基本信息"),
        (12, "报告编号: R-1"),
        (13, "## 处理与恢复结果"),
        (14, "结果: 正常"),
    ]
    segments = split_line_segments(lines)
    assert [seg.title for seg in segments] == ["基本信息", "处理与恢复结果"]
    # 首个 `## ` 之前的行(id=10)被丢弃;标题行归入本段。
    assert segments[0].line_ids == [11, 12]
    assert segments[1].line_ids == [13, 14]
    assert isinstance(segments[0], LineSegment)


def test_split_line_segments_drops_empty_template_section() -> None:
    lines = [
        (1, "## 空段"),
        (2, "_No response_"),
        (3, "## 有内容"),
        (4, "真实正文"),
    ]
    segments = split_line_segments(lines)
    assert [seg.title for seg in segments] == ["有内容"]
    assert segments[0].line_ids == [3, 4]


def test_split_line_segments_degrades_to_whole_file_without_headings() -> None:
    # 无任何 `## ` 的纯散文(如 PDF 抽取文本)-> 整篇 1 段,保证可切片。
    lines = [(1, "这是一段没有二级标题的散文。"), (2, "第二行继续。")]
    segments = split_line_segments(lines)
    assert len(segments) == 1
    assert segments[0].line_ids == [1, 2]
    assert segments[0].title  # 非空标题(取首行摘要)


def test_split_line_segments_empty_input() -> None:
    assert split_line_segments([]) == []
