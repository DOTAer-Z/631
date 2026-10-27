from __future__ import annotations

from app.services.unstructured.detector import classify_file


def test_txt_with_level2_heading_is_unstructured() -> None:
    head = "# 报告标题\n## 基本信息\n报告编号: R-1\n"
    assert classify_file("R-0001.txt", head) == "unstructured"


def test_plain_log_txt_is_semi_structured() -> None:
    head = "1727592759.365 module started\n1727592760.001 tick\n"
    assert classify_file("app.txt", head) == "semi_structured"


def test_txt_without_level2_heading_is_semi_structured() -> None:
    # `# ` 一级标题不触发;`### ` 三级也不触发。
    head = "# 只有一级标题\n### 三级标题\n正文\n"
    assert classify_file("note.txt", head) == "semi_structured"


def test_md_always_unstructured() -> None:
    assert classify_file("readme.md", "任意内容") == "unstructured"


def test_pdf_always_unstructured_regardless_of_head() -> None:
    assert classify_file("report.pdf", "") == "unstructured"


def test_log_extension_is_semi_structured() -> None:
    assert classify_file("kernel.log", "## looks like heading but .log") == "semi_structured"
