from __future__ import annotations

import asyncio
import io
import json
import zipfile

import pytest

import app.services.recommendation_service as rec_module
from app.api.v1.packages import create_package
from app.api.v1.recommendations import (
    create_recommendation,
    get_recommendation,
    get_recommendation_batch_progress,
    start_recommendation_batch,
)
from app.api.v1.slice_tasks import create_slice_task
from app.api.v1.slice_windows import list_windows
from app.core.errors import RecommendationNotFoundError
from app.schemas.slice_task import SliceTaskCreateRequest
from app.services.import_service import ImportService
from tests.fakes import FakeLLMClient


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
        name="rec-api",
        description=None,
        file=DummyUpload(
            filename="rec-api.zip",
            content_type="application/zip",
            data=_zip_bytes({"cpu0/a.log": "1.0 a\n2.0 b\n301.0 c\n"}),
        ),
        db=db_session,
    )
    ImportService(db_session).run_import_task(task_id=pkg.import_task_id)
    task = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name="rec-api-task", window_seconds=300),
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


_ABNORMAL = json.dumps({"label": "abnormal", "anomaly_type": "看门狗复位/超时", "reason": "检测到看门狗复位"})


def _patch_client(monkeypatch, client) -> None:
    monkeypatch.setattr(rec_module, "get_llm_client", lambda: client)


def test_api_create_and_get_recommendation(db_session, phase2_settings, monkeypatch) -> None:
    _, _, window_ids = asyncio.run(_prepare_windows(db_session))
    _patch_client(monkeypatch, FakeLLMClient(responses=_ABNORMAL))

    created = create_recommendation(window_id=window_ids[0], force_refresh=False, db=db_session)
    assert created.status == "success"
    assert created.recommended_label == "abnormal"
    assert created.recommended_anomaly_type == "看门狗复位/超时"

    fetched = get_recommendation(window_id=window_ids[0], db=db_session)
    assert fetched.window_id == window_ids[0]
    assert fetched.status == "success"


def test_api_get_recommendation_missing_raises(db_session, phase2_settings, monkeypatch) -> None:
    _, _, window_ids = asyncio.run(_prepare_windows(db_session))
    _patch_client(monkeypatch, FakeLLMClient(responses=_ABNORMAL))
    with pytest.raises(RecommendationNotFoundError):
        get_recommendation(window_id=window_ids[0], db=db_session)


def test_api_create_recommendation_degrades_without_llm(db_session, phase2_settings, monkeypatch) -> None:
    _, _, window_ids = asyncio.run(_prepare_windows(db_session))
    _patch_client(monkeypatch, FakeLLMClient(configured=False))
    created = create_recommendation(window_id=window_ids[0], force_refresh=False, db=db_session)
    assert created.status == "failed"
    assert created.error_message is not None


def test_api_batch_progress(db_session, phase2_settings, monkeypatch) -> None:
    _, task_id, window_ids = asyncio.run(_prepare_windows(db_session))
    _patch_client(monkeypatch, FakeLLMClient(responses=_ABNORMAL))

    progress = start_recommendation_batch(task_id=task_id, db=db_session)
    assert progress.total_windows == len(window_ids)
    assert progress.success_count == len(window_ids)

    fetched = get_recommendation_batch_progress(task_id=task_id, db=db_session)
    assert fetched.success_count == len(window_ids)
    assert fetched.not_started_count == 0
