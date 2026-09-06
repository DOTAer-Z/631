from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app.api.v1.annotations import create_annotation
from app.api.v1.packages import create_package, delete_package
from app.api.v1.slice_tasks import create_slice_task
from app.api.v1.slice_windows import list_windows
from app.core.errors import PackageNameConfirmMismatchError
from app.db.models import Annotation, DatasetPackage, ImportTask, SliceTask, SliceWindow, SliceWindowLine
from app.schemas.annotation import AnnotationCreateRequest
from app.schemas.slice_task import SliceTaskCreateRequest
from app.services.import_service import ImportService


class DummyUpload:
    def __init__(self, filename: str, content_type: str, data: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self._data = data

    async def read(self) -> bytes:
        return self._data


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        for path, content in files.items():
            zf.writestr(f"dataset/data/{path}", content)
    return buf.getvalue()


def test_package_delete_requires_name_confirm_and_cleans_full_chain(db_session, phase2_settings) -> None:
    pkg = asyncio.run(
        create_package(
            name="cleanup-pkg",
            description=None,
            file=DummyUpload(
                filename="cleanup.zip",
                content_type="application/zip",
                data=_zip_bytes({"cpu0/a.log": "1.0 a\n2.0 b\n"}),
            ),
            db=db_session,
        )
    )
    stored_path = Path(pkg.stored_path)
    assert stored_path.exists()

    ImportService(db_session).run_import_task(task_id=pkg.import_task_id)
    task = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name="cleanup-task", window_seconds=300),
        db=db_session,
    )
    windows = list_windows(
        task_id=task.id,
        page=1,
        page_size=20,
        sort_by="window_start_ts",
        sort_order="asc",
        start_ts=None,
        end_ts=None,
        db=db_session,
    )
    create_annotation(
        window_id=windows.items[0].window_id,
        payload=AnnotationCreateRequest(label="normal", anomaly_type=None, note="ok"),
        db=db_session,
    )

    with pytest.raises(PackageNameConfirmMismatchError):
        delete_package(package_id=pkg.id, package_name_confirm="wrong-name", db=db_session)

    delete_package(package_id=pkg.id, package_name_confirm="cleanup-pkg", db=db_session)

    assert db_session.scalar(select(DatasetPackage).where(DatasetPackage.id == pkg.id)) is None
    assert db_session.scalar(select(ImportTask).where(ImportTask.package_id == pkg.id)) is None
    assert db_session.scalar(select(SliceTask).where(SliceTask.package_id == pkg.id)) is None
    assert db_session.scalar(select(SliceWindow).join(SliceTask).where(SliceTask.package_id == pkg.id)) is None
    assert db_session.scalar(select(SliceWindowLine).join(SliceWindow).join(SliceTask).where(SliceTask.package_id == pkg.id)) is None
    assert db_session.scalar(select(Annotation).join(SliceWindow).join(SliceTask).where(SliceTask.package_id == pkg.id)) is None
    assert not stored_path.exists()
