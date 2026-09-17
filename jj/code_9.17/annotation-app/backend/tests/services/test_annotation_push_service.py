from __future__ import annotations

import asyncio
import io
import zipfile

import pytest

from app.api.v1.packages import create_package
from app.api.v1.slice_tasks import create_slice_task
from app.api.v1.slice_windows import list_windows
from app.core.errors import WindowNotFoundError
from app.schemas.slice_task import SliceTaskCreateRequest
from app.services.annotation_push_service import AnnotationPushService
from app.services.annotation_service import AnnotationService
from app.schemas.annotation import AnnotationCreateRequest
from app.services.import_service import ImportService
from app.services.main_system_client import MainSystemClient


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


async def _prepare_window(db_session):
    pkg = await create_package(
        name="push-ann",
        description=None,
        file=DummyUpload(
            filename="push-ann.zip",
            content_type="application/zip",
            data=_zip_bytes(
                {"cpu0/a.log": "1.0 a\n2.0 b\n301.0 c\n", "cpu1/b.log": "1.0 d\n2.0 e\n"}
            ),
        ),
        db=db_session,
    )
    ImportService(db_session).run_import_task(task_id=pkg.import_task_id)
    task = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name="push-ann-task", window_seconds=300),
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


def test_push_payload_assembles_lines_and_annotations(db_session, phase2_settings, monkeypatch) -> None:
    pkg_id, task_id, window_ids = asyncio.run(_prepare_window(db_session))
    window_id = window_ids[0]

    # 给窗口打一条 abnormal 标注（整窗），作为 is_fault / fault_type 来源。
    AnnotationService(db_session).save_for_window(
        window_id=window_id,
        payload=AnnotationCreateRequest(label="abnormal", anomaly_type="内存泄漏", note="test note"),
    )

    captured: dict = {}

    def _fake_push(self, payload: dict) -> dict:
        captured["payload"] = payload
        return {"run_id": payload["run_id"], "is_new": True, "entries": 3, "case_id": "annot_push-ann"}

    monkeypatch.setattr(MainSystemClient, "push_annotated_run", _fake_push)

    result = AnnotationPushService(db_session).push_window_to_main(window_id=window_id)
    assert result["is_new"] is True
    assert result["entries"] == 3

    payload = captured["payload"]
    assert payload["run_id"] == f"annot_pkg{pkg_id}_win{window_id}"
    assert payload["source_type"] == "annotation"
    assert payload["test_name"] == "push-ann-task"

    # is_fault / fault_type 来自 abnormal 标注。
    assert payload["is_fault"] is True
    assert payload["fault_type"] == "内存泄漏"
    assert payload["description"] == "test note"

    # 行按源文件分组，含 line_no/content/timestamp。
    assert len(payload["lines"]) == 2
    by_path = {f["logical_path"]: f["lines"] for f in payload["lines"]}
    assert set(by_path) == {"cpu0/a.log", "cpu1/b.log"}
    first = by_path["cpu0/a.log"][0]
    assert first["line_no"] == 1
    assert first["content"] == "1.0 a"
    assert first["timestamp"] is not None


def test_push_rejects_missing_window(db_session, phase2_settings) -> None:
    with pytest.raises(WindowNotFoundError):
        AnnotationPushService(db_session).push_window_to_main(window_id=999999)
