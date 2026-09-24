"""非结构化数据(txt/pdf/md 维护/故障报告)支持。

结构化时间序列日志走原有时间窗口切片链路;本包提供:
- extractor: 从 txt/md/pdf 提取纯文本
- segmenter: 按 `## ` 二级标题切分语义段
- detector: 逐文件判定 structured / unstructured
"""

from app.services.unstructured.detector import classify_file
from app.services.unstructured.extractor import extract_text
from app.services.unstructured.segmenter import (
    LineSegment,
    Segment,
    split_line_segments,
    split_segments,
    split_text,
)

__all__ = [
    "classify_file",
    "extract_text",
    "LineSegment",
    "Segment",
    "split_line_segments",
    "split_segments",
    "split_text",
]
