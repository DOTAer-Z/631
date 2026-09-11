from __future__ import annotations

import asyncio
import io
import zipfile

from sqlalchemy import select

from app.api.v1.packages import create_package
from app.api.v1.slice_tasks import create_slice_task, delete_slice_task
from app.db.models import Annotation, SliceTask, SliceWindow, SliceWindowLine, SourceLogFile, SourceLogLine
from app.schemas.slice_task import SliceTaskCreateRequest
from app.services.import_service import ImportService


class DummyUpload:
    def __init__(self, filename: str, content_type: str, data: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self._data = data

    async def read(self) -> bytes:
        return self._data


def _zip_bytes_with_files(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        for rel_path, content in files.items():
            zf.writestr(f"dataset/data/{rel_path}", content)
    return buf.getvalue()


def test_slice_task_integration_flow_and_cleanup_isolation(db_session, phase2_settings) -> None:
    pkg = asyncio.run(
        create_package(
            name="slice-flow",
            description="flow package",
            file=DummyUpload(
                filename="slice-flow.zip",
                content_type="application/zip",
                data=_zip_bytes_with_files(
                    {
                        "cpu0/mod/a.log": "0.0 start\n120.0 done\n",
                        "cpu1/mod/b.log": "180.0 keep\nbroken\n",
                    }
                ),
            ),
            db=db_session,
        )
    )
    ImportService(db_session).run_import_task(task_id=pkg.import_task_id)

    first = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name="task-one", window_seconds=180),
        db=db_session,
    )
    second = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name="task-two", window_seconds=300),
        db=db_session,
    )

    assert first.status == "success"
    assert first.total_files == 2
    assert first.total_lines == 3
    assert first.total_windows == 2

    assert second.status == "success"
    assert second.total_files == 2
    assert second.total_lines == 3
    assert second.total_windows == 1

    first_task = db_session.scalar(select(SliceTask).where(SliceTask.id == first.id))
    assert first_task is not None
    assert first_task.started_at is not None
    assert first_task.finished_at is not None
    assert first_task.error_message is None

    first_windows = list(
        db_session.scalars(
            select(SliceWindow)
            .where(SliceWindow.slice_task_id == first.id)
            .order_by(SliceWindow.window_start_ts, SliceWindow.id)
        ).all()
    )
    assert len(first_windows) == 2
    assert [window.line_count for window in first_windows] == [2, 1]
    assert [window.file_count for window in first_windows] == [1, 1]
    assert [window.cpu_count for window in first_windows] == [1, 1]
    assert [window.module_count for window in first_windows] == [1, 1]

    first_links = list(
        db_session.scalars(
            select(SliceWindowLine)
            .join(SliceWindow, SliceWindow.id == SliceWindowLine.slice_window_id)
            .where(SliceWindow.slice_task_id == first.id)
        ).all()
    )
    assert len(first_links) == 3

    db_session.add(Annotation(slice_window_id=first_windows[0].id, label="normal"))
    db_session.commit()

    delete_slice_task(task_id=first.id, db=db_session)

    remaining_first_task = db_session.scalar(select(SliceTask).where(SliceTask.id == first.id))
    assert remaining_first_task is None

    remaining_first_windows = list(
        db_session.scalars(select(SliceWindow).where(SliceWindow.slice_task_id == first.id)).all()
    )
    assert remaining_first_windows == []

    remaining_first_links = list(
        db_session.scalars(
            select(SliceWindowLine)
            .join(SliceWindow, SliceWindow.id == SliceWindowLine.slice_window_id)
            .where(SliceWindow.slice_task_id == first.id)
        ).all()
    )
    assert remaining_first_links == []

    remaining_annotations = list(
        db_session.scalars(
            select(Annotation).where(Annotation.slice_window_id.in_([window.id for window in first_windows]))
        ).all()
    )
    assert remaining_annotations == []

    remaining_second_task = db_session.scalar(select(SliceTask).where(SliceTask.id == second.id))
    assert remaining_second_task is not None

    second_links = list(
        db_session.scalars(
            select(SliceWindowLine)
            .join(SliceWindow, SliceWindow.id == SliceWindowLine.slice_window_id)
            .where(SliceWindow.slice_task_id == second.id)
        ).all()
    )
    assert len(second_links) == 3

    remaining_source_files = list(
        db_session.scalars(select(SourceLogFile).where(SourceLogFile.package_id == pkg.id)).all()
    )
    remaining_source_lines = list(
        db_session.scalars(
            select(SourceLogLine).join(SourceLogFile).where(SourceLogFile.package_id == pkg.id)
        ).all()
    )
    assert len(remaining_source_files) == 2
    assert len(remaining_source_lines) == 4
