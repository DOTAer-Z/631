from __future__ import annotations

import math
import shutil

from sqlalchemy import delete as sa_delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import (
    SliceTaskValidationError,
    WindowHasAnnotationError,
    WindowNotFoundError,
)
from app.db.models import (
    Annotation,
    SliceTask,
    SliceWindow,
    SliceWindowLine,
    SourceLogFile,
    SourceLogLine,
)
from app.services.slice_engine.windowing import MAX_WINDOW_SECONDS, MIN_WINDOW_SECONDS
from app.services.slice_engine.writer import WindowMaterializationInput, materialize_windows


class SliceWindowService:
    """Mutations on `slice_windows`. Reads still go through SourceLogQueryService."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def delete(self, *, window_id: int) -> None:
        """Delete an unannotated window.

        ORM cascade ("all, delete-orphan" on SliceWindow.window_lines and on
        SliceWindow.annotations) drops slice_window_lines and any annotation
        automatically; the self-referential parent FK (ondelete CASCADE) drops
        any sub-windows. We still refuse to delete a window that already carries
        an annotation, or whose sub-windows carry annotations — silently
        dropping human-curated work is never OK.

        Source-of-truth tables (`source_log_files` / `source_log_lines`) are
        untouched; re-running a slice task will regenerate this window.
        """
        window = self.db.get(SliceWindow, window_id)
        if window is None:
            raise WindowNotFoundError()

        annotation = self.db.query(Annotation).filter(Annotation.slice_window_id == window_id).first()
        if annotation is not None:
            raise WindowHasAnnotationError()

        child_ids = self._child_window_ids(window_id)
        if child_ids and self._any_annotated(child_ids):
            raise WindowHasAnnotationError()

        # Remove on-disk projections for the window and any sub-windows.
        self._rmtree_window(window)
        for child in self.db.scalars(
            select(SliceWindow).where(SliceWindow.id.in_(child_ids))
        ).all() if child_ids else []:
            self._rmtree_window(child)

        task = self.db.get(SliceTask, window.slice_task_id)
        removed = 1 + len(child_ids)
        self.db.delete(window)
        # Keep the parent task's window counter consistent so the dashboard /
        # task-detail views don't drift after a deletion.
        if task is not None and task.total_windows > 0:
            task.total_windows = max(0, task.total_windows - removed)
            self.db.add(task)
        self.db.commit()

    def subdivide(self, *, window_id: int, window_seconds: int) -> list[SliceWindow]:
        """Subdivide a window into finer sub-windows.

        The original window and its annotations are preserved; the children are
        materialized alongside it with ``parent_window_id`` set and the parent is
        flagged ``has_children=True``. Re-subdividing first clears prior children,
        but refuses if any existing child already carries an annotation.
        """
        window = self.db.get(SliceWindow, window_id)
        if window is None:
            raise WindowNotFoundError()

        span = float(window.window_end_ts) - float(window.window_start_ts)
        if window_seconds < MIN_WINDOW_SECONDS or window_seconds > MAX_WINDOW_SECONDS:
            raise SliceTaskValidationError(
                f"window_seconds must be within [{MIN_WINDOW_SECONDS}, {MAX_WINDOW_SECONDS}]"
            )
        if window_seconds >= span:
            raise SliceTaskValidationError(
                "subdivision window_seconds must be smaller than the window span"
            )

        # Re-subdivision: clear prior children unless they hold human annotations.
        existing_child_ids = self._child_window_ids(window_id)
        if existing_child_ids:
            if self._any_annotated(existing_child_ids):
                raise WindowHasAnnotationError()
            for child in self.db.scalars(
                select(SliceWindow).where(SliceWindow.id.in_(existing_child_ids))
            ).all():
                self._rmtree_window(child)
            self.db.execute(
                sa_delete(SliceWindowLine).where(
                    SliceWindowLine.slice_window_id.in_(existing_child_ids)
                )
            )
            self.db.execute(
                sa_delete(SliceWindow).where(SliceWindow.id.in_(existing_child_ids))
            )
            self.db.flush()

        rows = self._load_window_source_lines(window_id=window_id)
        if not rows:
            raise SliceTaskValidationError("window has no timestamped lines to subdivide")

        groups = self._bucket_relative(
            rows=rows,
            window_start=float(window.window_start_ts),
            window_end=float(window.window_end_ts),
            window_seconds=window_seconds,
        )

        children = materialize_windows(
            db=self.db,
            task_id=window.slice_task_id,
            windows=groups,
            parent_window_id=window.id,
            wipe_task_dir=False,
        )

        window.has_children = True
        self.db.add(window)

        task = self.db.get(SliceTask, window.slice_task_id)
        if task is not None:
            task.total_windows = (task.total_windows or 0) + len(children)
            self.db.add(task)

        self.db.commit()
        return children

    # --- helpers -----------------------------------------------------------

    def _child_window_ids(self, window_id: int) -> list[int]:
        return [
            int(cid)
            for cid in self.db.scalars(
                select(SliceWindow.id).where(SliceWindow.parent_window_id == window_id)
            ).all()
        ]

    def _any_annotated(self, window_ids: list[int]) -> bool:
        if not window_ids:
            return False
        return (
            self.db.scalar(
                select(Annotation.id).where(Annotation.slice_window_id.in_(window_ids)).limit(1)
            )
            is not None
        )

    def _rmtree_window(self, window: SliceWindow) -> None:
        try:
            from pathlib import Path

            path = Path(window.window_path)
            if path.exists():
                shutil.rmtree(path)
        except Exception:  # noqa: BLE001 - disk cleanup is best-effort
            pass

    def _load_window_source_lines(self, *, window_id: int):
        stmt = (
            select(
                SourceLogLine.id,
                SourceLogLine.source_file_id,
                SourceLogLine.timestamp,
                SourceLogFile.cpu_name,
                SourceLogFile.module_path,
            )
            .join(SourceLogFile, SourceLogFile.id == SourceLogLine.source_file_id)
            .join(SliceWindowLine, SliceWindowLine.source_line_id == SourceLogLine.id)
            .where(SliceWindowLine.slice_window_id == window_id)
            .where(SourceLogLine.timestamp.is_not(None))
            .order_by(SourceLogLine.timestamp, SourceLogLine.id)
        )
        return [row for row in self.db.execute(stmt).all() if row[2] is not None]

    def _bucket_relative(
        self, *, rows, window_start: float, window_end: float, window_seconds: int
    ) -> list[WindowMaterializationInput]:
        # Bucket relative to the parent's start so children always tile within
        # [window_start, window_end) regardless of the absolute epoch grid.
        buckets: dict[int, dict] = {}
        for source_line_id, source_file_id, timestamp, cpu_name, module_path in rows:
            ts = float(timestamp)
            offset = int(math.floor((ts - window_start) / window_seconds))
            bucket_start = int(window_start) + offset * window_seconds
            entry = buckets.setdefault(
                bucket_start,
                {"line_ids": [], "files": set(), "cpus": set(), "modules": set()},
            )
            entry["line_ids"].append(int(source_line_id))
            entry["files"].add(int(source_file_id))
            entry["cpus"].add(str(cpu_name))
            entry["modules"].add(str(module_path))

        result: list[WindowMaterializationInput] = []
        for bucket_start in sorted(buckets.keys()):
            entry = buckets[bucket_start]
            bucket_end = min(bucket_start + window_seconds, int(window_end))
            result.append(
                WindowMaterializationInput(
                    window_start=bucket_start,
                    window_end=bucket_end,
                    source_line_ids=entry["line_ids"],
                    line_count=len(entry["line_ids"]),
                    file_count=len(entry["files"]),
                    cpu_count=len(entry["cpus"]),
                    module_count=len(entry["modules"]),
                )
            )
        return result
