from __future__ import annotations

import asyncio
import io
import zipfile

from sqlalchemy import select

from app.api.v1.import_tasks import get_import_task
from app.api.v1.packages import create_package
from app.db.models import DatasetPackage, SourceLogFile, SourceLogLine
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
            zf.writestr(path, content)
    return buf.getvalue()


def test_import_flow_end_to_end(db_session, phase2_settings, monkeypatch) -> None:
    monkeypatch.setattr(ImportService, "start_import_task_async", lambda self, task_id, bind=None: None)

    created = asyncio.run(
        create_package(
            name="import-flow",
            description="integration import",
            file=DummyUpload(
                filename="import-flow.zip",
                content_type="application/zip",
                data=_zip_bytes(
                    {
                        "bundle/data/cpu0/modA/a.log": "1.0 start\n2.0 done\n",
                        "bundle/data/cpu0/modB/b.log": "3.0 next\n",
                    }
                ),
            ),
            db=db_session,
        )
    )

    task = get_import_task(task_id=created.import_task_id, db=db_session)
    assert task.status == "pending"

    service = ImportService(db_session)
    service.run_import_task(task_id=task.id)

    refreshed_task = get_import_task(task_id=task.id, db=db_session)
    pkg_row = db_session.get(DatasetPackage, created.id)
    source_files = list(db_session.scalars(select(SourceLogFile).where(SourceLogFile.package_id == created.id)).all())
    source_lines = list(
        db_session.scalars(
            select(SourceLogLine).join(SourceLogFile).where(SourceLogFile.package_id == created.id)
        ).all()
    )

    assert refreshed_task.status == "completed"
    assert pkg_row is not None
    assert pkg_row.import_status == "imported"
    assert pkg_row.source_file_count == 2
    assert pkg_row.source_line_count == 3
    assert pkg_row.cpu_count == 1
    assert pkg_row.module_count == 2
    assert len(source_files) == 2
    assert len(source_lines) == 3


def test_import_flow_accepts_single_child_dir_named_arbitrarily(db_session, phase2_settings, monkeypatch) -> None:
    monkeypatch.setattr(ImportService, "start_import_task_async", lambda self, task_id, bind=None: None)

    created = asyncio.run(
        create_package(
            name="import-flow-child-dir",
            description="integration import child dir",
            file=DummyUpload(
                filename="import-flow-child-dir.zip",
                content_type="application/zip",
                data=_zip_bytes(
                    {
                        "logs_root/CPU0/modA/a.log": "1.0 start\n",
                        "logs_root/CPU1/modB/b.log": "2.0 done\n",
                    }
                ),
            ),
            db=db_session,
        )
    )

    service = ImportService(db_session)
    service.run_import_task(task_id=created.import_task_id)

    pkg_row = db_session.get(DatasetPackage, created.id)
    source_files = list(db_session.scalars(select(SourceLogFile).where(SourceLogFile.package_id == created.id)).all())

    assert pkg_row is not None
    assert pkg_row.import_status == "imported"
    assert pkg_row.source_file_count == 2
    assert pkg_row.cpu_count == 2
    assert {row.cpu_name for row in source_files} == {"CPU0", "CPU1"}
