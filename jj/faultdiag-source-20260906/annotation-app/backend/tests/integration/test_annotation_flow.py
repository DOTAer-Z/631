from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path

from app.api.v1.annotations import (
    create_annotation,
    export_annotations,
    get_annotation_for_window,
    get_annotation_pending,
    get_annotation_stats,
    get_annotation_workbench,
    list_annotations,
    update_annotation,
)
from app.api.v1.packages import create_package
from app.api.v1.slice_tasks import create_slice_task
from app.api.v1.slice_windows import get_window_full, get_window_tree, list_windows
from app.schemas.annotation import AnnotationCreateRequest, AnnotationUpdateRequest
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


def test_annotation_end_to_end_flow(db_session, phase2_settings) -> None:
    pkg = asyncio.run(
        create_package(
            name="phase5-flow",
            description=None,
            file=DummyUpload(
                filename="phase5-flow.zip",
                content_type="application/zip",
                data=_zip_bytes(
                    {
                        "cpu0/a.log": "1.0 a\n2.0 b\n",
                        "cpu0/b.log": "301.0 c\n",
                    }
                ),
            ),
            db=db_session,
        )
    )
    ImportService(db_session).run_import_task(task_id=pkg.import_task_id)
    task = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name="phase5-flow-task", window_seconds=300),
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
    assert windows.total >= 2
    first_window_id = windows.items[0].window_id
    second_window_id = windows.items[1].window_id

    tree = get_window_tree(window_id=first_window_id, db=db_session)
    assert tree.root

    created = create_annotation(
        window_id=first_window_id,
        payload=AnnotationCreateRequest(label="abnormal", anomaly_type="cpu", note="high cpu"),
        db=db_session,
    )
    assert created.id > 0
    assert created.label == "abnormal"

    overwritten = create_annotation(
        window_id=first_window_id,
        payload=AnnotationCreateRequest(label="normal", anomaly_type=None, note="covered"),
        db=db_session,
    )
    assert overwritten.id == created.id
    assert overwritten.label == "normal"

    second = create_annotation(
        window_id=second_window_id,
        payload=AnnotationCreateRequest(label="abnormal", anomaly_type="memory", note="memory pressure"),
        db=db_session,
    )

    updated = update_annotation(
        annotation_id=second.id,
        payload=AnnotationUpdateRequest(label="abnormal", anomaly_type="io", note="io pressure"),
        db=db_session,
    )
    assert updated.anomaly_type == "io"

    fetched = get_annotation_for_window(window_id=second_window_id, db=db_session)
    assert fetched.id == second.id
    assert fetched.note == "io pressure"

    queried = list_annotations(
        package_id=pkg.id,
        task_id=task.id,
        label=None,
        anomaly_type=None,
        start_ts=None,
        end_ts=None,
        page=1,
        page_size=20,
        db=db_session,
    )
    assert queried.total == 2

    stats = get_annotation_stats(
        package_id=pkg.id,
        task_id=task.id,
        label=None,
        anomaly_type=None,
        start_ts=None,
        end_ts=None,
        db=db_session,
    )
    assert stats.total_annotations == 2
    assert stats.normal_count == 1
    assert stats.abnormal_count == 1

    pending = get_annotation_pending(
        package_id=pkg.id,
        task_id=task.id,
        page=1,
        page_size=20,
        db=db_session,
    )
    assert pending.total == 0

    workbench = get_annotation_workbench(
        package_id=pkg.id,
        task_id=task.id,
        page=1,
        page_size=20,
        db=db_session,
    )
    assert workbench.total == 2

    csv_result = export_annotations(
        format="csv",
        scope="current_filter",
        package_id=pkg.id,
        task_id=task.id,
        label=None,
        anomaly_type=None,
        start_ts=None,
        end_ts=None,
        db=db_session,
    )
    json_result = export_annotations(
        format="json",
        scope="all",
        package_id=None,
        task_id=None,
        label=None,
        anomaly_type=None,
        start_ts=None,
        end_ts=None,
        db=db_session,
    )

    csv_path = Path(csv_result.file_path)
    json_path = Path(json_result.file_path)
    assert csv_path.exists()
    assert json_path.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert {row["window_id"] for row in payload} == {first_window_id, second_window_id}

    full = get_window_full(window_id=second_window_id, db=db_session)
    assert len(full.annotations) == 1
    assert full.annotations[0].id == second.id
