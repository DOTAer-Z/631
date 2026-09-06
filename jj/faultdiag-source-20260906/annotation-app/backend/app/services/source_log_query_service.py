from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from sqlalchemy import and_, asc, desc, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import InvalidPaginationParamsError, WindowNotFoundError
from app.db.models import Annotation, SliceWindow, SliceWindowLine, SourceLogFile, SourceLogLine
from app.schemas.annotation import AnnotationResponse
from app.schemas.slice_window import (
    SliceWindowFullResponse,
    SliceWindowLogItem,
    SliceWindowLogsResponse,
    SliceWindowNavigationResponse,
    SliceWindowSummaryResponse,
    SliceWindowTreeNode,
    SliceWindowTreeResponse,
)


@dataclass(frozen=True)
class WindowListFilters:
    start_ts: float | None = None
    end_ts: float | None = None


class SourceLogQueryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_windows(
        self,
        *,
        task_id: int,
        page: int,
        page_size: int,
        sort_by: str,
        sort_order: str,
        filters: WindowListFilters | None = None,
    ) -> tuple[list[SliceWindow], int]:
        if page < 1 or page_size < 1 or page_size > 200:
            raise InvalidPaginationParamsError("page/page_size out of allowed range")
        if sort_by not in {"window_start_ts", "created_at"}:
            raise InvalidPaginationParamsError("unsupported sort_by")
        if sort_order not in {"asc", "desc"}:
            raise InvalidPaginationParamsError("unsupported sort_order")
        if filters and filters.start_ts is not None and filters.end_ts is not None and filters.start_ts > filters.end_ts:
            raise InvalidPaginationParamsError("start_ts cannot be greater than end_ts")

        stmt = select(SliceWindow).where(SliceWindow.slice_task_id == task_id)
        count_stmt = select(func.count(SliceWindow.id)).where(SliceWindow.slice_task_id == task_id)

        if filters:
            if filters.start_ts is not None:
                stmt = stmt.where(SliceWindow.window_start_ts >= filters.start_ts)
                count_stmt = count_stmt.where(SliceWindow.window_start_ts >= filters.start_ts)
            if filters.end_ts is not None:
                stmt = stmt.where(SliceWindow.window_end_ts <= filters.end_ts)
                count_stmt = count_stmt.where(SliceWindow.window_end_ts <= filters.end_ts)

        sort_col = SliceWindow.window_start_ts if sort_by == "window_start_ts" else SliceWindow.created_at
        order_expr = desc(sort_col) if sort_order == "desc" else asc(sort_col)
        stmt = stmt.order_by(order_expr, asc(SliceWindow.id)).offset((page - 1) * page_size).limit(page_size)

        items = list(self.db.scalars(stmt).all())
        total = int(self.db.scalar(count_stmt) or 0)
        return items, total

    def get_window_detail(self, *, window_id: int) -> SliceWindowSummaryResponse:
        window = self._get_window(window_id=window_id)
        return self._to_window_summary(window)

    def get_window_tree(self, *, window_id: int) -> SliceWindowTreeResponse:
        window = self._get_window(window_id=window_id)
        rows = list(
            self.db.execute(
                select(
                    SourceLogFile.cpu_name,
                    SourceLogFile.module_path,
                    SourceLogFile.filename,
                    SourceLogFile.logical_path,
                    SourceLogFile.id,
                )
                .join(SourceLogLine, SourceLogLine.source_file_id == SourceLogFile.id)
                .join(SliceWindowLine, SliceWindowLine.source_line_id == SourceLogLine.id)
                .where(SliceWindowLine.slice_window_id == window.id)
                .group_by(
                    SourceLogFile.id,
                    SourceLogFile.cpu_name,
                    SourceLogFile.module_path,
                    SourceLogFile.filename,
                    SourceLogFile.logical_path,
                )
                .order_by(SourceLogFile.cpu_name, SourceLogFile.module_path, SourceLogFile.filename, SourceLogFile.id)
            ).all()
        )

        cpu_map: dict[str, dict[str, dict]] = {}
        for cpu_name, module_path, filename, logical_path, source_file_id in rows:
            cpu_bucket = cpu_map.setdefault(str(cpu_name), {})
            module_key = str(module_path)
            module_bucket = cpu_bucket.setdefault(module_key, {})
            module_bucket[str(filename)] = {
                "path": str(logical_path),
                "source_file_id": int(source_file_id),
            }

        root: list[SliceWindowTreeNode] = []
        for cpu_name in sorted(cpu_map.keys()):
            module_nodes: list[SliceWindowTreeNode] = []
            for module_path in sorted(cpu_map[cpu_name].keys()):
                file_nodes = [
                    SliceWindowTreeNode(
                        name=filename,
                        type="file",
                        path=file_meta["path"],
                        source_file_id=file_meta["source_file_id"],
                        children=[],
                    )
                    for filename, file_meta in sorted(cpu_map[cpu_name][module_path].items())
                ]
                module_nodes.append(
                    SliceWindowTreeNode(
                        name=module_path,
                        type="module",
                        path=None,
                        source_file_id=None,
                        children=file_nodes,
                    )
                )
            root.append(
                SliceWindowTreeNode(
                    name=cpu_name,
                    type="cpu",
                    path=None,
                    source_file_id=None,
                    children=module_nodes,
                )
            )

        return SliceWindowTreeResponse(window_id=window.id, root=root)

    def get_window_full(self, *, window_id: int) -> SliceWindowFullResponse:
        window = self._get_window(window_id=window_id)
        annotations = self._get_annotations(window_id=window.id)
        navigation = self._get_navigation(window=window)
        return SliceWindowFullResponse(
            window=self._to_window_summary(window),
            tree=self.get_window_tree(window_id=window.id),
            annotations=annotations,
            navigation=navigation,
        )

    def get_window_logs(
        self,
        *,
        window_id: int,
        source_file_id: int,
        cursor: str | None,
        limit: int,
        keyword: str | None,
    ) -> SliceWindowLogsResponse:
        window = self._get_window(window_id=window_id)
        if limit <= 0 or limit > 500:
            raise InvalidPaginationParamsError("limit must be between 1 and 500")

        cursor_ts, cursor_id = self._decode_cursor(cursor)

        stmt = (
            select(
                SourceLogLine.id,
                SourceLogLine.source_file_id,
                SourceLogLine.line_no,
                SourceLogLine.timestamp,
                SourceLogLine.content,
            )
            .join(SliceWindowLine, SliceWindowLine.source_line_id == SourceLogLine.id)
            .where(SliceWindowLine.slice_window_id == window.id)
            .where(SourceLogLine.source_file_id == source_file_id)
        )

        if keyword:
            stmt = stmt.where(SourceLogLine.content.contains(keyword))

        if cursor is not None:
            if cursor_ts is None:
                # 非结构化语义段的行时间戳全为 NULL,按 id 纯序推进(等价于全 NULL 下的排序)。
                stmt = stmt.where(SourceLogLine.id > cursor_id)
            else:
                stmt = stmt.where(
                    or_(
                        and_(SliceWindowLine.timestamp == cursor_ts, SourceLogLine.id > cursor_id),
                        SliceWindowLine.timestamp > cursor_ts,
                    )
                )

        stmt = stmt.order_by(asc(SliceWindowLine.timestamp), asc(SourceLogLine.id)).limit(limit + 1)
        rows = list(self.db.execute(stmt).all())

        page_rows = rows[:limit]
        has_more = len(rows) > limit
        next_cursor = None
        if has_more and page_rows:
            last_row = page_rows[-1]
            next_cursor = self._encode_cursor(timestamp=last_row[3], line_id=last_row[0])

        return SliceWindowLogsResponse(
            items=[
                SliceWindowLogItem(
                    id=int(line_id),
                    source_file_id=int(row_source_file_id),
                    line_no=int(line_no),
                    timestamp=timestamp,
                    content=str(content),
                )
                for line_id, row_source_file_id, line_no, timestamp, content in page_rows
            ],
            next_cursor=next_cursor,
            has_more=has_more,
        )

    def _get_window(self, *, window_id: int) -> SliceWindow:
        window = self.db.get(SliceWindow, window_id)
        if window is None:
            raise WindowNotFoundError()
        return window

    def _get_annotations(self, *, window_id: int) -> list[AnnotationResponse]:
        rows = list(
            self.db.scalars(
                select(Annotation)
                .where(Annotation.slice_window_id == window_id)
                .order_by(Annotation.source_log_file_id.is_(None).desc(), Annotation.id.asc())
            ).all()
        )
        if not rows:
            return []

        file_ids = {row.source_log_file_id for row in rows if row.source_log_file_id is not None}
        path_by_id: dict[int, str] = {}
        if file_ids:
            path_by_id = {
                int(fid): str(path)
                for fid, path in self.db.execute(
                    select(SourceLogFile.id, SourceLogFile.logical_path).where(
                        SourceLogFile.id.in_(file_ids)
                    )
                ).all()
            }

        return [
            AnnotationResponse(
                id=row.id,
                slice_window_id=row.slice_window_id,
                source_log_file_id=row.source_log_file_id,
                binding_label=(
                    path_by_id.get(int(row.source_log_file_id))
                    if row.source_log_file_id is not None
                    else None
                ),
                label=row.label,
                anomaly_type=row.anomaly_type,
                note=row.note,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def _get_navigation(self, *, window: SliceWindow) -> SliceWindowNavigationResponse:
        # Navigate only among leaf windows (has_children == False): a window that
        # has been subdivided is no longer an annotation target — its children are.
        prev_window_id = self.db.scalar(
            select(SliceWindow.id)
            .where(SliceWindow.slice_task_id == window.slice_task_id)
            .where(SliceWindow.has_children.is_(False))
            .where(
                or_(
                    SliceWindow.window_start_ts < window.window_start_ts,
                    and_(
                        SliceWindow.window_start_ts == window.window_start_ts,
                        SliceWindow.id < window.id,
                    ),
                )
            )
            .order_by(desc(SliceWindow.window_start_ts), desc(SliceWindow.id))
            .limit(1)
        )
        next_window_id = self.db.scalar(
            select(SliceWindow.id)
            .where(SliceWindow.slice_task_id == window.slice_task_id)
            .where(SliceWindow.has_children.is_(False))
            .where(
                or_(
                    SliceWindow.window_start_ts > window.window_start_ts,
                    and_(
                        SliceWindow.window_start_ts == window.window_start_ts,
                        SliceWindow.id > window.id,
                    ),
                )
            )
            .order_by(asc(SliceWindow.window_start_ts), asc(SliceWindow.id))
            .limit(1)
        )
        return SliceWindowNavigationResponse(
            current_window_id=window.id,
            prev_window_id=int(prev_window_id) if prev_window_id is not None else None,
            next_window_id=int(next_window_id) if next_window_id is not None else None,
        )

    def _to_window_summary(self, window: SliceWindow) -> SliceWindowSummaryResponse:
        return SliceWindowSummaryResponse(
            id=window.id,
            slice_task_id=window.slice_task_id,
            window_start_ts=window.window_start_ts,
            window_end_ts=window.window_end_ts,
            segment_title=window.segment_title,
            line_count=window.line_count,
            file_count=window.file_count,
            cpu_count=window.cpu_count,
            module_count=window.module_count,
            created_at=window.created_at,
        )

    def _decode_cursor(self, cursor: str | None) -> tuple[float | None, int]:
        if cursor is None:
            return float("-inf"), 0
        try:
            payload = json.loads(base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8"))
            raw_ts = payload["timestamp"]
            timestamp = float(raw_ts) if raw_ts is not None else None
            return timestamp, int(payload["id"])
        except Exception as exc:  # noqa: BLE001
            raise InvalidPaginationParamsError("invalid cursor") from exc

    def _encode_cursor(self, *, timestamp: float | None, line_id: int) -> str:
        # 非结构化语义段行的 timestamp 为 None,游标记 null,分页按 id 推进。
        payload = {
            "timestamp": float(timestamp) if timestamp is not None else None,
            "id": int(line_id),
        }
        return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("utf-8")
