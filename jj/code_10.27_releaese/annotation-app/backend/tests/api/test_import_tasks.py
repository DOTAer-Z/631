from __future__ import annotations

import asyncio
import io
import zipfile

import pytest
from fastapi import HTTPException

from app.api.v1.import_tasks import get_import_task
from app.api.v1.packages import create_package
from app.services.import_service import ImportService


class DummyUpload:
    def __init__(self, filename: str, content_type: str, data: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self._data = data

    async def read(self) -> bytes:
        return self._data


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("data/cpu0/a.log", "1.0 hello\n")
    return buf.getvalue()


def test_get_import_task_success(db_session, phase2_settings, monkeypatch) -> None:
    monkeypatch.setattr(ImportService, "start_import_task_async", lambda self, task_id, bind=None: None)

    created = asyncio.run(
        create_package(
            name="task-pkg",
            description=None,
            file=DummyUpload("task-pkg.zip", "application/zip", _zip_bytes()),
            db=db_session,
        )
    )

    task = get_import_task(task_id=created.import_task_id, db=db_session)
    assert task.id == created.import_task_id
    assert task.package_id == created.id
    assert task.status == "pending"


def test_get_import_task_404(db_session) -> None:
    with pytest.raises(HTTPException) as exc:
        get_import_task(task_id=999999, db=db_session)
    assert exc.value.status_code == 404
