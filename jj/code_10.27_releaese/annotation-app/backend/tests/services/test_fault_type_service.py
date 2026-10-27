from __future__ import annotations

import asyncio
import io
import zipfile

import pytest

from app.api.v1.packages import create_package
from app.api.v1.slice_tasks import create_slice_task
from app.api.v1.slice_windows import list_windows
from app.core.errors import FaultTypeNameConflictError, FaultTypeNotFoundError
from app.db.models import Annotation
from app.schemas.annotation import AnnotationCreateRequest
from app.schemas.fault_type import FaultTypeCreateRequest, FaultTypeUpdateRequest
from app.schemas.slice_task import SliceTaskCreateRequest
from app.services.annotation_service import AnnotationService
from app.services.fault_type_service import FaultTypeService
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


def test_fault_type_crud_and_conflict(db_session, phase2_settings) -> None:
    service = FaultTypeService(db_session)
    seed_count = len(service.list_all())

    created = service.create(
        payload=FaultTypeCreateRequest(name="新故障X", description="自定义说明")
    )
    assert created.id > 0
    assert created.name == "新故障X"
    assert created.description == "自定义说明"

    listed = service.list_all()
    assert len(listed) == seed_count + 1

    fetched = service.get(fault_type_id=created.id)
    assert fetched.name == "新故障X"

    updated = service.update(
        fault_type_id=created.id,
        payload=FaultTypeUpdateRequest(name="新故障Y", description="改后"),
    )
    assert updated.name == "新故障Y"
    assert updated.description == "改后"

    # Updating to an existing name conflicts.
    other = service.create(payload=FaultTypeCreateRequest(name="另一个", description="x"))
    with pytest.raises(FaultTypeNameConflictError):
        service.update(
            fault_type_id=other.id,
            payload=FaultTypeUpdateRequest(name="新故障Y", description="dup"),
        )

    # Creating with an existing name conflicts.
    with pytest.raises(FaultTypeNameConflictError):
        service.create(payload=FaultTypeCreateRequest(name="新故障Y", description="dup"))


def test_fault_type_get_missing(db_session, phase2_settings) -> None:
    service = FaultTypeService(db_session)
    with pytest.raises(FaultTypeNotFoundError):
        service.get(fault_type_id=999_999)


def test_fault_type_delete_returns_referenced_count(db_session, phase2_settings) -> None:
    async def _prep():
        pkg = await create_package(
            name="ft-del",
            description=None,
            file=DummyUpload(
                filename="ft-del.zip",
                content_type="application/zip",
                data=_zip_bytes({"cpu0/a.log": "1.0 a\n2.0 b\n301.0 c\n601.0 d\n"}),
            ),
            db=db_session,
        )
        ImportService(db_session).run_import_task(task_id=pkg.import_task_id)
        task = create_slice_task(
            package_id=pkg.id,
            payload=SliceTaskCreateRequest(name="ft-del-task", window_seconds=300),
            db=db_session,
        )
        windows = list_windows(
            task_id=task.id,
            page=1, page_size=20,
            sort_by="window_start_ts", sort_order="asc",
            start_ts=None, end_ts=None,
            db=db_session,
        )
        return [w.window_id for w in windows.items]

    window_ids = asyncio.run(_prep())
    service = FaultTypeService(db_session)
    target = service.create(payload=FaultTypeCreateRequest(name="待删除-X", description="d"))

    # Annotate two windows with the to-be-deleted fault type.
    ann_service = AnnotationService(db_session)
    for wid in window_ids[:2]:
        ann_service.save_for_window(
            window_id=wid,
            payload=AnnotationCreateRequest(label="abnormal", anomaly_type="待删除-X", note="x"),
        )

    deleted_name, count = service.delete(fault_type_id=target.id)
    assert deleted_name == "待删除-X"
    assert count == 2
    # Historical annotations keep the original string value.
    rows = db_session.query(Annotation).filter(Annotation.anomaly_type == "待删除-X").all()
    assert len(rows) == 2
