"""非结构化报告切分。

参照 `test_build_segments.py` 的 `split_text` 语义(该模块在仓库中不存在,按测试断言原生重建):
- 以 `## ` 二级标题为段边界;首个 `## ` 之前的内容为前言,丢弃。
- 标题与其正文粘连(丢弃段内空行,单 `\n` 连接)。
- 丢弃「空模板段」:正文只有空行 / 占位符(如 `_No response_`)的段。
- 无任何 `## ` 标题时按段落(空行分隔)切分,过短(< min_chars)段落并入相邻段落(`\n\n` 连接)。
- `### ` 等更深标题不作为段边界。

`split_line_segments` 是切片引擎实际使用的行级映射:在已入库的 (line_id, content) 上按同样
的边界/丢弃规则分组,返回每段的 line_id 列表;无 `## ` 段时降级为「整篇 1 段」,保证切片不空。
"""

from __future__ import annotations

from dataclasses import dataclass, field


HEADING_PREFIX = "## "
# 模板占位符(GitHub issue 模板等):正文仅为这些即视为空段。
_PLACEHOLDER_BODY = {"", "no response", "n/a", "none", "null", "nil", "-"}


def _is_heading(line: str) -> bool:
    """恰好二级标题 `## x`;`###`+ 更深标题不算段边界。"""
    stripped = line.lstrip()
    if not stripped.startswith(HEADING_PREFIX):
        return False
    hashes = len(stripped) - len(stripped.lstrip("#"))
    return hashes == 2


def _heading_title(line: str) -> str:
    return line.lstrip().lstrip("#").strip()


def _is_placeholder(line: str) -> bool:
    """空行或模板占位符正文。"""
    t = line.strip().strip("_*").strip().lower()
    return t in _PLACEHOLDER_BODY


@dataclass
class Segment:
    """一个语义段:标题(不含 `## ` 前缀,可为空)+ 该段完整文本行。"""

    title: str
    lines: list[str] = field(default_factory=list)


@dataclass
class LineSegment:
    """按行 id 切分的语义段:标题 + 该段包含的 source_log_line id 列表。"""

    title: str
    line_ids: list[int] = field(default_factory=list)


def _merge_short(blocks: list[str], min_chars: int) -> list[str]:
    """过短块并入相邻块(向后合并,`\n\n` 连接)。"""
    if not blocks:
        return []
    result = [blocks[0]]
    for block in blocks[1:]:
        if len(result[-1]) < min_chars:
            result[-1] = result[-1] + "\n\n" + block
        else:
            result.append(block)
    return result


def _split_sections_to_text(lines: list[str]) -> list[str]:
    """section 模式:按 `## ` 切段,丢弃前言与空模板段,段内空行折叠。"""
    groups: list[tuple[str, list[str]]] = []
    heading: str | None = None
    body: list[str] = []
    for line in lines:
        if _is_heading(line):
            if heading is not None:
                groups.append((heading, body))
            heading = line
            body = []
        elif heading is not None:
            body.append(line)
        # heading is None -> 前言,丢弃
    if heading is not None:
        groups.append((heading, body))

    blocks: list[str] = []
    for head, body_lines in groups:
        kept = [b for b in body_lines if b.strip() and not _is_placeholder(b)]
        if not kept:  # 空模板段 -> 丢弃
            continue
        blocks.append("\n".join([head] + kept))
    return blocks


def _split_paragraphs_to_text(lines: list[str]) -> list[str]:
    """paragraph 模式(无 `## `):空行分隔为段落,丢弃纯占位段落。"""
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip() == "":
            if current:
                paragraphs.append(current)
                current = []
        else:
            current.append(line)
    if current:
        paragraphs.append(current)

    blocks: list[str] = []
    for para in paragraphs:
        if all(_is_placeholder(x) for x in para):
            continue
        blocks.append("\n".join(para))
    return blocks


def split_text(text: str, *, min_chars: int = 30, max_chars: int = 4000) -> list[str]:
    """把整篇文本切分为语义块列表(每块为 `## 标题\n正文` 或段落文本)。

    max_chars 目前仅作签名/意图保留:为「稳妥不出问题」不做句中硬切,超长块整体保留。
    """
    lines = text.splitlines()
    if any(_is_heading(line) for line in lines):
        blocks = _split_sections_to_text(lines)
    else:
        blocks = _split_paragraphs_to_text(lines)
    return _merge_short(blocks, min_chars)


def split_segments(text: str) -> list[Segment]:
    """文本级切分,返回 Segment(title + 该块所有行)。"""
    segments: list[Segment] = []
    for block in split_text(text):
        block_lines = block.split("\n")
        title = _heading_title(block_lines[0]) if _is_heading(block_lines[0]) else ""
        segments.append(Segment(title=title, lines=block_lines))
    return segments


def split_line_segments(lines: list[tuple[int, str]]) -> list[LineSegment]:
    """在已入库的逐行数据 (line_id, content) 上按 `## ` 切分,返回每段的 line_id 列表。

    规则与 split_text 的 section 模式一致(前言丢弃、空模板段丢弃);为保真,窗口内包含该段
    的全部行(含空行)。无任何 `## ` 段时降级为「整篇 1 段」,避免切片产生 0 段而失败。
    """
    groups: list[tuple[str, list[tuple[int, str]]]] = []
    heading: str | None = None
    body: list[tuple[int, str]] = []
    has_heading = False
    for line_id, content in lines:
        if _is_heading(content):
            has_heading = True
            if heading is not None:
                groups.append((heading, body))
            heading = content
            body = [(line_id, content)]
        elif heading is not None:
            body.append((line_id, content))
        # heading is None -> 前言,丢弃
    if heading is not None:
        groups.append((heading, body))

    segments: list[LineSegment] = []
    for head, records in groups:
        # 段内除标题外是否有实质正文;全为空/占位 -> 丢弃该段。
        body_records = records[1:]
        kept = [c for _, c in body_records if c.strip() and not _is_placeholder(c)]
        if not kept:
            continue
        segments.append(
            LineSegment(title=_heading_title(head), line_ids=[lid for lid, _ in records])
        )

    if segments:
        return segments

    # 降级:无 `## ` 段(或全被判空)的纯散文文件 -> 整篇作 1 段,保证可切片可标注。
    if not lines:
        return []
    if has_heading:
        # 有标题但正文全空 -> 仍整篇 1 段,标题取首个标题。
        title = next((_heading_title(c) for _, c in lines if _is_heading(c)), "全文")
    else:
        title = next((c.strip()[:200] for _, c in lines if c.strip()), "全文")
    return [LineSegment(title=title or "全文", line_ids=[lid for lid, _ in lines])]
