from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DatasetPackage, ImportTask, SourceLogFile, SourceLogLine
from app.services.archive_service import find_data_root
from app.services.llm import get_llm_client
from app.services.llm.timestamp_inference import TimestampFormatSpec, apply_spec, infer_format
from app.services.slice_engine.extractor import extract_package_archive
from app.services.unstructured import classify_file, extract_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImportRunResult:
    source_file_count: int
    source_line_count: int
    cpu_count: int
    module_count: int
    earliest_timestamp: float | None
    latest_timestamp: float | None


@dataclass
class _SourceFile:
    cpu_name: str
    module_path: str
    filename: str
    logical_path: str
    lines: list[str]


class ImportWorker:
    def __init__(self, db: Session, llm_client=None) -> None:
        self.db = db
        self.settings = get_settings()
        self._llm = llm_client if llm_client is not None else get_llm_client()

    def run(self, *, import_task_id: int) -> ImportTask:
        task = self.db.get(ImportTask, import_task_id)
        if task is None:
            raise ValueError("import task not found")

        pkg = self.db.get(DatasetPackage, task.package_id)
        if pkg is None:
            raise ValueError("dataset package not found")

        extracted_path: Path | None = None
        self._mark_running(task=task, pkg=pkg)
        try:
            extracted = extract_package_archive(
                package_id=pkg.id,
                task_id=task.id,
                stored_path=pkg.stored_path,
            )
            extracted_path = Path(extracted.extracted_path)
            result = self._import_extracted_data(pkg=pkg, extracted_root=extracted_path)
            self._mark_completed(task=task, pkg=pkg, result=result)
            return task
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(task=task, pkg=pkg, message=str(exc))
            raise
        finally:
            if extracted_path is not None and extracted_path.exists():
                shutil.rmtree(extracted_path, ignore_errors=True)

    def _import_extracted_data(self, *, pkg: DatasetPackage, extracted_root: Path) -> ImportRunResult:
        data_root = find_data_root(extracted_root)

        # 导入阶段自动区别半结构化日志 / 非结构化报告:非结构化(txt含`## `/md/pdf 报告)走语义段链路,
        # 其余(时间序列日志)保持原时间窗口切片链路不变,data_kind 记为 semi_structured。
        is_unstructured, report_files = self._classify_package(data_root=data_root)
        if is_unstructured:
            return self._import_unstructured(pkg=pkg, data_root=data_root, report_files=report_files)
        return self._import_structured(pkg=pkg, data_root=data_root)

    def _classify_package(self, *, data_root: Path) -> tuple[bool, list[Path]]:
        """扫描 data_root,判定包为 semi_structured / unstructured,并返回非结构化报告文件列表。

        逐文件规则见 unstructured.detector.classify_file;包级规则:非结构化文件数 > 0 且
        ≥ 结构化文件数 → 整包按非结构化处理(只导入报告文件)。
        """
        report_files: list[Path] = []
        structured_count = 0
        unstructured_count = 0
        for file_path in sorted(data_root.rglob("*")):
            if not file_path.is_file():
                continue
            if self._is_excluded_meta(file_path, data_root):
                continue
            head = self._read_head_text(file_path)
            if classify_file(file_path.name, head) == "unstructured":
                unstructured_count += 1
                report_files.append(file_path)
            else:
                structured_count += 1
        is_unstructured = unstructured_count > 0 and unstructured_count >= structured_count
        return is_unstructured, report_files

    def _read_head_text(self, file_path: Path) -> str:
        """读取文件开头若干文本,供 detector 判定;仅对文本扩展名读取,其余返回空串。"""
        if file_path.suffix.lower() not in {".txt", ".md"}:
            return ""
        try:
            with file_path.open("r", encoding="utf-8", errors="replace") as handle:
                return handle.read(8192)
        except OSError:
            return ""

    def _import_unstructured(
        self, *, pkg: DatasetPackage, data_root: Path, report_files: list[Path]
    ) -> ImportRunResult:
        """导入非结构化报告:每份报告一条 source_log_file,完整正文逐行落库(时间戳全 None)。

        切分为语义段发生在切片阶段(slice_engine),此处只保存原始文件名与完整正文。
        """
        source_file_rows: list[dict] = []
        source_line_rows_by_file: list[list[dict]] = []
        source_line_count = 0

        for file_path in report_files:
            rel_path = file_path.relative_to(data_root)
            text = extract_text(file_path)
            line_rows = [
                {"line_no": idx, "timestamp": None, "content": raw_line}
                for idx, raw_line in enumerate(text.splitlines(), start=1)
            ]
            source_line_count += len(line_rows)
            source_file_rows.append(
                {
                    "package_id": pkg.id,
                    # 报告号(去扩展名的文件名)作为 cpu_name,保证列表/浏览有可读标识。
                    "cpu_name": file_path.stem,
                    "module_path": "",
                    "filename": file_path.name,
                    "logical_path": rel_path.as_posix(),
                    "data_kind": "unstructured",
                    "line_count": len(line_rows),
                    "earliest_timestamp": None,
                    "latest_timestamp": None,
                }
            )
            source_line_rows_by_file.append(line_rows)

        if not source_file_rows:
            raise ValueError("no unstructured report files found under data directory")

        inserted_file_ids = self._bulk_insert_source_files(source_file_rows)
        self._bulk_insert_source_lines(
            inserted_file_ids=inserted_file_ids, line_rows_by_file=source_line_rows_by_file
        )

        pkg.data_kind = "unstructured"
        return ImportRunResult(
            source_file_count=len(source_file_rows),
            source_line_count=source_line_count,
            cpu_count=len(source_file_rows),
            module_count=0,
            earliest_timestamp=None,
            latest_timestamp=None,
        )

    def _import_structured(self, *, pkg: DatasetPackage, data_root: Path) -> ImportRunResult:
        source_files = self._read_source_files(data_root=data_root)
        if not source_files:
            raise ValueError("no source log files found under data directory")

        # Adaptive timestamp parsing: infer a format spec per cpu, converging to a
        # package-wide spec once enough cpus agree. Empty when LLM is unavailable.
        spec_by_cpu = self._infer_specs_by_cpu(source_files)

        source_file_rows: list[dict] = []
        source_line_rows_by_file: list[list[dict]] = []
        cpu_names: set[str] = set()
        module_paths: set[str] = set()
        source_line_count = 0
        earliest_timestamp: float | None = None
        latest_timestamp: float | None = None

        for source_file in source_files:
            cpu_names.add(source_file.cpu_name)
            module_paths.add(source_file.module_path)
            spec = spec_by_cpu.get(source_file.cpu_name)

            line_rows: list[dict] = []
            file_earliest: float | None = None
            file_latest: float | None = None

            for idx, raw_line in enumerate(source_file.lines, start=1):
                timestamp, content = self._parse_with_spec(spec=spec, raw_line=raw_line)
                line_rows.append({"line_no": idx, "timestamp": timestamp, "content": content})
                source_line_count += 1

                if timestamp is not None:
                    file_earliest = timestamp if file_earliest is None else min(file_earliest, timestamp)
                    file_latest = timestamp if file_latest is None else max(file_latest, timestamp)
                    earliest_timestamp = timestamp if earliest_timestamp is None else min(earliest_timestamp, timestamp)
                    latest_timestamp = timestamp if latest_timestamp is None else max(latest_timestamp, timestamp)

            source_file_rows.append(
                {
                    "package_id": pkg.id,
                    "cpu_name": source_file.cpu_name,
                    "module_path": source_file.module_path,
                    "filename": source_file.filename,
                    "logical_path": source_file.logical_path,
                    "data_kind": "semi_structured",
                    "line_count": len(line_rows),
                    "earliest_timestamp": file_earliest,
                    "latest_timestamp": file_latest,
                }
            )
            source_line_rows_by_file.append(line_rows)

        inserted_file_ids = self._bulk_insert_source_files(source_file_rows)
        self._bulk_insert_source_lines(inserted_file_ids=inserted_file_ids, line_rows_by_file=source_line_rows_by_file)

        pkg.data_kind = "semi_structured"
        return ImportRunResult(
            source_file_count=len(source_file_rows),
            source_line_count=source_line_count,
            cpu_count=len(cpu_names),
            module_count=len(module_paths),
            earliest_timestamp=earliest_timestamp,
            latest_timestamp=latest_timestamp,
        )

    def _read_source_files(self, *, data_root: Path) -> list[_SourceFile]:
        files: list[_SourceFile] = []
        for file_path in sorted(data_root.rglob("*")):
            if not file_path.is_file():
                continue
            if self._is_not_source_file(file_path, data_root):
                continue

            rel_path = file_path.relative_to(data_root)
            parts = rel_path.parts
            cpu_name = parts[0] if len(parts) >= 1 else ""
            filename = file_path.name
            module_path = "/".join(parts[1:-1]) if len(parts) > 2 else ""
            logical_path = rel_path.as_posix()

            with file_path.open("r", encoding="utf-8", errors="replace") as handle:
                lines = [raw_line.rstrip("\n") for raw_line in handle]

            files.append(
                _SourceFile(
                    cpu_name=cpu_name,
                    module_path=module_path,
                    filename=filename,
                    logical_path=logical_path,
                    lines=lines,
                )
            )
        return files

    def _is_excluded_meta(self, file_path: Path, data_root: Path) -> bool:
        """排除平台元数据 / 打包残留(与二进制无关的那部分判定)。

        主系统数据集里夹带的 fip_info.data / ground_truth.json 是校验用的元数据,
        macOS 打包会混入 __MACOSX/ 与 ._* 资源分支,均不属于标注需要解析的内容。
        非结构化分类扫描复用此判定(需保留 pdf 等二进制报告,故不能带 NUL 排除)。
        """
        rel = file_path.relative_to(data_root)
        if "__MACOSX" in rel.parts:
            return True
        if file_path.name.startswith("._"):
            return True
        if file_path.name in {"fip_info.data", "ground_truth.json"}:
            return True
        return False

    def _is_not_source_file(self, file_path: Path, data_root: Path) -> bool:
        """排除非日志源文件，避免把平台元数据/打包残留/二进制文件误当日志解析。

        在元数据排除之外再排除二进制文件（含 NUL 字节）:若不排除，PostgreSQL TEXT 列会
        直接拒绝写入（psycopg.DataError），导致整包导入失败。NUL 是文本编码里不会出现的
        字节，用它作二进制判定在 utf-8/latin-1 下都安全。（结构化路径专用。）
        """
        if self._is_excluded_meta(file_path, data_root):
            return True
        with file_path.open("rb") as handle:
            if b"\x00" in handle.read(4096):
                return True
        return False

    def _infer_specs_by_cpu(self, source_files: list[_SourceFile]) -> dict[str, TimestampFormatSpec]:
        """Per-cpu inference with 3-hit convergence.

        Iterate cpus in order; once any spec reaches the convergence threshold it is
        frozen as the package-wide spec and remaining cpus reuse it (no more LLM calls).
        Returns {} (rule-based for everyone) when no LLM is configured.
        """
        if self._llm is None or not self._llm.is_configured():
            return {}

        cpu_names = sorted({source_file.cpu_name for source_file in source_files})
        threshold = self.settings.timestamp_convergence_threshold
        result: dict[str, TimestampFormatSpec] = {}
        counts: dict[tuple, int] = {}
        unified: TimestampFormatSpec | None = None

        for cpu_name in cpu_names:
            if unified is not None:
                result[cpu_name] = unified
                continue

            samples = self._sample_cpu_lines(source_files, cpu_name)
            spec = infer_format(self._llm, samples) if samples else None
            if spec is None:
                continue

            result[cpu_name] = spec
            key = spec.normalized_key()
            counts[key] = counts.get(key, 0) + 1
            if counts[key] >= threshold:
                unified = spec
                logger.info("timestamp format converged to package-wide spec after %d cpus", counts[key])

        return result

    def _sample_cpu_lines(self, source_files: list[_SourceFile], cpu_name: str) -> list[str]:
        per_file = self.settings.sample_lines_per_file
        cap = self.settings.sample_lines_per_cpu_cap

        buckets: list[list[str]] = []
        for source_file in source_files:
            if source_file.cpu_name != cpu_name:
                continue
            non_empty = [line for line in source_file.lines if line.strip()][:per_file]
            if non_empty:
                buckets.append(non_empty)

        samples: list[str] = []
        index = 0
        while len(samples) < cap and any(index < len(bucket) for bucket in buckets):
            for bucket in buckets:
                if index < len(bucket):
                    samples.append(bucket[index])
                    if len(samples) >= cap:
                        break
            index += 1
        return samples

    def _parse_with_spec(
        self, *, spec: TimestampFormatSpec | None, raw_line: str
    ) -> tuple[float | None, str]:
        if spec is not None:
            timestamp, content = apply_spec(spec, raw_line)
            if timestamp is not None:
                return timestamp, content
        # Fall back to rule-based parsing for lines the spec can't handle.
        return self._parse_source_line(raw_line)

    def _bulk_insert_source_files(self, rows: list[dict]) -> list[int]:
        stmt = insert(SourceLogFile).returning(SourceLogFile.id)
        inserted_ids = list(self.db.execute(stmt, rows).scalars().all())
        return inserted_ids

    def _bulk_insert_source_lines(self, *, inserted_file_ids: list[int], line_rows_by_file: list[list[dict]]) -> None:
        payload: list[dict] = []
        batch_size = self.settings.import_line_batch_size

        for source_file_id, line_rows in zip(inserted_file_ids, line_rows_by_file, strict=True):
            for row in line_rows:
                payload.append(
                    {
                        "source_file_id": source_file_id,
                        "line_no": row["line_no"],
                        "timestamp": row["timestamp"],
                        "content": row["content"],
                    }
                )
                if len(payload) >= batch_size:
                    self.db.execute(insert(SourceLogLine), payload)
                    payload.clear()

        if payload:
            self.db.execute(insert(SourceLogLine), payload)

    def _parse_source_line(self, raw_line: str) -> tuple[float | None, str]:
        stripped = raw_line.strip()
        if not stripped:
            return None, ""

        candidate_values = self._candidate_timestamp_tokens(stripped)
        timestamp = None
        for candidate in candidate_values:
            timestamp = self._parse_timestamp(candidate)
            if timestamp is not None:
                break
        return timestamp, stripped

    def _candidate_timestamp_tokens(self, stripped: str) -> list[str]:
        parts = stripped.split()
        if not parts:
            return []
        candidates = [parts[0]]
        if len(parts) >= 2:
            candidates.append(f"{parts[0]} {parts[1]}")
        return candidates

    def _parse_timestamp(self, raw_value: str) -> float | None:
        value = raw_value.strip()
        if not value:
            return None

        try:
            return float(value)
        except ValueError:
            pass

        normalized = value
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"

        if "T" in normalized or " " in normalized:
            try:
                dt = datetime.fromisoformat(normalized)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=ZoneInfo(self.settings.import_default_timezone))
                return dt.astimezone(timezone.utc).timestamp()
            except ValueError:
                pass

        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S.%f",
            "%Y/%m/%d %H:%M:%S",
        ):
            try:
                dt = datetime.strptime(value, fmt).replace(
                    tzinfo=ZoneInfo(self.settings.import_default_timezone)
                )
                return dt.astimezone(timezone.utc).timestamp()
            except ValueError:
                continue

        return None

    def _mark_running(self, *, task: ImportTask, pkg: DatasetPackage) -> None:
        now = datetime.now(timezone.utc)
        task.status = "running"
        task.error_message = None
        task.started_at = now
        task.finished_at = None
        pkg.import_status = "importing"
        pkg.import_error_message = None
        pkg.import_started_at = now
        pkg.import_finished_at = None
        self.db.add_all([task, pkg])
        self.db.commit()
        self.db.refresh(task)
        self.db.refresh(pkg)

    def _mark_completed(self, *, task: ImportTask, pkg: DatasetPackage, result: ImportRunResult) -> None:
        now = datetime.now(timezone.utc)
        task.status = "completed"
        task.error_message = None
        task.finished_at = now

        pkg.import_status = "imported"
        pkg.import_error_message = None
        pkg.import_finished_at = now
        pkg.source_file_count = result.source_file_count
        pkg.source_line_count = result.source_line_count
        pkg.cpu_count = result.cpu_count
        pkg.module_count = result.module_count
        pkg.earliest_timestamp = result.earliest_timestamp
        pkg.latest_timestamp = result.latest_timestamp

        self.db.add_all([task, pkg])
        self.db.commit()
        self.db.refresh(task)
        self.db.refresh(pkg)

    def _mark_failed(self, *, task: ImportTask, pkg: DatasetPackage, message: str) -> None:
        now = datetime.now(timezone.utc)
        error_message = message[:10000] if message else "import failed"
        task.status = "failed"
        task.error_message = error_message
        task.finished_at = now

        pkg.import_status = "failed"
        pkg.import_error_message = error_message
        pkg.import_finished_at = now

        self.db.add_all([task, pkg])
        self.db.commit()
        self.db.refresh(task)
        self.db.refresh(pkg)
