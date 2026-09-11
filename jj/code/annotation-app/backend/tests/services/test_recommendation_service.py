from __future__ import annotations

import asyncio
import io
import json
import zipfile

import pytest

from app.api.v1.packages import create_package
from app.api.v1.slice_tasks import create_slice_task
from app.api.v1.slice_windows import list_windows
from app.core.errors import RecommendationNotFoundError, WindowNotFoundError
from app.schemas.slice_task import SliceTaskCreateRequest
from app.services.import_service import ImportService
from app.services.recommendation_service import RecommendationService
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
        name="rec",
        description=None,
        file=DummyUpload(
            filename="rec.zip",
            content_type="application/zip",
            data=_zip_bytes({"cpu0/a.log": "1.0 a\n2.0 b\n301.0 c\n"}),
        ),
        db=db_session,
    )
    ImportService(db_session).run_import_task(task_id=pkg.import_task_id)
    task = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name="rec-task", window_seconds=300),
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


_ABNORMAL = json.dumps({"label": "abnormal", "anomaly_type": "内存泄漏", "reason": "存在内存持续增长"})
_NORMAL = json.dumps({"label": "normal", "anomaly_type": None, "reason": "无异常"})


def test_recommend_success_and_cache(db_session, phase2_settings) -> None:
    _, _, window_ids = asyncio.run(_prepare_windows(db_session))
    fake = FakeLLMClient(responses=_ABNORMAL)
    service = RecommendationService(db_session, llm_client=fake)

    row = service.recommend_for_window(window_id=window_ids[0])
    assert row.status == "success"
    assert row.recommended_label == "abnormal"
    assert row.recommended_anomaly_type == "内存泄漏"
    assert row.reason == "存在内存持续增长"
    assert fake.call_count == 1
    assert fake.purposes == ["annotation_recommendation"]

    # Cached: not regenerated without force_refresh.
    again = service.recommend_for_window(window_id=window_ids[0])
    assert again.status == "success"
    assert fake.call_count == 1

    # force_refresh re-calls the LLM.
    service.recommend_for_window(window_id=window_ids[0], force_refresh=True)
    assert fake.call_count == 2


def test_recommend_repairs_non_json_llm_response_once(db_session, phase2_settings) -> None:
    _, _, window_ids = asyncio.run(_prepare_windows(db_session))
    fake = FakeLLMClient(responses=["我认为这是内存泄漏，因为存在内存持续增长。", _ABNORMAL])
    service = RecommendationService(db_session, llm_client=fake)

    row = service.recommend_for_window(window_id=window_ids[0])

    assert row.status == "success"
    assert row.recommended_label == "abnormal"
    assert row.recommended_anomaly_type == "内存泄漏"
    assert fake.call_count == 2
    assert fake.purposes == ["annotation_recommendation", "annotation_recommendation"]
    assert "只返回一个合法 JSON 对象" in fake.calls[1][0]["content"]


def test_recommend_persists_gateway_model(db_session, phase2_settings) -> None:
    _, _, window_ids = asyncio.run(_prepare_windows(db_session))
    fake = FakeLLMClient(responses=_ABNORMAL, model="gateway-model-a")

    row = RecommendationService(db_session, llm_client=fake).recommend_for_window(
        window_id=window_ids[0]
    )

    assert row.model == "gateway-model-a"


def test_recommend_normal_clears_anomaly_type(db_session, phase2_settings) -> None:
    _, _, window_ids = asyncio.run(_prepare_windows(db_session))
    service = RecommendationService(db_session, llm_client=FakeLLMClient(responses=_NORMAL))
    row = service.recommend_for_window(window_id=window_ids[0])
    assert row.recommended_label == "normal"
    assert row.recommended_anomaly_type is None


def test_recommend_degrades_when_llm_unconfigured(db_session, phase2_settings) -> None:
    _, _, window_ids = asyncio.run(_prepare_windows(db_session))
    fake = FakeLLMClient(configured=False)
    service = RecommendationService(db_session, llm_client=fake)
    row = service.recommend_for_window(window_id=window_ids[0])
    assert row.status == "failed"
    assert row.error_message is not None
    assert fake.call_count == 0


def test_get_for_window_raises_when_missing(db_session, phase2_settings) -> None:
    _, _, window_ids = asyncio.run(_prepare_windows(db_session))
    service = RecommendationService(db_session, llm_client=FakeLLMClient(responses=_ABNORMAL))
    with pytest.raises(RecommendationNotFoundError):
        service.get_for_window(window_id=window_ids[0])


def test_recommend_unknown_window_raises(db_session, phase2_settings) -> None:
    asyncio.run(_prepare_windows(db_session))
    service = RecommendationService(db_session, llm_client=FakeLLMClient(responses=_ABNORMAL))
    with pytest.raises(WindowNotFoundError):
        service.recommend_for_window(window_id=999999)


def test_batch_recommend_for_task(db_session, phase2_settings) -> None:
    _, task_id, window_ids = asyncio.run(_prepare_windows(db_session))
    fake = FakeLLMClient(responses=_ABNORMAL)
    service = RecommendationService(db_session, llm_client=fake)

    progress = service.batch_recommend_for_task(task_id=task_id)

    assert progress["total_windows"] == len(window_ids)
    assert progress["success_count"] == len(window_ids)
    assert progress["not_started_count"] == 0
    assert fake.call_count == len(window_ids)

    # Re-running only targets windows without a success recommendation (none here).
    progress2 = service.batch_recommend_for_task(task_id=task_id)
    assert progress2["success_count"] == len(window_ids)
    assert fake.call_count == len(window_ids)


_UNDEFINED_ABNORMAL = json.dumps(
    {"label": "abnormal", "anomaly_type": "完全自创类型", "reason": "随便编的"}
)


def test_recommend_rejects_undefined_fault_type(db_session, phase2_settings) -> None:
    _, _, window_ids = asyncio.run(_prepare_windows(db_session))
    fake = FakeLLMClient(responses=_UNDEFINED_ABNORMAL)
    service = RecommendationService(db_session, llm_client=fake)
    row = service.recommend_for_window(window_id=window_ids[0])
    assert row.status == "failed"
    assert "不在已定义列表中" in (row.error_message or "")


def test_recommend_records_suggestion_when_llm_proposes_new_fault_type(db_session, phase2_settings) -> None:
    """Two-step path: pass 1 picks an unmatched anomaly_type, pass 2 confirms it
    is a genuinely new fault type. The recommendation succeeds (no anomaly_type
    written) and a pending fault_type_suggestion row is persisted."""
    from app.db.models import FaultTypeSuggestion

    _, _, window_ids = asyncio.run(_prepare_windows(db_session))
    pass1 = json.dumps(
        {"label": "abnormal", "anomaly_type": "调度抖动失稳", "reason": "周期任务抖动"}
    )
    pass2 = json.dumps(
        {
            "should_create_new": True,
            "suggested_name": "调度抖动失稳",
            "suggested_description": "实时任务调度周期抖动超过阈值，导致下游响应抖动。",
            "reason": "现有类型未覆盖该现象。",
        }
    )
    fake = FakeLLMClient(responses=[pass1, pass2])
    service = RecommendationService(db_session, llm_client=fake)
    row = service.recommend_for_window(window_id=window_ids[0])

    assert row.status == "success"
    assert row.recommended_label == "abnormal"
    assert row.recommended_anomaly_type is None
    assert "调度抖动失稳" in (row.reason or "")

    suggestion = db_session.query(FaultTypeSuggestion).filter(
        FaultTypeSuggestion.slice_window_id == window_ids[0]
    ).first()
    assert suggestion is not None
    assert suggestion.status == "pending"
    assert suggestion.suggested_name == "调度抖动失稳"
    assert "实时任务" in suggestion.suggested_description
    assert fake.call_count == 2


def test_recommend_falls_through_when_pass2_says_no_new_type(db_session, phase2_settings) -> None:
    """If pass 2 declines to create a new type, the recommendation must fall back
    to the original 'fault type not in defined list' failure — no suggestion row."""
    from app.db.models import FaultTypeSuggestion

    _, _, window_ids = asyncio.run(_prepare_windows(db_session))
    pass1 = json.dumps({"label": "abnormal", "anomaly_type": "随便起的名字", "reason": "x"})
    pass2 = json.dumps(
        {"should_create_new": False, "reason": "其实和已定义的内存泄漏是同一类。"}
    )
    fake = FakeLLMClient(responses=[pass1, pass2])
    service = RecommendationService(db_session, llm_client=fake)
    row = service.recommend_for_window(window_id=window_ids[0])

    assert row.status == "failed"
    assert "不在已定义列表中" in (row.error_message or "")
    assert db_session.query(FaultTypeSuggestion).count() == 0


def test_recommend_failed_when_no_fault_types_defined(db_session, phase2_settings) -> None:
    from app.db.models import FaultType
    _, _, window_ids = asyncio.run(_prepare_windows(db_session))
    db_session.query(FaultType).delete()
    db_session.commit()

    fake = FakeLLMClient(responses=_ABNORMAL)
    service = RecommendationService(db_session, llm_client=fake)
    row = service.recommend_for_window(window_id=window_ids[0])
    assert row.status == "failed"
    assert "请先在故障类型管理中定义" in (row.error_message or "")
    assert fake.call_count == 0


def test_analyze_window_multi_snaps_anomaly_type(db_session, phase2_settings) -> None:
    from app.services.source_log_query_service import SourceLogQueryService

    _, _, window_ids = asyncio.run(_prepare_windows(db_session))
    window_id = window_ids[0]

    tree = SourceLogQueryService(db_session).get_window_tree(window_id=window_id)

    def _first_file(nodes):
        for node in nodes:
            if node.type == "file" and node.source_file_id is not None:
                return node.source_file_id
            found = _first_file(node.children)
            if found is not None:
                return found
        return None

    file_id = _first_file(tree.root)
    assert file_id is not None

    payload = json.dumps(
        {
            "multiple_faults": True,
            "suggest_subdivide": True,
            "suggested_window_seconds": 60,
            "per_file": [
                {"source_log_file_id": file_id, "label": "abnormal", "anomaly_type": "内存泄漏", "reason": "leak"},
                {"source_log_file_id": file_id, "label": "abnormal", "anomaly_type": "不存在类型", "reason": "x"},
            ],
        }
    )
    fake = FakeLLMClient(responses=payload)
    service = RecommendationService(db_session, llm_client=fake)

    result = service.analyze_window_multi(window_id=window_id)
    assert result["status"] == "success"
    assert result["multiple_faults"] is True
    assert result["suggest_subdivide"] is True
    # Suggested sub-window length is validated (< 300s window span) and returned.
    assert result["suggested_window_seconds"] == 60
    assert len(result["files"]) == 2
    assert result["files"][0]["anomaly_type"] == "内存泄漏"
    # Unmatched anomaly type degrades to None with a note appended.
    assert result["files"][1]["anomaly_type"] is None
    assert "未匹配" in (result["files"][1]["reason"] or "")
    assert fake.purposes == ["annotation_window_analysis"]


def test_analyze_window_multi_rejects_oversized_subdivision(db_session, phase2_settings) -> None:
    _, _, window_ids = asyncio.run(_prepare_windows(db_session))
    window_id = window_ids[0]
    payload = json.dumps(
        {
            "multiple_faults": False,
            "suggest_subdivide": True,
            "suggested_window_seconds": 600,  # >= 300s span -> rejected
            "per_file": [],
        }
    )
    service = RecommendationService(db_session, llm_client=FakeLLMClient(responses=payload))
    result = service.analyze_window_multi(window_id=window_id)
    assert result["suggested_window_seconds"] is None


def test_analyze_window_multi_degrades_without_llm(db_session, phase2_settings) -> None:
    _, _, window_ids = asyncio.run(_prepare_windows(db_session))
    fake = FakeLLMClient(responses=None, configured=False)
    service = RecommendationService(db_session, llm_client=fake)
    result = service.analyze_window_multi(window_id=window_ids[0])
    assert result["status"] == "failed"
    assert result["files"] == []
