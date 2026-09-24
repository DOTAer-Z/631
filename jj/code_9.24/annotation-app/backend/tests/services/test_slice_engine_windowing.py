from __future__ import annotations

import pytest

from app.services.slice_engine.windowing import (
    DEFAULT_WINDOW_SECONDS,
    MAX_WINDOW_SECONDS,
    MIN_WINDOW_SECONDS,
    WindowParseError,
    aggregate_source_lines_by_window,
    parse_window_seconds,
)


def test_parse_window_seconds_supported_values() -> None:
    assert parse_window_seconds(None) == DEFAULT_WINDOW_SECONDS
    for value in (MIN_WINDOW_SECONDS, 10, 60, 300, 600, MAX_WINDOW_SECONDS):
        assert parse_window_seconds(value) == value
        assert parse_window_seconds(str(value)) == value


@pytest.mark.parametrize(
    "value",
    ["", "0", 0, MIN_WINDOW_SECONDS - 1, -1, MAX_WINDOW_SECONDS + 1, "abc", "10m", object()],
)
def test_parse_window_seconds_invalid(value: object) -> None:
    with pytest.raises(WindowParseError):
        parse_window_seconds(value)


def test_aggregate_source_lines_by_window_half_open_ranges() -> None:
    rows = [
        (1, 0.0),
        (2, 59.9),
        (3, 60.0),
        (4, 119.9),
    ]

    groups = aggregate_source_lines_by_window(rows, window_seconds=180)

    assert len(groups) == 1
    assert groups[0].window_start == 0
    assert groups[0].window_end == 180
    assert groups[0].source_line_ids == [1, 2, 3, 4]


def test_aggregate_source_lines_by_window_multiple_buckets() -> None:
    rows = [
        (11, 61.0),
        (12, 180.0),
        (13, 359.0),
    ]

    groups = aggregate_source_lines_by_window(rows, window_seconds=180)

    assert len(groups) == 2
    assert groups[0].window_start == 0
    assert groups[0].window_end == 180
    assert groups[0].source_line_ids == [11]
    assert groups[1].window_start == 180
    assert groups[1].window_end == 360
    assert groups[1].source_line_ids == [12, 13]


def test_aggregate_source_lines_by_window_sub_minute_buckets() -> None:
    """10-second windows must produce per-10s buckets."""
    rows = [(101, 0.0), (102, 9.5), (103, 10.0), (104, 21.0), (105, 35.0)]

    groups = aggregate_source_lines_by_window(rows, window_seconds=10)

    assert [(g.window_start, g.window_end) for g in groups] == [
        (0, 10),
        (10, 20),
        (20, 30),
        (30, 40),
    ]
    assert groups[0].source_line_ids == [101, 102]
    assert groups[1].source_line_ids == [103]
    assert groups[2].source_line_ids == [104]
    assert groups[3].source_line_ids == [105]
