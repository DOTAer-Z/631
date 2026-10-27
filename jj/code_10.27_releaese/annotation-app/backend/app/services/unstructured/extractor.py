"""从 txt / md / pdf 提取纯文本。

- `.txt` / `.md`:按 utf-8 读取,`errors="replace"`(与 import_worker 现有读法一致)。
- `.pdf`:惰性 import `pypdf`,逐页 `extract_text()` 拼接;缺库时抛清晰错误。
"""

from __future__ import annotations

from pathlib import Path


TEXT_SUFFIXES = {".txt", ".md"}
PDF_SUFFIXES = {".pdf"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | PDF_SUFFIXES


def extract_text(path: Path) -> str:
    """提取 path 的纯文本正文。不支持的扩展名抛 ValueError。"""

    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix in PDF_SUFFIXES:
        return _extract_pdf(path)
    raise ValueError(f"unsupported unstructured file type: {suffix}")


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - 生产镜像装了 pypdf
        raise RuntimeError(
            "解析 PDF 需要 pypdf,请在标注后端环境安装 `pypdf>=4.0.0`"
        ) from exc

    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)
