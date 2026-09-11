from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import DatasetPackage, SliceTask, SliceWindow, SliceWindowLine, SourceLogFile, SourceLogLine
from app.services.slice_engine import aggregate_source_lines_by_window
from app.services.slice_engine.writer import WindowMaterializationInput, materialize_windows
from app.services.unstructured import split_line_segments


@dataclass(frozen=True)
class TaskRunResult:
    total_files: int
    total_lines: int
    total_windows: int


@dataclass(frozen=True)
class SourceLineForSlice:
    source_line_id: int
    source_file_id: int
    timestamp: float
    cpu_name: str
    module_path: str


class TaskRunner:
    """Executes a slice task in-process using database-resident source logs."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def run(self, *, task: SliceTask, package: DatasetPackage) -> SliceTask:
        self._mark_running(task)
        try:
            result = self._execute_pipeline(task=task, package=package)
            self._mark_success(task=task, result=result)
            return task
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(task=task, error_message=str(exc))
            return task

    def _execute_pipeline(self, *, task: SliceTask, package: DatasetPackage) -> TaskRunResult:
        # 非结构化包按语义段(## 标题)切分,而非时间窗口；其余(semi_structured 日志、
        # 以及未来 structured 监控/追踪数据)一律按时间窗口切片。
        if package.data_kind == "unstructured":
            return self._execute_segment_pipeline(task=task, package=package)

        rows = self._load_source_lines(package_id=package.id)
        if not rows:
            raise ValueError("no timestamped source_log_lines found for package")

        self._clear_existing_materialization(task_id=task.id)

        grouped = aggregate_source_lines_by_window(
            [(row.source_line_id, row.timestamp) for row in rows],
            window_seconds=task.window_seconds,
        )
        row_map = {row.source_line_id: row for row in rows}

        windows = materialize_windows(
            db=self.db,
            task_id=task.id,
            windows=[
                self._build_window_input(group=group, row_map=row_map)
                for group in grouped
            ],
        )

        touched_files = {row.source_file_id for row in rows}
        return TaskRunResult(
            total_files=len(touched_files),
            total_lines=sum(window.line_count for window in windows),
            total_windows=len(windows),
        )

    def _execute_segment_pipeline(self, *, task: SliceTask, package: DatasetPackage) -> TaskRunResult:
        """非结构化切片:每份报告按 `## ` 二级标题切分,每段映射为一个窗口。

        窗口 start/end 用全局递增序号(段 i → i, i+1)作合成时间轴,保证窗口顺序/导航正确;
        segment_title 记段标题,浏览时按标题渲染而非按时间戳。window_seconds 对本路径无意义。
        """
        files = list(
            self.db.execute(
                select(SourceLogFile.id, SourceLogFile.cpu_name)
                .where(SourceLogFile.package_id == package.id)
                .where(SourceLogFile.data_kind == "unstructured")
                .order_by(SourceLogFile.logical_path, SourceLogFile.id)
            ).all()
        )
        if not files:
            raise ValueError("no unstructured source_log_files found for package")

        self._clear_existing_materialization(task_id=task.id)

        window_inputs: list[WindowMaterializationInput] = []
        ordinal = 0
        touched_files: set[int] = set()
        for source_file_id, cpu_name in files:
            line_rows = self.db.execute(
                select(SourceLogLine.id, SourceLogLine.content)
                .where(SourceLogLine.source_file_id == source_file_id)
                .order_by(SourceLogLine.line_no, SourceLogLine.id)
            ).all()
            lines = [(int(line_id), str(content or "")) for line_id, content in line_rows]

            for segment in split_line_segments(lines):
                touched_files.add(int(source_file_id))
                window_inputs.append(
                    WindowMaterializationInput(
                        window_start=ordinal,
                        window_end=ordinal + 1,
                        source_line_ids=list(segment.line_ids),
                        line_count=len(segment.line_ids),
                        file_count=1,
                        cpu_count=1,
                        module_count=0,
                        segment_title=segment.title,
                    )
                )
                ordinal += 1

        if not window_inputs:
            raise ValueError("no `## ` segments found in unstructured package")

        windows = materialize_windows(db=self.db, task_id=task.id, windows=window_inputs)
        return TaskRunResult(
            total_files=len(touched_files),
            total_lines=sum(window.line_count for window in windows),
            total_windows=len(windows),
        )

    def _load_source_lines(self, *, package_id: int) -> list[SourceLineForSlice]:
        stmt = (
            select(
                SourceLogLine.id,
                SourceLogLine.source_file_id,
                SourceLogLine.timestamp,
                SourceLogFile.cpu_name,
                SourceLogFile.module_path,
            )
            .join(SourceLogFile, SourceLogFile.id == SourceLogLine.source_file_id)
            .where(SourceLogFile.package_id == package_id)
            .where(SourceLogLine.timestamp.is_not(None))
            .order_by(SourceLogLine.timestamp, SourceLogLine.id)
        )
        return [
            SourceLineForSlice(
                source_line_id=int(source_line_id),
                source_file_id=int(source_file_id),
                timestamp=float(timestamp),
                cpu_name=str(cpu_name),
                module_path=str(module_path),
            )
            for source_line_id, source_file_id, timestamp, cpu_name, module_path in self.db.execute(stmt).all()
            if timestamp is not None
        ]

    def _build_window_input(
        self,
        *,
        group,
        row_map: dict[int, SourceLineForSlice],
    ) -> WindowMaterializationInput:
        files: set[int] = set()
        cpus: set[str] = set()
        modules: set[str] = set()

        for source_line_id in group.source_line_ids:
            row = row_map[source_line_id]
            files.add(row.source_file_id)
            cpus.add(row.cpu_name)
            modules.add(row.module_path)

        return WindowMaterializationInput(
            window_start=group.window_start,
            window_end=group.window_end,
            source_line_ids=list(group.source_line_ids),
            line_count=len(group.source_line_ids),
            file_count=len(files),
            cpu_count=len(cpus),
            module_count=len(modules),
        )

    def _clear_existing_materialization(self, *, task_id: int) -> None:
        window_ids = list(
            self.db.scalars(
                select(SliceWindow.id).where(SliceWindow.slice_task_id == task_id)
            ).all()
        )
        if window_ids:
            self.db.execute(delete(SliceWindowLine).where(SliceWindowLine.slice_window_id.in_(window_ids)))
        self.db.execute(delete(SliceWindow).where(SliceWindow.slice_task_id == task_id))
        self.db.flush()

    def _mark_running(self, task: SliceTask) -> None:
        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        task.finished_at = None
        task.error_message = None
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)

    def _mark_success(self, *, task: SliceTask, result: TaskRunResult) -> None:
        task.status = "success"
        task.total_files = result.total_files
        task.total_lines = result.total_lines
        task.total_windows = result.total_windows
        task.error_message = None
        task.finished_at = datetime.now(timezone.utc)
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)

    def _mark_failed(self, *, task: SliceTask, error_message: str) -> None:
        task.status = "failed"
        task.error_message = error_message[:10000] if error_message else "slice task failed"
        task.finished_at = datetime.now(timezone.utc)
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
