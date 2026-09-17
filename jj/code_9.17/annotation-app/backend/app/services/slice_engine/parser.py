from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedRecord:
    timestamp: float
    message: str
    source_rel_path: str


@dataclass(frozen=True)
class ParseLogResult:
    records: list[ParsedRecord]
    invalid_line_count: int


def parse_log_lines(*, lines: list[str], source_rel_path: str) -> ParseLogResult:
    records: list[ParsedRecord] = []
    invalid_line_count = 0

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            invalid_line_count += 1
            continue

        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            invalid_line_count += 1
            continue

        timestamp_str, message = parts
        try:
            timestamp = float(timestamp_str)
        except ValueError:
            invalid_line_count += 1
            continue

        if not message:
            invalid_line_count += 1
            continue

        records.append(
            ParsedRecord(
                timestamp=timestamp,
                message=message,
                source_rel_path=source_rel_path,
            )
        )

    return ParseLogResult(records=records, invalid_line_count=invalid_line_count)
