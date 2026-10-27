from __future__ import annotations

import json
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import SliceWindow, SliceWindowLine, SourceLogFile, SourceLogLine


@dataclass(frozen=True)
class WindowMaterializationInput:
    window_start: int
    window_end: int
    source_line_ids: list[int]
    line_count: int
    file_count: int
    cpu_count: int
    module_count: int
    # 非结构化语义段标题;非空表示该窗口是「语义段」而非时间窗口。
    segment_title: str | None = None


def materialize_windows(
    *,
    db: Session,
    task_id: int,
    windows: list[WindowMaterializationInput],
    parent_window_id: int | None = None,
    wipe_task_dir: bool = True,
) -> list[SliceWindow]:
    persisted_windows: list[SliceWindow] = []
    source_line_timestamps = _load_source_line_timestamps(
        db=db,
        source_line_ids=[
            source_line_id
            for window in windows
            for source_line_id in window.source_line_ids
        ],
    )

    for window in windows:
        row = SliceWindow(
            slice_task_id=task_id,
            parent_window_id=parent_window_id,
            window_start_ts=float(window.window_start),
            window_end_ts=float(window.window_end),
            segment_title=window.segment_title,
            line_count=window.line_count,
            file_count=window.file_count,
            cpu_count=window.cpu_count,
            module_count=window.module_count,
        )
        db.add(row)
        db.flush()

        if window.source_line_ids:
            db.execute(
                insert(SliceWindowLine),
                [
                    {
                        "slice_window_id": row.id,
                        "source_line_id": source_line_id,
                        "timestamp": source_line_timestamps.get(source_line_id),
                    }
                    for source_line_id in window.source_line_ids
                ],
            )

        persisted_windows.append(row)

    if persisted_windows:
        _write_window_projection(
            db=db, task_id=task_id, windows=persisted_windows, wipe_task_dir=wipe_task_dir
        )

    return persisted_windows


def _load_source_line_timestamps(*, db: Session, source_line_ids: list[int]) -> dict[int, float | None]:
    unique_ids = sorted(set(source_line_ids))
    if not unique_ids:
        return {}

    stmt = select(SourceLogLine.id, SourceLogLine.timestamp).where(SourceLogLine.id.in_(unique_ids))
    return {
        int(source_line_id): timestamp
        for source_line_id, timestamp in db.execute(stmt).all()
    }


def _write_window_projection(
    *, db: Session, task_id: int, windows: list[SliceWindow], wipe_task_dir: bool = True
) -> None:
    settings = get_settings()
    task_dir = settings.slices_dir / f"task_{task_id}"
    if wipe_task_dir and task_dir.exists():
        # Full (re-)slice: clear the whole task directory. NEVER do this on the
        # subdivision path — it would delete sibling/parent window projections.
        shutil.rmtree(task_dir)
    task_dir.mkdir(parents=True, exist_ok=True)

    for window in windows:
        window_dir = task_dir / _window_dir_name(window)
        if not wipe_task_dir and window_dir.exists():
            # Re-subdivision overwrites only this window's own directory.
            shutil.rmtree(window_dir)
        window_dir.mkdir(parents=True, exist_ok=True)

        stmt = (
            select(
                SourceLogFile.logical_path,
                SourceLogLine.timestamp,
                SourceLogLine.content,
            )
            .join(SourceLogLine, SourceLogLine.source_file_id == SourceLogFile.id)
            .join(SliceWindowLine, SliceWindowLine.source_line_id == SourceLogLine.id)
            .where(SliceWindowLine.slice_window_id == window.id)
            .order_by(SourceLogFile.logical_path, SourceLogLine.line_no, SourceLogLine.id)
        )
        rows = list(db.execute(stmt).all())

        grouped: dict[str, list[tuple[float | None, str]]] = defaultdict(list)
        for logical_path, timestamp, content in rows:
            grouped[str(logical_path)].append((timestamp, str(content)))

        files: list[str] = []
        for logical_path in sorted(grouped.keys()):
            output_file = window_dir / logical_path
            output_file.parent.mkdir(parents=True, exist_ok=True)
            with output_file.open("w", encoding="utf-8") as handle:
                for timestamp, content in grouped[logical_path]:
                    if timestamp is None:
                        handle.write(f"{content}\n")
                    else:
                        handle.write(f"{content}\n")
            files.append(logical_path)

        manifest_path = window_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "window_start": window.window_start_ts,
                    "window_end": window.window_end_ts,
                    "record_count": window.line_count,
                    "invalid_line_count": 0,
                    "files": files,
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )


def _window_dir_name(window: SliceWindow) -> str:
    start = int(window.window_start_ts) if float(window.window_start_ts).is_integer() else window.window_start_ts
    end = int(window.window_end_ts) if float(window.window_end_ts).is_integer() else window.window_end_ts
    return f"window_{start}_{end}"
