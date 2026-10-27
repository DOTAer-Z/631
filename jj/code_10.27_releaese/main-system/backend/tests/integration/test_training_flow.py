import json
import os
import struct
import zipfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.case import Case
from app.models.run import Run
from app.models.system import System
from app.models.training_data import (
    TrainingImportItem,
    TrainingTest,
    TrainingTestLog,
    TrainingTestVersion,
)
from app.models.training_task import (
    TrainingArtifact,
    TrainingEvaluation,
    TrainingMetric,
    TrainingTask,
    TrainingTaskTest,
    TrainingWorker,
)
from app.schemas.model_training import TrainingTaskCreate
from app.services import data_import_ingest_service
from app.services.training_artifact_service import (
    TrainingArtifactConflict,
    soft_delete_task,
    stage_artifact_deletion,
)
from app.services.training_task_service import create_evaluation, create_task
from app.training_worker.executor import (
    ProcessCancellationController,
    WorkerServices,
    execute_task,
)
from app.training_worker.main import WorkerRuntime


FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "training_test" / "Test_1"
TABLES = (
    System.__table__,
    TrainingTest.__table__,
    TrainingTestVersion.__table__,
    TrainingTestLog.__table__,
    TrainingImportItem.__table__,
    Case.__table__,
    Run.__table__,
    TrainingTask.__table__,
    TrainingArtifact.__table__,
    TrainingTaskTest.__table__,
    TrainingEvaluation.__table__,
    TrainingMetric.__table__,
    TrainingWorker.__table__,
)


class MemoryCollection:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    def delete_many(self, query: dict):
        self.documents = [doc for doc in self.documents if not self._matches(doc, query)]

    def insert_many(self, documents, ordered=False):
        inserted_ids = []
        for document in documents:
            stored = deepcopy(document)
            stored.setdefault("_id", f"memory-{len(self.documents) + 1}")
            self.documents.append(stored)
            inserted_ids.append(stored["_id"])
        return SimpleNamespace(inserted_ids=inserted_ids)

    def find(self, query: dict, projection=None, sort=None):
        documents = [deepcopy(doc) for doc in self.documents if self._matches(doc, query)]
        for key, direction in reversed(sort or []):
            documents.sort(key=lambda doc: doc.get(key), reverse=direction < 0)
        if projection:
            included = {key for key, enabled in projection.items() if enabled}
            documents = [
                {key: value for key, value in doc.items() if key in included}
                for doc in documents
            ]
        return documents

    @staticmethod
    def _matches(document: dict, query: dict) -> bool:
        for key, expected in query.items():
            actual = document.get(key)
            if expected == {"$type": "date"}:
                if not isinstance(actual, datetime):
                    return False
            elif actual != expected:
                return False
        return True


class MemoryMongoDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, MemoryCollection] = {}

    def __getitem__(self, name: str) -> MemoryCollection:
        return self.collections.setdefault(name, MemoryCollection())

    def command(self, name: str) -> dict[str, int]:
        assert name == "ping"
        return {"ok": 1}


def _write_safetensors(path: Path) -> None:
    header = json.dumps(
        {"weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}},
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(header)) + header + b"\0\0\0\0")


class FakeTrainingProcess:
    """Publish deterministic child outputs without CUDA or model inference."""

    def __init__(self, spec_path: str) -> None:
        self.pid = 14001
        self.returncode = None
        self.poll_count = 0
        spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
        task_root = Path(spec["task_root"])
        adapter = task_root / "run" / "final_adapter"
        adapter.mkdir(parents=True)
        (adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
        (adapter / "tokenizer.json").write_text("{}", encoding="utf-8")
        _write_safetensors(adapter / "adapter_model.safetensors")
        Path(spec["metrics_path"]).write_text(
            json.dumps(
                {
                    "step": 1,
                    "epoch": 0.1,
                    "loss": 1.0,
                    "eval_loss": None,
                    "learning_rate": 0.00005,
                    "timestamp": "2026-07-17T00:00:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        Path(spec["result_path"]).write_text(
            json.dumps({"status": "succeeded", "final_adapter": str(adapter)}),
            encoding="utf-8",
        )

    def poll(self):
        self.poll_count += 1
        if self.poll_count >= 2:
            self.returncode = 0
        return self.returncode


def _fixture_archive(tmp_path: Path, test_count: int = 4) -> Path:
    archive = tmp_path / "training-fixture.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for index in range(1, test_count + 1):
            for source in FIXTURE_DIR.rglob("*"):
                if source.is_file():
                    relative = source.relative_to(FIXTURE_DIR)
                    bundle.writestr(
                        f"Test_{index}/{relative.as_posix()}", source.read_bytes()
                    )
    return archive


def _task_request(name: str, task_type: str, version_ids: list[int], **changes):
    payload = {
        "name": name,
        "task_type": task_type,
        "test_version_ids": version_ids,
        "preset": "quick",
        "overrides": {},
        "train_ratio": 0.5,
        "validation_ratio": 0.25,
        "test_ratio": 0.25,
        "seed": 631,
    }
    payload.update(changes)
    return TrainingTaskCreate(**payload)


def test_fake_runtime_training_flow(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'training-flow.db'}")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE dataset_imports (import_id VARCHAR(64) PRIMARY KEY)")
        )
    for table in TABLES:
        table.create(bind=engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    model_root = tmp_path / "model"
    model_root.mkdir()
    (model_root / "config.json").write_text("{}", encoding="utf-8")

    with sessions.begin() as db:
        db.execute(
            text("INSERT INTO dataset_imports (import_id) VALUES (:import_id)"),
            {"import_id": "integration-import"},
        )

    mongo_db = MemoryMongoDatabase()
    with (
        mock.patch("app.database.SessionLocal", sessions),
        mock.patch("app.database.get_mongo_db", return_value=mongo_db),
    ):
        imported = data_import_ingest_service.run_ingest_pipeline(
            _fixture_archive(tmp_path), tmp_path / "extract", "integration-import"
        )

    assert imported.training_complete_count == 4
    assert imported.run_count == 8
    with sessions() as db:
        versions = db.query(TrainingTestVersion).order_by(TrainingTestVersion.id).all()
        version_ids = [version.id for version in versions]
        assert all(
            version.version_number == 1
            and version.completeness == "complete"
            and len(version.content_sha256) == 64
            for version in versions
        )
        assert all(
            {run.round_no for run in db.query(Run).filter_by(test_version_id=version_id)}
            == {1, 2}
            for version_id in version_ids
        )
        assert all(
            db.query(TrainingTestLog).filter_by(test_version_id=version_id).count() == 6
            for version_id in version_ids
        )
        immutable_snapshot = {
            version.id: (
                version.content_sha256,
                deepcopy(version.ground_truth),
                tuple(
                    (log.round_no, log.log_type, log.content)
                    for log in db.query(TrainingTestLog)
                    .filter_by(test_version_id=version.id)
                    .order_by(TrainingTestLog.round_no, TrainingTestLog.log_type)
                ),
            )
            for version in versions
        }

    with sessions.begin() as db:
        db.execute(
            text("INSERT INTO dataset_imports (import_id) VALUES (:import_id)"),
            {"import_id": "integration-duplicate"},
        )
    with (
        mock.patch("app.database.SessionLocal", sessions),
        mock.patch("app.database.get_mongo_db", return_value=mongo_db),
    ):
        duplicate = data_import_ingest_service.run_ingest_pipeline(
            _fixture_archive(tmp_path), tmp_path / "extract", "integration-duplicate"
        )
    assert duplicate.training_duplicate_count == 4
    with sessions() as db:
        assert db.query(TrainingTestVersion).count() == 4
        for version_id, snapshot in immutable_snapshot.items():
            version = db.get(TrainingTestVersion, version_id)
            logs = tuple(
                (log.round_no, log.log_type, log.content)
                for log in db.query(TrainingTestLog)
                .filter_by(test_version_id=version_id)
                .order_by(TrainingTestLog.round_no, TrainingTestLog.log_type)
            )
            assert (version.content_sha256, version.ground_truth, logs) == snapshot

    with sessions.begin() as db:
        cpt = create_task(db, _task_request("fixture CPT", "cpt", version_ids))
        cpt_id = cpt.id

    spawned_specs = []

    def fake_process_factory(argv, **_kwargs):
        spawned_specs.append(Path(argv[-1]))
        return FakeTrainingProcess(argv[-1])

    controller = ProcessCancellationController(killpg=lambda _pid, _signal: None)
    services = WorkerServices(
        session_factory=sessions,
        output_root=output_root,
        base_model_path=model_root,
        worker_id="integration-worker",
        process_factory=fake_process_factory,
        gpu_snapshot_provider=lambda: {
            "index": 0,
            "name": "NVIDIA Fake Runtime",
            "memory_total_mb": 24576,
            "memory_used_mb": 1024,
            "memory_free_mb": 23552,
            "utilization_percent": 0,
            "temperature_c": 30,
            "driver_version": "0.0",
        },
        disk_usage=lambda _path: SimpleNamespace(free=100 * 1024**3),
        sleep=lambda _seconds: None,
        heartbeat_seconds=0,
        cancellation_controller=controller,
        environ={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)},
    )
    runtime = WorkerRuntime(
        session_factory=sessions,
        worker_id="integration-worker",
        output_root=output_root,
        base_model_path=model_root,
        executor=lambda task_id: execute_task(task_id, services),
        cancellation_controller=controller,
        gpu_snapshot_provider=services.gpu_snapshot_provider,
        sleep=lambda _seconds: None,
    )
    runtime.run_forever(max_cycles=1)

    assert len(spawned_specs) == 1
    with sessions.begin() as db:
        cpt = db.get(TrainingTask, cpt_id)
        assert cpt.state == "succeeded"
        artifacts = db.query(TrainingArtifact).filter_by(task_id=cpt_id).all()
        artifact_types = {artifact.artifact_type for artifact in artifacts}
        assert {"final_adapter", "tokenizer", "config", "split", "dataset", "log"} <= artifact_types
        assert all((output_root / artifact.relative_path).is_file() for artifact in artifacts)
        cpt_adapter = next(
            artifact for artifact in artifacts if artifact.artifact_type == "final_adapter"
        )
        historical_dataset = next(
            artifact for artifact in artifacts if artifact.artifact_type == "dataset"
        )

        base_sft = create_task(db, _task_request("base SFT", "sft", version_ids))
        backed_sft = create_task(
            db,
            _task_request(
                "CPT-backed SFT",
                "sft",
                version_ids,
                cpt_adapter_artifact_id=cpt_adapter.id,
            ),
        )
        assert base_sft.cpt_adapter_artifact_id is None
        assert backed_sft.cpt_adapter_artifact_id == cpt_adapter.id

        source_test_ids = {
            row.test_version_id
            for row in db.query(TrainingTaskTest).filter_by(
                task_id=base_sft.id, split_name="test"
            )
        }
        assert source_test_ids
        base_sft.state = "succeeded"
        evaluation = create_evaluation(db, base_sft.id)
        frozen_evaluation_ids = {
            row.test_version_id
            for row in db.query(TrainingTaskTest).filter_by(task_id=evaluation.id)
        }
        assert frozen_evaluation_ids == source_test_ids
        evaluation_row = db.query(TrainingEvaluation).filter_by(task_id=evaluation.id).one()
        assert evaluation_row.source_sft_task_id == base_sft.id

        with pytest.raises(TrainingArtifactConflict, match="referenced by an SFT task"):
            stage_artifact_deletion(db, cpt_adapter.id, output_root)
        with pytest.raises(TrainingArtifactConflict, match="historical task data"):
            stage_artifact_deletion(db, historical_dataset.id, output_root)
        with pytest.raises(TrainingArtifactConflict, match="only terminal"):
            soft_delete_task(db, backed_sft.id)

        assert db.get(TrainingArtifact, cpt_adapter.id).deleted_at is None
        assert (output_root / cpt_adapter.relative_path).is_file()

    engine.dispose()
