"""逐文件判定 semi_structured / unstructured。

规则:文件为「非结构化报告」当且仅当
  扩展名 ∈ {.pdf, .md}
  或 (扩展名 .txt 且正文含至少一行以 `## ` 开头的二级标题)。
其余为日志(半结构化,按时间窗口切片)。
"""

from __future__ import annotations

from pathlib import PurePosixPath


UNSTRUCTURED_ALWAYS = {".pdf", ".md"}
CONDITIONAL_TEXT = {".txt"}


def _has_level2_heading(head_text: str) -> bool:
    for line in head_text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            return True
    return False


def classify_file(name: str, head_text: str) -> str:
    """返回 'unstructured' 或 'semi_structured'。head_text 可只含文件开头若干行。"""

    suffix = PurePosixPath(name).suffix.lower()
    if suffix in UNSTRUCTURED_ALWAYS:
        return "unstructured"
    if suffix in CONDITIONAL_TEXT and _has_level2_heading(head_text):
        return "unstructured"
    return "semi_structured"
