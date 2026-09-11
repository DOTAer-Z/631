from __future__ import annotations

import asyncio
import io
import json
import zipfile

import pytest

from app.api.v1.fault_type_suggestions import (
    accept_fault_type_suggestion,
    delete_fault_type_suggestion,
    get_fault_type_suggestion,
    list_fault_type_suggestions,
    reject_fault_type_suggestion,
)
from app.api.v1.fault_types import list_fault_types
from app.api.v1.packages import create_package
from app.api.v1.slice_tasks import create_slice_task
from app.api.v1.slice_windows import list_windows
from app.core.errors import (
    FaultTypeSuggestionInvalidStateError,
    FaultTypeSuggestionNotFoundError,
)
from app.db.models import FaultTypeSuggestion
from app.schemas.fault_type_suggestion import FaultTypeSuggestionAcceptRequest
from app.schemas.slice_task import SliceTaskCreateRequest
from app.services.import_service import ImportService
from app.services.recommendation_service import RecommendationService
from tests.fakes import FakeLLMClient


class _DummyUpload:
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


def _seed_pending_suggestion(db_session) -> int:
    """Run the two-step pipeline to materialize a pending suggestion. Returns the
    suggestion id."""
    pkg = asyncio.run(
        create_package(
            name="fts-pkg",
            description=None,
            file=_DummyUpload(
                filename="fts.zip",
                content_type="application/zip",
                data=_zip_bytes({"cpu0/a.log": "1.0 a\n2.0 b\n"}),
            ),
            db=db_session,
        )
    )
    ImportService(db_session).run_import_task(task_id=pkg.import_task_id)
    task = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name="fts-task", window_seconds=300),
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
    pass1 = json.dumps(
        {"label": "abnormal", "anomaly_type": "调度抖动失稳", "reason": "周期任务抖动"}
    )
    pass2 = json.dumps(
        {
            "should_create_new": True,
            "suggested_name": "调度抖动失稳",
            "suggested_description": "实时任务调度周期抖动超过阈值。",
            "reason": "现有类型未覆盖。",
        }
    )
    RecommendationService(db_session, llm_client=FakeLLMClient(responses=[pass1, pass2])).recommend_for_window(
        window_id=windows.items[0].window_id,
    )
    suggestion = db_session.query(FaultTypeSuggestion).first()
    assert suggestion is not None
    return suggestion.id


def test_list_get_and_default_pending_filter(db_session, phase2_settings) -> None:
    suggestion_id = _seed_pending_suggestion(db_session)

    listed = list_fault_type_suggestions(status_filter="pending", page=1, page_size=20, db=db_session)
    assert listed.total == 1
    assert listed.items[0].id == suggestion_id
    assert listed.items[0].status == "pending"

    fetched = get_fault_type_suggestion(suggestion_id=suggestion_id, db=db_session)
    assert fetched.suggested_name == "调度抖动失稳"


def test_accept_creates_fault_type_and_marks_suggestion_accepted(db_session, phase2_settings) -> None:
    suggestion_id = _seed_pending_suggestion(db_session)

    response = accept_fault_type_suggestion(
        suggestion_id=suggestion_id,
        payload=FaultTypeSuggestionAcceptRequest(name=None, description=None),
        db=db_session,
    )
    assert response.suggestion.status == "accepted"
    assert response.suggestion.accepted_fault_type_id == response.fault_type.id
    assert response.fault_type.name == "调度抖动失稳"

    # The new fault type now lives in the canonical dictionary.
    listed = list_fault_types(db=db_session)
    assert any(item.name == "调度抖动失稳" for item in listed.items)

    # Re-accepting a non-pending row must fail.
    with pytest.raises(FaultTypeSuggestionInvalidStateError):
        accept_fault_type_suggestion(
            suggestion_id=suggestion_id,
            payload=FaultTypeSuggestionAcceptRequest(name=None, description=None),
            db=db_session,
        )


def test_accept_with_user_overrides(db_session, phase2_settings) -> None:
    suggestion_id = _seed_pending_suggestion(db_session)

    response = accept_fault_type_suggestion(
        suggestion_id=suggestion_id,
        payload=FaultTypeSuggestionAcceptRequest(
            name="周期抖动",
            description="人类编辑后的定义。",
        ),
        db=db_session,
    )
    assert response.fault_type.name == "周期抖动"
    assert response.fault_type.description == "人类编辑后的定义。"


def test_reject_marks_suggestion_rejected(db_session, phase2_settings) -> None:
    suggestion_id = _seed_pending_suggestion(db_session)
    rejected = reject_fault_type_suggestion(suggestion_id=suggestion_id, db=db_session)
    assert rejected.status == "rejected"


def test_delete_removes_suggestion(db_session, phase2_settings) -> None:
    suggestion_id = _seed_pending_suggestion(db_session)
    delete_fault_type_suggestion(suggestion_id=suggestion_id, db=db_session)
    with pytest.raises(FaultTypeSuggestionNotFoundError):
        get_fault_type_suggestion(suggestion_id=suggestion_id, db=db_session)
