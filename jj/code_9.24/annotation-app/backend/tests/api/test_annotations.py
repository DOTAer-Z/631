from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path

import pytest

from app.api.v1.annotations import (
    create_annotation,
    delete_annotation,
    download_annotation_export,
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
from app.api.v1.slice_windows import get_window_full, list_windows
from app.core.errors import AnnotationValidationError
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


def _prepare_windows(db_session):
    pkg = asyncio.run(
        create_package(
            name="anno-pkg",
            description=None,
            file=DummyUpload(
                filename="anno.zip",
                content_type="application/zip",
                data=_zip_bytes(
                    {
                        "cpu1/a.log": "1.0 x\n2.0 y\n",
                        "cpu1/b.log": "301.0 z\n",
                    }
                ),
            ),
            db=db_session,
        )
    )
    ImportService(db_session).run_import_task(task_id=pkg.import_task_id)
    task = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name="anno-task", window_seconds=300),
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
    return pkg, task, [item.window_id for item in windows.items]


def test_annotation_crud_overwrite_and_rules(db_session, phase2_settings) -> None:
    _, _, window_ids = _prepare_windows(db_session)
    first_window_id = window_ids[0]

    with pytest.raises(AnnotationValidationError):
        create_annotation(
            window_id=first_window_id,
            payload=AnnotationCreateRequest(label="abnormal", anomaly_type=None, note="x"),
            db=db_session,
        )

    created = create_annotation(
        window_id=first_window_id,
        payload=AnnotationCreateRequest(label="abnormal", anomaly_type="softlockup", note="high usage"),
        db=db_session,
    )
    assert created.label == "abnormal"
    assert created.anomaly_type == "softlockup"

    overwritten = create_annotation(
        window_id=first_window_id,
        payload=AnnotationCreateRequest(label="normal", anomaly_type="should-clear", note="normalized"),
        db=db_session,
    )
    assert overwritten.id == created.id
    assert overwritten.label == "normal"
    assert overwritten.anomaly_type is None
    assert overwritten.note == "normalized"

    fetched = get_annotation_for_window(window_id=first_window_id, db=db_session)
    assert fetched.id == created.id

    updated = update_annotation(
        annotation_id=created.id,
        payload=AnnotationUpdateRequest(label="abnormal", anomaly_type="kernel_panic", note="memory pressure"),
        db=db_session,
    )
    assert updated.label == "abnormal"
    assert updated.anomaly_type == "kernel_panic"

    delete_annotation(annotation_id=created.id, db=db_session)
    with pytest.raises(Exception):
        get_annotation_for_window(window_id=first_window_id, db=db_session)


def _first_source_file_id(db_session, window_id: int) -> int:
    from app.services.source_log_query_service import SourceLogQueryService

    tree = SourceLogQueryService(db_session).get_window_tree(window_id=window_id)

    def walk(nodes):
        for node in nodes:
            if node.type == "file" and node.source_file_id is not None:
                return node.source_file_id
            found = walk(node.children)
            if found is not None:
                return found
        return None

    file_id = walk(tree.root)
    assert file_id is not None
    return file_id


def test_annotation_multi_label_whole_and_file_level(db_session, phase2_settings) -> None:
    _, _, window_ids = _prepare_windows(db_session)
    window_id = window_ids[0]
    file_id = _first_source_file_id(db_session, window_id)

    whole = create_annotation(
        window_id=window_id,
        payload=AnnotationCreateRequest(label="normal", anomaly_type=None, note="whole"),
        db=db_session,
    )
    file_anno = create_annotation(
        window_id=window_id,
        payload=AnnotationCreateRequest(
            label="abnormal", anomaly_type="softlockup", note="on file", source_log_file_id=file_id
        ),
        db=db_session,
    )
    assert whole.id != file_anno.id
    assert whole.source_log_file_id is None
    assert file_anno.source_log_file_id == file_id

    # Re-saving the same (window, file) binding updates rather than duplicates.
    updated = create_annotation(
        window_id=window_id,
        payload=AnnotationCreateRequest(
            label="abnormal", anomaly_type="kernel_panic", note="updated", source_log_file_id=file_id
        ),
        db=db_session,
    )
    assert updated.id == file_anno.id
    assert updated.anomaly_type == "kernel_panic"

    # The window now exposes both annotations.
    full = get_window_full(window_id=window_id, db=db_session)
    assert len(full.annotations) == 2
    assert {a.source_log_file_id for a in full.annotations} == {None, file_id}

    # Binding a file that does not belong to the window is rejected.
    with pytest.raises(AnnotationValidationError):
        create_annotation(
            window_id=window_id,
            payload=AnnotationCreateRequest(
                label="abnormal", anomaly_type="softlockup", note="bad", source_log_file_id=999999
            ),
            db=db_session,
        )


def test_annotation_query_stats_pending_workbench_and_export(db_session, phase2_settings) -> None:
    pkg, task, window_ids = _prepare_windows(db_session)
    first_window_id, second_window_id = window_ids[0], window_ids[1]

    first = create_annotation(
        window_id=first_window_id,
        payload=AnnotationCreateRequest(label="abnormal", anomaly_type="disk_jitter", note="disk latency"),
        db=db_session,
    )
    second = create_annotation(
        window_id=second_window_id,
        payload=AnnotationCreateRequest(label="normal", anomaly_type=None, note="ok"),
        db=db_session,
    )

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

    # Sorting by id ascending vs descending flips the first row.
    asc_first = list_annotations(
        package_id=pkg.id, task_id=task.id, label=None, anomaly_type=None,
        start_ts=None, end_ts=None, page=1, page_size=20,
        sort_by="id", sort_order="asc", db=db_session,
    )
    desc_first = list_annotations(
        package_id=pkg.id, task_id=task.id, label=None, anomaly_type=None,
        start_ts=None, end_ts=None, page=1, page_size=20,
        sort_by="id", sort_order="desc", db=db_session,
    )
    assert asc_first.items[0].id < asc_first.items[-1].id
    assert desc_first.items[0].id > desc_first.items[-1].id
    assert asc_first.items[0].id == desc_first.items[-1].id

    # Page size caps the page; page 2 holds the remainder.
    page1 = list_annotations(
        package_id=pkg.id, task_id=task.id, label=None, anomaly_type=None,
        start_ts=None, end_ts=None, page=1, page_size=1,
        sort_by="id", sort_order="asc", db=db_session,
    )
    page2 = list_annotations(
        package_id=pkg.id, task_id=task.id, label=None, anomaly_type=None,
        start_ts=None, end_ts=None, page=2, page_size=1,
        sort_by="id", sort_order="asc", db=db_session,
    )
    assert len(page1.items) == 1 and len(page2.items) == 1
    assert page1.items[0].id != page2.items[0].id

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
    assert {item.annotation_id for item in workbench.items} == {first.id, second.id}

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

    assert csv_result.item_count == 2
    assert json_result.item_count == 2
    csv_path = Path(csv_result.file_path)
    json_path = Path(json_result.file_path)
    assert csv_path.exists()
    assert json_path.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert {row["window_id"] for row in payload} == {first_window_id, second_window_id}
    assert "window_start_at" in payload[0]
    assert "window_end_at" in payload[0]
    assert "window_context" in payload[0]
    assert "annotation" in payload[0]

    full = get_window_full(window_id=first_window_id, db=db_session)
    assert len(full.annotations) == 1
    assert full.annotations[0].id == first.id


def test_export_download_url_is_relative_and_endpoint_streams_file(db_session, phase2_settings) -> None:
    """The exported download_url must be a relative path so the frontend can resolve
    it against window.location.origin (works behind any reverse proxy / K8s gateway,
    independent of FastAPI's request.url_for which leaks cluster-internal hosts).
    The download endpoint must serve the file with attachment headers."""
    pkg, task, window_ids = _prepare_windows(db_session)
    create_annotation(
        window_id=window_ids[0],
        payload=AnnotationCreateRequest(label="abnormal", anomaly_type="disk_jitter", note="dl-test"),
        db=db_session,
    )

    result = export_annotations(
        format="json",
        scope="current_filter",
        mode="light",
        package_id=pkg.id,
        task_id=task.id,
        label=None,
        anomaly_type=None,
        start_ts=None,
        end_ts=None,
        db=db_session,
    )

    assert result.download_url.startswith("/api/v1/annotations/export/download?file=")
    assert "://" not in result.download_url

    response = download_annotation_export(file=result.file_name, db=db_session)
    assert response.status_code == 200
    disposition = response.headers.get("content-disposition", "")
    assert "attachment" in disposition.lower()
    assert result.file_name in disposition


def test_pending_endpoint_supports_filters_and_sort(db_session, phase2_settings) -> None:
    """Pending must surface package/task names + line/file counts, expose
    min_line_count/keyword filters, and respect sort_by=line_count."""
    from app.api.v1.slice_windows import delete_slice_window  # local import to avoid cycle
    from app.api.v1.annotations import get_annotation_pending

    pkg, task, window_ids = _prepare_windows(db_session)

    pending = get_annotation_pending(
        package_id=pkg.id,
        task_id=task.id,
        page=1,
        page_size=20,
        db=db_session,
    )
    assert pending.total >= 2
    sample = pending.items[0]
    assert sample.package_name == pkg.name
    assert sample.task_name == task.name
    assert sample.line_count >= 1

    # sort_by=line_count ascending — first window must have the smallest count.
    sorted_items = get_annotation_pending(
        package_id=pkg.id,
        task_id=task.id,
        sort_by="line_count",
        sort_order="asc",
        page=1,
        page_size=20,
        db=db_session,
    ).items
    assert [item.line_count for item in sorted_items] == sorted(item.line_count for item in sorted_items)

    # min_line_count filter trims the result set.
    biggest = max(item.line_count for item in pending.items)
    filtered = get_annotation_pending(
        package_id=pkg.id,
        task_id=task.id,
        min_line_count=biggest,
        page=1,
        page_size=20,
        db=db_session,
    )
    assert filtered.total >= 1
    assert all(item.line_count >= biggest for item in filtered.items)

    # keyword filter must match log content; "x" appears in cpu1/a.log line "1.0 x".
    kw = get_annotation_pending(
        package_id=pkg.id,
        task_id=task.id,
        keyword="x",
        page=1,
        page_size=20,
        db=db_session,
    )
    assert kw.total >= 1
    nope = get_annotation_pending(
        package_id=pkg.id,
        task_id=task.id,
        keyword="this-string-is-not-in-any-log-line",
        page=1,
        page_size=20,
        db=db_session,
    )
    assert nope.total == 0


def test_delete_slice_window_drops_unannotated_window_only(db_session, phase2_settings) -> None:
    """Deleting an unannotated window removes it (and its lines via cascade);
    deleting an annotated window must 409 and leave both rows intact."""
    from app.api.v1.annotations import get_annotation_pending
    from app.api.v1.slice_windows import delete_slice_window
    from app.core.errors import WindowHasAnnotationError
    from app.db.models import SliceWindow, SliceWindowLine

    pkg, task, window_ids = _prepare_windows(db_session)
    target = window_ids[0]
    keeper = window_ids[1]

    # Annotate the keeper so we can also exercise the 409 path.
    create_annotation(
        window_id=keeper,
        payload=AnnotationCreateRequest(label="abnormal", anomaly_type="softlockup", note="kept"),
        db=db_session,
    )

    delete_slice_window(window_id=target, db=db_session)
    assert db_session.get(SliceWindow, target) is None
    leftover_lines = (
        db_session.query(SliceWindowLine).filter(SliceWindowLine.slice_window_id == target).count()
    )
    assert leftover_lines == 0

    # Window vanishes from pending too.
    after = get_annotation_pending(
        package_id=pkg.id,
        task_id=task.id,
        page=1,
        page_size=20,
        db=db_session,
    )
    assert all(item.window_id != target for item in after.items)

    # Annotated window cannot be deleted.
    with pytest.raises(WindowHasAnnotationError):
        delete_slice_window(window_id=keeper, db=db_session)
    assert db_session.get(SliceWindow, keeper) is not None
