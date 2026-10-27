from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.errors import InvalidPaginationParamsError, InvalidRelativePathError, WindowNotFoundError
from app.db.models import DatasetPackage, SliceTask, SliceWindow
from app.services.window_browser_service import WindowBrowserService, WindowListFilters


def _seed_window(db_session, phase2_settings, *, task_id: int = 1) -> SliceWindow:
    package = DatasetPackage(
        name="pkg",
        archive_type="zip",
        stored_path=str((phase2_settings.storage_root / "packages" / "a.zip")),
        file_size=10,
        sha256="a" * 64,
        description=None,
    )
    db_session.add(package)
    db_session.commit()
    db_session.refresh(package)

    task = SliceTask(
        id=task_id,
        package_id=package.id,
        name="task",
        window_seconds=60,
        status="success",
        extracted_path=str(phase2_settings.storage_root / "extracted" / "package_1" / f"task_{task_id}"),
        output_path=str(phase2_settings.storage_root / "slices" / f"task_{task_id}"),
        total_files=1,
        total_windows=1,
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    window_dir = Path(task.output_path) / "window_0_60"
    window_dir.mkdir(parents=True, exist_ok=True)
    log_path = window_dir / "cpu1/mod/a.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("1.0 hello\nbad-line\n2.0 world\n", encoding="utf-8")

    manifest_path = window_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "window_start": 0,
                "window_end": 60,
                "record_count": 2,
                "invalid_line_count": 1,
                "files": ["cpu1/mod/a.log"],
            }
        ),
        encoding="utf-8",
    )

    window = SliceWindow(
        slice_task_id=task.id,
        window_start_ts=0,
        window_end_ts=60,
        record_count=2,
        invalid_line_count=1,
        window_path=str(window_dir),
        manifest_path=str(manifest_path),
    )
    db_session.add(window)
    db_session.commit()
    db_session.refresh(window)
    return window


def test_window_list_and_detail_and_tree(db_session, phase2_settings) -> None:
    window = _seed_window(db_session, phase2_settings)
    service = WindowBrowserService(db_session)

    items, total = service.list_windows(
        task_id=window.slice_task_id,
        page=1,
        page_size=20,
        sort_by="window_start_ts",
        sort_order="asc",
        filters=WindowListFilters(),
    )
    assert total == 1
    assert len(items) == 1
    assert items[0].id == window.id

    detail = service.get_window(window_id=window.id)
    assert detail.id == window.id

    manifest = service.read_manifest(window=window)
    assert manifest.version == "1.0"
    assert manifest.invalid_line_count == 1

    tree = service.build_tree_from_manifest(manifest=manifest)
    assert len(tree) == 1
    assert tree[0].name == "cpu1"
    assert tree[0].children[0].name == "mod"
    assert tree[0].children[0].children[0].type == "file"
    assert tree[0].children[0].children[0].path == "cpu1/mod/a.log"


def test_manifest_cache_hit(db_session, phase2_settings) -> None:
    window = _seed_window(db_session, phase2_settings)
    service = WindowBrowserService(db_session)

    first = service.read_manifest(window=window)
    second = service.read_manifest(window=window)
    assert first == second


def test_log_pagination_and_parse(db_session, phase2_settings) -> None:
    window = _seed_window(db_session, phase2_settings)
    service = WindowBrowserService(db_session)

    page = service.read_logs(window=window, relative_path="cpu1/mod/a.log", offset=0, limit=2)
    assert page.total_lines == 3
    assert page.current_offset == 0
    assert page.next_offset == 2
    assert page.has_more is True
    assert len(page.lines) == 2
    assert page.lines[0].timestamp == 1.0
    assert page.lines[0].message == "hello"
    assert page.lines[1].timestamp is None

    tail = service.read_logs(window=window, relative_path="cpu1/mod/a.log", offset=2, limit=2)
    assert tail.has_more is False
    assert len(tail.lines) == 1
    assert tail.lines[0].timestamp == 2.0


def test_path_traversal_rejected(db_session, phase2_settings) -> None:
    window = _seed_window(db_session, phase2_settings)
    service = WindowBrowserService(db_session)

    with pytest.raises(InvalidRelativePathError):
        service.read_logs(window=window, relative_path="../../etc/passwd", offset=0, limit=10)


def test_invalid_pagination_rejected(db_session, phase2_settings) -> None:
    window = _seed_window(db_session, phase2_settings)
    service = WindowBrowserService(db_session)

    with pytest.raises(InvalidPaginationParamsError):
        service.read_logs(window=window, relative_path="cpu1/mod/a.log", offset=-1, limit=10)
    with pytest.raises(InvalidPaginationParamsError):
        service.read_logs(window=window, relative_path="cpu1/mod/a.log", offset=0, limit=0)
    with pytest.raises(InvalidPaginationParamsError):
        service.read_logs(window=window, relative_path="cpu1/mod/a.log", offset=0, limit=9999)


def test_window_list_invalid_ranges(db_session, phase2_settings) -> None:
    window = _seed_window(db_session, phase2_settings)
    service = WindowBrowserService(db_session)

    with pytest.raises(InvalidPaginationParamsError):
        service.list_windows(
            task_id=window.slice_task_id,
            page=1,
            page_size=20,
            sort_by="window_start_ts",
            sort_order="asc",
            filters=WindowListFilters(start_ts=100, end_ts=10),
        )

    with pytest.raises(WindowNotFoundError):
        service.list_windows(
            task_id=99999,
            page=1,
            page_size=20,
            sort_by="window_start_ts",
            sort_order="asc",
            filters=WindowListFilters(),
        )

