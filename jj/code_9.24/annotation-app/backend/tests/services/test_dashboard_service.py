from __future__ import annotations

import asyncio
import io
import zipfile

from app.api.v1.annotations import create_annotation
from app.api.v1.packages import create_package
from app.api.v1.slice_tasks import create_slice_task
from app.api.v1.slice_windows import list_windows
from app.schemas.annotation import AnnotationCreateRequest
from app.schemas.slice_task import SliceTaskCreateRequest
from app.services.dashboard_service import DashboardService
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


def _seed_data(db_session) -> None:
    first_pkg = asyncio.run(
        create_package(
            name="dash-a",
            description=None,
            file=DummyUpload(
                filename="dash-a.zip",
                content_type="application/zip",
                data=_zip_bytes({"cpu0/a.log": "1.0 a\n2.0 b\n"}),
            ),
            db=db_session,
        )
    )
    second_pkg = asyncio.run(
        create_package(
            name="dash-b",
            description=None,
            file=DummyUpload(
                filename="dash-b.zip",
                content_type="application/zip",
                data=_zip_bytes({"cpu1/a.log": "301.0 c\n"}),
            ),
            db=db_session,
        )
    )
    ImportService(db_session).run_import_task(task_id=first_pkg.import_task_id)
    ImportService(db_session).run_import_task(task_id=second_pkg.import_task_id)

    first_task = create_slice_task(
        package_id=first_pkg.id,
        payload=SliceTaskCreateRequest(name="dash-task-a", window_seconds=300),
        db=db_session,
    )
    second_task = create_slice_task(
        package_id=second_pkg.id,
        payload=SliceTaskCreateRequest(name="dash-task-b", window_seconds=300),
        db=db_session,
    )

    first_windows = list_windows(
        task_id=first_task.id,
        page=1,
        page_size=20,
        sort_by="window_start_ts",
        sort_order="asc",
        start_ts=None,
        end_ts=None,
        db=db_session,
    )
    second_windows = list_windows(
        task_id=second_task.id,
        page=1,
        page_size=20,
        sort_by="window_start_ts",
        sort_order="asc",
        start_ts=None,
        end_ts=None,
        db=db_session,
    )
    create_annotation(
        window_id=first_windows.items[0].window_id,
        payload=AnnotationCreateRequest(label="normal", anomaly_type=None, note="ok"),
        db=db_session,
    )
    create_annotation(
        window_id=second_windows.items[0].window_id,
        payload=AnnotationCreateRequest(label="abnormal", anomaly_type="cpu", note="hot"),
        db=db_session,
    )


def test_dashboard_summary_and_recent(db_session, phase2_settings) -> None:
    _seed_data(db_session)
    service = DashboardService(db_session)

    summary = service.get_summary()
    assert summary.total_packages == 2
    assert summary.total_slice_tasks == 2
    assert summary.total_windows >= 2
    assert summary.annotated_windows == 2
    assert summary.pending_windows >= 0
    assert summary.normal_count == 1
    assert summary.abnormal_count == 1

    recent_packages = service.get_recent_packages(limit=10)
    recent_tasks = service.get_recent_slice_tasks(limit=10)
    recent_annotations = service.get_recent_annotations(limit=10)

    assert len(recent_packages) == 2
    assert len(recent_tasks) == 2
    assert len(recent_annotations) == 2
    assert recent_packages[0].created_at >= recent_packages[1].created_at
    assert recent_tasks[0].created_at >= recent_tasks[1].created_at
    assert recent_annotations[0].updated_at >= recent_annotations[1].updated_at


def _make_package(db_session, *, name: str, data_kind: str):
    from app.db.models import DatasetPackage

    pkg = DatasetPackage(
        name=name,
        archive_type="zip",
        stored_path=f"/tmp/{name}.zip",
        file_size=1,
        sha256=name,
        import_status="imported",
        data_kind=data_kind,
    )
    db_session.add(pkg)
    db_session.commit()
    db_session.refresh(pkg)
    return pkg


def _make_windows(db_session, *, package, count: int, segment: bool):
    from app.db.models import SliceTask, SliceWindow

    task = SliceTask(package_id=package.id, name=f"t-{package.name}", window_seconds=300, status="success")
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    for i in range(count):
        db_session.add(
            SliceWindow(
                slice_task_id=task.id,
                window_start_ts=float(i),
                window_end_ts=float(i + 1),
                segment_title=("段%d" % i) if segment else None,
                line_count=1,
                file_count=1,
                cpu_count=1,
                module_count=0,
            )
        )
    db_session.commit()
    return task


def test_dashboard_summary_splits_by_data_kind(db_session, phase2_settings) -> None:
    struct = _make_package(db_session, name="struct-pkg", data_kind="structured")
    semi = _make_package(db_session, name="semi-pkg", data_kind="semi_structured")
    unstruct = _make_package(db_session, name="unstruct-pkg", data_kind="unstructured")
    _make_windows(db_session, package=struct, count=2, segment=False)
    _make_windows(db_session, package=semi, count=3, segment=False)
    _make_windows(db_session, package=unstruct, count=4, segment=True)

    summary = DashboardService(db_session).get_summary()
    assert summary.total_packages == 3
    assert summary.structured_packages == 1
    assert summary.semi_structured_packages == 1
    assert summary.unstructured_packages == 1
    assert summary.total_windows == 9
    assert summary.structured_windows == 2
    assert summary.semi_structured_windows == 3
    assert summary.unstructured_windows == 4


def test_dashboard_empty_state(db_session, phase2_settings) -> None:
    service = DashboardService(db_session)
    summary = service.get_summary()
    assert summary.total_packages == 0
    assert summary.total_slice_tasks == 0
    assert summary.total_windows == 0
    assert summary.annotated_windows == 0
    assert summary.pending_windows == 0
    assert summary.normal_count == 0
    assert summary.abnormal_count == 0
    assert summary.structured_packages == 0
    assert summary.semi_structured_packages == 0
    assert summary.unstructured_packages == 0
    assert summary.structured_windows == 0
    assert summary.semi_structured_windows == 0
    assert summary.unstructured_windows == 0
    assert service.get_recent_packages(limit=5) == []
    assert service.get_recent_slice_tasks(limit=5) == []
    assert service.get_recent_annotations(limit=5) == []
