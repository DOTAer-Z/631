from __future__ import annotations

import asyncio
import io
import zipfile

from app.api.v1.annotations import create_annotation
from app.api.v1.dashboard import (
    get_dashboard_recent_annotations,
    get_dashboard_recent_packages,
    get_dashboard_recent_slice_tasks,
    get_dashboard_summary,
)
from app.api.v1.packages import create_package
from app.api.v1.slice_tasks import create_slice_task
from app.api.v1.slice_windows import list_windows
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


def _seed_dashboard(db_session) -> None:
    pkg = asyncio.run(
        create_package(
            name="dash-api",
            description=None,
            file=DummyUpload(
                filename="dash-api.zip",
                content_type="application/zip",
                data=_zip_bytes({"cpu0/a.log": "1.0 a\n2.0 b\n"}),
            ),
            db=db_session,
        )
    )
    ImportService(db_session).run_import_task(task_id=pkg.import_task_id)
    task = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name="dash-api-task", window_seconds=300),
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


def test_dashboard_api_summary_and_recent(db_session, phase2_settings) -> None:
    _seed_dashboard(db_session)

    summary = get_dashboard_summary(db=db_session)
    assert summary.total_packages == 1
    assert summary.total_slice_tasks == 1
    assert summary.total_windows >= 1

    recent_packages = get_dashboard_recent_packages(limit=5, db=db_session)
    recent_tasks = get_dashboard_recent_slice_tasks(limit=5, db=db_session)
    recent_annotations = get_dashboard_recent_annotations(limit=5, db=db_session)

    assert len(recent_packages.items) == 1
    assert len(recent_tasks.items) == 1
    assert len(recent_annotations.items) == 1


def test_dashboard_api_empty_state(db_session, phase2_settings) -> None:
    summary = get_dashboard_summary(db=db_session)
    assert summary.total_packages == 0
    assert get_dashboard_recent_packages(limit=5, db=db_session).items == []
    assert get_dashboard_recent_slice_tasks(limit=5, db=db_session).items == []
    assert get_dashboard_recent_annotations(limit=5, db=db_session).items == []
