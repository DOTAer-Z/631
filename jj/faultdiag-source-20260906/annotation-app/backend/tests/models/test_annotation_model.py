from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Annotation, DatasetPackage, SliceTask, SliceWindow


def _build_window(db: Session) -> SliceWindow:
        pkg = DatasetPackage(
            name="model-ann",
            archive_type="zip",
            stored_path="/tmp/storage/packages/model-ann.zip",
            file_size=256,
            sha256="b" * 64,
            description=None,
            import_status="imported",
            import_error_message=None,
            source_file_count=1,
            source_line_count=2,
            cpu_count=1,
            module_count=1,
            earliest_timestamp=0.0,
            latest_timestamp=60.0,
        )
        db.add(pkg)
        db.commit()
        db.refresh(pkg)

        task = SliceTask(
            package_id=pkg.id,
            name="task",
            window_seconds=300,
            status="success",
            total_files=1,
            total_lines=2,
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
            line_count=2,
            file_count=1,
            cpu_count=1,
            module_count=1,
        )
        db.add(window)
        db.commit()
        db.refresh(window)
        return window


def test_annotation_unique_constraint_on_slice_window_id() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        window = _build_window(db)

        first = Annotation(
            slice_window_id=window.id,
            label="normal",
            anomaly_type=None,
            note="ok",
        )
        db.add(first)
        db.commit()

        second = Annotation(
            slice_window_id=window.id,
            label="abnormal",
            anomaly_type="cpu",
            note="dup",
        )
        db.add(second)
        try:
            db.commit()
            raise AssertionError("expected unique constraint violation")
        except IntegrityError:
            db.rollback()

        rows = list(db.scalars(select(Annotation).where(Annotation.slice_window_id == window.id)))
        assert len(rows) == 1


def test_annotation_relationship_back_to_window() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        window = _build_window(db)
        row = Annotation(
            slice_window_id=window.id,
            label="abnormal",
            anomaly_type="cpu",
            note="high cpu",
        )
        db.add(row)
        db.commit()
        db.refresh(row)

        assert row.slice_window is not None
        assert row.slice_window.id == window.id
