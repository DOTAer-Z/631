from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path

import pytest

from app.api.v1.packages import create_package
from app.api.v1.slice_tasks import create_slice_task
from app.api.v1.slice_windows import list_windows
from app.core.errors import AnnotationValidationError, InvalidPaginationParamsError
from app.schemas.annotation import AnnotationCreateRequest, AnnotationUpdateRequest
from app.schemas.slice_task import SliceTaskCreateRequest
from app.services.annotation_service import AnnotationQueryFilters, AnnotationService
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


async def _prepare_windows(db_session):
    pkg = await create_package(
        name="svc-ann",
        description=None,
        file=DummyUpload(
            filename="svc-ann.zip",
            content_type="application/zip",
            data=_zip_bytes({"cpu0/a.log": "1.0 a\n2.0 b\n301.0 c\n"}),
        ),
        db=db_session,
    )
    ImportService(db_session).run_import_task(task_id=pkg.import_task_id)
    task = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name="svc-ann-task", window_seconds=300),
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
    return pkg.id, task.id, [item.window_id for item in windows.items]


def test_annotation_service_validation_and_overwrite(db_session, phase2_settings) -> None:
    _, _, window_ids = asyncio.run(_prepare_windows(db_session))
    window_id = window_ids[0]
    service = AnnotationService(db_session)

    with pytest.raises(AnnotationValidationError):
        service.save_for_window(
            window_id=window_id,
            payload=AnnotationCreateRequest(label="abnormal", anomaly_type=None, note="invalid"),
        )

    created = service.save_for_window(
        window_id=window_id,
        payload=AnnotationCreateRequest(label="abnormal", anomaly_type="softlockup", note="valid"),
    )
    assert created.id > 0
    assert created.anomaly_type == "softlockup"

    overwritten = service.save_for_window(
        window_id=window_id,
        payload=AnnotationCreateRequest(label="normal", anomaly_type="should-be-cleared", note="reset"),
    )
    assert overwritten.id == created.id
    assert overwritten.label == "normal"
    assert overwritten.anomaly_type is None

    with pytest.raises(AnnotationValidationError):
        service.update(
            annotation_id=created.id,
            payload=AnnotationUpdateRequest(label="abnormal", anomaly_type=None, note="bad"),
        )


def test_annotation_service_query_stats_pending_workbench_and_export(db_session, phase2_settings) -> None:
    pkg_id, task_id, window_ids = asyncio.run(_prepare_windows(db_session))
    first_window_id, second_window_id = window_ids[0], window_ids[1]
    service = AnnotationService(db_session)

    first = service.save_for_window(
        window_id=first_window_id,
        payload=AnnotationCreateRequest(label="abnormal", anomaly_type="disk_jitter", note="disk"),
    )
    second = service.save_for_window(
        window_id=second_window_id,
        payload=AnnotationCreateRequest(label="normal", anomaly_type=None, note="ok"),
    )

    items, total = service.query(
        page=1,
        page_size=20,
        filters=AnnotationQueryFilters(package_id=pkg_id, task_id=task_id),
    )
    assert total == 2
    assert {item["window_id"] for item in items} == {first_window_id, second_window_id}

    stats = service.stats(filters=AnnotationQueryFilters(package_id=pkg_id, task_id=task_id))
    assert stats.total_annotations == 2
    assert stats.normal_count == 1
    assert stats.abnormal_count == 1

    pending_items, pending_total = service.pending(
        page=1,
        page_size=20,
        filters=AnnotationQueryFilters(package_id=pkg_id, task_id=task_id),
    )
    assert pending_items == []
    assert pending_total == 0

    workbench_items, workbench_total = service.workbench(
        page=1,
        page_size=20,
        filters=AnnotationQueryFilters(package_id=pkg_id, task_id=task_id),
    )
    assert workbench_total == 2
    assert {item["annotation_id"] for item in workbench_items} == {first.id, second.id}

    with pytest.raises(InvalidPaginationParamsError):
        service.query(
            page=1,
            page_size=20,
            filters=AnnotationQueryFilters(start_ts=100, end_ts=10),
        )

    csv_result = service.export(
        export_format="csv",
        scope="current_filter",
        filters=AnnotationQueryFilters(package_id=pkg_id, task_id=task_id),
    )
    json_result = service.export(
        export_format="json",
        scope="all",
        filters=AnnotationQueryFilters(),
    )

    csv_path = Path(csv_result.file_path)
    json_path = Path(json_result.file_path)
    assert csv_path.exists()
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert {row["window_id"] for row in payload} == {first_window_id, second_window_id}
    assert "window_start_at" in payload[0]
    assert "window_end_at" in payload[0]
    assert "annotation" in payload[0]
    assert "window_context" in payload[0]


def test_annotation_service_supports_light_and_full_json_export(db_session, phase2_settings) -> None:
    pkg_id, task_id, window_ids = asyncio.run(_prepare_windows(db_session))
    service = AnnotationService(db_session)

    service.save_for_window(
        window_id=window_ids[0],
        payload=AnnotationCreateRequest(label="abnormal", anomaly_type="disk_jitter", note="disk"),
    )

    light_result = service.export(
        export_format="json",
        scope="current_filter",
        export_mode="light",
        filters=AnnotationQueryFilters(package_id=pkg_id, task_id=task_id),
    )
    full_result = service.export(
        export_format="json",
        scope="current_filter",
        export_mode="full",
        filters=AnnotationQueryFilters(package_id=pkg_id, task_id=task_id),
    )

    light_payload = json.loads(Path(light_result.file_path).read_text(encoding="utf-8"))
    full_payload = json.loads(Path(full_result.file_path).read_text(encoding="utf-8"))

    assert "logs" not in light_payload[0]
    assert "logs" in full_payload[0]
    assert isinstance(full_payload[0]["logs"], list)


def test_annotation_rejects_undefined_fault_type(db_session, phase2_settings) -> None:
    _, _, window_ids = asyncio.run(_prepare_windows(db_session))
    service = AnnotationService(db_session)

    with pytest.raises(AnnotationValidationError) as exc:
        service.save_for_window(
            window_id=window_ids[0],
            payload=AnnotationCreateRequest(label="abnormal", anomaly_type="undefined_type", note="bad"),
        )
    assert "必须是已定义的故障类型之一" in str(exc.value)
