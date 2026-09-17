"""jsonl_ingest_router 检测/路由单测。

重点不加 DB：探测逻辑(_probe_kind / detect_jsonl_formats)无 DB 依赖，可直接测。
"""
import tempfile
from pathlib import Path

from app.services.jsonl_ingest_router import (
    FORMAT_SEGMENT,
    FORMAT_STRUCTURED,
    _probe_kind,
    detect_jsonl_formats,
)


def _write(tmp: Path, name: str, lines) -> Path:
    p = tmp / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_probe_structured_has_id_round_text():
    with tempfile.TemporaryDirectory() as td:
        p = _write(
            Path(td), "struct.jsonl",
            ['{"id":"Test_50005:round_1:qemu_console","round":"round_1","log_type":"qemu_console","text":"x"}'],
        )
        assert _probe_kind(p) == FORMAT_STRUCTURED


def test_probe_segment_has_segment_id_text():
    with tempfile.TemporaryDirectory() as td:
        p = _write(
            Path(td), "notstruct.jsonl",
            ['{"segment_id":"seg_1","document_id":"doc_1","ground_truth":{"label":"abnormal"},"text":"y"}'],
        )
        assert _probe_kind(p) == FORMAT_SEGMENT


def test_probe_none_for_unrecognized():
    with tempfile.TemporaryDirectory() as td:
        p = _write(Path(td), "random.jsonl", ['{"foo":"bar"}'])
        assert _probe_kind(p) is None


def test_detect_returns_both_formats():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "struct.jsonl", ['{"id":"a:round_1:b","round":"round_1","text":"x"}'])
        _write(root, "notstruct.jsonl", ['{"segment_id":"s","document_id":"d","text":"y"}'])
        fmts = detect_jsonl_formats(root)
        assert set(fmts) == {FORMAT_STRUCTURED, FORMAT_SEGMENT}
        assert fmts[FORMAT_STRUCTURED].name == "struct.jsonl"
        assert fmts[FORMAT_SEGMENT].name == "notstruct.jsonl"


def test_detect_recurses_and_nests():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "inner"
        root.mkdir()
        _write(root, "data_struct.jsonl", ['{"id":"a:round_1:b","round":"round_1","text":"x"}'])
        fmts = detect_jsonl_formats(Path(td))
        assert fmts[FORMAT_STRUCTURED].name == "data_struct.jsonl"


def test_empty_dir_returns_empty():
    with tempfile.TemporaryDirectory() as td:
        assert detect_jsonl_formats(Path(td)) == {}
