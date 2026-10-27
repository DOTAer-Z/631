from __future__ import annotations

import math
from dataclasses import dataclass


class WindowParseError(ValueError):
    pass


@dataclass(frozen=True)
class WindowGroup:
    window_start: int
    window_end: int
    source_line_ids: list[int]


# Window length is now a free-form duration in seconds (no fixed preset list),
# bounded to a sane range so a user can pick anything from one second up to an
# hour. 300s (5 min) keeps the previous default behaviour.
MIN_WINDOW_SECONDS = 1
MAX_WINDOW_SECONDS = 3600
DEFAULT_WINDOW_SECONDS = 300


def parse_window_seconds(value: object | None) -> int:
    if value is None:
        return DEFAULT_WINDOW_SECONDS

    if isinstance(value, bool):
        raise WindowParseError("unsupported window input type")
    if isinstance(value, int):
        seconds = value
    elif isinstance(value, str):
        raw = value.strip().lower()
        if not raw:
            raise WindowParseError("window seconds cannot be empty")
        if not raw.isdigit():
            raise WindowParseError("window seconds must be a positive integer")
        seconds = int(raw)
    else:
        raise WindowParseError("unsupported window input type")

    if seconds < MIN_WINDOW_SECONDS or seconds > MAX_WINDOW_SECONDS:
        raise WindowParseError(
            f"window_seconds must be between {MIN_WINDOW_SECONDS} and {MAX_WINDOW_SECONDS}"
        )
    return seconds


def aggregate_source_lines_by_window(
    rows: list[tuple[int, float]],
    window_seconds: int,
) -> list[WindowGroup]:
    if window_seconds <= 0:
        raise ValueError("window_seconds must be a positive integer")

    grouped: dict[int, list[int]] = {}
    for source_line_id, timestamp in rows:
        start = int(math.floor(timestamp / window_seconds) * window_seconds)
        grouped.setdefault(start, []).append(source_line_id)

    windows: list[WindowGroup] = []
    for start in sorted(grouped.keys()):
        windows.append(
            WindowGroup(
                window_start=start,
                window_end=start + window_seconds,
                source_line_ids=grouped[start],
            )
        )

    return windows
