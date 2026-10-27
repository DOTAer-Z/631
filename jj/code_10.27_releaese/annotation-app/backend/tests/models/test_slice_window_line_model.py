from __future__ import annotations

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import (
    DatasetPackage,
    SliceTask,
    SliceWindow,
    SliceWindowLine,
    SourceLogFile,
    SourceLogLine,
)


def _build_window_and_source_line(db: Session) -> tuple[SliceWindow, SourceLogLine]:
    pkg = DatasetPackage(
        name="window-line-model",
        archive_type="zip",
        stored_path="/tmp/storage/packages/window-line-model.zip",
        file_size=789,
        sha256="f" * 64,
        description=None,
        import_status="imported",
        import_error_message=None,
        source_file_count=1,
        source_line_count=1,
        cpu_count=1,
        module_count=1,
        earliest_timestamp=10.0,
        latest_timestamp=10.0,
    )
    db.add(pkg)
    db.commit()
    db.refresh(pkg)

    task = SliceTask(
        package_id=pkg.id,
        name="slice-task",
        window_seconds=300,
        status="success",
        total_files=1,
        total_lines=1,
        total_windows=1,
        error_message=None,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    window = SliceWindow(
        slice_task_id=task.id,
        window_start_ts=0.0,
        window_end_ts=300.0,
        line_count=1,
        file_count=1,
        cpu_count=1,
        module_count=1,
    )
    db.add(window)
    db.commit()
    db.refresh(window)

    source_file = SourceLogFile(
        package_id=pkg.id,
        cpu_name="cpu0",
        module_path="module/y",
        filename="y.log",
        logical_path="cpu0/module/y.log",
        line_count=1,
        earliest_timestamp=10.0,
        latest_timestamp=10.0,
    )
    db.add(source_file)
    db.commit()
    db.refresh(source_file)

    source_line = SourceLogLine(
        source_file_id=source_file.id,
        line_no=1,
        timestamp=10.0,
        content="10.0 line",
    )
    db.add(source_line)
    db.commit()
    db.refresh(source_line)
    return window, source_line


def test_slice_window_line_unique_constraint() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        window, source_line = _build_window_and_source_line(db)

        first = SliceWindowLine(
            slice_window_id=window.id,
            source_line_id=source_line.id,
            timestamp=10.0,
        )
        db.add(first)
        db.commit()

        second = SliceWindowLine(
            slice_window_id=window.id,
            source_line_id=source_line.id,
            timestamp=10.0,
        )
        db.add(second)
        try:
            db.commit()
            raise AssertionError("expected unique constraint violation")
        except IntegrityError:
            db.rollback()

        rows = list(
            db.scalars(select(SliceWindowLine).where(SliceWindowLine.slice_window_id == window.id))
        )
        assert len(rows) == 1


def test_slice_window_line_indexes_exist() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)

    indexes = {index["name"] for index in inspect(engine).get_indexes("ann_slice_window_lines")}
    assert "idx_slice_window_lines_slice_window_id" in indexes
    assert "idx_slice_window_lines_source_line_id" in indexes
