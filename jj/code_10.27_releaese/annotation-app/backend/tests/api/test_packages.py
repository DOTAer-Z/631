from __future__ import annotations

import asyncio
import io
import tarfile
import zipfile

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.v1.packages import create_package, delete_package, get_package, list_packages, router, update_package
from app.core.errors import FileTooLargeError, InvalidArchiveTypeError
from app.db.models import DatasetPackage, ImportTask
from app.schemas.package import PackageUpdateRequest
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
        zf.writestr("a.txt", "hello")
    return buf.getvalue()


def _tar_bytes() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        data = b"hello"
        info = tarfile.TarInfo(name="a.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _targz_bytes() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"hello"
        info = tarfile.TarInfo(name="a.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _upload(filename: str, content_type: str, data: bytes) -> DummyUpload:
    return DummyUpload(filename=filename, content_type=content_type, data=data)


def _create_package(name: str, filename: str, content_type: str, data: bytes, db_session, description: str | None = None):
    return asyncio.run(
        create_package(
            file=_upload(filename, content_type, data),
            name=name,
            description=description,
            db=db_session,
        )
    )


def test_upload_zip_tar_targz_success(db_session, phase2_settings, monkeypatch) -> None:
    monkeypatch.setattr(ImportService, "start_import_task_async", lambda self, task_id, bind=None: None)

    created_zip = _create_package("pkg-zip", "dataset.zip", "application/zip", _zip_bytes(), db_session, "zip package")
    created_tar = _create_package("pkg-tar", "dataset.tar", "application/x-tar", _tar_bytes(), db_session)
    created_targz = _create_package("pkg-targz", "dataset.tar.gz", "application/gzip", _targz_bytes(), db_session)

    assert created_zip.archive_type == "zip"
    assert created_tar.archive_type == "tar"
    assert created_targz.archive_type == "tar.gz"
    assert created_zip.file_size > 0
    assert len(created_zip.sha256) == 64
    assert created_zip.slice_task_count == 0
    assert created_zip.import_status == "uploaded"
    assert created_zip.import_task_id > 0

    pkg_row = db_session.scalar(select(DatasetPackage).where(DatasetPackage.id == created_zip.id))
    task_row = db_session.scalar(select(ImportTask).where(ImportTask.id == created_zip.import_task_id))
    assert pkg_row is not None
    assert task_row is not None
    assert pkg_row.import_status == "uploaded"
    assert task_row.status == "pending"


def test_invalid_archive_rejected(db_session) -> None:
    with pytest.raises(InvalidArchiveTypeError):
        _create_package("invalid", "dataset.txt", "text/plain", b"not-archive", db_session)


def test_too_large_archive_rejected(db_session, phase2_settings) -> None:
    with pytest.raises(FileTooLargeError):
        _create_package("too-big", "dataset.zip", "application/zip", b"x" * (11 * 1024 * 1024), db_session)


def test_upload_query_update_delete_flow(db_session, monkeypatch) -> None:
    monkeypatch.setattr(ImportService, "start_import_task_async", lambda self, task_id, bind=None: None)

    created = _create_package("city-data", "city.zip", "application/zip", _zip_bytes(), db_session, "first description")
    package_id = created.id

    page = list_packages(
        page=1,
        page_size=10,
        query="city",
        sort_by="created_at",
        sort_order="desc",
        db=db_session,
    )
    assert page.total == 1
    assert len(page.items) == 1
    assert page.items[0].name == "city-data"

    detail = get_package(package_id=package_id, db=db_session)
    assert detail.id == package_id
    assert detail.description == "first description"

    updated = update_package(
        package_id=package_id,
        payload=PackageUpdateRequest(description="updated"),
        db=db_session,
    )
    assert updated.description == "updated"

    delete_package(package_id=package_id, package_name_confirm="city-data", db=db_session)
    with pytest.raises(HTTPException) as exc:
        get_package(package_id=package_id, db=db_session)
    assert exc.value.status_code == 404


def test_create_package_declares_accepted_status_code() -> None:
    post_route = next(route for route in router.routes if route.path == "/packages" and "POST" in route.methods)
    assert post_route.status_code == 202


def test_list_packages_marks_abnormal(db_session, phase2_settings) -> None:
    from app.api.v1.annotations import create_annotation
    from app.api.v1.slice_tasks import create_slice_task
    from app.api.v1.slice_windows import list_windows
    from app.schemas.annotation import AnnotationCreateRequest
    from app.schemas.slice_task import SliceTaskCreateRequest

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("dataset/data/cpu1/a.log", "1.0 x\n2.0 y\n")
    pkg = asyncio.run(
        create_package(
            file=_upload("abn.zip", "application/zip", buf.getvalue()),
            name="abn-pkg",
            description=None,
            db=db_session,
        )
    )
    ImportService(db_session).run_import_task(task_id=pkg.import_task_id)
    task = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name="abn-task", window_seconds=300),
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

    # No annotations yet -> no abnormal mark.
    before = get_package(package_id=pkg.id, db=db_session)
    assert before.has_abnormal is False

    create_annotation(
        window_id=windows.items[0].window_id,
        payload=AnnotationCreateRequest(label="abnormal", anomaly_type="softlockup", note="bad"),
        db=db_session,
    )

    after = get_package(package_id=pkg.id, db=db_session)
    assert after.has_abnormal is True

    listed = list_packages(
        page=1, page_size=10, query=None, sort_by="created_at", sort_order="desc", db=db_session
    )
    target = next(item for item in listed.items if item.id == pkg.id)
    assert target.has_abnormal is True

