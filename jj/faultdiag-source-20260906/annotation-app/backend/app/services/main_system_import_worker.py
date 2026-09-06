from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DatasetPackage, ImportTask
from app.services.import_worker import ImportRunResult, ImportWorker
from app.services.main_system_client import MainSystemClient

logger = logging.getLogger(__name__)


class MainSystemImportWorker:
    """把主系统某 run 的原始日志（经导出接口拉取）落库为标注子系统的
    source_log_files / source_log_lines，绕过压缩包解压。

    复用 ImportWorker 的批量插入 / 规则时间戳解析 / 状态标记；导入完成后的
    「切片 → 标注」流程与普通上传包完全一致。
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        # 复用 ImportWorker 的 _mark_* / _bulk_insert_* / _parse_source_line
        self._iw = ImportWorker(db)

    def run(self, *, import_task_id: int, run_id: str) -> ImportTask:
        task = self.db.get(ImportTask, import_task_id)
        if task is None:
            raise ValueError("import task not found")
        pkg = self.db.get(DatasetPackage, task.package_id)
        if pkg is None:
            raise ValueError("dataset package not found")

        self._iw._mark_running(task=task, pkg=pkg)
        try:
            export = MainSystemClient(self.settings).fetch_export(run_id)
            result = self._ingest_export(pkg=pkg, export=export)
            self._iw._mark_completed(task=task, pkg=pkg, result=result)
            return task
        except Exception as exc:  # noqa: BLE001
            self._iw._mark_failed(task=task, pkg=pkg, message=f"从主系统导入失败: {exc}")
            raise

    def _ingest_export(self, *, pkg: DatasetPackage, export: dict) -> ImportRunResult:
        lines = export.get("lines") or []
        if not lines:
            raise ValueError("主系统该 run 无日志行可导入")

        # 按 file_path 分组为多个 source_file（保持出现顺序）
        groups: dict[str, list[dict]] = {}
        order: list[str] = []
        for ln in lines:
            fp = ln.get("file_path") or export.get("filename") or "main-system.log"
            if fp not in groups:
                groups[fp] = []
                order.append(fp)
            groups[fp].append(ln)

        cpu_name = "main-system"
        source_file_rows: list[dict] = []
        line_rows_by_file: list[list[dict]] = []
        module_paths: set[str] = set()
        source_line_count = 0
        earliest_ts: float | None = None
        latest_ts: float | None = None

        for fp in order:
            norm = str(fp).replace("\\", "/").strip("/")
            parts = norm.split("/") if norm else []
            filename = parts[-1] if parts else "main-system.log"
            module_path = "/".join(parts[:-1]) if len(parts) > 1 else cpu_name
            module_paths.add(module_path)

            row_lines: list[dict] = []
            file_earliest: float | None = None
            file_latest: float | None = None
            for idx, ln in enumerate(groups[fp], start=1):
                ts = self._to_epoch(ln.get("ts"), ln.get("content") or "")
                content = str(ln.get("content") or "")
                row_lines.append({"line_no": idx, "timestamp": ts, "content": content})
                source_line_count += 1
                if ts is not None:
                    file_earliest = ts if file_earliest is None else min(file_earliest, ts)
                    file_latest = ts if file_latest is None else max(file_latest, ts)
                    earliest_ts = ts if earliest_ts is None else min(earliest_ts, ts)
                    latest_ts = ts if latest_ts is None else max(latest_ts, ts)

            source_file_rows.append({
                "package_id": pkg.id,
                "cpu_name": cpu_name,
                "module_path": module_path,
                "filename": filename,
                "logical_path": norm or filename,
                "line_count": len(row_lines),
                "earliest_timestamp": file_earliest,
                "latest_timestamp": file_latest,
            })
            line_rows_by_file.append(row_lines)

        inserted_ids = self._iw._bulk_insert_source_files(source_file_rows)
        self._iw._bulk_insert_source_lines(
            inserted_file_ids=inserted_ids, line_rows_by_file=line_rows_by_file
        )
        # 提交由随后的 _mark_completed 统一完成（与 ImportWorker.run 一致）

        return ImportRunResult(
            source_file_count=len(source_file_rows),
            source_line_count=source_line_count,
            cpu_count=1,
            module_count=len(module_paths),
            earliest_timestamp=earliest_ts,
            latest_timestamp=latest_ts,
        )

    def _to_epoch(self, ts_str, content: str) -> float | None:
        """主系统导出的 ts 为 ISO 字符串（或 None）。转 UTC epoch float；
        无法解析时回退用规则解析器从原文提取（epoch/ISO/strptime，Asia/Shanghai 兜底）。"""
        if ts_str:
            try:
                s = str(ts_str)
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=ZoneInfo(self.settings.import_default_timezone))
                return dt.astimezone(timezone.utc).timestamp()
            except Exception:
                pass
        try:
            ts, _ = self._iw._parse_source_line(content)
            return ts
        except Exception:
            return None
