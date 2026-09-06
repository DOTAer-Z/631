from __future__ import annotations

import io
import json
import zipfile

from sqlalchemy import select

from app.db.models import SourceLogFile, SourceLogLine
from app.services.archive_service import validate_archive
from app.services.import_service import ImportService
from app.services.import_worker import ImportWorker
from app.services.storage_service import StorageService
from tests.fakes import FakeLLMClient


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def _setup_package(db_session, files: dict[str, str], name: str):
    file_bytes = _zip_bytes(files)
    validated = validate_archive(f"{name}.zip", "application/zip", file_bytes)
    stored = StorageService().save_package_bytes(file_bytes, validated.extension)
    service = ImportService(db_session)
    pkg, task = service.create_upload(
        name=name,
        archive_type=validated.archive_type,
        stored_path=stored.stored_path,
        file_size=stored.file_size,
        sha256=stored.sha256,
        description=None,
    )
    return pkg, task


_EPOCH_SPEC = json.dumps(
    {
        "kind": "epoch",
        "regex": r"^(\d+\.\d+)",
        "strptime_fmt": "",
        "timezone": "UTC",
        "ts_at_line_start": True,
    }
)


def _lines_for(ts_values) -> str:
    return "".join(f"{value} message-{value}\n" for value in ts_values)


def test_worker_converges_to_package_spec_after_threshold(db_session, phase2_settings) -> None:
    # 5 cpus, identical epoch format -> converges at 3, stops calling the LLM.
    files = {f"data/cpu{i}/m/app.log": _lines_for([float(i) + 0.1]) for i in range(5)}
    pkg, task = _setup_package(db_session, files, "converge")

    fake = FakeLLMClient(responses=_EPOCH_SPEC)
    ImportWorker(db_session, llm_client=fake).run(import_task_id=task.id)

    assert fake.call_count == 3  # threshold default; remaining 2 cpus reuse unified spec

    lines = list(
        db_session.scalars(
            select(SourceLogLine).join(SourceLogFile).where(SourceLogFile.package_id == pkg.id)
        ).all()
    )
    assert len(lines) == 5
    assert all(line.timestamp is not None for line in lines)


def test_worker_no_convergence_infers_each_cpu(db_session, phase2_settings) -> None:
    # Only 2 cpus < threshold -> one inference per cpu, no convergence.
    files = {
        "data/cpu0/a.log": _lines_for([1.0, 2.0]),
        "data/cpu1/b.log": _lines_for([3.0]),
    }
    pkg, task = _setup_package(db_session, files, "no-converge")

    fake = FakeLLMClient(responses=_EPOCH_SPEC)
    ImportWorker(db_session, llm_client=fake).run(import_task_id=task.id)

    assert fake.call_count == 2
    lines = list(
        db_session.scalars(
            select(SourceLogLine).join(SourceLogFile).where(SourceLogFile.package_id == pkg.id)
        ).all()
    )
    assert len(lines) == 3
    assert all(line.timestamp is not None for line in lines)


def test_worker_degrades_to_rules_when_llm_unconfigured(db_session, phase2_settings) -> None:
    files = {"data/cpu0/a.log": _lines_for([1.0, 2.0])}
    pkg, task = _setup_package(db_session, files, "degrade")

    fake = FakeLLMClient(configured=False)
    ImportWorker(db_session, llm_client=fake).run(import_task_id=task.id)

    assert fake.call_count == 0  # never called the LLM
    lines = list(
        db_session.scalars(
            select(SourceLogLine).join(SourceLogFile).where(SourceLogFile.package_id == pkg.id)
        ).all()
    )
    # Rule-based parser still resolves the epoch timestamps.
    assert len(lines) == 2
    assert all(line.timestamp is not None for line in lines)


def test_worker_spec_miss_falls_back_to_rules_per_line(db_session, phase2_settings) -> None:
    # Spec parses epoch lines; an ISO line the spec misses is recovered by rules.
    files = {"data/cpu0/a.log": "1.0 epoch-line\n2018-09-29 16:12:39.365 iso-line\n"}
    pkg, task = _setup_package(db_session, files, "fallback")

    fake = FakeLLMClient(responses=_EPOCH_SPEC)
    ImportWorker(db_session, llm_client=fake).run(import_task_id=task.id)

    lines = list(
        db_session.scalars(
            select(SourceLogLine)
            .join(SourceLogFile)
            .where(SourceLogFile.package_id == pkg.id)
            .order_by(SourceLogLine.line_no)
        ).all()
    )
    assert len(lines) == 2
    assert all(line.timestamp is not None for line in lines)
