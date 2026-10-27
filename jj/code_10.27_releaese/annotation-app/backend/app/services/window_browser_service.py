from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import (
    InvalidPaginationParamsError,
    InvalidRelativePathError,
    WindowNotFoundError,
)
from app.db.models import SliceTask, SliceWindow


@dataclass(frozen=True)
class WindowListFilters:
    start_ts: float | None = None
    end_ts: float | None = None


@dataclass(frozen=True)
class ManifestData:
    version: str
    window_start: float
    window_end: float
    record_count: int
    invalid_line_count: int
    files: list[str]


@dataclass(frozen=True)
class WindowTreeNode:
    name: str
    type: str  # dir | file
    path: str | None
    children: list["WindowTreeNode"]


@dataclass(frozen=True)
class ParsedLogLine:
    timestamp: float | None
    message: str
    raw: str


@dataclass(frozen=True)
class PagedLogResult:
    total_lines: int
    current_offset: int
    next_offset: int
    has_more: bool
    lines: list[ParsedLogLine]


class ManifestCache:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._store: dict[str, tuple[float, float, int, ManifestData]] = {}

    def get_or_load(self, manifest_path: Path) -> ManifestData:
        now = time.time()
        stat = manifest_path.stat()
        mtime = stat.st_mtime
        size = stat.st_size
        key = str(manifest_path)

        cached = self._store.get(key)
        if cached is not None:
            cached_at, cached_mtime, cached_size, payload = cached
            if now - cached_at <= self.ttl_seconds and cached_mtime == mtime and cached_size == size:
                return payload

        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload = ManifestData(
            version=str(data.get("version", "")),
            window_start=float(data.get("window_start", 0)),
            window_end=float(data.get("window_end", 0)),
            record_count=int(data.get("record_count", 0)),
            invalid_line_count=int(data.get("invalid_line_count", 0)),
            files=[str(item) for item in data.get("files", []) if isinstance(item, str)],
        )
        self._store[key] = (now, mtime, size, payload)
        return payload


_MANIFEST_CACHE: ManifestCache | None = None


def _manifest_cache() -> ManifestCache:
    global _MANIFEST_CACHE
    if _MANIFEST_CACHE is None:
        _MANIFEST_CACHE = ManifestCache(ttl_seconds=get_settings().manifest_cache_ttl_seconds)
    return _MANIFEST_CACHE


class WindowBrowserService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()

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
        if filters and filters.start_ts is not None and filters.end_ts is not None:
            if filters.start_ts > filters.end_ts:
                raise InvalidPaginationParamsError("start_ts cannot be greater than end_ts")

        task_exists = self.db.get(SliceTask, task_id)
        if task_exists is None:
            raise WindowNotFoundError("slice task not found")

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

    def get_window(self, *, window_id: int) -> SliceWindow:
        window = self.db.get(SliceWindow, window_id)
        if window is None:
            raise WindowNotFoundError()
        return window

    def read_manifest(self, *, window: SliceWindow) -> ManifestData:
        manifest_path = Path(window.manifest_path)
        if not manifest_path.exists():
            raise WindowNotFoundError("manifest file not found for window")
        return _manifest_cache().get_or_load(manifest_path)

    def build_tree_from_manifest(self, *, manifest: ManifestData) -> list[WindowTreeNode]:
        root: dict[str, dict] = {}

        for rel_file in sorted(manifest.files):
            parts = [p for p in Path(rel_file).parts if p]
            if not parts:
                continue
            node = root
            for idx, part in enumerate(parts):
                last = idx == len(parts) - 1
                if part not in node:
                    node[part] = {"_type": "file" if last else "dir", "_children": {}}
                if last:
                    node[part]["_type"] = "file"
                node = node[part]["_children"]

        def to_nodes(tree: dict, parent_parts: list[str]) -> list[WindowTreeNode]:
            items: list[WindowTreeNode] = []
            for name in sorted(tree.keys()):
                meta = tree[name]
                node_type = meta["_type"]
                current_parts = [*parent_parts, name]
                path = "/".join(current_parts) if node_type == "file" else None
                children = to_nodes(meta["_children"], current_parts) if meta["_children"] else []
                items.append(
                    WindowTreeNode(
                        name=name,
                        type=node_type,
                        path=path,
                        children=children,
                    )
                )
            return items

        return to_nodes(root, [])

    def read_logs(
        self,
        *,
        window: SliceWindow,
        relative_path: str,
        offset: int,
        limit: int,
    ) -> PagedLogResult:
        if offset < 0:
            raise InvalidPaginationParamsError("offset must be >= 0")
        if limit <= 0:
            raise InvalidPaginationParamsError("limit must be > 0")
        if limit > self.settings.log_page_max_limit:
            raise InvalidPaginationParamsError(
                f"limit must be <= {self.settings.log_page_max_limit}"
            )

        log_path = self._resolve_window_relative_file(window_path=Path(window.window_path), relative_path=relative_path)

        total_lines = 0
        selected: list[str] = []
        start = offset
        end = offset + limit

        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            for idx, raw_line in enumerate(handle):
                total_lines += 1
                if idx < start:
                    continue
                if idx >= end:
                    continue
                selected.append(raw_line.rstrip("\n"))

        next_offset = min(offset + len(selected), total_lines)
        has_more = next_offset < total_lines

        parsed_lines = [self._parse_line(line) for line in selected]
        return PagedLogResult(
            total_lines=total_lines,
            current_offset=offset,
            next_offset=next_offset,
            has_more=has_more,
            lines=parsed_lines,
        )

    def _resolve_window_relative_file(self, *, window_path: Path, relative_path: str) -> Path:
        rel = relative_path.strip()
        if not rel:
            raise InvalidRelativePathError("relative_path is required")

        candidate_rel = Path(rel)
        if candidate_rel.is_absolute():
            raise InvalidRelativePathError("absolute path is not allowed")

        resolved_window = window_path.resolve()
        resolved_file = (window_path / candidate_rel).resolve()
        try:
            resolved_file.relative_to(resolved_window)
        except ValueError as exc:
            raise InvalidRelativePathError("path traversal is not allowed") from exc

        if not resolved_file.exists() or not resolved_file.is_file():
            raise InvalidRelativePathError("target log file does not exist")

        return resolved_file

    def _parse_line(self, line: str) -> ParsedLogLine:
        raw = line
        trimmed = line.strip()
        if not trimmed:
            return ParsedLogLine(timestamp=None, message="", raw=raw)

        parts = trimmed.split(maxsplit=1)
        if len(parts) == 1:
            return ParsedLogLine(timestamp=None, message=parts[0], raw=raw)

        ts_text, message = parts
        try:
            ts = float(ts_text)
            return ParsedLogLine(timestamp=ts, message=message, raw=raw)
        except ValueError:
            return ParsedLogLine(timestamp=None, message=trimmed, raw=raw)
