from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import DatasetPackage, ImportTask


def test_import_task_model_persists_and_links_to_package() -> None:
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
            name="import-task-model",
            archive_type="zip",
            stored_path="/tmp/storage/packages/import-task-model.zip",
            file_size=123,
            sha256="d" * 64,
            description=None,
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

        task = ImportTask(
            package_id=pkg.id,
            status="running",
            error_message=None,
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        row = db.scalar(select(ImportTask).where(ImportTask.id == task.id))
        assert row is not None
        assert row.package_id == pkg.id
        assert row.status == "running"
        assert row.package is not None
        assert row.package.id == pkg.id
