from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import DatasetPackage, ImportTask, SourceLogFile, SliceTask


def test_dataset_package_model_persists_and_queries() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        pkg = DatasetPackage(
            name="model-test",
            archive_type="zip",
            stored_path="/tmp/storage/packages/model-test.zip",
            file_size=128,
            sha256="a" * 64,
            description="model description",
            import_status="uploaded",
            import_error_message=None,
            source_file_count=0,
            source_line_count=0,
            cpu_count=0,
            module_count=0,
            earliest_timestamp=None,
            latest_timestamp=None,
        )
        db.add(pkg)
        db.commit()
        db.refresh(pkg)

        assert pkg.id > 0

        row = db.scalar(select(DatasetPackage).where(DatasetPackage.id == pkg.id))
        assert row is not None
        assert row.name == "model-test"
        assert row.archive_type == "zip"
        assert row.import_status == "uploaded"
        assert row.source_file_count == 0
        assert row.source_line_count == 0
        assert row.cpu_count == 0
        assert row.module_count == 0
        assert row.import_tasks == []
        assert row.source_log_files == []
        assert row.slice_tasks == []


def test_dataset_package_relationships_link_import_source_and_slice_rows() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        pkg = DatasetPackage(
            name="pkg-rel",
            archive_type="tar.gz",
            stored_path="/tmp/storage/packages/pkg-rel.tar.gz",
            file_size=512,
            sha256="c" * 64,
            description=None,
            import_status="imported",
            import_error_message=None,
            source_file_count=1,
            source_line_count=3,
            cpu_count=1,
            module_count=1,
            earliest_timestamp=1.0,
            latest_timestamp=3.0,
        )
        db.add(pkg)
        db.commit()
        db.refresh(pkg)

        import_task = ImportTask(package_id=pkg.id, status="completed", error_message=None)
        source_file = SourceLogFile(
            package_id=pkg.id,
            cpu_name="cpu0",
            module_path="module/a",
            filename="a.log",
            logical_path="cpu0/module/a.log",
            line_count=3,
            earliest_timestamp=1.0,
            latest_timestamp=3.0,
        )
        slice_task = SliceTask(
            package_id=pkg.id,
            name="slice-1",
            window_seconds=300,
            status="success",
            total_files=1,
            total_lines=3,
            total_windows=1,
            error_message=None,
        )
        db.add_all([import_task, source_file, slice_task])
        db.commit()
        db.refresh(pkg)

        row = db.scalar(select(DatasetPackage).where(DatasetPackage.id == pkg.id))
        assert row is not None
        assert len(row.import_tasks) == 1
        assert len(row.source_log_files) == 1
        assert len(row.slice_tasks) == 1
