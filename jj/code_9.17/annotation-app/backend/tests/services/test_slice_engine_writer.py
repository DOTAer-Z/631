from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import DatasetPackage, SliceTask, SliceWindow, SliceWindowLine, SourceLogFile, SourceLogLine
from app.services.slice_engine.writer import WindowMaterializationInput, materialize_windows


def _seed_task_with_source_lines(db_session) -> tuple[SliceTask, list[SourceLogLine]]:
    package = DatasetPackage(
        name="writer-pkg",
        archive_type="zip",
        stored_path="/tmp/writer-pkg.zip",
        file_size=12,
        sha256="a" * 64,
        description=None,
        import_status="imported",
    )
    db_session.add(package)
    db_session.commit()
    db_session.refresh(package)

    file_a = SourceLogFile(
        package_id=package.id,
        cpu_name="cpu0",
        module_path="mod",
        filename="a.log",
        logical_path="cpu0/mod/a.log",
        line_count=2,
    )
    file_b = SourceLogFile(
        package_id=package.id,
        cpu_name="cpu1",
        module_path="net",
        filename="b.log",
        logical_path="cpu1/net/b.log",
        line_count=1,
    )
    db_session.add_all([file_a, file_b])
    db_session.commit()
    db_session.refresh(file_a)
    db_session.refresh(file_b)

    lines = [
        SourceLogLine(source_file_id=file_a.id, line_no=1, timestamp=1.0, content="1.0 alpha"),
        SourceLogLine(source_file_id=file_a.id, line_no=2, timestamp=2.0, content="2.0 beta"),
        SourceLogLine(source_file_id=file_b.id, line_no=1, timestamp=3.0, content="3.0 gamma"),
    ]
    db_session.add_all(lines)
    db_session.commit()
    for line in lines:
        db_session.refresh(line)

    task = SliceTask(package_id=package.id, name="slice-writer", window_seconds=300, status="running")
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    return task, lines


def test_materialize_windows_persists_windows_and_links(db_session) -> None:
    task, lines = _seed_task_with_source_lines(db_session)

    windows = materialize_windows(
        db=db_session,
        task_id=task.id,
        windows=[
            WindowMaterializationInput(
                window_start=0,
                window_end=300,
                source_line_ids=[line.id for line in lines],
                line_count=3,
                file_count=2,
                cpu_count=2,
                module_count=2,
            )
        ],
    )
    db_session.commit()

    assert len(windows) == 1
    persisted_window = db_session.scalar(select(SliceWindow).where(SliceWindow.id == windows[0].id))
    assert persisted_window is not None
    assert persisted_window.line_count == 3
    assert persisted_window.file_count == 2
    assert persisted_window.cpu_count == 2
    assert persisted_window.module_count == 2

    links = list(
        db_session.scalars(
            select(SliceWindowLine)
            .where(SliceWindowLine.slice_window_id == persisted_window.id)
            .order_by(SliceWindowLine.source_line_id)
        ).all()
    )
    assert [link.source_line_id for link in links] == [line.id for line in lines]
    assert [link.timestamp for link in links] == [1.0, 2.0, 3.0]


def test_materialize_windows_enforces_unique_window_line_links(db_session) -> None:
    task, lines = _seed_task_with_source_lines(db_session)

    with pytest.raises(IntegrityError):
        materialize_windows(
            db=db_session,
            task_id=task.id,
            windows=[
                WindowMaterializationInput(
                    window_start=0,
                    window_end=300,
                    source_line_ids=[lines[0].id, lines[0].id],
                    line_count=2,
                    file_count=1,
                    cpu_count=1,
                    module_count=1,
                )
            ],
        )
