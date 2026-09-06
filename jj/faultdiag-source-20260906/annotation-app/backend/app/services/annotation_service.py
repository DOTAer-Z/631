from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import (
    AnnotationExportFileNotFoundError,
    AnnotationNotFoundError,
    AnnotationValidationError,
    InvalidPaginationParamsError,
    WindowNotFoundError,
)
from app.db.models import Annotation, DatasetPackage, FaultType, SliceTask, SliceWindow, SliceWindowLine, SourceLogFile, SourceLogLine
from app.schemas.annotation import (
    AnnotationCreateRequest,
    ExportMode,
    AnnotationStatsResponse,
    AnnotationUpdateRequest,
    ExportScope,
)


@dataclass(frozen=True)
class AnnotationQueryFilters:
    package_id: int | None = None
    task_id: int | None = None
    label: str | None = None
    anomaly_type: str | None = None
    start_ts: float | None = None
    end_ts: float | None = None
    # Pending-only knobs (ignored by annotation queries):
    min_line_count: int | None = None
    max_line_count: int | None = None
    keyword: str | None = None


_PENDING_SORT_FIELDS = {
    "window_start_ts": SliceWindow.window_start_ts,
    "line_count": SliceWindow.line_count,
}

# Sortable columns for the annotated-records list.
_ANNOTATION_SORT_FIELDS = {
    "id": Annotation.id,
    "label": Annotation.label,
    "anomaly_type": Annotation.anomaly_type,
    "created_at": Annotation.created_at,
    "updated_at": Annotation.updated_at,
    "window_start_ts": SliceWindow.window_start_ts,
}


@dataclass(frozen=True)
class ExportResult:
    format: str
    scope: str
    mode: str
    file_path: str
    file_name: str
    download_url: str
    item_count: int


class AnnotationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()

    def save_for_window(self, *, window_id: int, payload: AnnotationCreateRequest) -> Annotation:
        window = self.db.get(SliceWindow, window_id)
        if window is None:
            raise WindowNotFoundError()

        file_id = payload.source_log_file_id
        self._validate_file_belongs_to_window(window_id=window_id, source_log_file_id=file_id)

        normalized_anomaly_type = self._normalize_anomaly_type(label=payload.label, anomaly_type=payload.anomaly_type)

        row = self._get_by_window_and_file(window_id=window_id, source_log_file_id=file_id)
        if row is None:
            row = Annotation(
                slice_window_id=window_id,
                source_log_file_id=file_id,
                label=payload.label,
                anomaly_type=normalized_anomaly_type,
                note=payload.note,
            )
        else:
            row.label = payload.label
            row.anomaly_type = normalized_anomaly_type
            row.note = payload.note

        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def create_for_window(self, *, window_id: int, payload: AnnotationCreateRequest) -> Annotation:
        return self.save_for_window(window_id=window_id, payload=payload)

    def get_by_window(self, *, window_id: int) -> Annotation:
        window = self.db.get(SliceWindow, window_id)
        if window is None:
            raise WindowNotFoundError()

        # Prefer the whole-window annotation; fall back to the first file-level one.
        row = self._get_by_window_and_file(window_id=window_id, source_log_file_id=None)
        if row is None:
            row = self.db.scalar(
                select(Annotation)
                .where(Annotation.slice_window_id == window_id)
                .order_by(Annotation.id.asc())
            )
        if row is None:
            raise AnnotationNotFoundError("Annotation not found for this window")
        return row

    def update(self, *, annotation_id: int, payload: AnnotationUpdateRequest) -> Annotation:
        row = self.db.get(Annotation, annotation_id)
        if row is None:
            raise AnnotationNotFoundError()

        normalized_anomaly_type = self._normalize_anomaly_type(label=payload.label, anomaly_type=payload.anomaly_type)

        row.label = payload.label
        row.anomaly_type = normalized_anomaly_type
        row.note = payload.note

        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete(self, *, annotation_id: int) -> None:
        row = self.db.get(Annotation, annotation_id)
        if row is None:
            raise AnnotationNotFoundError()
        self.db.delete(row)
        self.db.commit()

    def query(
        self,
        *,
        page: int,
        page_size: int,
        filters: AnnotationQueryFilters,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
    ) -> tuple[list[dict], int]:
        self._validate_page_params(page=page, page_size=page_size)
        if sort_by not in _ANNOTATION_SORT_FIELDS:
            raise InvalidPaginationParamsError(f"unsupported sort_by: {sort_by}")
        if sort_order not in {"asc", "desc"}:
            raise InvalidPaginationParamsError(f"unsupported sort_order: {sort_order}")
        rows_stmt, count_stmt = self._build_annotation_base_queries(filters=filters)

        sort_col = _ANNOTATION_SORT_FIELDS[sort_by]
        primary = sort_col.desc() if sort_order == "desc" else sort_col.asc()
        # Tie-break on id (matching direction) so paging is stable.
        tiebreak = Annotation.id.desc() if sort_order == "desc" else Annotation.id.asc()
        rows = self.db.execute(
            rows_stmt.order_by(primary, tiebreak)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        total = int(self.db.scalar(count_stmt) or 0)

        items = [
            {
                "id": row.id,
                "package_id": row.package_id,
                "package_name": row.package_name,
                "task_id": row.task_id,
                "task_name": row.task_name,
                "window_id": row.slice_window_id,
                "window_start_ts": row.window_start_ts,
                "window_end_ts": row.window_end_ts,
                "line_count": row.line_count,
                "file_count": row.file_count,
                "cpu_count": row.cpu_count,
                "module_count": row.module_count,
                "source_log_file_id": row.source_log_file_id,
                "binding_label": row.binding_label,
                "label": row.label,
                "anomaly_type": row.anomaly_type,
                "note": row.note,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in rows
        ]
        return items, total

    def stats(self, *, filters: AnnotationQueryFilters) -> AnnotationStatsResponse:
        rows_stmt, _ = self._build_annotation_base_queries(filters=filters)
        rows = self.db.execute(rows_stmt).all()
        normal_count = sum(1 for row in rows if row.label == "normal")
        abnormal_count = sum(1 for row in rows if row.label == "abnormal")
        return AnnotationStatsResponse(
            total_annotations=len(rows),
            normal_count=normal_count,
            abnormal_count=abnormal_count,
        )

    def pending(
        self,
        *,
        page: int,
        page_size: int,
        filters: AnnotationQueryFilters,
        sort_by: str = "window_start_ts",
        sort_order: str = "asc",
    ) -> tuple[list[dict], int]:
        self._validate_page_params(page=page, page_size=page_size)
        if sort_by not in _PENDING_SORT_FIELDS:
            raise InvalidPaginationParamsError(f"unsupported sort_by: {sort_by}")
        if sort_order not in {"asc", "desc"}:
            raise InvalidPaginationParamsError(f"unsupported sort_order: {sort_order}")

        window_stmt, count_stmt = self._build_pending_base_queries(filters=filters)
        sort_col = _PENDING_SORT_FIELDS[sort_by]
        order_expr = sort_col.desc() if sort_order == "desc" else sort_col.asc()

        rows = self.db.execute(
            window_stmt.order_by(order_expr, SliceWindow.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        total = int(self.db.scalar(count_stmt) or 0)
        items = [
            {
                "package_id": row.package_id,
                "package_name": row.package_name,
                "task_id": row.task_id,
                "task_name": row.task_name,
                "window_id": row.window_id,
                "window_start_ts": row.window_start_ts,
                "window_end_ts": row.window_end_ts,
                "line_count": int(row.line_count or 0),
                "file_count": int(row.file_count or 0),
                "cpu_count": int(row.cpu_count or 0),
                "module_count": int(row.module_count or 0),
            }
            for row in rows
        ]
        return items, total

    def workbench(
        self,
        *,
        page: int,
        page_size: int,
        filters: AnnotationQueryFilters,
    ) -> tuple[list[dict], int]:
        self._validate_page_params(page=page, page_size=page_size)
        rows_stmt, count_stmt = self._build_annotation_base_queries(filters=filters)
        rows = self.db.execute(
            rows_stmt.order_by(Annotation.updated_at.desc(), Annotation.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        total = int(self.db.scalar(count_stmt) or 0)
        items = [
            {
                "annotation_id": row.id,
                "package_id": row.package_id,
                "task_id": row.task_id,
                "window_id": row.slice_window_id,
                "window_start_ts": row.window_start_ts,
                "window_end_ts": row.window_end_ts,
                "label": row.label,
                "anomaly_type": row.anomaly_type,
                "note": row.note,
                "updated_at": row.updated_at,
            }
            for row in rows
        ]
        return items, total

    def export(self, *, export_format: str, scope: ExportScope, export_mode: ExportMode = "light", filters: AnnotationQueryFilters) -> ExportResult:
        fmt = export_format.lower()
        if fmt not in {"csv", "json"}:
            raise AnnotationValidationError("format must be csv or json")
        if export_mode not in {"light", "full"}:
            raise AnnotationValidationError("mode must be light or full")
        if fmt == "csv" and export_mode == "full":
            raise AnnotationValidationError("csv export only supports light mode")

        export_filters = filters if scope == "current_filter" else AnnotationQueryFilters()
        all_items = self._collect_all_annotation_items(filters=export_filters, export_mode=export_mode)

        self.settings.annotations_dir.mkdir(parents=True, exist_ok=True)
        filename = f"annotations_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.{fmt}"
        output_path = self.settings.annotations_dir / filename

        if fmt == "json":
            output_path.write_text(json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            with output_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "package_id",
                        "package_name",
                        "task_id",
                        "task_name",
                        "window_id",
                        "window_start_at",
                        "window_end_at",
                        "line_count",
                        "file_count",
                        "cpu_count",
                        "module_count",
                        "binding",
                        "label",
                        "anomaly_type",
                        "note",
                        "created_at",
                        "updated_at",
                    ],
                )
                writer.writeheader()
                for row in all_items:
                    writer.writerow(
                        {
                            "package_id": row["package_id"],
                            "package_name": row["package_name"],
                            "task_id": row["task_id"],
                            "task_name": row["task_name"],
                            "window_id": row["window_id"],
                            "window_start_at": row["window_start_at"],
                            "window_end_at": row["window_end_at"],
                            "line_count": row["line_count"],
                            "file_count": row["file_count"],
                            "cpu_count": row["cpu_count"],
                            "module_count": row["module_count"],
                            "binding": row["annotation"]["binding"],
                            "label": row["annotation"]["label"],
                            "anomaly_type": row["annotation"]["anomaly_type"] or "",
                            "note": row["annotation"]["note"] or "",
                            "created_at": row["annotation"]["created_at"] or "",
                            "updated_at": row["annotation"]["updated_at"] or "",
                        }
                    )

        return ExportResult(
            format=fmt,
            scope=scope,
            mode=export_mode,
            file_path=str(output_path),
            file_name=filename,
            download_url=f"/api/v1/annotations/export/download?file={filename}",
            item_count=len(all_items),
        )

    def _collect_all_annotation_items(self, *, filters: AnnotationQueryFilters, export_mode: ExportMode) -> list[dict]:
        items: list[dict] = []
        page = 1
        while True:
            page_items, _ = self.query(page=page, page_size=200, filters=filters)
            items.extend([self._build_export_item(item, export_mode=export_mode) for item in page_items])
            if len(page_items) < 200:
                break
            page += 1
        return items

    def get_export_file_path(self, *, file_name: str) -> Path:
        candidate = (self.settings.annotations_dir / file_name).resolve()
        annotations_root = self.settings.annotations_dir.resolve()
        if annotations_root not in candidate.parents and candidate != annotations_root:
            raise AnnotationExportFileNotFoundError()
        if not candidate.exists() or not candidate.is_file():
            raise AnnotationExportFileNotFoundError()
        return candidate

    def _build_annotation_base_queries(self, *, filters: AnnotationQueryFilters):
        self._validate_range(filters=filters)
        rows_stmt = (
            select(
                Annotation.id,
                Annotation.slice_window_id,
                Annotation.source_log_file_id,
                Annotation.label,
                Annotation.anomaly_type,
                Annotation.note,
                Annotation.created_at,
                Annotation.updated_at,
                SourceLogFile.logical_path.label("binding_label"),
                SliceWindow.window_start_ts,
                SliceWindow.window_end_ts,
                SliceWindow.line_count,
                SliceWindow.file_count,
                SliceWindow.cpu_count,
                SliceWindow.module_count,
                SliceWindow.id.label("window_id"),
                SliceTask.id.label("task_id"),
                SliceTask.name.label("task_name"),
                SliceTask.package_id.label("package_id"),
                DatasetPackage.name.label("package_name"),
            )
            .join(SliceWindow, SliceWindow.id == Annotation.slice_window_id)
            .join(SliceTask, SliceTask.id == SliceWindow.slice_task_id)
            .join(DatasetPackage, DatasetPackage.id == SliceTask.package_id)
            .outerjoin(SourceLogFile, SourceLogFile.id == Annotation.source_log_file_id)
        )
        count_stmt = (
            select(func.count(Annotation.id))
            .join(SliceWindow, SliceWindow.id == Annotation.slice_window_id)
            .join(SliceTask, SliceTask.id == SliceWindow.slice_task_id)
            .join(DatasetPackage, DatasetPackage.id == SliceTask.package_id)
        )

        predicates = self._build_filter_predicates(filters=filters, include_annotation_filters=True)
        if predicates:
            rows_stmt = rows_stmt.where(and_(*predicates))
            count_stmt = count_stmt.where(and_(*predicates))
        return rows_stmt, count_stmt

    def _build_pending_base_queries(self, *, filters: AnnotationQueryFilters):
        self._validate_range(filters=filters)
        if (
            filters.min_line_count is not None
            and filters.max_line_count is not None
            and filters.min_line_count > filters.max_line_count
        ):
            raise InvalidPaginationParamsError("min_line_count cannot be greater than max_line_count")

        # Pending = leaf windows (not subdivided) with no annotation of any kind.
        no_annotation = ~(
            select(Annotation.id)
            .where(Annotation.slice_window_id == SliceWindow.id)
            .limit(1)
            .exists()
        )
        window_stmt = (
            select(
                SliceTask.package_id,
                DatasetPackage.name.label("package_name"),
                SliceTask.id.label("task_id"),
                SliceTask.name.label("task_name"),
                SliceWindow.id.label("window_id"),
                SliceWindow.window_start_ts,
                SliceWindow.window_end_ts,
                SliceWindow.line_count,
                SliceWindow.file_count,
                SliceWindow.cpu_count,
                SliceWindow.module_count,
            )
            .join(SliceTask, SliceTask.id == SliceWindow.slice_task_id)
            .join(DatasetPackage, DatasetPackage.id == SliceTask.package_id)
            .where(SliceWindow.has_children.is_(False))
            .where(no_annotation)
        )
        count_stmt = (
            select(func.count(SliceWindow.id))
            .join(SliceTask, SliceTask.id == SliceWindow.slice_task_id)
            .join(DatasetPackage, DatasetPackage.id == SliceTask.package_id)
            .where(SliceWindow.has_children.is_(False))
            .where(no_annotation)
        )
        predicates = self._build_filter_predicates(filters=filters, include_annotation_filters=False)
        if filters.min_line_count is not None:
            predicates.append(SliceWindow.line_count >= int(filters.min_line_count))
        if filters.max_line_count is not None:
            predicates.append(SliceWindow.line_count <= int(filters.max_line_count))
        if filters.keyword:
            keyword = filters.keyword.strip()
            if keyword:
                # Keep this as an EXISTS subquery so the outer count_stmt stays a
                # plain COUNT(window.id) — joining lines directly would multiply rows.
                exists_subq = (
                    select(SliceWindowLine.id)
                    .join(SourceLogLine, SourceLogLine.id == SliceWindowLine.source_line_id)
                    .where(SliceWindowLine.slice_window_id == SliceWindow.id)
                    .where(SourceLogLine.content.contains(keyword))
                    .limit(1)
                    .exists()
                )
                predicates.append(exists_subq)
        if predicates:
            window_stmt = window_stmt.where(and_(*predicates))
            count_stmt = count_stmt.where(and_(*predicates))
        return window_stmt, count_stmt

    def _build_filter_predicates(self, *, filters: AnnotationQueryFilters, include_annotation_filters: bool) -> list:
        predicates = []
        if filters.package_id is not None:
            predicates.append(SliceTask.package_id == filters.package_id)
        if filters.task_id is not None:
            predicates.append(SliceTask.id == filters.task_id)
        if include_annotation_filters and filters.label is not None:
            predicates.append(Annotation.label == filters.label)
        if include_annotation_filters and filters.anomaly_type is not None:
            predicates.append(Annotation.anomaly_type == filters.anomaly_type)
        if filters.start_ts is not None:
            predicates.append(SliceWindow.window_start_ts >= filters.start_ts)
        if filters.end_ts is not None:
            predicates.append(SliceWindow.window_end_ts <= filters.end_ts)
        return predicates

    def _build_export_item(self, row: dict, *, export_mode: ExportMode) -> dict:
        window_context = {
            "window_id": row["window_id"],
            "window_start_at": self._to_iso_datetime(row["window_start_ts"]),
            "window_end_at": self._to_iso_datetime(row["window_end_ts"]),
            "line_count": row["line_count"],
            "file_count": row["file_count"],
            "cpu_count": row["cpu_count"],
            "module_count": row["module_count"],
        }
        item = {
            "package_id": row["package_id"],
            "package_name": row["package_name"],
            "task_id": row["task_id"],
            "task_name": row["task_name"],
            "window_id": row["window_id"],
            "window_start_at": window_context["window_start_at"],
            "window_end_at": window_context["window_end_at"],
            "line_count": row["line_count"],
            "file_count": row["file_count"],
            "cpu_count": row["cpu_count"],
            "module_count": row["module_count"],
            "annotation": {
                "source_log_file_id": row.get("source_log_file_id"),
                "binding": row.get("binding_label") or "整窗口",
                "label": row["label"],
                "anomaly_type": row["anomaly_type"],
                "note": row["note"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            },
            "window_context": window_context,
        }
        if export_mode == "full":
            item["logs"] = self._load_window_logs(window_id=row["window_id"])
        return item

    def export_cases(
        self,
        *,
        anomaly_type: str | None = None,
        package_id: int | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """导出典型异常标注案例（供主系统「从标注典型案例导入」拉取）。

        每条：anomaly_type + note + 故障类型描述 + 数据包/主系统 run 溯源 + 该窗口完整日志文本。
        """
        conditions = [Annotation.label == "abnormal"]
        if anomaly_type:
            conditions.append(Annotation.anomaly_type == anomaly_type)
        if package_id:
            conditions.append(SliceTask.package_id == package_id)

        rows = self.db.execute(
            select(
                Annotation.slice_window_id,
                Annotation.anomaly_type,
                Annotation.note,
                FaultType.description,
                DatasetPackage.name,
                DatasetPackage.external_run_id,
            )
            .join(SliceWindow, SliceWindow.id == Annotation.slice_window_id)
            .join(SliceTask, SliceTask.id == SliceWindow.slice_task_id)
            .join(DatasetPackage, DatasetPackage.id == SliceTask.package_id)
            .join(FaultType, FaultType.name == Annotation.anomaly_type, isouter=True)
            .where(and_(*conditions))
            .order_by(Annotation.updated_at.desc(), Annotation.id.desc())
            .limit(limit)
        ).all()

        items: list[dict] = []
        for window_id, atype, note, ft_desc, pkg_name, ext_run_id in rows:
            files = self._load_window_logs(window_id=int(window_id))
            text_lines: list[str] = []
            for f in files:
                for ln in f["lines"]:
                    text_lines.append(str(ln["content"]))
            items.append(
                {
                    "window_id": int(window_id),
                    "anomaly_type": atype,
                    "note": note,
                    "fault_type_description": ft_desc,
                    "package_name": pkg_name,
                    "external_run_id": ext_run_id,
                    "log_text": "\n".join(text_lines),
                }
            )
        return items

    def _load_window_logs(self, *, window_id: int) -> list[dict]:
        rows = self.db.execute(
            select(
                SourceLogFile.id,
                SourceLogFile.logical_path,
                SourceLogLine.line_no,
                SourceLogLine.timestamp,
                SourceLogLine.content,
            )
            .join(SourceLogLine, SourceLogLine.source_file_id == SourceLogFile.id)
            .join(SliceWindowLine, SliceWindowLine.source_line_id == SourceLogLine.id)
            .where(SliceWindowLine.slice_window_id == window_id)
            .order_by(SourceLogFile.logical_path.asc(), SourceLogLine.line_no.asc(), SourceLogLine.id.asc())
        ).all()

        grouped: dict[tuple[int, str], list[dict]] = {}
        for source_file_id, logical_path, line_no, timestamp, content in rows:
            key = (int(source_file_id), str(logical_path))
            grouped.setdefault(key, []).append(
                {
                    "line_no": int(line_no),
                    "timestamp": self._to_iso_datetime(timestamp),
                    "content": str(content),
                }
            )

        return [
            {
                "source_file_id": source_file_id,
                "path": logical_path,
                "lines": lines,
            }
            for (source_file_id, logical_path), lines in grouped.items()
        ]

    def _to_iso_datetime(self, ts: float | int | None) -> str | None:
        if ts is None:
            return None
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()

    def _validate_business_rules(self, *, label: str, anomaly_type: str | None) -> None:
        if label == "normal":
            return
        if label == "abnormal" and anomaly_type is None:
            raise AnnotationValidationError("anomaly_type is required when label is abnormal")

    def _normalize_anomaly_type(self, *, label: str, anomaly_type: str | None) -> str | None:
        normalized = anomaly_type.strip() if anomaly_type is not None else None
        normalized = normalized or None
        self._validate_business_rules(label=label, anomaly_type=normalized)
        if label == "normal":
            return None
        # abnormal: the anomaly type must be one of the user-defined fault types.
        if normalized not in self._allowed_fault_type_names():
            raise AnnotationValidationError(
                f"anomaly_type 必须是已定义的故障类型之一: {normalized}"
            )
        return normalized

    def _allowed_fault_type_names(self) -> set[str]:
        return set(self.db.scalars(select(FaultType.name)).all())

    def _validate_page_params(self, *, page: int, page_size: int) -> None:
        if page < 1 or page_size < 1 or page_size > 200:
            raise InvalidPaginationParamsError("page/page_size out of allowed range")

    def _validate_range(self, *, filters: AnnotationQueryFilters) -> None:
        if filters.start_ts is not None and filters.end_ts is not None and filters.start_ts > filters.end_ts:
            raise InvalidPaginationParamsError("start_ts cannot be greater than end_ts")

    def _get_by_window_and_file(self, *, window_id: int, source_log_file_id: int | None) -> Annotation | None:
        stmt = select(Annotation).where(Annotation.slice_window_id == window_id)
        if source_log_file_id is None:
            stmt = stmt.where(Annotation.source_log_file_id.is_(None))
        else:
            stmt = stmt.where(Annotation.source_log_file_id == source_log_file_id)
        return self.db.scalar(stmt)

    def _validate_file_belongs_to_window(self, *, window_id: int, source_log_file_id: int | None) -> None:
        if source_log_file_id is None:
            return
        exists_stmt = (
            select(SliceWindowLine.id)
            .join(SourceLogLine, SourceLogLine.id == SliceWindowLine.source_line_id)
            .where(SliceWindowLine.slice_window_id == window_id)
            .where(SourceLogLine.source_file_id == source_log_file_id)
            .limit(1)
        )
        if self.db.scalar(exists_stmt) is None:
            raise AnnotationValidationError(
                f"source_log_file_id={source_log_file_id} 不属于该窗口"
            )
