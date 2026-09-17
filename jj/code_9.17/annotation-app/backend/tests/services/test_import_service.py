from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.models import DatasetPackage, ImportTask, SourceLogFile, SourceLogLine
from app.services.archive_service import validate_archive
from app.services.import_service import ImportService
from app.services.storage_service import StorageService


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def test_import_service_create_upload_creates_package_and_task(db_session, phase2_settings) -> None:
    service = ImportService(db_session)

    pkg, task = service.create_upload(
        name="upload-service",
        archive_type="zip",
        stored_path="/tmp/storage/packages/upload-service.zip",
        file_size=1024,
        sha256="a" * 64,
        description="upload test",
    )

    pkg_row = db_session.scalar(select(DatasetPackage).where(DatasetPackage.id == pkg.id))
    task_row = db_session.scalar(select(ImportTask).where(ImportTask.id == task.id))

    assert pkg_row is not None
    assert task_row is not None
    assert pkg_row.import_status == "uploaded"
    assert task_row.package_id == pkg.id
    assert task_row.status == "pending"


def test_import_service_get_task_returns_row(db_session, phase2_settings) -> None:
    service = ImportService(db_session)
    pkg, task = service.create_upload(
        name="get-task-service",
        archive_type="tar",
        stored_path="/tmp/storage/packages/get-task-service.tar",
        file_size=2048,
        sha256="b" * 64,
        description=None,
    )

    row = service.get_task(task_id=task.id)
    assert row is not None
    assert row.id == task.id
    assert row.package_id == pkg.id


def test_import_service_run_task_success_updates_status_and_statistics(db_session, phase2_settings) -> None:
    file_bytes = _zip_bytes(
        {
            "dataset/data/cpu0/moduleA/a.log": "1.0 alpha\n2.0 beta\n",
            "dataset/data/cpu1/moduleB/b.log": "5.0 gamma\nbroken\n",
        }
    )
    validated = validate_archive("import-ok.zip", "application/zip", file_bytes)
    stored = StorageService().save_package_bytes(file_bytes, validated.extension)

    service = ImportService(db_session)
    pkg, task = service.create_upload(
        name="import-ok",
        archive_type=validated.archive_type,
        stored_path=stored.stored_path,
        file_size=stored.file_size,
        sha256=stored.sha256,
        description=None,
    )

    service.run_import_task(task_id=task.id)

    pkg_row = db_session.get(DatasetPackage, pkg.id)
    task_row = db_session.get(ImportTask, task.id)
    source_files = list(db_session.scalars(select(SourceLogFile).where(SourceLogFile.package_id == pkg.id)).all())
    source_lines = list(
        db_session.scalars(
            select(SourceLogLine).join(SourceLogFile).where(SourceLogFile.package_id == pkg.id)
        ).all()
    )

    assert pkg_row is not None
    assert task_row is not None
    assert pkg_row.import_status == "imported"
    assert task_row.status == "completed"
    assert pkg_row.source_file_count == 2
    assert pkg_row.source_line_count == 4
    assert pkg_row.cpu_count == 2
    assert pkg_row.module_count == 2
    assert pkg_row.earliest_timestamp == 1.0
    assert pkg_row.latest_timestamp == 5.0
    assert len(source_files) == 2
    assert len(source_lines) == 4
    assert Path(stored.stored_path).exists()


def test_import_service_accepts_single_child_dir_with_cpu_children_case_insensitive(db_session, phase2_settings) -> None:
    file_bytes = _zip_bytes(
        {
            "data_a/CPU0/moduleA/a.log": "1.0 alpha\n",
            "data_a/cpu1/moduleB/b.log": "2.0 beta\n",
        }
    )
    validated = validate_archive("import-case-ok.zip", "application/zip", file_bytes)
    stored = StorageService().save_package_bytes(file_bytes, validated.extension)

    service = ImportService(db_session)
    pkg, task = service.create_upload(
        name="import-case-ok",
        archive_type=validated.archive_type,
        stored_path=stored.stored_path,
        file_size=stored.file_size,
        sha256=stored.sha256,
        description=None,
    )

    service.run_import_task(task_id=task.id)

    pkg_row = db_session.get(DatasetPackage, pkg.id)
    source_files = list(db_session.scalars(select(SourceLogFile).where(SourceLogFile.package_id == pkg.id)).all())

    assert pkg_row is not None
    assert pkg_row.import_status == "imported"
    assert pkg_row.source_file_count == 2
    assert pkg_row.cpu_count == 2
    assert {row.cpu_name for row in source_files} == {"CPU0", "cpu1"}
    assert {row.logical_path for row in source_files} == {"CPU0/moduleA/a.log", "cpu1/moduleB/b.log"}


def test_import_service_run_task_failure_preserves_package_and_records_error(db_session, phase2_settings) -> None:
    file_bytes = _zip_bytes({"emptyroot/": ""})
    validated = validate_archive("import-fail.zip", "application/zip", file_bytes)
    stored = StorageService().save_package_bytes(file_bytes, validated.extension)

    service = ImportService(db_session)
    pkg, task = service.create_upload(
        name="import-fail",
        archive_type=validated.archive_type,
        stored_path=stored.stored_path,
        file_size=stored.file_size,
        sha256=stored.sha256,
        description=None,
    )

    with pytest.raises(ValueError):
        service.run_import_task(task_id=task.id)

    pkg_row = db_session.get(DatasetPackage, pkg.id)
    task_row = db_session.get(ImportTask, task.id)
    source_file_total = int(
        db_session.scalar(select(func.count(SourceLogFile.id)).where(SourceLogFile.package_id == pkg.id))
        or 0
    )

    assert pkg_row is not None
    assert task_row is not None
    assert pkg_row.import_status == "failed"
    assert pkg_row.import_error_message is not None
    assert task_row.status == "failed"
    assert task_row.error_message is not None
    assert source_file_total == 0


def test_import_service_accepts_arbitrary_top_level_folder_names(db_session, phase2_settings) -> None:
    # No "data" folder: each top-level directory is treated as a cpu (arbitrary names).
    file_bytes = _zip_bytes(
        {
            "backup/cpu0/a.log": "1.0 backup\n",
            "logs/cpu0/b.log": "2.0 logs\n",
        }
    )
    validated = validate_archive("import-arbitrary-tops.zip", "application/zip", file_bytes)
    stored = StorageService().save_package_bytes(file_bytes, validated.extension)

    service = ImportService(db_session)
    pkg, task = service.create_upload(
        name="import-arbitrary-tops",
        archive_type=validated.archive_type,
        stored_path=stored.stored_path,
        file_size=stored.file_size,
        sha256=stored.sha256,
        description=None,
    )

    service.run_import_task(task_id=task.id)

    pkg_row = db_session.get(DatasetPackage, pkg.id)
    source_files = list(db_session.scalars(select(SourceLogFile).where(SourceLogFile.package_id == pkg.id)).all())

    assert pkg_row is not None
    assert pkg_row.import_status == "imported"
    assert pkg_row.source_file_count == 2
    assert {row.cpu_name for row in source_files} == {"backup", "logs"}


def test_import_service_accepts_renamed_data_marker_and_freeform_names(db_session, phase2_settings) -> None:
    # Top folder is "data_sample" (not exactly "data"), cpu/module names are free-form,
    # and module_path is multi-level.
    file_bytes = _zip_bytes(
        {
            "data_sample/boardA/sub/mod/x.log": "1.0 alpha\n2.0 beta\n",
            "data_sample/boardB/y.log": "5.0 gamma\n",
        }
    )
    validated = validate_archive("import-data-sample.zip", "application/zip", file_bytes)
    stored = StorageService().save_package_bytes(file_bytes, validated.extension)

    service = ImportService(db_session)
    pkg, task = service.create_upload(
        name="import-data-sample",
        archive_type=validated.archive_type,
        stored_path=stored.stored_path,
        file_size=stored.file_size,
        sha256=stored.sha256,
        description=None,
    )

    service.run_import_task(task_id=task.id)

    pkg_row = db_session.get(DatasetPackage, pkg.id)
    source_files = {
        row.logical_path: row
        for row in db_session.scalars(select(SourceLogFile).where(SourceLogFile.package_id == pkg.id)).all()
    }

    assert pkg_row is not None
    assert pkg_row.import_status == "imported"
    assert pkg_row.source_file_count == 2
    assert {row.cpu_name for row in source_files.values()} == {"boardA", "boardB"}
    # Multi-level module_path preserved; cpu-direct file has empty module_path.
    assert source_files["boardA/sub/mod/x.log"].module_path == "sub/mod"
    assert source_files["boardB/y.log"].module_path == ""


def test_import_service_run_task_supports_mixed_timestamp_formats(db_session, phase2_settings) -> None:
    file_bytes = _zip_bytes(
        {
            "dataset/data/cpu0/moduleA/a.log": (
                "2018-09-29 16:12:39.365 alpha\n"
                "2026-05-23T07:49:27.684147+00:00 beta\n"
                "1717286940.0 gamma\n"
            )
        }
    )
    validated = validate_archive("import-mixed-ts.zip", "application/zip", file_bytes)
    stored = StorageService().save_package_bytes(file_bytes, validated.extension)

    service = ImportService(db_session)
    pkg, task = service.create_upload(
        name="import-mixed-ts",
        archive_type=validated.archive_type,
        stored_path=stored.stored_path,
        file_size=stored.file_size,
        sha256=stored.sha256,
        description=None,
    )

    service.run_import_task(task_id=task.id)

    pkg_row = db_session.get(DatasetPackage, pkg.id)
    source_lines = list(
        db_session.scalars(
            select(SourceLogLine).join(SourceLogFile).where(SourceLogFile.package_id == pkg.id).order_by(SourceLogLine.line_no)
        ).all()
    )

    assert pkg_row is not None
    assert pkg_row.import_status == "imported"
    assert len(source_lines) == 3
    assert all(line.timestamp is not None for line in source_lines)


def test_import_service_run_task_parses_naive_datetime_with_default_timezone(db_session, phase2_settings, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("IMPORT_DEFAULT_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))

    from app.core.config import get_settings

    get_settings.cache_clear()

    file_bytes = _zip_bytes(
        {
            "dataset/data/cpu0/moduleA/a.log": "2018-09-29 16:12:39.365 alpha\n"
        }
    )
    validated = validate_archive("import-naive-ts.zip", "application/zip", file_bytes)
    stored = StorageService().save_package_bytes(file_bytes, validated.extension)

    service = ImportService(db_session)
    pkg, task = service.create_upload(
        name="import-naive-ts",
        archive_type=validated.archive_type,
        stored_path=stored.stored_path,
        file_size=stored.file_size,
        sha256=stored.sha256,
        description=None,
    )

    service.run_import_task(task_id=task.id)

    line = db_session.scalar(
        select(SourceLogLine).join(SourceLogFile).where(SourceLogFile.package_id == pkg.id)
    )
    assert line is not None
    assert line.timestamp is not None
    assert line.timestamp == pytest.approx(1538208759.365)

    get_settings.cache_clear()
