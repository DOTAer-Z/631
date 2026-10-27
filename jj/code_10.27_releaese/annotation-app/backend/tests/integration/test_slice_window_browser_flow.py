from __future__ import annotations

import asyncio
import io
import zipfile

from app.api.v1.packages import create_package
from app.api.v1.slice_tasks import create_slice_task
from app.api.v1.slice_windows import get_window_full, get_window_logs, get_window_tree, list_windows
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


def test_slice_window_browser_flow(db_session, phase2_settings) -> None:
    pkg = asyncio.run(
        create_package(
            name="phase4-flow",
            description=None,
            file=DummyUpload(
                filename="phase4-flow.zip",
                content_type="application/zip",
                data=_zip_bytes(
                    {
                        "cpu1/io/a.log": "1.0 a\n2.0 b\n",
                        "cpu2/net/b.log": "61.0 c\n",
                    }
                ),
            ),
            db=db_session,
        )
    )
    ImportService(db_session).run_import_task(task_id=pkg.import_task_id)
    task = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name="phase4-flow-task", window_seconds=300),
        db=db_session,
    )
    assert task.status == "success"

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
    assert windows.total >= 1
    first_window_id = windows.items[0].window_id
    full = get_window_full(window_id=first_window_id, db=db_session)
    assert full.window.id == first_window_id
    assert full.tree.window_id == first_window_id

    tree = get_window_tree(window_id=first_window_id, db=db_session)
    assert tree.root

    source_file_id = None
    queue = list(tree.root)
    while queue and source_file_id is None:
        node = queue.pop(0)
        if node.type == "file":
            source_file_id = node.source_file_id
            break
        queue.extend(node.children)
    assert source_file_id is not None

    first_page = get_window_logs(
        window_id=first_window_id,
        source_file_id=source_file_id,
        cursor=None,
        limit=1,
        keyword=None,
        db=db_session,
    )
    assert len(first_page.items) == 1
    assert first_page.next_cursor is not None

    second_page = get_window_logs(
        window_id=first_window_id,
        source_file_id=source_file_id,
        cursor=first_page.next_cursor,
        limit=10,
        keyword=None,
        db=db_session,
    )
    assert len(second_page.items) >= 1

    keyword_page = get_window_logs(
        window_id=first_window_id,
        source_file_id=source_file_id,
        cursor=None,
        limit=10,
        keyword="b",
        db=db_session,
    )
    assert all("b" in item.content for item in keyword_page.items)
