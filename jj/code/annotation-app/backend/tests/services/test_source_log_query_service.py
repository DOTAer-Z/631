from __future__ import annotations

import asyncio
import io
import zipfile

import pytest

from app.api.v1.annotations import create_annotation
from app.api.v1.packages import create_package
from app.api.v1.slice_tasks import create_slice_task
from app.api.v1.slice_windows import list_windows
from app.core.errors import WindowNotFoundError
from app.schemas.annotation import AnnotationCreateRequest
from app.schemas.slice_task import SliceTaskCreateRequest
from app.services.import_service import ImportService
from app.services.source_log_query_service import SourceLogQueryService


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


def _prepare_window(db_session):
    pkg = asyncio.run(
        create_package(
            name="query-window",
            description=None,
            file=DummyUpload(
                filename="query-window.zip",
                content_type="application/zip",
                data=_zip_bytes(
                    {
                        "cpu0/mod/a.log": "1.0 alpha\n2.0 beta\n",
                        "cpu0/mod/b.log": "3.0 bravo\n",
                        "cpu1/net/c.log": "301.0 gamma\n302.0 delta\n",
                    }
                ),
            ),
            db=db_session,
        )
    )
    ImportService(db_session).run_import_task(task_id=pkg.import_task_id)
    task = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name="query-window-task", window_seconds=300),
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
    return task, windows.items[0].window_id


def test_get_window_full_includes_tree_annotation_and_navigation(db_session, phase2_settings) -> None:
    task, window_id = _prepare_window(db_session)
    service = SourceLogQueryService(db_session)
    create_annotation(
        window_id=window_id,
        payload=AnnotationCreateRequest(label="normal", anomaly_type=None, note="ok"),
        db=db_session,
    )

    full = service.get_window_full(window_id=window_id)
    assert full.window.id == window_id
    assert len(full.annotations) == 1
    assert full.annotations[0].label == "normal"
    assert full.annotations[0].source_log_file_id is None
    assert full.navigation.current_window_id == window_id
    assert full.navigation.prev_window_id is None
    assert full.navigation.next_window_id is not None
    assert len(full.tree.root) >= 1
    assert full.tree.root[0].type == "cpu"

    detail = service.get_window_detail(window_id=window_id)
    assert detail.line_count == full.window.line_count
    assert detail.file_count == full.window.file_count


def test_get_window_tree_and_not_found(db_session, phase2_settings) -> None:
    _, window_id = _prepare_window(db_session)
    service = SourceLogQueryService(db_session)

    tree = service.get_window_tree(window_id=window_id)
    assert len(tree.root) >= 1
    assert tree.root[0].type == "cpu"
    assert tree.root[0].children[0].type == "module"
    assert tree.root[0].children[0].children[0].type == "file"

    with pytest.raises(WindowNotFoundError):
        service.get_window_detail(window_id=999999)


def test_cursor_logs_pagination_and_keyword_scope(db_session, phase2_settings) -> None:
    _, window_id = _prepare_window(db_session)
    service = SourceLogQueryService(db_session)
    tree = service.get_window_tree(window_id=window_id)
    file_node = tree.root[0].children[0].children[0]
    source_file_id = int(file_node.source_file_id or 0)

    first_page = service.get_window_logs(
        window_id=window_id,
        source_file_id=source_file_id,
        cursor=None,
        limit=1,
        keyword=None,
    )
    assert len(first_page.items) == 1
    assert first_page.has_more is True
    assert first_page.next_cursor is not None

    second_page = service.get_window_logs(
        window_id=window_id,
        source_file_id=source_file_id,
        cursor=first_page.next_cursor,
        limit=10,
        keyword=None,
    )
    assert len(second_page.items) >= 1

    searched = service.get_window_logs(
        window_id=window_id,
        source_file_id=source_file_id,
        cursor=None,
        limit=10,
        keyword="beta",
    )
    assert len(searched.items) == 1
    assert searched.items[0].content.endswith("beta")

    empty = service.get_window_logs(
        window_id=window_id,
        source_file_id=source_file_id,
        cursor=None,
        limit=10,
        keyword="gamma",
    )
    assert empty.items == []
    assert empty.has_more is False
    assert empty.next_cursor is None
