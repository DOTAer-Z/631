from __future__ import annotations

from app.services.slice_engine.parser import parse_log_lines


def test_parse_log_lines_valid_and_invalid_counts() -> None:
    lines = [
        "1717245600.123 boot ok",
        "42 hello",
        "not-a-ts msg",
        "123",  # missing message
        "",
        "   ",
    ]

    parsed = parse_log_lines(lines=lines, source_rel_path="cpu0/sys.log")

    assert parsed.invalid_line_count == 4
    assert len(parsed.records) == 2
    assert parsed.records[0].timestamp == 1717245600.123
    assert parsed.records[0].message == "boot ok"
    assert parsed.records[0].source_rel_path == "cpu0/sys.log"
    assert parsed.records[1].timestamp == 42.0
    assert parsed.records[1].message == "hello"


def test_parse_log_lines_does_not_throw_on_invalid() -> None:
    parsed = parse_log_lines(lines=["x", "y", "z"], source_rel_path="a.log")

    assert parsed.invalid_line_count == 3
    assert parsed.records == []
