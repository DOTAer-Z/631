from __future__ import annotations

import asyncio
import io
import zipfile

import pytest
from fastapi import HTTPException

from app.api.v1.packages import create_package
from app.services.import_service import ImportService
from app.api.v1.slice_tasks import (
    create_slice_task,
    delete_slice_task,
    get_slice_task,
    list_slice_tasks,
)
from app.schemas.slice_task import SliceTaskCreateRequest


class DummyUpload:
    def __init__(self, filename: str, content_type: str, data: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self._data = data

    async def read(self) -> bytes:
        return self._data


def _zip_bytes_with_logs(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        for path, content in files.items():
            zf.writestr(f"dataset/data/{path}", content)
    return buf.getvalue()


def _create_package_with_logs(*, db_session, name: str, files: dict[str, str]):
    created = asyncio.run(
        create_package(
            file=DummyUpload(
                filename=f"{name}.zip",
                content_type="application/zip",
                data=_zip_bytes_with_logs(files),
            ),
            name=name,
            description=None,
            db=db_session,
        )
    )
    ImportService(db_session).run_import_task(task_id=created.import_task_id)
    return created


def test_create_list_get_delete_slice_tasks_api(db_session, phase2_settings) -> None:
    pkg = _create_package_with_logs(
        db_session=db_session,
        name="slice-api",
        files={
            "cpu0/logs/a.log": "1.0 boot\nbroken_line\n2.0 ready\n",
            "cpu1/logs/b.log": "305.0 tick\n",
        },
    )

    created = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name="task-api"),
        db=db_session,
    )
    assert created.package_id == pkg.id
    assert created.name == "task-api"
    assert created.window_seconds == 300
    assert created.status == "success"
    assert created.windows_count >= 1
    assert created.total_windows == created.windows_count
    assert created.total_lines == 3

    listed = list_slice_tasks(package_id=pkg.id, db=db_session)
    assert listed.total == 1
    assert len(listed.items) == 1
    assert listed.items[0].id == created.id
    assert listed.items[0].windows_count == created.windows_count

    detail = get_slice_task(task_id=created.id, db=db_session)
    assert detail.id == created.id
    assert len(detail.windows) == detail.windows_count
    assert sum(window.line_count for window in detail.windows) == created.total_lines
    assert all(window.file_count >= 1 for window in detail.windows)

    delete_slice_task(task_id=created.id, db=db_session)

    with pytest.raises(HTTPException) as exc:
        get_slice_task(task_id=created.id, db=db_session)
    assert exc.value.status_code == 404


def test_create_slice_task_window_formats(db_session, phase2_settings) -> None:
    pkg = _create_package_with_logs(
        db_session=db_session,
        name="slice-window",
        files={"cpu0/logs/source.log": "0.0 a\n59.0 b\n"},
    )

    task_default = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name="default-window"),
        db=db_session,
    )
    task_text = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name="text-window", window_seconds=600),
        db=db_session,
    )
    task_numeric = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name="numeric-window", window_seconds=10),
        db=db_session,
    )

    assert task_default.window_seconds == 300
    assert task_text.window_seconds == 600
    assert task_numeric.window_seconds == 10
    assert task_default.status == "success"
    assert task_text.status == "success"
    assert task_numeric.status == "success"


def test_create_slice_task_rejects_invalid_window_and_non_imported_package(db_session, phase2_settings) -> None:
    created = asyncio.run(
        create_package(
            file=DummyUpload(
                filename="slice-invalid.zip",
                content_type="application/zip",
                data=_zip_bytes_with_logs({"cpu0/a.log": "1.0 x\n"}),
            ),
            name="slice-invalid",
            description=None,
            db=db_session,
        )
    )

    with pytest.raises(Exception):
        create_slice_task(
            package_id=created.id,
            payload=SliceTaskCreateRequest(name="not-ready"),
            db=db_session,
        )

    ImportService(db_session).run_import_task(task_id=created.import_task_id)

    with pytest.raises(Exception):
        create_slice_task(
            package_id=created.id,
            payload=SliceTaskCreateRequest(name="bad-window", window_seconds=0),
            db=db_session,
        )
