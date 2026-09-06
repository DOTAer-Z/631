from __future__ import annotations

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import DatasetPackage, SourceLogFile, SourceLogLine


def test_source_log_models_persist_and_link() -> None:
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
            name="source-log-model",
            archive_type="zip",
            stored_path="/tmp/storage/packages/source-log-model.zip",
            file_size=456,
            sha256="e" * 64,
            description=None,
            import_status="imported",
            import_error_message=None,
            source_file_count=1,
            source_line_count=2,
            cpu_count=1,
            module_count=1,
            earliest_timestamp=1.0,
            latest_timestamp=2.0,
        )
        db.add(pkg)
        db.commit()
        db.refresh(pkg)

        source_file = SourceLogFile(
            package_id=pkg.id,
            cpu_name="cpu0",
            module_path="module/x",
            filename="x.log",
            logical_path="cpu0/module/x.log",
            line_count=2,
            earliest_timestamp=1.0,
            latest_timestamp=2.0,
        )
        db.add(source_file)
        db.commit()
        db.refresh(source_file)

        line1 = SourceLogLine(
            source_file_id=source_file.id,
            line_no=1,
            timestamp=1.0,
            content="1.0 started",
        )
        line2 = SourceLogLine(
            source_file_id=source_file.id,
            line_no=2,
            timestamp=2.0,
            content="2.0 finished",
        )
        db.add_all([line1, line2])
        db.commit()

        file_row = db.scalar(select(SourceLogFile).where(SourceLogFile.id == source_file.id))
        assert file_row is not None
        assert file_row.package is not None
        assert len(file_row.lines) == 2
        assert file_row.logical_path == "cpu0/module/x.log"


def test_source_log_line_indexes_exist() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)

    indexes = {index["name"] for index in inspect(engine).get_indexes("ann_source_log_lines")}
    assert "idx_source_log_lines_timestamp" in indexes
    assert "idx_source_log_lines_source_file_id_line_no" in indexes
