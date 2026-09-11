import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.main import app
from app.models.training_data import TrainingTest, TrainingTestVersion
from app.models.training_task import (
    TrainingArtifact,
    TrainingEvaluation,
    TrainingMetric,
    TrainingTask,
    TrainingTaskTest,
    TrainingWorker,
)
from app.schemas.model_training import (
    TrainingArtifactListOut,
    TrainingTaskDetailOut,
    TrainingTaskListOut,
    TrainingWorkerOut,
)


class ModelTrainingProductionImportTests(unittest.TestCase):
    def test_api_router_import_does_not_require_worker_model_package(self):
        backend_root = Path(__file__).resolve().parents[1]
        script = """
import builtins
original_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name == 'embedded_fault_diag' or name.startswith('embedded_fault_diag.'):
        raise AssertionError('FastAPI imported Worker-only model package')
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
import app.api.v1.model_training
"""
        environment = os.environ.copy()
        environment.update({
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(backend_root),
        })

        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=backend_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)


class ModelTrainingTaskRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temp_dir.name) / "outputs"
        self.output_root.mkdir()
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        for table in (
            TrainingTest.__table__,
            TrainingTestVersion.__table__,
            TrainingTask.__table__,
            TrainingArtifact.__table__,
            TrainingTaskTest.__table__,
            TrainingEvaluation.__table__,
            TrainingMetric.__table__,
            TrainingWorker.__table__,
        ):
            table.create(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.version_ids = [self._version(f"Test_{index}").id for index in range(1, 5)]
        self._seed_tasks()

        def get_test_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = get_test_db
        self.settings_patch = patch("app.api.v1.model_training.settings.TRAINING_OUTPUT_ROOT", str(self.output_root))
        self.settings_patch.start()
        self.run_sync_patch = patch(
            "anyio.to_thread.run_sync",
            new=self._run_sync_inline,
        )
        self.run_sync_patch.start()
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        self.settings_patch.stop()
        await self.client.aclose()
        self.run_sync_patch.stop()
        self.db.close()
        self.engine.dispose()
        self.temp_dir.cleanup()

    async def _run_sync_inline(self, func, *args, **kwargs):
        kwargs.pop("abandon_on_cancel", None)
        kwargs.pop("cancellable", None)
        kwargs.pop("limiter", None)
        return func(*args, **kwargs)

    def _version(self, name):
        test = TrainingTest(platform="NuttX", test_name=name)
        self.db.add(test)
        self.db.flush()
        version = TrainingTestVersion(
            test_id=test.id,
            version_number=1,
            content_sha256=f"{test.id:064d}",
            ground_truth={},
            fip_info={},
            completeness="complete",
            import_id=f"import-{test.id}",
            sample_class="fault",
            domain="kernel",
            fault_type="watchdog",
        )
        self.db.add(version)
        self.db.flush()
        return version

    def _task(self, *, name, task_type="cpt", state="queued", job_kind="training", queued_at=None):
        task = TrainingTask(
            name=name,
            task_type=task_type,
            job_kind=job_kind,
            state=state,
            model_id="qwen-qwen3.5-9b",
            config_snapshot={"training": {"learning_rate": 0.001}, "secret_path": "/not/for/api"},
            split_seed=631,
            queued_at=queued_at or datetime(2026, 7, 16, 8, 0, 0),
            error_message="failed at /private/training/output",
        )
        self.db.add(task)
        self.db.flush()
        return task

    def _artifact(self, task, name, *, artifact_type="final_adapter"):
        path = self.output_root / task.id / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
        artifact = TrainingArtifact(
            task_id=task.id,
            artifact_type=artifact_type,
            relative_path=f"{task.id}/{name}",
            metadata_json={"filename": name, "server_path": "/not/for/api"},
        )
        self.db.add(artifact)
        self.db.flush()
        return artifact

    def _uploaded_adapter(
        self,
        *,
        stage="cpt",
        base_model_id="qwen-qwen3.5-9b",
        metadata_overrides=None,
        relative_path=None,
        deleted_at=None,
    ):
        artifact_id = str(__import__("uuid").uuid4())
        metadata = {
            "source": "uploaded",
            "adapter_stage": stage,
            "base_model_id": base_model_id,
            "display_name": "Imported CPT",
            "description": "safe",
            "uploaded_archive_sha256": "b" * 64,
            "peft_type": "LORA",
            "rank": 2,
            "target_modules": ["q_proj"],
            "original_archive_format": "zip",
        }
        if metadata_overrides:
            metadata.update(metadata_overrides)
        artifact = TrainingArtifact(
            id=artifact_id,
            task_id=None,
            artifact_type="final_adapter",
            relative_path=relative_path or f"imports/{artifact_id}/final_adapter.tar",
            size_bytes=1024,
            sha256="a" * 64,
            metadata_json=metadata,
            deleted_at=deleted_at,
        )
        self.db.add(artifact)
        self.db.flush()
        return artifact

    def _seed_tasks(self):
        self.first = self._task(name="queued-first", queued_at=datetime(2026, 7, 16, 8, 0, 0))
        self.second = self._task(name="queued-second", queued_at=datetime(2026, 7, 16, 8, 1, 0))
        self.succeeded = self._task(
            name="succeeded", task_type="sft", state="succeeded", queued_at=datetime(2026, 7, 16, 8, 2, 0)
        )
        self.failed = self._task(name="failed", state="failed", queued_at=datetime(2026, 7, 16, 8, 3, 0))
        self.deleted = self._task(
            name="deleted", state="succeeded", queued_at=datetime(2026, 7, 16, 8, 4, 0)
        )
        self.deleted.deleted_at = datetime.utcnow()
        self.db.add_all(
            [
                TrainingTaskTest(task_id=self.succeeded.id, test_version_id=self.version_ids[0], split_name="train"),
                TrainingTaskTest(task_id=self.succeeded.id, test_version_id=self.version_ids[1], split_name="validation"),
                TrainingTaskTest(task_id=self.succeeded.id, test_version_id=self.version_ids[2], split_name="test"),
                TrainingMetric(task_id=self.succeeded.id, stream_offset=1, step=1, epoch=0.5, loss=1.2, created_at=datetime.utcnow() - timedelta(minutes=1)),
                TrainingMetric(task_id=self.succeeded.id, stream_offset=2, step=2, epoch=1.0, loss=0.4, eval_loss=0.5, created_at=datetime.utcnow()),
                TrainingEvaluation(task_id=self.succeeded.id, source_sft_task_id=self.succeeded.id, status="succeeded", summary={"accuracy": 0.9}),
                TrainingWorker(id="worker-1", status="busy", current_task_id=self.first.id, model_status="ready", gpu_snapshot={"gpu": "1", "token": "not-for-api"}),
            ]
        )
        self.log_artifact = self._artifact(self.succeeded, "public/training.log", artifact_type="log")
        self.adapter = self._artifact(self.succeeded, "adapter.bin")
        (self.output_root / self.succeeded.id / "public" / "training.log").write_text(
            "".join(f"line-{index}\n" for index in range(2003)), encoding="utf-8"
        )
        self.db.commit()

    def _route_for(self, path, method):
        for route in app.routes:
            if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
                return route
        self.fail(f"route not found: {method} {path}")

    def test_routes_are_registered_with_response_models(self):
        expected = (
            ("/api/v1/model-training/tasks", "GET", TrainingTaskListOut),
            ("/api/v1/model-training/tasks/{task_id}", "GET", TrainingTaskDetailOut),
            ("/api/v1/model-training/artifacts", "GET", TrainingArtifactListOut),
            ("/api/v1/model-training/worker", "GET", TrainingWorkerOut),
        )
        for path, method, response_model in expected:
            with self.subTest(path=path, method=method):
                self.assertIs(self._route_for(path, method).response_model, response_model)
        for path, method in (
            ("/api/v1/model-training/tasks", "POST"),
            ("/api/v1/model-training/tasks/{task_id}/cancel", "POST"),
            ("/api/v1/model-training/tasks/{task_id}/retry", "POST"),
            ("/api/v1/model-training/tasks/{task_id}/evaluate", "POST"),
            ("/api/v1/model-training/tasks/{task_id}/logs", "GET"),
            ("/api/v1/model-training/tasks/{task_id}", "DELETE"),
            ("/api/v1/model-training/artifacts/{artifact_id}/download", "GET"),
            ("/api/v1/model-training/artifacts/{artifact_id}", "DELETE"),
        ):
            with self.subTest(path=path, method=method):
                self._route_for(path, method)

    def test_worker_serializer_exposes_only_safe_compute_device_fields(self):
        from app.api.v1.model_training import get_training_worker

        worker = self.db.get(TrainingWorker, "worker-1")
        worker.gpu_snapshot = {
            "device_type": "cpu",
            "profile_name": "cpu_qlora_4bit_fp32",
            "device": "CPU",
            "quantization": "nf4_4bit",
            "compute_dtype": "fp32",
            "token": "private",
            "name": "NVIDIA private",
            "memory_total_mb": 24576,
        }
        self.db.commit()

        cpu = get_training_worker(self.db).model_dump()
        self.assertEqual(cpu["device_type"], "cpu")
        self.assertEqual(cpu["profile_name"], "cpu_qlora_4bit_fp32")
        self.assertEqual(cpu["device"], "CPU")
        self.assertIsNone(cpu["gpu"])
        self.assertNotIn("private", str(cpu))

        worker.gpu_snapshot = {
            "device_type": "cuda",
            "profile_name": "cuda_qlora_bf16",
            "device": "NVIDIA GeForce RTX 4090",
            "name": "NVIDIA GeForce RTX 4090",
            "memory_total_mb": 24576,
            "authorization": "Bearer private",
        }
        self.db.commit()
        cuda = get_training_worker(self.db).model_dump()
        self.assertEqual(
            {key: cuda[key] for key in ("device_type", "profile_name", "device")},
            {
                "device_type": "cuda",
                "profile_name": "cuda_qlora_bf16",
                "device": "NVIDIA GeForce RTX 4090",
            },
        )
        self.assertEqual(
            cuda["gpu"],
            {"name": "NVIDIA GeForce RTX 4090", "memory_total_mb": 24576},
        )
        self.assertNotIn("private", str(cuda))

        worker.gpu_snapshot = {
            "device_type": "cpu",
            "profile_name": "cuda_qlora_bf16",
            "device": "https://private.example",
            "name": "NVIDIA GeForce RTX 4090",
        }
        self.db.commit()
        invalid = get_training_worker(self.db).model_dump()
        self.assertIsNone(invalid["device_type"])
        self.assertIsNone(invalid["profile_name"])
        self.assertIsNone(invalid["device"])
        self.assertIsNone(invalid["gpu"])
        self.assertNotIn("private", str(invalid))

        worker.status = "offline"
        worker.gpu_snapshot = {
            "device_type": "cuda",
            "profile_name": "cuda_qlora_bf16",
            "device": "NVIDIA GeForce RTX 4090",
            "name": "NVIDIA GeForce RTX 4090",
        }
        self.db.commit()
        offline = get_training_worker(self.db).model_dump()
        for key in ("device_type", "profile_name", "device", "gpu"):
            self.assertIsNone(offline[key])

    def test_unknown_worker_status_redacts_stale_compute_device_snapshot(self):
        from app.api.v1.model_training import get_training_worker

        worker = self.db.get(TrainingWorker, "worker-1")
        worker.status = "Bearer unknown-private-status"
        worker.gpu_snapshot = {
            "device_type": "cuda",
            "profile_name": "cuda_qlora_bf16",
            "device": "NVIDIA GeForce RTX 4090",
            "name": "NVIDIA GeForce RTX 4090",
            "memory_total_mb": 24576,
        }
        self.db.commit()

        response = get_training_worker(self.db).model_dump()

        self.assertEqual(response["status"], "unknown")
        for key in ("device_type", "profile_name", "device", "gpu"):
            self.assertIsNone(response[key])
        self.assertNotIn("private", str(response))

    def test_repository_and_api_share_the_bounded_nvidia_name_policy(self):
        from app.api.v1.model_training import get_training_worker
        from app.training_worker import repository

        worker = self.db.get(TrainingWorker, "worker-1")
        for name in (
            "NVIDIA RTX A6000",
            "NVIDIA RTX PRO 6000 Blackwell Workstation Edition",
        ):
            with self.subTest(name=name):
                snapshot = repository._safe_gpu_snapshot({
                    "device_type": "cuda",
                    "profile_name": "cuda_qlora_bf16",
                    "device": name,
                    "name": name,
                    "memory_total_mb": 49140,
                })
                self.assertEqual(snapshot["name"], name)
                self.assertEqual(snapshot["device"], name)
                worker.status = "idle"
                worker.gpu_snapshot = snapshot
                self.db.commit()

                response = get_training_worker(self.db).model_dump()

                self.assertEqual(response["device"], name)
                self.assertEqual(response["gpu"]["name"], name)

        for name in ("RTX A6000", "NVIDIA RTX A6000\nprivate", "private GPU"):
            with self.subTest(rejected=name):
                snapshot = repository._safe_gpu_snapshot({
                    "device_type": "cuda",
                    "profile_name": "cuda_qlora_bf16",
                    "device": name,
                    "name": name,
                })
                self.assertEqual(snapshot, {})

    async def test_list_and_detail_are_deterministic_and_redact_server_paths(self):
        response = await self.client.get("/api/v1/model-training/tasks", params={"page_size": 2})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 4)
        self.assertEqual([item["id"] for item in body["items"]], [self.first.id, self.second.id])
        self.assertEqual([item["queue_position"] for item in body["items"]], [1, 2])
        self.assertNotIn("relative_path", response.text)
        self.assertNotIn("/not/for/api", response.text)
        self.assertNotIn("/private/training/output", response.text)

        filtered = await self.client.get("/api/v1/model-training/tasks", params={"state": "succeeded"})
        self.assertEqual([item["id"] for item in filtered.json()["items"]], [self.succeeded.id])
        self.assertIsNone(filtered.json()["items"][0]["queue_position"])

        detail = await self.client.get(f"/api/v1/model-training/tasks/{self.succeeded.id}")
        self.assertEqual(detail.status_code, 200)
        detail_body = detail.json()
        self.assertEqual(detail_body["split_counts"], {"train": 1, "validation": 1, "test": 1})
        self.assertEqual(detail_body["split_ids"]["test"], [self.version_ids[2]])
        self.assertEqual(detail_body["latest_metric"]["step"], 2)
        self.assertEqual(len(detail_body["metrics"]), 2)
        self.assertEqual(detail_body["artifacts"][0]["id"], self.log_artifact.id)
        self.assertNotIn("relative_path", detail.text)
        self.assertNotIn("/not/for/api", detail.text)

    async def test_list_uses_one_windowed_task_snapshot_query(self):
        statements = []

        def capture(_, __, statement, ___, ____, _____):
            if "training_tasks" in statement.lower() and statement.lstrip().upper().startswith(("SELECT", "WITH")):
                statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            response = await self.client.get("/api/v1/model-training/tasks", params={"state": "queued"})
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 2)
        self.assertEqual(len(statements), 1)
        self.assertIn("over", statements[0].lower())
        self.assertIn("row_number", statements[0].lower())

    async def test_empty_page_keeps_total_from_the_single_snapshot_statement(self):
        statements = []

        def capture(_, __, statement, ___, ____, _____):
            if "training_tasks" in statement.lower() and statement.lstrip().upper().startswith(("SELECT", "WITH")):
                statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            response = await self.client.get(
                "/api/v1/model-training/tasks",
                params={"state": "queued", "page": 99, "page_size": 2},
            )
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"items": [], "total": 2, "page": 99, "page_size": 2})
        self.assertEqual(len(statements), 1)

    async def test_task_header_projection_is_single_postgresql_statement(self):
        from sqlalchemy.dialects import postgresql

        from app.api.v1.model_training import _task_header_statement

        statement = _task_header_statement(self.first.id)
        compiled = str(statement.compile(dialect=postgresql.dialect()))
        rows = self.db.execute(statement).all()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0].id, self.first.id)
        self.assertIsNone(rows[0][1])
        self.assertEqual(rows[0][2], 1)
        self.assertIn("ROW_NUMBER", compiled.upper())
        self.assertIn("WITH", compiled.upper())
        self.assertNotIn("SQLITE", compiled.upper())

    async def test_detail_and_action_responses_use_the_shared_task_header(self):
        from app.api.v1 import model_training

        payload = {
            "name": "header task",
            "task_type": "cpt",
            "test_version_ids": self.version_ids,
            "preset": "quick",
            "train_ratio": 0.5,
            "validation_ratio": 0.25,
            "test_ratio": 0.25,
            "seed": 631,
        }
        with patch.object(model_training, "_task_header", wraps=model_training._task_header) as header:
            detail = await self.client.get(f"/api/v1/model-training/tasks/{self.first.id}")
            created = await self.client.post("/api/v1/model-training/tasks", json=payload)
            cancelled = await self.client.post(f"/api/v1/model-training/tasks/{self.second.id}/cancel")
            retried = await self.client.post(f"/api/v1/model-training/tasks/{self.failed.id}/retry")
            evaluated = await self.client.post(f"/api/v1/model-training/tasks/{self.succeeded.id}/evaluate")

        for response in (detail, created, cancelled, retried, evaluated):
            self.assertEqual(response.status_code, 200)
        self.assertEqual(header.call_count, 5)

    async def test_create_cancel_retry_evaluate_and_unknown_transition_statuses_own_transactions(self):
        payload = {
            "name": "new task",
            "task_type": "cpt",
            "test_version_ids": self.version_ids,
            "preset": "quick",
            "train_ratio": 0.5,
            "validation_ratio": 0.25,
            "test_ratio": 0.25,
            "seed": 631,
        }
        created = await self.client.post("/api/v1/model-training/tasks", json=payload)
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["state"], "queued")

        unknown = await self.client.post("/api/v1/model-training/tasks/missing/cancel")
        self.assertEqual(unknown.status_code, 404)
        invalid = await self.client.post(f"/api/v1/model-training/tasks/{self.succeeded.id}/cancel")
        self.assertEqual(invalid.status_code, 409)
        invalid_evaluation = await self.client.post(f"/api/v1/model-training/tasks/{self.first.id}/evaluate")
        self.assertEqual(invalid_evaluation.status_code, 409)
        invalid_create = await self.client.post("/api/v1/model-training/tasks", json={**payload, "test_version_ids": []})
        self.assertEqual(invalid_create.status_code, 422)

        unknown_version = await self.client.post(
            "/api/v1/model-training/tasks", json={**payload, "test_version_ids": [999999]}
        )
        self.assertEqual(unknown_version.status_code, 404)
        unavailable_adapter = await self.client.post(
            "/api/v1/model-training/tasks",
            json={**payload, "task_type": "sft", "cpt_adapter_artifact_id": "missing-adapter"},
        )
        self.assertEqual(unavailable_adapter.status_code, 404)
        invalid_adapter = self._artifact(self.succeeded, "checkpoint.bin", artifact_type="checkpoint")
        invalid_adapter_response = await self.client.post(
            "/api/v1/model-training/tasks",
            json={**payload, "task_type": "sft", "cpt_adapter_artifact_id": invalid_adapter.id},
        )
        self.assertEqual(invalid_adapter_response.status_code, 409)

        cancelled = await self.client.post(f"/api/v1/model-training/tasks/{self.first.id}/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["state"], "cancelled")
        retried = await self.client.post(f"/api/v1/model-training/tasks/{self.failed.id}/retry")
        self.assertEqual(retried.status_code, 200)
        evaluated = await self.client.post(f"/api/v1/model-training/tasks/{self.succeeded.id}/evaluate")
        self.assertEqual(evaluated.status_code, 200)
        self.assertEqual(evaluated.json()["job_kind"], "evaluation")

    async def test_retry_checkpoint_errors_are_precisely_mapped(self):
        other = self._task(name="other failed", state="failed")
        wrong_owner = self._artifact(other, "wrong-owner.bin", artifact_type="checkpoint")
        wrong_type = self._artifact(self.failed, "not-a-checkpoint.bin", artifact_type="final_adapter")
        deleted = self._artifact(self.failed, "deleted.bin", artifact_type="checkpoint")
        deleted.deleted_at = datetime.utcnow()
        self.db.commit()

        for checkpoint_id in ("missing-checkpoint", deleted.id):
            with self.subTest(checkpoint_id=checkpoint_id):
                response = await self.client.post(
                    f"/api/v1/model-training/tasks/{self.failed.id}/retry",
                    json={"resume_checkpoint_artifact_id": checkpoint_id},
                )
                self.assertEqual(response.status_code, 404)
        for checkpoint_id in (wrong_owner.id, wrong_type.id):
            with self.subTest(checkpoint_id=checkpoint_id):
                response = await self.client.post(
                    f"/api/v1/model-training/tasks/{self.failed.id}/retry",
                    json={"resume_checkpoint_artifact_id": checkpoint_id},
                )
                self.assertEqual(response.status_code, 409)

    async def test_sft_create_accepts_only_trusted_uploaded_cpt_metadata(self):
        payload = {
            "name": "uploaded CPT source",
            "task_type": "sft",
            "test_version_ids": self.version_ids,
            "preset": "quick",
            "train_ratio": 0.5,
            "validation_ratio": 0.25,
            "test_ratio": 0.25,
            "seed": 631,
        }
        uploaded = self._uploaded_adapter()
        self.db.commit()

        accepted = await self.client.post(
            "/api/v1/model-training/tasks",
            json={**payload, "cpt_adapter_artifact_id": uploaded.id},
        )

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["cpt_adapter_artifact_id"], uploaded.id)

        failed_cpt = self._task(name="failed CPT", task_type="cpt", state="failed")
        failed_trained = self._artifact(failed_cpt, "failed-cpt-adapter")
        invalid = [
            (self._uploaded_adapter(stage="sft"), 409),
            (self._uploaded_adapter(metadata_overrides={"source": "legacy"}), 409),
            (self._uploaded_adapter(metadata_overrides={"rank": True}), 409),
            (self._uploaded_adapter(metadata_overrides={"target_modules": [{}]}), 409),
            (
                self._uploaded_adapter(
                    metadata_overrides={"target_modules": ["q_proj", 1]}
                ),
                409,
            ),
            (self._uploaded_adapter(base_model_id="other-model"), 409),
            (
                self._uploaded_adapter(
                    relative_path=f"imports/{__import__('uuid').uuid4()}/final_adapter.tar"
                ),
                409,
            ),
            (
                self._uploaded_adapter(deleted_at=datetime.utcnow()),
                404,
            ),
            (self.adapter, 409),
            (failed_trained, 409),
        ]
        journaled = self._uploaded_adapter()
        journaled.deletion_state = "recovery_required"
        journaled.quarantine_name = f"{journaled.id}-{'c' * 32}"
        journaled.deletion_updated_at = datetime.utcnow()
        invalid.append((journaled, 404))
        self.db.commit()

        for artifact, status_code in invalid:
            with self.subTest(artifact_id=artifact.id, status_code=status_code):
                response = await self.client.post(
                    "/api/v1/model-training/tasks",
                    json={**payload, "cpt_adapter_artifact_id": artifact.id},
                )
                self.assertEqual(response.status_code, status_code)
                self.assertNotIn("relative_path", response.text)

    async def test_sft_create_acquires_registry_lock_before_adapter_and_source_rows(self):
        from sqlalchemy.orm import Query

        from app.services import training_task_service as service

        source = self._task(name="trusted CPT", task_type="cpt", state="succeeded")
        adapter = self._artifact(source, "trusted-cpt-adapter")
        self.db.commit()
        payload = {
            "name": "registry ordered SFT",
            "task_type": "sft",
            "test_version_ids": self.version_ids,
            "preset": "quick",
            "train_ratio": 0.5,
            "validation_ratio": 0.25,
            "test_ratio": 0.25,
            "seed": 631,
            "cpt_adapter_artifact_id": adapter.id,
        }
        events = []
        original_first = Query.first

        def capture_first(query):
            entity = query.column_descriptions[0].get("entity")
            if entity is TrainingArtifact:
                events.append("artifact_row")
            elif entity is TrainingTask:
                events.append("source_row")
            return original_first(query)

        with (
            patch.object(
                service,
                "acquire_artifact_registry_lock",
                create=True,
                side_effect=lambda _db: events.append("registry_lock"),
            ),
            patch.object(Query, "first", new=capture_first),
        ):
            response = await self.client.post("/api/v1/model-training/tasks", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(events[:3], ["registry_lock", "artifact_row", "source_row"])

    async def test_journaled_reference_artifacts_are_mapped_to_not_found(self):
        cpt = self._task(name="completed cpt", task_type="cpt", state="succeeded")
        adapter = self._artifact(cpt, "journaled-adapter.bin")
        adapter.deletion_state = "recovery_required"
        adapter.quarantine_name = f"{adapter.id}-{'a' * 32}"
        adapter.deletion_updated_at = datetime.utcnow()
        checkpoint = self._artifact(self.failed, "journaled-checkpoint.bin", artifact_type="checkpoint")
        checkpoint.deletion_state = "recovery_required"
        checkpoint.quarantine_name = f"{checkpoint.id}-{'b' * 32}"
        checkpoint.deletion_updated_at = datetime.utcnow()
        self.db.commit()

        create = await self.client.post(
            "/api/v1/model-training/tasks",
            json={
                "name": "journaled adapter",
                "task_type": "sft",
                "test_version_ids": self.version_ids,
                "preset": "quick",
                "train_ratio": 0.5,
                "validation_ratio": 0.25,
                "test_ratio": 0.25,
                "seed": 631,
                "cpt_adapter_artifact_id": adapter.id,
            },
        )
        retry = await self.client.post(
            f"/api/v1/model-training/tasks/{self.failed.id}/retry",
            json={"resume_checkpoint_artifact_id": checkpoint.id},
        )

        self.assertEqual(create.status_code, 404)
        self.assertEqual(retry.status_code, 404)

    async def test_log_cursor_is_bounded_deterministic_and_never_accepts_negative_values(self):
        response = await self.client.get(f"/api/v1/model-training/tasks/{self.succeeded.id}/logs")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["lines"]), 2000)
        self.assertEqual(body["lines"][0], "line-0")
        self.assertEqual(body["next_after_line"], 2000)
        self.assertTrue(body["has_more"])

        tail = await self.client.get(
            f"/api/v1/model-training/tasks/{self.succeeded.id}/logs", params={"after_line": 2000}
        )
        self.assertEqual(tail.json()["lines"], ["line-2000", "line-2001", "line-2002"])
        self.assertEqual(tail.json()["next_after_line"], 2003)
        self.assertFalse(tail.json()["has_more"])
        invalid = await self.client.get(
            f"/api/v1/model-training/tasks/{self.succeeded.id}/logs", params={"after_line": -1}
        )
        self.assertEqual(invalid.status_code, 422)

    def test_existing_task_without_registered_log_returns_an_empty_page(self):
        from app.api.v1.model_training import get_training_task_logs

        response = get_training_task_logs(self.first.id, after_line=17, db=self.db)

        self.assertEqual(
            response.model_dump(),
            {"lines": [], "next_after_line": 17, "has_more": False},
        )

    def test_active_training_task_reads_unregistered_public_log(self):
        from app.api.v1.model_training import get_training_task_logs

        self.first.state = "training"
        path = self.output_root / self.first.id / "public" / "training.log"
        path.parent.mkdir(parents=True)
        path.write_text("loading model\npreparing data\n", encoding="utf-8")
        self.db.commit()

        response = get_training_task_logs(self.first.id, after_line=1, db=self.db)

        self.assertEqual(
            response.model_dump(),
            {
                "lines": ["preparing data"],
                "next_after_line": 2,
                "has_more": False,
            },
        )

    def test_active_evaluation_task_reads_only_public_evaluation_log(self):
        from app.api.v1.model_training import get_training_task_logs

        evaluation = self._task(
            name="active-evaluation-log",
            task_type="sft",
            job_kind="evaluation",
            state="evaluating",
        )
        public = self.output_root / evaluation.id / "public"
        public.mkdir(parents=True)
        (public / "training.log").write_text("wrong log\n", encoding="utf-8")
        (public / "evaluation.log").write_text(
            "evaluation loading\n", encoding="utf-8"
        )
        self.db.commit()

        response = get_training_task_logs(evaluation.id, after_line=0, db=self.db)

        self.assertEqual(response.model_dump()["lines"], ["evaluation loading"])

    def test_terminal_task_without_registered_log_never_uses_mutable_public_file(self):
        from app.api.v1.model_training import get_training_task_logs

        path = self.output_root / self.failed.id / "public" / "training.log"
        path.parent.mkdir(parents=True)
        path.write_text("unregistered terminal log\n", encoding="utf-8")

        response = get_training_task_logs(self.failed.id, after_line=4, db=self.db)

        self.assertEqual(
            response.model_dump(),
            {"lines": [], "next_after_line": 4, "has_more": False},
        )

    def test_evaluation_logs_use_the_registered_public_evaluation_log(self):
        from app.api.v1.model_training import get_training_task_logs

        evaluation = self._task(
            name="evaluation-log",
            task_type="sft",
            job_kind="evaluation",
            state="succeeded",
        )
        path = self.output_root / evaluation.id / "public" / "evaluation.log"
        path.parent.mkdir(parents=True)
        path.write_text("evaluation line 1\nevaluation line 2\n", encoding="utf-8")
        self.db.add(TrainingArtifact(
            task_id=evaluation.id,
            artifact_type="log",
            relative_path=f"{evaluation.id}/public/evaluation.log",
            size_bytes=path.stat().st_size,
        ))
        self.db.commit()

        response = get_training_task_logs(evaluation.id, after_line=0, db=self.db)

        self.assertEqual(
            response.model_dump()["lines"],
            ["evaluation line 1", "evaluation line 2"],
        )

    async def test_logs_expose_only_the_registered_sanitized_log(self):
        raw_log = self.output_root / self.succeeded.id / "child.log"
        raw_log.write_text(
            f"output={self.output_root}\n"
            "url=https://user:password@private.example/v1\n"
            "api_key=sk-secret-value\n",
            encoding="utf-8",
        )
        sanitized_log = self.output_root / self.log_artifact.relative_path
        sanitized_log.write_text(
            "output=[REDACTED_PATH]\n"
            "url=https://[REDACTED]@private.example/v1\n"
            "api_key=[REDACTED]\n",
            encoding="utf-8",
        )
        metrics_log = self._artifact(self.succeeded, "metrics.jsonl", artifact_type="log")
        metrics_log.created_at = datetime(2020, 1, 1)
        self.log_artifact.created_at = datetime(2020, 1, 2)
        (self.output_root / metrics_log.relative_path).write_text(
            f"raw_path={self.output_root}\nsk-secret-value\n",
            encoding="utf-8",
        )
        self.db.commit()

        response = await self.client.get(f"/api/v1/model-training/tasks/{self.succeeded.id}/logs")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["lines"], sanitized_log.read_text(encoding="utf-8").splitlines())
        self.assertNotIn(str(self.output_root), response.text)
        self.assertNotIn("user:password", response.text)
        self.assertNotIn("sk-secret-value", response.text)
        log_artifacts = self.db.query(TrainingArtifact).filter_by(
            task_id=self.succeeded.id,
            artifact_type="log",
        ).all()
        self.assertIn(f"{self.succeeded.id}/public/training.log", [
            row.relative_path for row in log_artifacts
        ])
        self.assertFalse(any(row.relative_path.endswith("child.log") for row in log_artifacts))

    async def test_artifact_access_is_by_opaque_id_and_supports_file_not_directory_downloads(self):
        artifacts = await self.client.get("/api/v1/model-training/artifacts")
        self.assertEqual(artifacts.status_code, 200)
        self.assertEqual(artifacts.json()["total"], 2)
        self.assertNotIn("relative_path", artifacts.text)

        download = await self.client.get(f"/api/v1/model-training/artifacts/{self.adapter.id}/download")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, b"adapter.bin")
        path_like = await self.client.get("/api/v1/model-training/artifacts/../../etc/passwd/download")
        self.assertIn(path_like.status_code, {404, 422})

        directory = self.output_root / self.succeeded.id / "directory"
        directory.mkdir()
        directory_artifact = TrainingArtifact(
            task_id=self.succeeded.id,
            artifact_type="tokenizer",
            relative_path=f"{self.succeeded.id}/directory",
        )
        self.db.add(directory_artifact)
        self.db.commit()
        directory_download = await self.client.get(
            f"/api/v1/model-training/artifacts/{directory_artifact.id}/download"
        )
        self.assertEqual(directory_download.status_code, 409)

    async def test_journaled_artifacts_are_excluded_from_external_list_detail_and_download_access(self):
        journaled = self._artifact(self.succeeded, "journaled.bin")
        journaled.deletion_state = "recovery_required"
        journaled.quarantine_name = f"{journaled.id}-{'a' * 32}"
        journaled.deletion_updated_at = datetime.utcnow()
        self.db.commit()

        artifacts = await self.client.get("/api/v1/model-training/artifacts")
        detail = await self.client.get(f"/api/v1/model-training/tasks/{self.succeeded.id}")
        download = await self.client.get(f"/api/v1/model-training/artifacts/{journaled.id}/download")

        self.assertEqual(artifacts.status_code, 200)
        self.assertNotIn(journaled.id, [item["id"] for item in artifacts.json()["items"]])
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn(journaled.id, [item["id"] for item in detail.json()["artifacts"]])
        self.assertEqual(download.status_code, 404)

    async def test_download_and_logs_keep_open_descriptors_when_the_path_is_swapped(self):
        from app.services.training_artifact_service import open_artifact_file

        adapter_path = self.output_root / self.succeeded.id / "adapter.bin"
        log_path = self.output_root / self.succeeded.id / "public" / "training.log"
        outside = Path(self.temp_dir.name) / "outside.bin"
        outside.write_text("outside", encoding="utf-8")
        outside_log = Path(self.temp_dir.name) / "outside.log"
        outside_log.write_text("outside-log\n", encoding="utf-8")

        def open_then_swap(db, artifact_id, root):
            opened = open_artifact_file(db, artifact_id, root)
            if artifact_id == self.adapter.id:
                adapter_path.unlink()
                adapter_path.symlink_to(outside)
            elif artifact_id == self.log_artifact.id:
                log_path.unlink()
                log_path.symlink_to(outside_log)
            return opened

        with patch("app.api.v1.model_training.open_artifact_file", side_effect=open_then_swap):
            download = await self.client.get(f"/api/v1/model-training/artifacts/{self.adapter.id}/download")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, b"adapter.bin")
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside")

        with patch("app.api.v1.model_training.open_artifact_file", side_effect=open_then_swap):
            logs = await self.client.get(f"/api/v1/model-training/tasks/{self.succeeded.id}/logs")
        self.assertEqual(logs.status_code, 200)
        self.assertEqual(logs.json()["lines"][0], "line-0")
        self.assertEqual(outside_log.read_text(encoding="utf-8"), "outside-log\n")

    async def test_download_response_closes_descriptor_when_body_is_never_iterated(self):
        from app.api.v1.model_training import download_training_artifact
        from app.services.training_artifact_service import OpenedArtifactFile

        read_fd, write_fd = __import__("os").pipe()
        __import__("os").close(write_fd)
        opened = OpenedArtifactFile(read_fd)
        with patch("app.api.v1.model_training.open_artifact_file", return_value=opened):
            response = download_training_artifact(self.adapter.id, self.db)
        await response.background()
        self.assertEqual(opened.fd, -1)

    async def test_delete_staging_restores_after_commit_failure_and_cleanup_failure_converges(self):
        from app.services.training_artifact_service import reconcile_artifact_deletions

        owner = self._task(name="deletion-staging-owner", state="succeeded")
        adapter = self._artifact(owner, "adapter.bin")
        self.db.commit()
        artifact_path = self.output_root / owner.id / "adapter.bin"
        with patch.object(self.db, "commit", side_effect=RuntimeError("commit failed")):
            failed = await self.client.delete(f"/api/v1/model-training/artifacts/{adapter.id}")
        self.assertEqual(failed.status_code, 500)
        self.assertTrue(artifact_path.exists())
        self.assertIsNone(self.db.get(TrainingArtifact, adapter.id).deleted_at)

        with patch("app.api.v1.model_training.StagedArtifactDeletion.cleanup", side_effect=OSError("cleanup failed")):
            deleted = await self.client.delete(f"/api/v1/model-training/artifacts/{adapter.id}")
        self.assertEqual(deleted.status_code, 500)
        self.assertFalse(artifact_path.exists())
        artifact = self.db.get(TrainingArtifact, adapter.id)
        self.assertIsNotNone(artifact.deleted_at)
        self.assertEqual(artifact.deletion_state, "pending_cleanup")
        self.assertTrue(any((self.output_root / ".training-quarantine").iterdir()))

        reconcile_artifact_deletions(self.db, self.output_root)
        self.db.commit()

        artifact = self.db.get(TrainingArtifact, adapter.id)
        self.assertEqual(artifact.deletion_state, "cleaned")
        self.assertIsNone(artifact.quarantine_name)
        self.assertFalse(any((self.output_root / ".training-quarantine").iterdir()))

    async def test_delete_restore_failure_persists_recovery_journal_and_converges(self):
        from app.services.training_artifact_service import reconcile_artifact_deletions

        owner = self._task(name="deletion-recovery-owner", state="succeeded")
        adapter = self._artifact(owner, "adapter.bin")
        self.db.commit()
        artifact_path = self.output_root / owner.id / "adapter.bin"
        original_commit = self.db.commit
        commit_calls = 0

        def fail_initial_commit_then_commit_recovery():
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                raise RuntimeError("commit failed")
            original_commit()

        with patch.object(self.db, "commit", side_effect=fail_initial_commit_then_commit_recovery):
            with patch("app.api.v1.model_training.StagedArtifactDeletion.restore", side_effect=OSError("restore failed")):
                response = await self.client.delete(f"/api/v1/model-training/artifacts/{adapter.id}")

        self.assertEqual(response.status_code, 500)
        self.assertFalse(artifact_path.exists())
        artifact = self.db.get(TrainingArtifact, adapter.id)
        self.assertIsNone(artifact.deleted_at)
        self.assertEqual(artifact.deletion_state, "recovery_required")
        self.assertTrue(any((self.output_root / ".training-quarantine").iterdir()))

        reconcile_artifact_deletions(self.db, self.output_root)
        self.db.commit()

        artifact = self.db.get(TrainingArtifact, adapter.id)
        self.assertTrue(artifact_path.exists())
        self.assertIsNone(artifact.deleted_at)
        self.assertIsNone(artifact.deletion_state)

    async def test_delete_second_phase_commit_failure_leaves_staged_journal_for_reconciliation(self):
        from app.services.training_artifact_service import reconcile_artifact_deletions

        owner = self._task(name="deletion-second-phase-owner", state="succeeded")
        adapter = self._artifact(owner, "adapter.bin")
        self.db.commit()
        original_commit = self.db.commit
        commit_calls = 0

        def commit_stage_then_fail_journal():
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                original_commit()
                return
            raise RuntimeError("journal commit failed")

        with (
            patch("app.api.v1.model_training.StagedArtifactDeletion.cleanup", side_effect=OSError("cleanup failed")),
            patch.object(self.db, "commit", side_effect=commit_stage_then_fail_journal),
        ):
            response = await self.client.delete(f"/api/v1/model-training/artifacts/{adapter.id}")

        self.assertEqual(response.status_code, 500)
        artifact = self.db.get(TrainingArtifact, adapter.id)
        self.assertEqual(artifact.deletion_state, "staged")
        self.assertIsNotNone(artifact.quarantine_name)

        reconcile_artifact_deletions(self.db, self.output_root)
        self.db.commit()

        artifact = self.db.get(TrainingArtifact, adapter.id)
        self.assertEqual(artifact.deletion_state, "cleaned")
        self.assertIsNone(artifact.quarantine_name)

    async def test_delete_cleaned_journal_commit_failure_leaves_staged_journal_for_reconciliation(self):
        from app.services.training_artifact_service import reconcile_artifact_deletions

        owner = self._task(name="deletion-cleaned-owner", state="succeeded")
        adapter = self._artifact(owner, "adapter.bin")
        self.db.commit()
        original_commit = self.db.commit
        commit_calls = 0

        def commit_stage_then_fail_cleaned_journal():
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                original_commit()
                return
            raise RuntimeError("cleaned journal commit failed")

        with patch.object(self.db, "commit", side_effect=commit_stage_then_fail_cleaned_journal):
            response = await self.client.delete(f"/api/v1/model-training/artifacts/{adapter.id}")

        self.assertEqual(response.status_code, 500)
        artifact = self.db.get(TrainingArtifact, adapter.id)
        self.assertEqual(artifact.deletion_state, "staged")
        self.assertIsNotNone(artifact.quarantine_name)

        reconcile_artifact_deletions(self.db, self.output_root)
        self.db.commit()

        artifact = self.db.get(TrainingArtifact, adapter.id)
        self.assertEqual(artifact.deletion_state, "cleaned")
        self.assertIsNone(artifact.quarantine_name)

    async def test_artifact_and_task_deletion_protection_and_worker_response_redaction(self):
        protected = self._task(name="cpt", state="succeeded")
        adapter = self._artifact(protected, "protected-adapter")
        sft = self._task(name="dependent", task_type="sft", state="succeeded")
        sft.cpt_adapter_artifact_id = adapter.id
        self.db.commit()

        blocked = await self.client.delete(f"/api/v1/model-training/artifacts/{adapter.id}")
        self.assertEqual(blocked.status_code, 409)

        uploaded_sft = self._uploaded_adapter(stage="sft")
        uploaded_path = self.output_root / uploaded_sft.relative_path
        uploaded_path.parent.mkdir(parents=True)
        uploaded_path.write_bytes(b"uploaded SFT")
        external_evaluation = self._task(
            name="external evaluation",
            task_type="sft",
            state="failed",
            job_kind="evaluation",
        )
        external_evaluation.parent_task_id = None
        self.db.add(
            TrainingEvaluation(
                task_id=external_evaluation.id,
                source_sft_task_id=None,
                source_sft_artifact_id=uploaded_sft.id,
                status="failed",
            )
        )
        self.db.commit()
        external_blocked = await self.client.delete(
            f"/api/v1/model-training/artifacts/{uploaded_sft.id}"
        )
        self.assertEqual(external_blocked.status_code, 409)
        self.assertTrue(uploaded_path.is_file())

        active_delete = await self.client.delete(f"/api/v1/model-training/tasks/{self.second.id}")
        self.assertEqual(active_delete.status_code, 409)
        deleted = await self.client.delete(f"/api/v1/model-training/tasks/{self.succeeded.id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertIsNotNone(self.db.get(TrainingTask, self.succeeded.id).deleted_at)
        self.assertIsNotNone(self.db.get(TrainingArtifact, self.adapter.id))

        worker = await self.client.get("/api/v1/model-training/worker")
        self.assertEqual(worker.status_code, 200)
        self.assertEqual(worker.json()["queue_length"], 2)
        self.assertNotIn("gpu_snapshot", worker.text)
        self.assertNotIn("not-for-api", worker.text)

    async def test_response_serializers_use_explicit_whitelists_and_normalize_statuses(self):
        self.succeeded.config_snapshot = {
            "preset": "quick",
            "qlora": {"lora_r": 32, "authorization": "Bearer private"},
            "training": {"learning_rate": 0.001, "access_key": "private"},
            "cookie": "private",
        }
        evaluation = self.db.query(TrainingEvaluation).filter_by(task_id=self.succeeded.id).one()
        evaluation.summary = {
            "accuracy": 0.9,
            "per_class": {"normal": {"precision": 0.8}},
            "authorization": "Bearer private",
            "cookie": "private",
        }
        self.adapter.metadata_json = {
            "display_name": "sk-proj-private-value",
            "format": "safetensors",
            "access_key": "private",
            "bearer": "private",
        }
        worker = self.db.get(TrainingWorker, "worker-1")
        worker.status = "Bearer private"
        worker.model_status = "cookie private"
        worker.gpu_snapshot = {
            "index": 1,
            "name": "NVIDIA GeForce RTX 3090",
            "memory_total_mb": 24576,
            "utilization_percent": 50,
            "temperature_c": 70,
            "authorization": "Bearer private",
        }
        self.db.commit()

        detail = await self.client.get(f"/api/v1/model-training/tasks/{self.succeeded.id}")
        self.assertEqual(detail.status_code, 200)
        body = detail.json()
        self.assertEqual(body["config"], {
            "preset": "quick",
            "qlora": {"lora_r": 32},
            "training": {"learning_rate": 0.001},
        })
        self.assertEqual(body["evaluation"]["summary"], {"accuracy": 0.9, "per_class": {"normal": {"precision": 0.8}}})
        adapter = next(item for item in body["artifacts"] if item["id"] == self.adapter.id)
        self.assertEqual(adapter["metadata"], {"display_name": "Final adapter", "format": "safetensors"})

        worker_response = await self.client.get("/api/v1/model-training/worker")
        self.assertEqual(worker_response.status_code, 200)
        self.assertEqual(worker_response.json()["status"], "unknown")
        self.assertEqual(worker_response.json()["model_status"], "unknown")
        for key in ("device_type", "profile_name", "device", "gpu"):
            self.assertIsNone(worker_response.json()[key])
        self.assertNotIn("private", detail.text + worker_response.text)

    async def test_evaluation_summary_exposes_every_current_numeric_aggregate_only(self):
        metrics = {
            "json_valid": 1.0,
            "has_error_accuracy": 0.9,
            "fault_type_status_accuracy": 0.8,
            "known_fault_type_accuracy": 0.7,
            "new_candidate_detection": None,
            "evidence_recall": 0.5,
            "affected_component_match": 0.4,
            "affected_function_match": 0.3,
            "repair_suggestion_presence": 0.2,
            "root_cause_keyword_overlap": 0.1,
        }
        evaluation = self.db.query(TrainingEvaluation).filter_by(task_id=self.succeeded.id).one()
        evaluation.summary = {
            **metrics,
            "records": [{"prediction_path": "/private/prediction.txt"}],
            "prediction_path": "/private/summary.json",
            "authorization": "Bearer private",
        }
        self.db.commit()

        detail = await self.client.get(f"/api/v1/model-training/tasks/{self.succeeded.id}")

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["evaluation"]["summary"], metrics)
        self.assertNotIn("prediction_path", detail.text)
        self.assertNotIn("private", detail.text)

    async def test_response_serializers_reject_credentials_under_allowed_display_fields(self):
        self.adapter.metadata_json = {
            "display_name": "AKIAIOSFODNN7EXAMPLE",
            "format": "safetensors",
        }
        worker = self.db.get(TrainingWorker, "worker-1")
        self.db.commit()

        detail = await self.client.get(f"/api/v1/model-training/tasks/{self.succeeded.id}")
        adapter = next(item for item in detail.json()["artifacts"] if item["id"] == self.adapter.id)
        self.assertEqual(adapter["metadata"], {"display_name": "Final adapter", "format": "safetensors"})
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", detail.text)
        for gpu_name in ("sk-proj-private-value", "AKIAIOSFODNN7EXAMPLE", 3090):
            with self.subTest(gpu_name=gpu_name):
                worker.gpu_snapshot = {"name": gpu_name, "memory_total_mb": 81920}
                self.db.commit()
                worker_response = await self.client.get("/api/v1/model-training/worker")
                self.assertEqual(worker_response.json()["gpu"], {"memory_total_mb": 81920})
                self.assertNotIn(str(gpu_name), worker_response.text)

    async def test_response_serializers_reject_malicious_values_under_allowed_keys(self):
        self.succeeded.config_snapshot = {
            "preset": "quick",
            "qlora": {
                "load_in_4bit": 1,
                "bnb_4bit_quant_type": "https://private.example/nf4",
                "lora_r": "32",
                "lora_alpha": 64,
            },
            "training": {
                "learning_rate": "Bearer private",
                "num_train_epochs": float("inf"),
                "bf16": "true",
                "lr_scheduler_type": "https://private.example/cosine",
                "report_to": "private-token",
                "save_steps": 100,
            },
        }
        evaluation = self.db.query(TrainingEvaluation).filter_by(task_id=self.succeeded.id).one()
        evaluation.summary = {
            "accuracy": "Bearer private",
            "per_class": {"normal": {"precision": 0.8}},
            "loss": float("nan"),
        }
        self.adapter.metadata_json = {
            "display_name": "https://private.example/token",
            "format": "https://private.example/safetensors",
            "framework": "Bearer private",
            "base_model": "private-model",
            "step": "100",
            "epoch": float("inf"),
        }
        worker = self.db.get(TrainingWorker, "worker-1")
        worker.gpu_snapshot = {
            "index": "0",
            "name": "RTX 3090\nBearer private",
            "memory_total_mb": 24576,
            "memory_used_mb": "1024",
            "utilization_percent": float("nan"),
            "temperature_c": 70,
        }
        self.db.commit()

        detail = await self.client.get(f"/api/v1/model-training/tasks/{self.succeeded.id}")
        worker_response = await self.client.get("/api/v1/model-training/worker")

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["config"], {
            "preset": "quick",
            "qlora": {"lora_alpha": 64},
            "training": {"save_steps": 100},
        })
        self.assertEqual(detail.json()["evaluation"]["summary"], {"per_class": {"normal": {"precision": 0.8}}})
        adapter = next(item for item in detail.json()["artifacts"] if item["id"] == self.adapter.id)
        self.assertEqual(adapter["metadata"], {"display_name": "Final adapter"})
        self.assertEqual(worker_response.json()["gpu"], {
            "memory_total_mb": 24576,
            "temperature_c": 70,
        })
        self.assertNotIn("private", detail.text + worker_response.text)

    async def test_route_rolls_back_after_a_commit_failure(self):
        payload = {
            "name": "rollback",
            "task_type": "cpt",
            "test_version_ids": self.version_ids,
            "preset": "quick",
            "train_ratio": 0.5,
            "validation_ratio": 0.25,
            "test_ratio": 0.25,
            "seed": 631,
        }
        before = self.db.query(TrainingTask).count()
        with patch.object(self.db, "commit", side_effect=RuntimeError("commit failed")):
            response = await self.client.post("/api/v1/model-training/tasks", json=payload)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.db.query(TrainingTask).count(), before)
