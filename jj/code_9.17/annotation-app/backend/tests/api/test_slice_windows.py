from __future__ import annotations

import asyncio
import io
import zipfile

import pytest

from app.api.v1.annotations import create_annotation
from app.api.v1.packages import create_package
from app.api.v1.slice_tasks import create_slice_task
from app.api.v1.slice_windows import (
    SliceWindowSubdivideRequest,
    get_window_detail,
    get_window_full,
    get_window_logs,
    get_window_tree,
    list_windows,
    subdivide_slice_window,
)
from app.core.errors import SliceTaskValidationError, WindowHasAnnotationError, WindowNotFoundError
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


def _zip_bytes_with_logs(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        for rel, content in files.items():
            zf.writestr(f"dataset/data/{rel}", content)
    return buf.getvalue()


def _setup_task(db_session):
    pkg = asyncio.run(
        create_package(
            file=DummyUpload(
                filename="phase4.zip",
                content_type="application/zip",
                data=_zip_bytes_with_logs(
                    {
                        "cpu1/mod/a.log": "1.0 alpha\nbad_line\n2.0 beta\n",
                        "cpu2/net/b.log": "65.0 net ok\n",
                    }
                ),
            ),
            name="phase4",
            description=None,
            db=db_session,
        )
    )
    ImportService(db_session).run_import_task(task_id=pkg.import_task_id)
    task = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name="phase4-task", window_seconds=300),
        db=db_session,
    )
    return pkg, task


def test_list_windows_detail_tree_and_full(db_session, phase2_settings) -> None:
    _, task = _setup_task(db_session)

    page = list_windows(
        task_id=task.id,
        page=1,
        page_size=20,
        sort_by="window_start_ts",
        sort_order="asc",
        start_ts=None,
        end_ts=None,
        db=db_session,
    )
    assert page.total >= 1
    assert len(page.items) >= 1

    window_id = page.items[0].window_id
    create_annotation(
        window_id=window_id,
        payload=AnnotationCreateRequest(label="abnormal", anomaly_type="cpu", note="hot"),
        db=db_session,
    )

    detail = get_window_detail(window_id=window_id, db=db_session)
    assert detail.id == window_id
    assert detail.line_count >= 1
    assert detail.file_count >= 1
    assert detail.cpu_count >= 1
    assert detail.module_count >= 1

    tree = get_window_tree(window_id=window_id, db=db_session)
    assert tree.window_id == window_id
    assert len(tree.root) >= 1
    assert tree.root[0].type == "cpu"

    full = get_window_full(window_id=window_id, db=db_session)
    assert full.window.id == window_id
    assert full.tree.window_id == window_id
    assert len(full.annotations) == 1
    assert full.annotations[0].label == "abnormal"
    assert full.navigation.current_window_id == window_id


def test_window_not_found_is_raised_consistently(db_session, phase2_settings) -> None:
    with pytest.raises(WindowNotFoundError):
        get_window_detail(window_id=999999, db=db_session)

    with pytest.raises(WindowNotFoundError):
        get_window_tree(window_id=999999, db=db_session)

    with pytest.raises(WindowNotFoundError):
        get_window_full(window_id=999999, db=db_session)


def test_cursor_logs_api(db_session, phase2_settings) -> None:
    _, task = _setup_task(db_session)
    page = list_windows(
        task_id=task.id,
        page=1,
        page_size=20,
        sort_by="window_start_ts",
        sort_order="asc",
        start_ts=None,
        end_ts=None,
        db=db_session,
    )
    window_id = page.items[0].window_id
    tree = get_window_tree(window_id=window_id, db=db_session)
    first_file = tree.root[0].children[0].children[0]

    first_page = get_window_logs(
        window_id=window_id,
        source_file_id=int(first_file.source_file_id or 0),
        cursor=None,
        limit=1,
        keyword=None,
        db=db_session,
    )
    assert len(first_page.items) == 1
    assert first_page.has_more is True
    assert first_page.next_cursor is not None

    second_page = get_window_logs(
        window_id=window_id,
        source_file_id=int(first_file.source_file_id or 0),
        cursor=first_page.next_cursor,
        limit=10,
        keyword="beta",
        db=db_session,
    )
    assert all("beta" in item.content for item in second_page.items)


def test_subdivide_window_creates_children_and_preserves_parent(db_session, phase2_settings) -> None:
    _, task = _setup_task(db_session)
    page = list_windows(
        task_id=task.id,
        page=1,
        page_size=20,
        sort_by="window_start_ts",
        sort_order="asc",
        start_ts=None,
        end_ts=None,
        db=db_session,
    )
    parent_id = page.items[0].window_id

    # Window [0,300) holds lines at ts 1, 2, 65 -> 30s buckets -> [0,30) and [60,90).
    children = subdivide_slice_window(
        window_id=parent_id,
        payload=SliceWindowSubdivideRequest(window_seconds=30),
        db=db_session,
    )
    assert len(children) == 2
    assert all(c.window_start_ts >= 0 and c.window_end_ts <= 300 for c in children)

    # Parent is now flagged has_children and drops out of leaf navigation.
    full = get_window_full(window_id=parent_id, db=db_session)
    assert full.navigation.current_window_id == parent_id

    # Degenerate subdivision (>= span) is rejected.
    with pytest.raises(SliceTaskValidationError):
        subdivide_slice_window(
            window_id=parent_id,
            payload=SliceWindowSubdivideRequest(window_seconds=300),
            db=db_session,
        )

    # Annotating a child then re-subdividing the parent is refused.
    create_annotation(
        window_id=children[0].id,
        payload=AnnotationCreateRequest(label="abnormal", anomaly_type="cpu", note="hot child"),
        db=db_session,
    )
    with pytest.raises(WindowHasAnnotationError):
        subdivide_slice_window(
            window_id=parent_id,
            payload=SliceWindowSubdivideRequest(window_seconds=10),
            db=db_session,
        )

