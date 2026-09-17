import hashlib
import errno
import json
import math
import struct
import tarfile
import os
import signal
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
import yaml

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.training_task import (
    TrainingArtifact,
    TrainingEvaluation,
    TrainingMetric,
    TrainingTask,
    TrainingWorker,
)
from app.services.training_config_service import build_training_config
from app.training_contracts import EVALUATION_SCORE_KEYS
from app.services import training_artifact_service
from app.services import training_public_artifact_service
from app.training_worker.executor import (
    ExecutionError,
    ExecutionResult,
    ProcessCancellationController,
    TerminalPersistenceError,
    WorkerServices,
    _extract_adapter_archive,
    _lock_execution_graph,
    _monitor,
    _path_metadata,
    _persist_metric_records,
    execute_task,
)
from app.training_worker.child import ChildSpecError, ChildTaskSpec, run_spec
from app.training_worker.materializer import MaterializedTask
from app.training_worker import main as worker_main
from app.training_worker import executor as executor_module
from app.training_worker.executor import nvidia_smi_snapshot


def _write_safetensors(path: Path) -> None:
    header = json.dumps(
        {"weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}},
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(header)) + header + b"\0\0\0\0")


_CUDA_PROFILE = {
    "device_type": "cuda",
    "profile_name": "cuda_qlora_bf16",
    "quantization": "nf4_4bit",
    "compute_dtype": "bf16",
}


_CPU_PROFILE = {
    "device_type": "cpu",
    "profile_name": "cpu_qlora_4bit_bf16",
    "quantization": "nf4_4bit",
    "compute_dtype": "bf16",
}


_CPU_LORA_FP32_PROFILE = {
    "device_type": "cpu",
    "profile_name": "cpu_lora_fp32",
    "quantization": "none",
    "compute_dtype": "fp32",
}


class FakeProcess:
    def __init__(self, spec_path, *, outcome="success", cancel_mode=None):
        self.pid = 4321
        self.returncode = None
        self.spec_path = Path(spec_path)
        self.outcome = outcome
        self.cancel_mode = cancel_mode
        self.poll_count = 0
        self.terminated = False
        self.killed = False
        self.wait_count = 0
        self._publish()

    def _publish(self):
        spec = json.loads(self.spec_path.read_text(encoding="utf-8"))
        root = Path(spec["task_root"])
        metrics = Path(spec["metrics_path"])
        metrics.write_text(
            json.dumps({"step": 2, "epoch": 0.5, "loss": 1.25,
                        "eval_loss": 1.1, "learning_rate": 0.0001,
                        "timestamp": "2026-07-16T00:00:00Z"}) + "\n",
            encoding="utf-8",
        )
        profile = _CPU_PROFILE if spec["requested_device"] == "cpu" else _CUDA_PROFILE
        profile_path = Path(spec["profile_path"])
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        profile_path.chmod(0o600)
        if self.outcome == "success":
            if spec["job_kind"] == "evaluation":
                output = Path(spec["evaluation_output_dir"])
                output.mkdir(parents=True, exist_ok=True)
                summary = output / "summary.json"
                summary.write_text(
                    json.dumps({
                        "sft": {
                            "metrics": {
                                key: 0.5 for key in EVALUATION_SCORE_KEYS
                            }
                        }
                    }),
                    encoding="utf-8",
                )
                Path(spec["result_path"]).write_text(
                    json.dumps({
                        "status": "succeeded",
                        "evaluation_summary": str(summary),
                        **profile,
                    }),
                    encoding="utf-8",
                )
                return
            adapter_name = "final_adapter" if spec["task_type"] == "cpt" else "best_adapter"
            adapter = root / "run" / adapter_name
            adapter.mkdir(parents=True, exist_ok=True)
            (adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
            _write_safetensors(adapter / "adapter_model.safetensors")
            (adapter / "tokenizer.json").write_text("{}", encoding="utf-8")
            Path(spec["result_path"]).write_text(
                json.dumps({
                    "status": "succeeded",
                    "final_adapter": str(adapter),
                    **profile,
                }),
                encoding="utf-8",
            )
        else:
            checkpoint = root / "run" / "checkpoints" / "checkpoint-20"
            checkpoint.mkdir(parents=True, exist_ok=True)
            (checkpoint / "trainer_state.json").write_text("{}", encoding="utf-8")
            (checkpoint / "adapter_config.json").write_text("{}", encoding="utf-8")
            _write_safetensors(checkpoint / "adapter_model.safetensors")

    def poll(self):
        self.poll_count += 1
        if self.cancel_mode:
            return self.returncode
        if self.poll_count >= 2:
            self.returncode = 0 if self.outcome == "success" else 1
        return self.returncode

    def wait(self, timeout=None):
        self.wait_count += 1
        if self.cancel_mode == "forced" and self.wait_count == 1:
            raise TimeoutError("still running")
        self.returncode = -9 if self.killed else -15
        return self.returncode


class TrainingExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "outputs"
        self.root.mkdir()
        self.model = Path(self.temp_dir.name) / "model"
        self.model.mkdir()
        (self.model / "config.json").write_text("{}", encoding="utf-8")
        self.engine = create_engine("sqlite://")
        for table in (
            TrainingTask.__table__, TrainingArtifact.__table__,
            TrainingEvaluation.__table__, TrainingMetric.__table__,
            TrainingWorker.__table__,
        ):
            table.create(bind=self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.spawned = []
        self.process_outcome = "success"
        self.cancel_mode = None
        self.controller = ProcessCancellationController(killpg=self._killpg)

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_sanitized_log_is_atomically_replaced_and_redacted(self):
        source = self.root / "child.log"
        destination = self.root / "public" / "training.log"
        destination.parent.mkdir()
        destination.write_text("old\n", encoding="utf-8")
        source.write_text(
            f"root={self.root}\nmodel={self.model}\n"
            "url=https://user:password@example.test/v1\n"
            "api_key=sk-secret-value\n",
            encoding="utf-8",
        )
        real_replace = os.replace
        observed = []

        def capture_replace(
            source_name, destination_name, *, src_dir_fd, dst_dir_fd
        ):
            temporary_fd = os.open(source_name, os.O_RDONLY, dir_fd=src_dir_fd)
            with os.fdopen(temporary_fd, "r", encoding="utf-8") as handle:
                observed.append(handle.read())
            self.assertEqual(destination.read_text(encoding="utf-8"), "old\n")
            real_replace(
                source_name,
                destination_name,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )

        with patch.object(os, "replace", side_effect=capture_replace):
            training_public_artifact_service.write_sanitized_log(
                source, destination, self._services(), self.root
            )

        text = destination.read_text(encoding="utf-8")
        self.assertEqual(observed, [text])
        self.assertNotIn(str(self.root), text)
        self.assertNotIn(str(self.model), text)
        self.assertNotIn("user:password", text)
        self.assertNotIn("sk-secret-value", text)
        self.assertFalse(any(destination.parent.glob(".training.log.*.tmp")))

    def test_sanitized_log_replace_failure_preserves_previous_public_log(self):
        source = self.root / "child.log"
        destination = self.root / "public" / "training.log"
        destination.parent.mkdir()
        destination.write_text("previous public log\n", encoding="utf-8")
        source.write_text("new public log\n", encoding="utf-8")

        with patch.object(os, "replace", side_effect=OSError("replacement failed")):
            with self.assertRaisesRegex(OSError, "replacement failed"):
                training_public_artifact_service.write_sanitized_log(
                    source, destination, self._services(), self.root
                )

        self.assertEqual(
            destination.read_text(encoding="utf-8"), "previous public log\n"
        )
        self.assertFalse(any(destination.parent.glob(".training.log.*.tmp")))

    def test_sanitized_log_rejects_symlinked_source_and_public_directory(self):
        source = self.root / "child.log"
        destination = self.root / "public" / "training.log"
        outside_file = Path(self.temp_dir.name) / "outside.log"
        outside_file.write_text("private outside content\n", encoding="utf-8")
        source.symlink_to(outside_file)
        destination.parent.mkdir()

        with self.assertRaisesRegex(ValueError, "source"):
            training_public_artifact_service.write_sanitized_log(
                source, destination, self._services(), self.root
            )
        self.assertFalse(destination.exists())

        source.unlink()
        source.write_text("safe source\n", encoding="utf-8")
        destination.parent.rmdir()
        outside_directory = Path(self.temp_dir.name) / "outside-public"
        outside_directory.mkdir()
        outside_destination = outside_directory / "training.log"
        outside_destination.write_text("outside original\n", encoding="utf-8")
        destination.parent.symlink_to(outside_directory, target_is_directory=True)

        with self.assertRaisesRegex(ValueError, "destination"):
            training_public_artifact_service.write_sanitized_log(
                source, destination, self._services(), self.root
            )
        self.assertEqual(
            outside_destination.read_text(encoding="utf-8"), "outside original\n"
        )

    def test_live_sanitized_log_defers_partial_line_and_skips_unchanged_source(self):
        source = self.root / "child.log"
        destination = self.root / "public" / "training.log"
        destination.parent.mkdir()
        source.write_text("complete line\npartial", encoding="utf-8")

        source_size = training_public_artifact_service.write_sanitized_log(
            source,
            destination,
            self._services(),
            self.root,
            include_partial=False,
        )

        self.assertEqual(destination.read_text(encoding="utf-8"), "complete line\n")
        with patch.object(
            os,
            "replace",
            side_effect=AssertionError("unchanged source must not be republished"),
        ):
            unchanged_size = training_public_artifact_service.write_sanitized_log(
                source,
                destination,
                self._services(),
                self.root,
                include_partial=False,
                skip_source_size=source_size,
            )
        self.assertEqual(unchanged_size, source_size)

        with source.open("a", encoding="utf-8") as handle:
            handle.write(" remainder\n")
        training_public_artifact_service.write_sanitized_log(
            source,
            destination,
            self._services(),
            self.root,
            include_partial=False,
            skip_source_size=source_size,
        )
        self.assertEqual(
            destination.read_text(encoding="utf-8"),
            "complete line\npartial remainder\n",
        )

    def test_live_sanitized_log_has_a_bounded_snapshot_but_terminal_log_is_complete(self):
        source = self.root / "child.log"
        destination = self.root / "public" / "training.log"
        destination.parent.mkdir()
        original = "line-1\nline-2\nline-3\nline-4\n"
        source.write_text(original, encoding="utf-8")

        with patch.object(
            training_public_artifact_service,
            "_MAX_LIVE_PUBLIC_LOG_BYTES",
            16,
            create=True,
        ):
            source_size = training_public_artifact_service.write_sanitized_log(
                source,
                destination,
                self._services(),
                self.root,
                include_partial=False,
            )
            self.assertEqual(destination.read_text(encoding="utf-8"), "line-1\nline-2\n")

            with source.open("a", encoding="utf-8") as handle:
                handle.write("line-5\n")
            with patch.object(
                os,
                "replace",
                side_effect=AssertionError("capped live log must not be republished"),
            ):
                next_size = training_public_artifact_service.write_sanitized_log(
                    source,
                    destination,
                    self._services(),
                    self.root,
                    include_partial=False,
                    skip_source_size=source_size,
                )
            self.assertGreater(next_size, source_size)

            training_public_artifact_service.write_sanitized_log(
                source,
                destination,
                self._services(),
                self.root,
                include_partial=True,
            )
        self.assertEqual(
            destination.read_text(encoding="utf-8"), original + "line-5\n"
        )

    def test_monitor_publishes_sanitized_log_while_child_is_running(self):
        class RunningOnceProcess:
            pid = 9001

            def __init__(self):
                self.returncode = None
                self.poll_count = 0

            def poll(self):
                self.poll_count += 1
                if self.poll_count >= 2:
                    self.returncode = 0
                return self.returncode

        for job_kind, filename in (
            ("training", "training.log"),
            ("evaluation", "evaluation.log"),
        ):
            with self.subTest(job_kind=job_kind):
                task_id = self._task()
                task_root = self.root / task_id
                task_root.mkdir()
                child_log = task_root / "child.log"
                child_log.write_text(
                    f"loading from {self.model}\napi_key=sk-secret-value\n",
                    encoding="utf-8",
                )
                observed = []

                def assert_published(_seconds):
                    public_log = task_root / "public" / filename
                    text = public_log.read_text(encoding="utf-8")
                    observed.append(text)
                    self.assertIn("loading from [REDACTED_PATH]", text)
                    self.assertNotIn(str(self.model), text)
                    self.assertNotIn("sk-secret-value", text)

                state = _monitor(
                    RunningOnceProcess(),
                    task_id,
                    task_root / "metrics.jsonl",
                    self._services(sleep=assert_published),
                    job_kind=job_kind,
                )

                self.assertEqual(state, "finished")
                self.assertTrue(observed)
                self.assertTrue((task_root / "public" / filename).is_file())

    def _task(
        self,
        *,
        task_type="cpt",
        state="preparing_data",
        job_kind="training",
        **values,
    ):
        with self.sessions.begin() as db:
            task = TrainingTask(
                name="task", task_type=task_type, job_kind=job_kind, state=state,
                model_id="qwen-qwen3.5-9b", worker_id="gpu-worker-1",
                config_snapshot={"preset": "quick", "qlora": {
                    "load_in_4bit": True, "bnb_4bit_quant_type": "nf4",
                    "bnb_4bit_compute_dtype": "bfloat16",
                    "bnb_4bit_use_double_quant": True, "lora_r": 32,
                    "lora_alpha": 64, "lora_dropout": 0.05,
                }, "training": {
                    "max_seq_length": 2048, "learning_rate": 0.00005 if task_type == "cpt" else 0.0001,
                    "num_train_epochs": 0.1, "gradient_accumulation_steps": 16,
                    "per_device_train_batch_size": 1, "per_device_eval_batch_size": 1,
                    "warmup_ratio": 0.03 if task_type == "cpt" else 0.05,
                    "lr_scheduler_type": "cosine", "eval_strategy": "no",
                    "logging_steps": 5, "save_steps": 100 if task_type == "cpt" else 250,
                    "eval_steps": 100, "save_total_limit": 2, "bf16": True,
                    "report_to": "none",
                }},
                **values,
            )
            db.add(task)
            db.flush()
            worker = db.get(TrainingWorker, "gpu-worker-1")
            if worker is None:
                worker = TrainingWorker(id="gpu-worker-1")
                db.add(worker)
            worker.status = "busy"
            worker.current_task_id = task.id
            return task.id

    def _materialize(self, _db, task, task_dir):
        data = Path(task_dir) / "data"
        data.mkdir(parents=True, exist_ok=True)
        paths = MaterializedTask(
            split_path=data / "split.json", train_file=data / "train.jsonl",
            validation_file=data / "validation.jsonl", test_file=data / "test.jsonl",
        )
        paths.split_path.write_text('{"train": [], "validation": [], "test": []}\n', encoding="utf-8")
        for path in (paths.train_file, paths.validation_file, paths.test_file):
            path.write_text("{}\n", encoding="utf-8")
        return paths

    def _popen(self, argv, **kwargs):
        self.spawned.append((argv, kwargs))
        process = FakeProcess(
            argv[-1], outcome=self.process_outcome, cancel_mode=self.cancel_mode
        )
        kwargs["_process"] = process
        return process

    def _killpg(self, _pid, sig):
        process = self.spawned[-1][1]["_process"] if "_process" in self.spawned[-1][1] else None
        if process is not None:
            if sig == 15:
                process.terminated = True
            else:
                process.killed = True

    def _services(self, **changes):
        values = dict(
            session_factory=self.sessions, output_root=self.root,
            base_model_path=self.model, worker_id="gpu-worker-1",
            materialize=self._materialize, process_factory=self._popen,
            gpu_snapshot_provider=lambda: {"index": 0, "name": "NVIDIA RTX 4090",
                                            "memory_total_mb": 24564,
                                            "memory_free_mb": 22000},
            disk_usage=lambda _path: SimpleNamespace(free=100 * 1024**3),
            requested_device="cuda", cpu_profile="auto_low_memory",
            min_free_cpu_gb=24,
            virtual_memory=lambda: SimpleNamespace(available=100 * 1024**3),
            sleep=lambda _seconds: None, heartbeat_seconds=0,
            cancel_timeout_seconds=0.01, cancellation_controller=self.controller,
            environ={"PATH": os.environ.get("PATH", ""), "HOME": "/tmp",
                     "PYTHONPATH": "safe", "LLM_API_KEY": "secret",
                     "MODEL_API_ENCRYPTION_KEY": "secret", "ANNOTATE_TOKEN": "secret"},
        )
        values.update(changes)
        return WorkerServices(**values)

    def test_cpu_preflight_uses_available_memory_and_never_calls_gpu_provider(self):
        gpu_provider = Mock(side_effect=AssertionError("CPU path touched NVIDIA"))
        task_id = self._task()

        result = execute_task(
            task_id,
            self._services(
                requested_device="cpu",
                gpu_snapshot_provider=gpu_provider,
                virtual_memory=lambda: SimpleNamespace(available=100 * 1024**3),
            ),
        )

        self.assertEqual(result.state, "succeeded")
        gpu_provider.assert_not_called()
        spec = json.loads(Path(self.spawned[-1][0][-1]).read_text(encoding="utf-8"))
        self.assertEqual(spec["requested_device"], "cpu")
        self.assertIsNone(spec["required_device_profile"])
        self.assertEqual(
            spec["profile_path"],
            str(self.root / task_id / "effective-device-profile.json"),
        )
        with self.sessions() as db:
            worker = db.get(TrainingWorker, "gpu-worker-1")
            self.assertEqual(worker.gpu_snapshot, {
                "device_type": "cpu",
                "profile_name": "cpu_qlora_4bit_bf16",
                "device": "CPU",
            })

    def test_cpu_preflight_rejects_insufficient_available_memory_without_gpu_or_spawn(self):
        gpu_provider = Mock(side_effect=AssertionError("CPU path touched NVIDIA"))
        task_id = self._task()

        result = execute_task(
            task_id,
            self._services(
                requested_device="cpu",
                gpu_snapshot_provider=gpu_provider,
                virtual_memory=lambda: SimpleNamespace(available=23 * 1024**3),
            ),
        )

        self.assertEqual(result.state, "failed")
        self.assertEqual(
            result.error_message,
            "insufficient free system memory for training",
        )
        gpu_provider.assert_not_called()
        self.assertEqual(self.spawned, [])

    def test_worker_services_rejects_non_positive_cpu_memory_floor(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            self._services(min_free_cpu_gb=0)

    def test_preflight_rejects_missing_model_marker_low_disk_and_low_gpu_without_spawn(self):
        cases = ("model", "disk", "gpu")
        for case in cases:
            with self.subTest(case=case):
                task_id = self._task()
                services = self._services()
                if case == "model":
                    (self.model / "config.json").unlink()
                elif case == "disk":
                    services.disk_usage = lambda _path: SimpleNamespace(free=1)
                else:
                    services.gpu_snapshot_provider = lambda: {"index": 0, "memory_free_mb": 1}

                result = execute_task(task_id, services)

                self.assertEqual(result.state, "failed")
                with self.sessions() as db:
                    self.assertEqual(db.get(TrainingTask, task_id).state, "failed")
                if case == "model":
                    (self.model / "config.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.spawned, [])

    def test_default_runtime_uses_nvidia_smi_for_execution_preflight(self):
        captured = []
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "app.training_worker.main.execute_task",
            side_effect=lambda task_id, services: captured.append(services),
        ):
            runtime = worker_main.WorkerRuntime(
                session_factory=self.sessions,
                output_root=self.root,
                base_model_path=self.model,
            )
            runtime.executor("task-id")

        self.assertIs(captured[0].gpu_snapshot_provider, nvidia_smi_snapshot)

    def test_runtime_clears_only_local_id_after_durable_execution_result(self):
        task_id = self._task()
        runtime = worker_main.WorkerRuntime(
            session_factory=self.sessions,
            output_root=self.root,
            base_model_path=self.model,
            executor=lambda claimed_id: ExecutionResult(claimed_id, "succeeded"),
        )
        runtime.current_task_id = task_id

        runtime.execute_claimed_task(task_id)

        self.assertIsNone(runtime.current_task_id)
        with self.sessions() as db:
            worker = db.get(TrainingWorker, "gpu-worker-1")
            self.assertEqual((worker.status, worker.current_task_id), ("busy", task_id))

    def test_runtime_keeps_local_id_when_terminal_persistence_raises(self):
        task_id = self._task()

        def fail(_claimed_id):
            raise TerminalPersistenceError("terminal persistence failed")

        runtime = worker_main.WorkerRuntime(
            session_factory=self.sessions,
            output_root=self.root,
            base_model_path=self.model,
            executor=fail,
        )
        runtime.current_task_id = task_id

        with self.assertRaises(TerminalPersistenceError):
            runtime.execute_claimed_task(task_id)

        self.assertEqual(runtime.current_task_id, task_id)

    def test_execution_phases_use_worker_task_evaluation_artifact_lock_order(self):
        task_id = self._task()
        # SQLite strips FOR UPDATE, so compile the exact ORM statements against
        # PostgreSQL to verify lock tiers without requiring a live database.
        from sqlalchemy.dialects import postgresql

        captured = []
        original_query = self.sessions().query
        del original_query
        with self.sessions() as db:
            class QueryProxy:
                def __init__(self, query, model):
                    self.query = query
                    self.model = model

                def __getattr__(self, name):
                    attribute = getattr(self.query, name)
                    if not callable(attribute):
                        return attribute

                    def call(*args, **kwargs):
                        result = attribute(*args, **kwargs)
                        if hasattr(result, "statement"):
                            return QueryProxy(result, self.model)
                        return result

                    return call

                def all(self):
                    sql = str(
                        self.query.statement.compile(dialect=postgresql.dialect())
                    ).upper()
                    captured.append((self.model.__tablename__, " ".join(sql.split())))
                    return self.query.all()

                def one_or_none(self):
                    sql = str(
                        self.query.statement.compile(dialect=postgresql.dialect())
                    ).upper()
                    captured.append((self.model.__tablename__, " ".join(sql.split())))
                    return self.query.one_or_none()

            real_query = db.query

            def query(model, *entities):
                return QueryProxy(real_query(model, *entities), model)

            db.query = query
            with db.begin():
                _lock_execution_graph(db, task_id, "gpu-worker-1")

        locked_tables = [
            table for table, sql in captured if "FOR UPDATE" in sql
        ]
        self.assertEqual(locked_tables[:2], ["training_workers", "training_tasks"])
        task_sql = next(
            sql
            for table, sql in captured
            if table == "training_tasks" and "FOR UPDATE" in sql
        )
        self.assertIn("ORDER BY TRAINING_TASKS.ID", task_sql)

    def test_preflight_never_changes_or_spawns_an_unclaimed_task(self):
        task_id = self._task(state="queued")
        with self.sessions.begin() as db:
            task = db.get(TrainingTask, task_id)
            task.worker_id = None
            worker = db.get(TrainingWorker, "gpu-worker-1")
            worker.current_task_id = None
            worker.status = "idle"

        result = execute_task(task_id, self._services())

        self.assertEqual(result.state, "failed")
        with self.sessions() as db:
            self.assertEqual(db.get(TrainingTask, task_id).state, "queued")
        self.assertEqual(self.spawned, [])

    def test_task_root_symlink_alias_is_rejected_before_materialization(self):
        task_id = self._task()
        other_root = self.root / "other-task"
        other_data = other_root / "data"
        other_data.mkdir(parents=True)
        sentinel = other_data / "split.json"
        sentinel.write_text("other task frozen data\n", encoding="utf-8")
        (self.root / task_id).symlink_to(other_root, target_is_directory=True)

        result = execute_task(task_id, self._services())

        self.assertEqual(result.state, "failed")
        self.assertEqual(self.spawned, [])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "other task frozen data\n")
        with self.sessions() as db:
            task = db.get(TrainingTask, task_id)
            worker = db.get(TrainingWorker, "gpu-worker-1")
            self.assertEqual(task.state, "failed")
            self.assertEqual((worker.status, worker.current_task_id), ("idle", None))

    def test_claimed_task_root_setup_failure_is_durably_terminal(self):
        task_id = self._task()
        missing_root = Path(self.temp_dir.name) / "missing-output-root"

        result = execute_task(
            task_id,
            self._services(output_root=missing_root),
        )

        self.assertEqual(result.state, "failed")
        with self.sessions() as db:
            task = db.get(TrainingTask, task_id)
            worker = db.get(TrainingWorker, "gpu-worker-1")
            self.assertEqual(task.state, "failed")
            self.assertEqual((worker.status, worker.current_task_id), ("idle", None))
        self.assertEqual(self.spawned, [])

    def test_claimed_task_with_inconsistent_worker_graph_retains_local_ownership(self):
        for case in ("missing", "idle", "different_task"):
            with self.subTest(case=case):
                task_id = self._task()
                with self.sessions.begin() as db:
                    worker = db.get(TrainingWorker, "gpu-worker-1")
                    if case == "missing":
                        db.delete(worker)
                    elif case == "idle":
                        worker.status = "idle"
                    else:
                        worker.current_task_id = "another-task"

                with self.assertRaises(TerminalPersistenceError):
                    execute_task(task_id, self._services())

                with self.sessions() as db:
                    self.assertEqual(
                        db.get(TrainingTask, task_id).state,
                        "preparing_data",
                    )
                self.assertEqual(self.spawned, [])

    def test_child_spawn_occurs_while_final_running_lock_transaction_is_open(self):
        task_id = self._task()
        opened_sessions = []

        def session_factory():
            session = self.sessions()
            opened_sessions.append(session)
            return session

        def assert_locked_spawn(argv, **kwargs):
            self.assertTrue(any(session.in_transaction() for session in opened_sessions))
            return self._popen(argv, **kwargs)

        result = execute_task(
            task_id,
            self._services(
                session_factory=session_factory,
                process_factory=assert_locked_spawn,
            ),
        )

        self.assertEqual(result.state, "succeeded")

    def test_post_spawn_commit_and_termination_failure_retains_process_ownership(self):
        task_id = self._task()
        spawned = False
        calls = []

        def mark_spawned(argv, **kwargs):
            nonlocal spawned
            process = self._popen(argv, **kwargs)
            spawned = True
            return process

        def fail_post_spawn_commit(_session):
            if spawned:
                raise RuntimeError("running transition commit failed")

        def deny_termination(_pid, sent_signal):
            calls.append(sent_signal)
            raise PermissionError(errno.EPERM, "termination denied")

        controller = ProcessCancellationController(killpg=deny_termination)
        event.listen(self.sessions.class_, "before_commit", fail_post_spawn_commit)
        try:
            with self.assertRaises(TerminalPersistenceError):
                execute_task(
                    task_id,
                    self._services(
                        process_factory=mark_spawned,
                        cancellation_controller=controller,
                    ),
                )
        finally:
            event.remove(self.sessions.class_, "before_commit", fail_post_spawn_commit)

        self.assertEqual(calls, [signal.SIGTERM])
        self.assertEqual(controller._active, {task_id: 4321})
        with self.sessions() as db:
            task = db.get(TrainingTask, task_id)
            worker = db.get(TrainingWorker, "gpu-worker-1")
            self.assertEqual(task.state, "preparing_data")
            self.assertEqual((worker.status, worker.current_task_id), ("busy", task_id))

    def test_monitor_termination_failure_preserves_process_and_log_ownership(self):
        task_id = self._task()
        self.cancel_mode = "forced"
        signals = []

        def deny_termination(_pid, sent_signal):
            signals.append(sent_signal)
            raise PermissionError(errno.EPERM, "termination denied")

        controller = ProcessCancellationController(killpg=deny_termination)
        requested = False

        def request_external_stop(_seconds):
            nonlocal requested
            if not requested:
                requested = True
                controller._requested.add(task_id)

        with self.assertRaises(TerminalPersistenceError):
            execute_task(
                task_id,
                self._services(
                    cancellation_controller=controller,
                    sleep=request_external_stop,
                ),
            )

        process = self.spawned[-1][1]["_process"]
        self.assertEqual(signals, [signal.SIGKILL, signal.SIGTERM])
        self.assertEqual(controller._active, {task_id: process.pid})
        self.assertFalse(process._training_log_handle.closed)
        with self.sessions() as db:
            task = db.get(TrainingTask, task_id)
            worker = db.get(TrainingWorker, "gpu-worker-1")
            self.assertEqual(task.state, "training")
            self.assertEqual((worker.status, worker.current_task_id), ("busy", task_id))

    def test_post_spawn_rollback_cleans_descendants_after_leader_exit(self):
        task_id = self._task()
        spawned = False
        commit_failed = False
        signals = []

        def exited_leader(argv, **kwargs):
            nonlocal spawned
            process = self._popen(argv, **kwargs)
            process.returncode = 0
            spawned = True
            return process

        def fail_post_spawn_commit(_session):
            nonlocal commit_failed
            if spawned and not commit_failed:
                commit_failed = True
                raise RuntimeError("running transition commit failed")

        controller = ProcessCancellationController(
            killpg=lambda _pid, sent_signal: signals.append(sent_signal)
        )
        event.listen(self.sessions.class_, "before_commit", fail_post_spawn_commit)
        try:
            result = execute_task(
                task_id,
                self._services(
                    process_factory=exited_leader,
                    cancellation_controller=controller,
                    group_exists=lambda _pid: True,
                ),
            )
        finally:
            event.remove(self.sessions.class_, "before_commit", fail_post_spawn_commit)

        process = self.spawned[-1][1]["_process"]
        self.assertEqual(result.state, "failed")
        self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
        self.assertEqual(controller._active, {})
        self.assertTrue(process._training_log_handle.closed)

    def test_execution_graph_acquires_registry_lock_before_artifact_rows(self):
        source_id = self._task(state="failed")
        with self.sessions.begin() as db:
            source = db.get(TrainingTask, source_id)
            source.worker_id = None
            artifact = TrainingArtifact(
                task_id=source_id,
                artifact_type="checkpoint",
                relative_path=f"{source_id}/checkpoint.tar",
            )
            db.add(artifact)
            db.flush()
            current_id = self._task_id_in_session(
                db,
                parent_task_id=source_id,
                resume_checkpoint_artifact_id=artifact.id,
            )

        events = []
        original_all = __import__("sqlalchemy.orm", fromlist=["Query"]).Query.all

        def capture_all(query):
            if query.column_descriptions[0].get("entity") is TrainingArtifact:
                events.append("artifact_rows")
            return original_all(query)

        with patch.object(
            executor_module,
            "acquire_artifact_registry_lock",
            create=True,
            side_effect=lambda _db: events.append("registry_lock"),
        ), patch("sqlalchemy.orm.Query.all", new=capture_all):
            with self.sessions.begin() as db:
                _lock_execution_graph(db, current_id, "gpu-worker-1")

        self.assertIn("artifact_rows", events)
        self.assertEqual(events[0], "registry_lock")

    def _task_id_in_session(self, db, **values):
        task = TrainingTask(
            name="task",
            task_type="cpt",
            job_kind="training",
            state="preparing_data",
            model_id="qwen-qwen3.5-9b",
            worker_id="gpu-worker-1",
            config_snapshot={"training": {}},
            **values,
        )
        db.add(task)
        db.flush()
        worker = db.get(TrainingWorker, "gpu-worker-1")
        worker.status = "busy"
        worker.current_task_id = task.id
        return task.id

    def test_successful_cpt_uses_exact_child_command_sanitized_environment_metrics_and_artifacts(self):
        task_id = self._task()

        def process_with_secrets(argv, **kwargs):
            run_dir = self.root / task_id / "run"
            self.assertTrue(run_dir.is_dir())
            self.assertFalse(run_dir.is_symlink())
            process = self._popen(argv, **kwargs)
            child_log = self.root / task_id / "child.log"
            child_log.write_text(
                f"output={self.root}\nmodel={self.model}\n"
                "url=https://user:password@private.example/v1\n"
                "api_key=sk-secret-value\n",
                encoding="utf-8",
            )
            return process

        result = execute_task(task_id, self._services(process_factory=process_with_secrets))

        self.assertEqual(result.state, "succeeded")
        argv, kwargs = self.spawned[0]
        self.assertEqual(argv, [os.sys.executable, "-m", "app.training_worker.child", "--task-spec", argv[-1]])
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["env"]["CUDA_VISIBLE_DEVICES"], "0")
        self.assertNotIn("LLM_API_KEY", kwargs["env"])
        self.assertNotIn("MODEL_API_ENCRYPTION_KEY", kwargs["env"])
        self.assertNotIn("ANNOTATE_TOKEN", kwargs["env"])
        with self.sessions() as db:
            task = db.get(TrainingTask, task_id)
            self.assertEqual((task.state, task.progress), ("succeeded", 1.0))
            self.assertEqual(db.query(TrainingMetric).filter_by(task_id=task_id).count(), 1)
            artifacts = db.query(TrainingArtifact).filter_by(task_id=task_id).all()
            types = {row.artifact_type for row in artifacts}
            self.assertTrue({"final_adapter", "tokenizer", "config", "split", "dataset", "log"} <= types)
            for artifact in artifacts:
                registered = self.root / artifact.relative_path
                self.assertTrue(registered.is_file())
                self.assertFalse(registered.is_symlink())
            self.assertTrue(
                all(
                    row.relative_path.endswith(".tar")
                    for row in artifacts
                    if row.artifact_type in {"final_adapter", "tokenizer"}
                )
            )
            self.assertFalse(any(row.relative_path.endswith("child.log") for row in artifacts))
            self.assertFalse(any(row.relative_path.endswith("training.yaml") for row in artifacts))
            adapter = next(row for row in artifacts if row.artifact_type == "final_adapter")
            self.assertEqual(adapter.metadata_json, _CUDA_PROFILE)
            public_paths = [
                self.root / row.relative_path
                for row in artifacts
                if row.artifact_type in {"config", "log"}
            ]
        public_text = "\n".join(path.read_text(encoding="utf-8") for path in public_paths)
        self.assertNotIn(str(self.root), public_text)
        self.assertNotIn(str(self.model), public_text)
        self.assertNotIn("password", public_text)
        self.assertNotIn("sk-secret-value", public_text)
        self.assertFalse((self.root / task_id / "run" / "checkpoints").exists())

    def test_metric_ingestion_resumes_from_persisted_offsets_and_limits_each_call(self):
        task_id = self._task()
        path = self.root / task_id / "metrics.jsonl"
        path.parent.mkdir(exist_ok=True)
        records = [
            {
                "step": index,
                "epoch": index / 10,
                "loss": 1.0,
                "eval_loss": None,
                "learning_rate": 0.0001,
                "timestamp": "2026-07-16T00:00:00Z",
            }
            for index in range(105)
        ]
        path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

        first_offset = _persist_metric_records(task_id, path, 0, self._services())
        replay_offset = _persist_metric_records(task_id, path, 0, self._services())

        with self.sessions() as db:
            rows = (
                db.query(TrainingMetric)
                .filter_by(task_id=task_id)
                .order_by(TrainingMetric.stream_offset)
                .all()
            )
            self.assertEqual(len(rows), 105)
            self.assertEqual(len({row.stream_offset for row in rows}), 105)
            self.assertEqual(rows[0].stream_offset, 0)
        self.assertLess(first_offset, path.stat().st_size)
        self.assertEqual(replay_offset, path.stat().st_size)

    def test_legacy_negative_metric_offsets_do_not_seek_into_the_stream(self):
        task_id = self._task()
        with self.sessions.begin() as db:
            db.add(
                TrainingMetric(
                    task_id=task_id,
                    stream_offset=-1,
                    step=0,
                )
            )
        path = self.root / task_id / "metrics.jsonl"
        path.parent.mkdir(exist_ok=True)
        record = {
            "step": 1,
            "epoch": 0.1,
            "loss": 1.0,
            "eval_loss": None,
            "learning_rate": 0.0001,
            "timestamp": "2026-07-16T00:00:00Z",
        }
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        _persist_metric_records(task_id, path, 0, self._services())

        with self.sessions() as db:
            offsets = {
                row.stream_offset
                for row in db.query(TrainingMetric).filter_by(task_id=task_id)
            }
        self.assertEqual(offsets, {-1, 0})

    def test_fast_child_terminal_drain_persists_more_than_one_metric_batch(self):
        task_id = self._task()
        original_popen = self._popen

        def many_metrics(argv, **kwargs):
            process = original_popen(argv, **kwargs)
            spec = json.loads(Path(argv[-1]).read_text(encoding="utf-8"))
            records = [
                {
                    "step": index,
                    "epoch": index / 100,
                    "loss": 1.0,
                    "eval_loss": None,
                    "learning_rate": 0.0001,
                    "timestamp": "2026-07-16T00:00:00Z",
                }
                for index in range(205)
            ]
            Path(spec["metrics_path"]).write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            process.poll_count = 1
            return process

        result = execute_task(
            task_id,
            self._services(process_factory=many_metrics),
        )

        self.assertEqual(result.state, "succeeded")
        with self.sessions() as db:
            self.assertEqual(
                db.query(TrainingMetric).filter_by(task_id=task_id).count(),
                205,
            )

    def test_metric_ingestion_rejects_oversized_lines_and_non_utc_timestamps(self):
        task_id = self._task()
        path = self.root / task_id / "metrics.jsonl"
        path.parent.mkdir(exist_ok=True)
        base = {
            "step": 1,
            "epoch": 0.1,
            "loss": 1.0,
            "eval_loss": None,
            "learning_rate": 0.0001,
            "timestamp": "2026-07-16T00:00:00+08:00",
        }
        invalid_lines = [
            json.dumps(base) + "\n",
            json.dumps(dict(base, timestamp="2026-07-16T00:00:00")) + "\n",
            json.dumps(dict(base, timestamp="2026-07-16T00:00:00Z", loss=math.inf)) + "\n",
            (
                '{"step":1,"step":2,"epoch":0.1,"loss":1.0,'
                '"eval_loss":null,"learning_rate":0.0001,'
                '"timestamp":"2026-07-16T00:00:00Z"}\n'
            ),
            "{" + (" " * (64 * 1024)) + "}\n",
        ]
        for line in invalid_lines:
            with self.subTest(line_length=len(line)):
                path.write_text(line, encoding="utf-8")
                with self.assertRaises(ExecutionError):
                    _persist_metric_records(task_id, path, 0, self._services())

    def test_base_and_cpt_backed_sft_and_resume_use_only_validated_artifact_ids(self):
        cpt_source = self._task(state="succeeded")
        cpt_dir = self.root / cpt_source / "adapter"
        cpt_dir.mkdir(parents=True)
        (cpt_dir / "adapter_config.json").write_text("{}", encoding="utf-8")
        _write_safetensors(cpt_dir / "adapter_model.safetensors")
        sft_source = self._task(task_type="sft", state="failed")
        checkpoint_dir = self.root / sft_source / "checkpoint-40"
        checkpoint_dir.mkdir(parents=True)
        for name in ("adapter_config.json", "trainer_state.json"):
            (checkpoint_dir / name).write_text("{}", encoding="utf-8")
        _write_safetensors(checkpoint_dir / "adapter_model.safetensors")
        with self.sessions.begin() as db:
            adapter = TrainingArtifact(task_id=cpt_source, artifact_type="final_adapter",
                                       relative_path=f"{cpt_source}/adapter")
            checkpoint = TrainingArtifact(
                task_id=sft_source,
                artifact_type="checkpoint",
                relative_path=f"{sft_source}/checkpoint-40",
                metadata_json=_CUDA_PROFILE,
            )
            db.add_all([adapter, checkpoint]); db.flush()
            adapter_id, checkpoint_id = adapter.id, checkpoint.id
        sft_id = self._task(task_type="sft", cpt_adapter_artifact_id=adapter_id,
                            resume_checkpoint_artifact_id=checkpoint_id,
                            parent_task_id=sft_source)

        self.assertEqual(execute_task(sft_id, self._services()).state, "succeeded")

        spec = json.loads(Path(self.spawned[-1][0][-1]).read_text(encoding="utf-8"))
        local_cpt = self.root / sft_id / "inputs" / "cpt-adapter"
        local_resume = self.root / sft_id / "inputs" / "resume-checkpoint"
        self.assertEqual(Path(spec["cpt_adapter_path"]), local_cpt)
        self.assertEqual(Path(spec["resume_checkpoint"]), local_resume)
        self.assertEqual(spec["required_device_profile"], "cuda_qlora_bf16")
        config_path = self.root / sft_id / "training.yaml"
        config = config_path.read_text(encoding="utf-8")
        self.assertEqual(spec["config_sha256"], hashlib.sha256(config_path.read_bytes()).hexdigest())
        self.assertIn(str(local_cpt), config)
        self.assertNotIn(str(cpt_dir), config)
        self.assertTrue((local_cpt / "adapter_model.safetensors").is_file())
        self.assertTrue((local_resume / "trainer_state.json").is_file())
        self.assertFalse((self.root / sft_id / "run" / "final_adapter").exists())

    def _uploaded_cpt_archive(self, *, metadata_overrides=None, complete=True):
        artifact_id = str(uuid4())
        source = self.root / f"uploaded-source-{artifact_id}"
        source.mkdir()
        (source / "adapter_config.json").write_text("{}", encoding="utf-8")
        if complete:
            _write_safetensors(source / "adapter_model.safetensors")
        destination = self.root / "imports" / artifact_id / "final_adapter.tar"
        destination.parent.mkdir(parents=True)
        with tarfile.open(destination, "w") as archive:
            for path in sorted(source.iterdir()):
                archive.add(path, arcname=path.name)
        metadata = {
            "source": "uploaded",
            "adapter_stage": "cpt",
            "base_model_id": "qwen-qwen3.5-9b",
            "display_name": "Imported CPT",
            "description": None,
            "uploaded_archive_sha256": "b" * 64,
            "peft_type": "LORA",
            "rank": 2,
            "target_modules": ["q_proj"],
            "original_archive_format": "tar",
        }
        if metadata_overrides:
            metadata.update(metadata_overrides)
        with self.sessions.begin() as db:
            artifact = TrainingArtifact(
                id=artifact_id,
                task_id=None,
                artifact_type="final_adapter",
                relative_path=f"imports/{artifact_id}/final_adapter.tar",
                size_bytes=destination.stat().st_size,
                sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
                metadata_json=metadata,
            )
            db.add(artifact)
        return artifact_id, destination

    def test_uploaded_cpt_is_revalidated_and_safely_materialized_for_sft(self):
        artifact_id, archive = self._uploaded_cpt_archive()
        task_id = self._task(task_type="sft", cpt_adapter_artifact_id=artifact_id)

        result = execute_task(task_id, self._services())

        self.assertEqual(result.state, "succeeded")
        local = self.root / task_id / "inputs" / "cpt-adapter"
        spec = json.loads(Path(self.spawned[-1][0][-1]).read_text(encoding="utf-8"))
        self.assertEqual(Path(spec["cpt_adapter_path"]), local)
        self.assertTrue((local / "adapter_config.json").is_file())
        self.assertTrue((local / "adapter_model.safetensors").is_file())
        self.assertNotIn(str(archive), json.dumps(spec))

    def _external_evaluation_task(self, artifact_id, *, parent_task_id=None):
        task_id = self._task(
            task_type="sft",
            job_kind="evaluation",
            parent_task_id=parent_task_id,
        )
        with self.sessions.begin() as db:
            db.add(
                TrainingEvaluation(
                    task_id=task_id,
                    source_sft_task_id=None,
                    source_sft_artifact_id=artifact_id,
                    status="preparing_data",
                )
            )
        return task_id

    def test_uploaded_sft_evaluation_materializes_exact_archive_and_frozen_task_tests(self):
        artifact_id, archive = self._uploaded_cpt_archive(
            metadata_overrides={"adapter_stage": "sft"}
        )
        task_id = self._external_evaluation_task(artifact_id)
        materialized_task_ids = []

        def materialize(db, task, task_root):
            materialized_task_ids.append(task.id)
            return self._materialize(db, task, task_root)

        result = execute_task(
            task_id,
            self._services(materialize=materialize),
        )

        self.assertEqual(result.state, "succeeded")
        self.assertEqual(materialized_task_ids, [task_id])
        spec = json.loads(Path(self.spawned[-1][0][-1]).read_text(encoding="utf-8"))
        local_adapter = self.root / task_id / "inputs" / "sft-adapter"
        local_test = self.root / task_id / "inputs" / "test.jsonl"
        self.assertEqual(Path(spec["sft_adapter_path"]), local_adapter)
        self.assertEqual(Path(spec["test_file"]), local_test)
        self.assertIsNone(spec["cpt_adapter_path"])
        self.assertIsNone(spec["required_device_profile"])
        self.assertNotIn(str(archive), json.dumps(spec))
        self.assertTrue((local_adapter / "adapter_config.json").is_file())
        self.assertTrue((local_adapter / "adapter_model.safetensors").is_file())
        self.assertEqual(local_test.read_text(encoding="utf-8"), "{}\n")
        with self.sessions() as db:
            evaluation = db.query(TrainingEvaluation).filter_by(task_id=task_id).one()
            self.assertEqual(evaluation.status, "succeeded")
            self.assertEqual(set(evaluation.summary), set(EVALUATION_SCORE_KEYS))

    def test_uploaded_sft_evaluation_retry_preserves_artifact_lineage(self):
        artifact_id, _archive = self._uploaded_cpt_archive(
            metadata_overrides={"adapter_stage": "sft"}
        )
        parent_id = self._task(
            task_type="sft",
            state="failed",
            job_kind="evaluation",
        )
        with self.sessions.begin() as db:
            db.add(
                TrainingEvaluation(
                    task_id=parent_id,
                    source_sft_task_id=None,
                    source_sft_artifact_id=artifact_id,
                    status="failed",
                )
            )
        retry_id = self._external_evaluation_task(
            artifact_id,
            parent_task_id=parent_id,
        )

        result = execute_task(retry_id, self._services())

        self.assertEqual(result.state, "succeeded")
        spec = json.loads(Path(self.spawned[-1][0][-1]).read_text(encoding="utf-8"))
        self.assertEqual(
            Path(spec["sft_adapter_path"]),
            self.root / retry_id / "inputs" / "sft-adapter",
        )

    def test_uploaded_sft_evaluation_revalidates_metadata_and_archive_integrity(self):
        changed_id, _ = self._uploaded_cpt_archive(
            metadata_overrides={"adapter_stage": "sft"}
        )
        drifted_id, drifted_archive = self._uploaded_cpt_archive(
            metadata_overrides={"adapter_stage": "sft"}
        )
        directory_id, directory_archive = self._uploaded_cpt_archive(
            metadata_overrides={"adapter_stage": "sft"}
        )
        with self.sessions.begin() as db:
            changed = db.get(TrainingArtifact, changed_id)
            changed.metadata_json = {
                **changed.metadata_json,
                "adapter_stage": "cpt",
            }
        drifted_archive.write_bytes(drifted_archive.read_bytes() + b"changed")
        directory_archive.unlink()
        directory_archive.mkdir()
        (directory_archive / "adapter_config.json").write_text("{}", encoding="utf-8")
        _write_safetensors(directory_archive / "adapter_model.safetensors")

        for artifact_id in (changed_id, drifted_id, directory_id):
            with self.subTest(artifact_id=artifact_id):
                task_id = self._external_evaluation_task(artifact_id)
                result = execute_task(task_id, self._services())
                self.assertEqual(result.state, "failed")
                self.assertFalse(
                    (self.root / task_id / "inputs" / "sft-adapter").exists()
                )
        self.assertEqual(self.spawned, [])

    def test_task_backed_evaluation_execution_remains_unchanged(self):
        source_id = self._task(task_type="sft", state="succeeded")
        source_adapter = self.root / source_id / "adapter"
        source_adapter.mkdir(parents=True)
        (source_adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
        _write_safetensors(source_adapter / "adapter_model.safetensors")
        with self.sessions.begin() as db:
            source = db.get(TrainingTask, source_id)
            source.worker_id = None
            adapter = TrainingArtifact(
                task_id=source_id,
                artifact_type="final_adapter",
                relative_path=f"{source_id}/adapter",
            )
            db.add(adapter)
        task_id = self._task(
            task_type="sft",
            job_kind="evaluation",
            parent_task_id=source_id,
        )
        with self.sessions.begin() as db:
            db.add(
                TrainingEvaluation(
                    task_id=task_id,
                    source_sft_task_id=source_id,
                    source_sft_artifact_id=None,
                    status="preparing_data",
                )
            )

        result = execute_task(task_id, self._services())

        self.assertEqual(result.state, "succeeded")
        spec = json.loads(Path(self.spawned[-1][0][-1]).read_text(encoding="utf-8"))
        self.assertEqual(
            Path(spec["sft_adapter_path"]),
            self.root / task_id / "inputs" / "sft-adapter",
        )

    def test_uploaded_cpt_rejects_changed_metadata_and_malformed_archive_before_spawn(self):
        changed_id, _ = self._uploaded_cpt_archive()
        malformed_id, _ = self._uploaded_cpt_archive(complete=False)
        with self.sessions.begin() as db:
            changed = db.get(TrainingArtifact, changed_id)
            changed.metadata_json = {**changed.metadata_json, "adapter_stage": "sft"}

        task_ids = []
        for artifact_id in (changed_id, malformed_id):
            with self.subTest(artifact_id=artifact_id):
                task_id = self._task(
                    task_type="sft",
                    cpt_adapter_artifact_id=artifact_id,
                )
                task_ids.append(task_id)
                result = execute_task(task_id, self._services())
                self.assertEqual(result.state, "failed")
        self.assertEqual(self.spawned, [])
        self.assertTrue(
            all(not (self.root / task_id / "inputs" / "cpt-adapter").exists()
                for task_id in task_ids)
        )

    def test_uploaded_cpt_rejects_non_string_target_modules_with_stable_error(self):
        artifact_ids = [
            self._uploaded_cpt_archive(
                metadata_overrides={"target_modules": target_modules}
            )[0]
            for target_modules in ([{}], ["q_proj", 1])
        ]

        for artifact_id in artifact_ids:
            with self.subTest(artifact_id=artifact_id):
                task_id = self._task(
                    task_type="sft",
                    cpt_adapter_artifact_id=artifact_id,
                )
                result = execute_task(task_id, self._services())
                self.assertEqual(result.state, "failed")
                self.assertEqual(result.error_message, "CPT adapter artifact is not trusted")
        self.assertEqual(self.spawned, [])

    def test_uploaded_cpt_rejects_registered_file_replaced_by_legacy_directory(self):
        artifact_id, archive = self._uploaded_cpt_archive()
        archive.unlink()
        archive.mkdir()
        (archive / "adapter_config.json").write_text("{}", encoding="utf-8")
        _write_safetensors(archive / "adapter_model.safetensors")
        task_id = self._task(task_type="sft", cpt_adapter_artifact_id=artifact_id)

        result = execute_task(task_id, self._services())

        self.assertEqual(result.state, "failed")
        self.assertEqual(self.spawned, [])
        self.assertFalse((self.root / task_id / "inputs" / "cpt-adapter").exists())

    def test_uploaded_cpt_rejects_archive_content_drift_before_spawn(self):
        artifact_id, archive = self._uploaded_cpt_archive()
        archive.write_bytes(archive.read_bytes() + b"post-registration replacement")
        task_id = self._task(task_type="sft", cpt_adapter_artifact_id=artifact_id)

        result = execute_task(task_id, self._services())

        self.assertEqual(result.state, "failed")
        self.assertEqual(self.spawned, [])
        self.assertFalse((self.root / task_id / "inputs" / "cpt-adapter").exists())

    def test_uploaded_cpt_extracts_from_the_verified_descriptor_not_a_replaced_path(self):
        artifact_id, archive_path = self._uploaded_cpt_archive()
        task_id = self._task(task_type="sft", cpt_adapter_artifact_id=artifact_id)
        real_tar_open = tarfile.open
        replaced = False

        def replace_registered_path(*args, **kwargs):
            nonlocal replaced
            if kwargs.get("fileobj") is not None and not replaced:
                replacement = archive_path.with_name("replacement.tar")
                replacement.write_bytes(b"not a tar archive")
                os.replace(replacement, archive_path)
                replaced = True
            return real_tar_open(*args, **kwargs)

        with patch.object(executor_module.tarfile, "open", side_effect=replace_registered_path):
            result = execute_task(task_id, self._services())

        self.assertTrue(replaced)
        self.assertEqual(result.state, "succeeded")
        local = self.root / task_id / "inputs" / "cpt-adapter"
        self.assertTrue((local / "adapter_config.json").is_file())
        self.assertTrue((local / "adapter_model.safetensors").is_file())

    def test_archive_extraction_preflights_member_and_expanded_size_limits(self):
        source = self.root / "bounded-adapter"
        source.mkdir()
        (source / "adapter_config.json").write_text("{}", encoding="utf-8")
        _write_safetensors(source / "adapter_model.safetensors")
        archive_path = self.root / "bounded-adapter.tar"
        with tarfile.open(archive_path, "w") as archive:
            for path in sorted(source.iterdir()):
                archive.add(path, arcname=path.name)

        limits = (
            ("_MAX_ADAPTER_ARCHIVE_MEMBERS", 1),
            ("_MAX_ADAPTER_MEMBER_BYTES", 8),
            ("_MAX_ADAPTER_EXPANDED_BYTES", 8),
        )
        for constant, value in limits:
            destination = self.root / f"bounded-{constant}"
            with self.subTest(constant=constant):
                with (
                    patch.object(executor_module, constant, value),
                    self.assertRaises(ExecutionError),
                ):
                    _extract_adapter_archive(archive_path, destination)
                self.assertFalse(destination.exists())

    def test_resume_rejects_missing_profile_metadata_and_incompatible_worker(self):
        source = self._task(state="failed")
        checkpoint_dir = self.root / source / "checkpoint-12"
        checkpoint_dir.mkdir(parents=True)
        for name in ("trainer_state.json", "adapter_config.json"):
            (checkpoint_dir / name).write_text("{}", encoding="utf-8")
        _write_safetensors(checkpoint_dir / "adapter_model.safetensors")
        with self.sessions.begin() as db:
            checkpoint = TrainingArtifact(
                task_id=source,
                artifact_type="checkpoint",
                relative_path=f"{source}/checkpoint-12",
                metadata_json={},
            )
            db.add(checkpoint)
            db.flush()
            checkpoint_id = checkpoint.id

        missing_id = self._task(
            parent_task_id=source,
            resume_checkpoint_artifact_id=checkpoint_id,
        )
        missing = execute_task(missing_id, self._services())
        self.assertEqual(missing.state, "failed")
        self.assertEqual(self.spawned, [])

        with self.sessions.begin() as db:
            db.get(TrainingArtifact, checkpoint_id).metadata_json = _CUDA_PROFILE
        cpu_id = self._task(
            parent_task_id=source,
            resume_checkpoint_artifact_id=checkpoint_id,
        )
        incompatible = execute_task(
            cpu_id,
            self._services(requested_device="cpu"),
        )
        self.assertEqual(incompatible.state, "failed")
        self.assertEqual(self.spawned, [])

    def test_cpu_resume_preflight_uses_the_required_full_precision_profile(self):
        model_weights = self.model / "model.safetensors"
        with model_weights.open("wb") as handle:
            handle.truncate(16 * 1024**3)
        source = self._task(state="failed")
        checkpoint_dir = self.root / source / "checkpoint-12"
        checkpoint_dir.mkdir(parents=True)
        for name in ("trainer_state.json", "adapter_config.json"):
            (checkpoint_dir / name).write_text("{}", encoding="utf-8")
        _write_safetensors(checkpoint_dir / "adapter_model.safetensors")
        with self.sessions.begin() as db:
            checkpoint = TrainingArtifact(
                task_id=source,
                artifact_type="checkpoint",
                relative_path=f"{source}/checkpoint-12",
                metadata_json=_CPU_LORA_FP32_PROFILE,
            )
            db.add(checkpoint)
            db.flush()
            checkpoint_id = checkpoint.id
        task_id = self._task(
            parent_task_id=source,
            resume_checkpoint_artifact_id=checkpoint_id,
        )

        result = execute_task(
            task_id,
            self._services(
                requested_device="cpu",
                virtual_memory=lambda: SimpleNamespace(available=32 * 1024**3),
                gpu_snapshot_provider=Mock(
                    side_effect=AssertionError("CPU path touched NVIDIA")
                ),
            ),
        )

        self.assertEqual(result.state, "failed")
        self.assertEqual(
            result.error_message,
            "insufficient free system memory for training",
        )
        self.assertEqual(self.spawned, [])

    def test_profile_file_and_result_must_match_before_adapter_publication(self):
        task_id = self._task()
        original_popen = self._popen

        def mismatched_result(argv, **kwargs):
            process = original_popen(argv, **kwargs)
            spec = json.loads(Path(argv[-1]).read_text(encoding="utf-8"))
            result_path = Path(spec["result_path"])
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            payload.update(_CPU_PROFILE)
            result_path.write_text(json.dumps(payload), encoding="utf-8")
            return process

        result = execute_task(
            task_id,
            self._services(process_factory=mismatched_result),
        )

        self.assertEqual(result.state, "failed")
        with self.sessions() as db:
            self.assertIsNone(
                db.query(TrainingArtifact)
                .filter_by(task_id=task_id, artifact_type="final_adapter")
                .one_or_none()
            )

    def test_success_uses_the_profile_validated_with_the_child_result_once(self):
        task_id = self._task()
        original_load_result = executor_module._load_result

        def replace_profile_after_validation(*args, **kwargs):
            result = original_load_result(*args, **kwargs)
            profile_path = self.root / task_id / "effective-device-profile.json"
            profile_path.write_text(json.dumps(_CPU_PROFILE), encoding="utf-8")
            profile_path.chmod(0o600)
            return result

        with patch.object(
            executor_module,
            "_load_result",
            side_effect=replace_profile_after_validation,
        ):
            result = execute_task(task_id, self._services())

        self.assertEqual(result.state, "succeeded")
        with self.sessions() as db:
            adapter = (
                db.query(TrainingArtifact)
                .filter_by(task_id=task_id, artifact_type="final_adapter")
                .one()
            )
            self.assertEqual(adapter.metadata_json, _CUDA_PROFILE)

    def test_preflight_rejects_wrong_artifact_types_and_lineage(self):
        source = self._task(state="failed")
        path = self.root / source / "artifact"
        path.mkdir(parents=True)
        (path / "adapter_config.json").write_text("{}", encoding="utf-8")
        (path / "adapter_model.safetensors").write_bytes(b"x")
        with self.sessions.begin() as db:
            wrong_cpt = TrainingArtifact(task_id=source, artifact_type="checkpoint",
                                         relative_path=f"{source}/artifact")
            wrong_resume = TrainingArtifact(task_id=source, artifact_type="final_adapter",
                                            relative_path=f"{source}/artifact-2")
            db.add_all([wrong_cpt, wrong_resume]); db.flush()
            wrong_cpt_id, wrong_resume_id = wrong_cpt.id, wrong_resume.id
        for values in ({"cpt_adapter_artifact_id": wrong_cpt_id},
                       {"resume_checkpoint_artifact_id": wrong_resume_id}):
            with self.subTest(values=values):
                task_id = self._task(task_type="sft", **values)
                result = execute_task(task_id, self._services())
                self.assertEqual(result.state, "failed")
        self.assertEqual(self.spawned, [])

    def test_resume_rejects_checkpoint_from_incompatible_source_task(self):
        source = self._task(task_type="cpt", state="failed")
        checkpoint_dir = self.root / source / "checkpoint-12"
        checkpoint_dir.mkdir(parents=True)
        for name in ("trainer_state.json", "adapter_config.json"):
            (checkpoint_dir / name).write_text("{}", encoding="utf-8")
        _write_safetensors(checkpoint_dir / "adapter_model.safetensors")
        with self.sessions.begin() as db:
            checkpoint = TrainingArtifact(
                task_id=source,
                artifact_type="checkpoint",
                relative_path=f"{source}/checkpoint-12",
            )
            db.add(checkpoint)
            db.flush()
            checkpoint_id = checkpoint.id
        task_id = self._task(
            task_type="sft",
            parent_task_id=source,
            resume_checkpoint_artifact_id=checkpoint_id,
        )

        result = execute_task(task_id, self._services())

        self.assertEqual(result.state, "failed")
        self.assertEqual(self.spawned, [])

    def test_success_requires_a_tokenizer_artifact(self):
        task_id = self._task()
        original_popen = self._popen
        def adapter_without_tokenizer(argv, **kwargs):
            process = original_popen(argv, **kwargs)
            (self.root / task_id / "run" / "final_adapter" / "tokenizer.json").unlink()
            return process

        result = execute_task(task_id, self._services(process_factory=adapter_without_tokenizer))

        self.assertEqual(result.state, "failed")
        with self.sessions() as db:
            self.assertFalse(
                db.query(TrainingArtifact)
                .filter_by(task_id=task_id, artifact_type="final_adapter")
                .first()
            )

    def test_terminal_persistence_failure_preserves_recoverable_outputs_and_ownership(self):
        task_id = self._task()
        injected = False

        def fail_succeeded_flush(session, _context, _instances):
            nonlocal injected
            if not injected and any(
                isinstance(row, TrainingTask) and row.state == "succeeded"
                for row in session.dirty
            ):
                injected = True
                raise RuntimeError("terminal flush failed")

        event.listen(self.sessions.class_, "before_flush", fail_succeeded_flush)
        try:
            with self.assertRaisesRegex(TerminalPersistenceError, "terminal persistence failed"):
                execute_task(task_id, self._services())
        finally:
            event.remove(self.sessions.class_, "before_flush", fail_succeeded_flush)

        with self.sessions() as db:
            self.assertEqual(db.get(TrainingTask, task_id).state, "training")
            worker = db.get(TrainingWorker, "gpu-worker-1")
            self.assertEqual((worker.status, worker.current_task_id), ("busy", task_id))
            self.assertEqual(db.query(TrainingArtifact).filter_by(task_id=task_id).count(), 0)
        task_root = self.root / task_id
        self.assertTrue((task_root / "run" / "final_adapter").is_dir())
        self.assertTrue((task_root / "artifacts" / "final_adapter.tar").is_file())

    def test_metric_poller_waits_for_a_complete_jsonl_record(self):
        task_id = self._task(state="training")
        path = self.root / task_id / "metrics.jsonl"
        path.parent.mkdir(exist_ok=True)
        record = {"step": 3, "epoch": 0.05, "loss": 1.0, "eval_loss": None,
                  "learning_rate": 0.0001, "timestamp": "2026-07-16T00:00:00Z"}
        encoded = json.dumps(record)
        path.write_text(encoded[:-1], encoding="utf-8")

        offset = _persist_metric_records(task_id, path, 0, self._services())

        self.assertEqual(offset, 0)
        with self.sessions() as db:
            self.assertEqual(db.query(TrainingMetric).filter_by(task_id=task_id).count(), 0)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(encoded[-1] + "\n")
        offset = _persist_metric_records(task_id, path, offset, self._services())
        self.assertGreater(offset, 0)
        with self.sessions() as db:
            self.assertEqual(db.query(TrainingMetric).filter_by(task_id=task_id).count(), 1)

    def test_monitor_failure_terminates_the_active_process_group(self):
        task_id = self._task()
        self.cancel_mode = "graceful"
        original_popen = self._popen
        def malformed_metrics(argv, **kwargs):
            process = original_popen(argv, **kwargs)
            spec = json.loads(Path(argv[-1]).read_text(encoding="utf-8"))
            Path(spec["metrics_path"]).write_text("not-json\n", encoding="utf-8")
            return process
        calls = []
        controller = ProcessCancellationController(
            killpg=lambda pid, sig: calls.append((pid, sig))
        )

        result = execute_task(
            task_id,
            self._services(
                process_factory=malformed_metrics,
                cancellation_controller=controller,
            ),
        )

        self.assertEqual(result.state, "failed")
        self.assertEqual(calls[0][1], 15)

    def test_failure_retains_only_highest_valid_checkpoint_and_rejects_partial_adapter(self):
        task_id = self._task()
        self.process_outcome = "failure"

        original_popen = self._popen
        def popen_with_extra_checkpoints(argv, **kwargs):
            process = original_popen(argv, **kwargs)
            root = self.root / task_id / "run" / "checkpoints"
            older = root / "checkpoint-5"; older.mkdir()
            for name in ("trainer_state.json", "adapter_config.json"):
                (older / name).write_text("{}", encoding="utf-8")
            _write_safetensors(older / "adapter_model.safetensors")
            partial = root / "checkpoint-99"; partial.mkdir()
            (partial / "trainer_state.json").write_text("{}", encoding="utf-8")
            return process

        result = execute_task(task_id, self._services(process_factory=popen_with_extra_checkpoints))

        self.assertEqual(result.state, "failed")
        with self.sessions() as db:
            artifacts = db.query(TrainingArtifact).filter_by(task_id=task_id).all()
            checkpoints = [row for row in artifacts if row.artifact_type == "checkpoint"]
            self.assertEqual(len(checkpoints), 1)
            self.assertTrue(checkpoints[0].relative_path.endswith("checkpoint-20.tar"))
            self.assertEqual(
                checkpoints[0].metadata_json,
                {"step": 20, **_CUDA_PROFILE},
            )
            self.assertFalse(any(row.artifact_type == "final_adapter" for row in artifacts))
            checkpoint_archive = self.root / checkpoints[0].relative_path
        self.assertTrue(checkpoint_archive.is_file())
        self.assertFalse((self.root / task_id / "run" / "checkpoints").exists())

    def test_failure_deletes_all_invalid_checkpoints_and_partial_final_adapters(self):
        task_id = self._task()
        self.process_outcome = "failure"
        original_popen = self._popen
        def only_partial_outputs(argv, **kwargs):
            process = original_popen(argv, **kwargs)
            shutil = __import__("shutil")
            shutil.rmtree(self.root / task_id / "run" / "checkpoints" / "checkpoint-20")
            partial_checkpoint = self.root / task_id / "run" / "checkpoints" / "checkpoint-99"
            partial_checkpoint.mkdir()
            (partial_checkpoint / "trainer_state.json").write_text("{}", encoding="utf-8")
            partial_adapter = self.root / task_id / "run" / "final_adapter"
            partial_adapter.mkdir()
            (partial_adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
            return process

        result = execute_task(task_id, self._services(process_factory=only_partial_outputs))

        self.assertEqual(result.state, "failed")
        self.assertFalse((self.root / task_id / "run" / "checkpoints").exists())
        self.assertFalse((self.root / task_id / "run" / "final_adapter").exists())
        with self.sessions() as db:
            artifact_types = {
                row.artifact_type
                for row in db.query(TrainingArtifact).filter_by(task_id=task_id)
            }
            self.assertEqual(artifact_types, {"log"})

    def test_failure_deletes_even_a_complete_untrusted_final_adapter(self):
        task_id = self._task()
        self.process_outcome = "failure"
        original_popen = self._popen
        def complete_adapter_then_fail(argv, **kwargs):
            process = original_popen(argv, **kwargs)
            adapter = self.root / task_id / "run" / "final_adapter"
            adapter.mkdir()
            (adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
            (adapter / "adapter_model.safetensors").write_bytes(b"not-successful")
            return process

        result = execute_task(task_id, self._services(process_factory=complete_adapter_then_fail))

        self.assertEqual(result.state, "failed")
        self.assertFalse((self.root / task_id / "run" / "final_adapter").exists())

    def test_cancellation_uses_term_then_kill_when_child_does_not_exit(self):
        for mode, expect_kill in (("graceful", False), ("forced", True)):
            with self.subTest(mode=mode):
                task_id = self._task()
                self.cancel_mode = mode
                calls = []
                controller = ProcessCancellationController(killpg=lambda pid, sig: calls.append((pid, sig)))
                requested = False
                def request_cancel(_seconds):
                    nonlocal requested
                    if requested:
                        return
                    requested = True
                    with self.sessions.begin() as db:
                        task = db.get(TrainingTask, task_id)
                        task.state = "cancelling"
                        task.cancel_requested_at = __import__("datetime").datetime.utcnow()
                result = execute_task(
                    task_id,
                    self._services(cancellation_controller=controller, sleep=request_cancel),
                )
                self.assertEqual(result.state, "cancelled")
                self.assertEqual(calls[0][1], 15)
                self.assertEqual(any(sig == 9 for _, sig in calls), expect_kill)

    def test_graceful_leader_exit_kills_surviving_process_group_and_reaps_again(self):
        task_id = self._task()
        self.cancel_mode = "graceful"
        calls = []
        controller = ProcessCancellationController(
            killpg=lambda pid, sig: calls.append((pid, sig))
        )
        requested = False

        def request_cancel(_seconds):
            nonlocal requested
            if requested:
                return
            requested = True
            with self.sessions.begin() as db:
                task = db.get(TrainingTask, task_id)
                task.state = "cancelling"
                task.cancel_requested_at = __import__("datetime").datetime.utcnow()

        result = execute_task(
            task_id,
            self._services(
                cancellation_controller=controller,
                group_exists=lambda _pid: True,
                sleep=request_cancel,
            ),
        )

        self.assertEqual(result.state, "cancelled")
        self.assertEqual([signal for _, signal in calls], [15, 9])
        self.assertEqual(self.spawned[0][1]["_process"].wait_count, 2)

    def test_archive_extraction_rejects_traversal_and_symlinks(self):
        destination = self.root / "task-inputs"
        cases = (("../escape", b"bad"), ("link", None))
        for name, content in cases:
            archive_path = self.root / f"unsafe-{name.replace('/', '-')}.tar"
            with tarfile.open(archive_path, "w") as archive:
                info = tarfile.TarInfo(name)
                if content is None:
                    info.type = tarfile.SYMTYPE
                    info.linkname = "adapter_config.json"
                    archive.addfile(info)
                else:
                    info.size = len(content)
                    archive.addfile(info, __import__("io").BytesIO(content))

            with self.subTest(name=name):
                with self.assertRaises(ExecutionError):
                    _extract_adapter_archive(archive_path, destination)

    def test_structural_validation_rejects_non_object_json_and_empty_safetensors(self):
        task_id = self._task()
        self.process_outcome = "failure"
        original_popen = self._popen

        def invalid_checkpoint(argv, **kwargs):
            process = original_popen(argv, **kwargs)
            checkpoint = self.root / task_id / "run" / "checkpoints" / "checkpoint-20"
            (checkpoint / "adapter_config.json").write_text("[]", encoding="utf-8")
            (checkpoint / "adapter_model.safetensors").write_bytes(
                struct.pack("<Q", 2) + b"{}"
            )
            return process

        result = execute_task(
            task_id,
            self._services(process_factory=invalid_checkpoint),
        )

        self.assertEqual(result.state, "failed")
        with self.sessions() as db:
            self.assertEqual(
                db.query(TrainingArtifact)
                .filter_by(task_id=task_id, artifact_type="checkpoint")
                .count(),
                0,
            )

    def test_cancellation_requested_before_spawn_finishes_without_launching_child(self):
        task_id = self._task(
            state="cancelling",
            cancel_requested_at=__import__("datetime").datetime.utcnow(),
        )

        result = execute_task(task_id, self._services())

        self.assertEqual(result.state, "cancelled")
        self.assertEqual(self.spawned, [])
        with self.sessions() as db:
            task = db.get(TrainingTask, task_id)
            worker = db.get(TrainingWorker, "gpu-worker-1")
            self.assertEqual(task.state, "cancelled")
            self.assertEqual((worker.status, worker.current_task_id), ("idle", None))

    def test_cancellation_committed_during_prepare_prevents_child_spawn(self):
        task_id = self._task()

        def materialize_then_cancel(db, task, task_dir):
            materialized = self._materialize(db, task, task_dir)
            task.state = "cancelling"
            task.cancel_requested_at = __import__("datetime").datetime.utcnow()
            return materialized

        result = execute_task(
            task_id,
            self._services(materialize=materialize_then_cancel),
        )

        self.assertEqual(result.state, "cancelled")
        self.assertEqual(self.spawned, [])
        with self.sessions() as db:
            self.assertEqual(db.get(TrainingTask, task_id).state, "cancelled")

    def test_post_commit_cleanup_failure_is_retried_from_durable_journal(self):
        task_id = self._task()

        with patch.object(
            executor_module,
            "_remove_cleanup_entry",
            create=True,
            side_effect=OSError(errno.EACCES, "cleanup denied"),
        ):
            result = execute_task(task_id, self._services())

        self.assertEqual(result.state, "succeeded")
        raw_adapter = self.root / task_id / "run" / "final_adapter"
        self.assertTrue(raw_adapter.is_dir())
        with self.sessions() as db:
            task = db.get(TrainingTask, task_id)
            self.assertEqual(task.cleanup_state, "pending")
            self.assertIn("run/final_adapter", task.cleanup_paths)

        with self.sessions.begin() as db:
            executor_module.reconcile_task_cleanups(db, self.root)

        self.assertFalse(raw_adapter.exists())
        with self.sessions() as db:
            self.assertEqual(db.get(TrainingTask, task_id).cleanup_state, "cleaned")

    def test_process_controller_ignores_only_missing_process_groups(self):
        calls = []

        def missing_group(_pid, sent_signal):
            calls.append(sent_signal)
            raise ProcessLookupError("group exited")

        controller = ProcessCancellationController(killpg=missing_group)
        controller.register("task", 123)
        controller.request_cancel("task")
        controller.terminate("task")
        controller.kill("task")
        self.assertEqual(calls, [signal.SIGTERM, signal.SIGTERM, signal.SIGKILL])

        denied = ProcessCancellationController(
            killpg=lambda _pid, _signal: (_ for _ in ()).throw(
                PermissionError(errno.EPERM, "not permitted")
            )
        )
        denied.register("task", 123)
        with self.assertRaises(PermissionError):
            denied.terminate("task")

    def test_cancellation_wins_when_requested_as_the_child_exits(self):
        task_id = self._task()
        requested = False
        signals = []
        controller = ProcessCancellationController(
            killpg=lambda _pid, sent_signal: signals.append(sent_signal)
        )

        def request_cancel(_seconds):
            nonlocal requested
            if requested:
                return
            requested = True
            with self.sessions.begin() as db:
                task = db.get(TrainingTask, task_id)
                task.state = "cancelling"
                task.cancel_requested_at = __import__("datetime").datetime.utcnow()

        result = execute_task(
            task_id,
            self._services(
                sleep=request_cancel,
                cancellation_controller=controller,
                group_exists=lambda _pid: True,
            ),
        )

        process = self.spawned[-1][1]["_process"]
        self.assertEqual(result.state, "cancelled")
        self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
        self.assertEqual(controller._active, {})
        self.assertTrue(process._training_log_handle.closed)
        with self.sessions() as db:
            self.assertEqual(db.get(TrainingTask, task_id).state, "cancelled")
            self.assertFalse(
                db.query(TrainingArtifact)
                .filter_by(task_id=task_id, artifact_type="final_adapter")
                .first()
            )

    def test_post_exit_cancellation_cleanup_failure_preserves_ownership(self):
        task_id = self._task()
        requested = False
        signals = []

        def deny_termination(_pid, sent_signal):
            signals.append(sent_signal)
            raise PermissionError(errno.EPERM, "termination denied")

        controller = ProcessCancellationController(killpg=deny_termination)

        def request_cancel(_seconds):
            nonlocal requested
            if requested:
                return
            requested = True
            with self.sessions.begin() as db:
                task = db.get(TrainingTask, task_id)
                task.state = "cancelling"
                task.cancel_requested_at = __import__("datetime").datetime.utcnow()

        with self.assertRaises(TerminalPersistenceError):
            execute_task(
                task_id,
                self._services(
                    sleep=request_cancel,
                    cancellation_controller=controller,
                    group_exists=lambda _pid: True,
                ),
            )

        process = self.spawned[-1][1]["_process"]
        self.assertEqual(signals, [signal.SIGTERM, signal.SIGTERM])
        self.assertEqual(controller._active, {task_id: process.pid})
        self.assertFalse(process._training_log_handle.closed)
        with self.sessions() as db:
            task = db.get(TrainingTask, task_id)
            worker = db.get(TrainingWorker, "gpu-worker-1")
            self.assertEqual(task.state, "cancelling")
            self.assertEqual((worker.status, worker.current_task_id), ("busy", task_id))

    def test_signal_controller_interrupts_active_child_by_task_id(self):
        task_id = self._task()
        self.cancel_mode = "graceful"
        controller = ProcessCancellationController(killpg=lambda _pid, _sig: None)
        requested = False
        def request_stop(_seconds):
            nonlocal requested
            if not requested:
                requested = True
                controller.request_cancel(task_id)

        result = execute_task(
            task_id,
            self._services(cancellation_controller=controller, sleep=request_stop),
        )

        self.assertEqual(result.state, "interrupted")
        with self.sessions() as db:
            self.assertEqual(db.get(TrainingTask, task_id).state, "interrupted")


@pytest.mark.postgresql
@pytest.mark.skipif(
    not os.environ.get("TRAINING_TEST_DATABASE_URL"),
    reason="requires TRAINING_TEST_DATABASE_URL PostgreSQL test database",
)
def test_postgresql_terminal_registration_and_deletion_follow_one_lock_order(tmp_path):
    schema = f"task10_{uuid4().hex}"
    engine = create_engine(os.environ["TRAINING_TEST_DATABASE_URL"])
    schema_created = False
    errors = []
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        schema_created = True
        isolated = engine.execution_options(schema_translate_map={None: schema})
        Base.metadata.create_all(bind=isolated)
        sessions = sessionmaker(bind=isolated, expire_on_commit=False)
        output_root = tmp_path / "outputs"
        output_root.mkdir()
        source_id = str(uuid4())
        task_id = str(uuid4())
        artifact_id = str(uuid4())
        checkpoint_path = output_root / source_id / "checkpoint.tar"
        checkpoint_path.parent.mkdir()
        checkpoint_path.write_bytes(b"checkpoint")
        config_path = output_root / task_id / "public" / "config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("{}", encoding="utf-8")
        with sessions.begin() as db:
            db.add(TrainingTask(
                id=source_id, name="source", task_type="cpt",
                job_kind="training", state="failed", model_id="model",
                config_snapshot={},
            ))
            db.flush()
            db.add(TrainingArtifact(
                id=artifact_id, task_id=source_id, artifact_type="checkpoint",
                relative_path=f"{source_id}/checkpoint.tar",
            ))
            db.flush()
            db.add(TrainingTask(
                id=task_id, name="resume", task_type="cpt",
                job_kind="training", state="preparing_data", model_id="model",
                worker_id="gpu-worker-1", parent_task_id=source_id,
                resume_checkpoint_artifact_id=artifact_id, config_snapshot={},
            ))
            db.flush()
            db.add(TrainingWorker(
                id="gpu-worker-1", status="busy", current_task_id=task_id,
            ))

        graph_locked = threading.Event()
        deletion_started = threading.Event()

        def terminal_registration():
            try:
                with sessions.begin() as db:
                    _lock_execution_graph(db, task_id, "gpu-worker-1")
                    graph_locked.set()
                    assert deletion_started.wait(5)
                    training_artifact_service.register_artifact(
                        db,
                        output_root,
                        task_id=task_id,
                        artifact_type="config",
                        relative_path=f"{task_id}/public/config.json",
                    )
            except BaseException as exc:
                errors.append(exc)

        def concurrent_deletion():
            try:
                assert graph_locked.wait(5)
                with sessions.begin() as db:
                    deletion_started.set()
                    training_artifact_service.stage_artifact_deletion(
                        db, artifact_id, output_root
                    )
            except BaseException as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=terminal_registration),
            threading.Thread(target=concurrent_deletion),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
    finally:
        if schema_created:
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


class TrainingChildSpecTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output = Path(self.temp_dir.name) / "outputs"; self.output.mkdir()
        self.root = self.output / "task-1"; self.root.mkdir()
        self.model = Path(self.temp_dir.name) / "model"; self.model.mkdir()
        (self.root / "data").mkdir()
        for name in ("train.jsonl", "validation.jsonl"):
            (self.root / "data" / name).write_text("{}\n", encoding="utf-8")
        (self.root / "run").mkdir()
        self.config = self.root / "training.yaml"
        self.training_config = build_training_config(
            "cpt",
            "quick",
            {},
            {
                "model_name_or_path": str(self.model.resolve()),
                "train_file": str(self.root / "data" / "train.jsonl"),
                "validation_file": str(self.root / "data" / "validation.jsonl"),
                "output_dir": str(self.root / "run"),
            },
        )
        self._write_config(self.training_config)
        self.payload = {
            "schema_version": 1, "task_id": "task-1", "task_type": "cpt",
            "job_kind": "training", "output_root": str(self.output),
            "task_root": str(self.root), "config_path": str(self.config),
            "config_sha256": self._config_digest(),
            "test_file": None, "base_model_path": str(self.model),
            "cpt_adapter_path": None, "sft_adapter_path": None,
            "resume_checkpoint": None, "metrics_path": str(self.root / "metrics.jsonl"),
            "result_path": str(self.root / "result.json"), "log_path": str(self.root / "child.log"),
            "evaluation_output_dir": None, "evaluation_checkpoint": None,
            "requested_device": "cuda", "required_device_profile": None,
            "profile_path": str(self.root / "effective-device-profile.json"),
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write(self, payload):
        path = self.root / "task-spec.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _write_config(self, payload):
        self.config.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    def _config_digest(self):
        return hashlib.sha256(self.config.read_bytes()).hexdigest()

    def test_accepts_exact_typed_internal_schema(self):
        spec = ChildTaskSpec.load(self._write(self.payload))
        self.assertEqual(spec.task_id, "task-1")

    def test_rejects_extra_wrong_type_and_noncanonical_fixed_paths(self):
        other_root = self.output / "task-2"
        other_root.mkdir()
        other_config = self.root / "other.yaml"
        other_config.write_text("{}\n", encoding="utf-8")
        cases = []
        extra = dict(self.payload, command="rm -rf /"); cases.append(extra)
        wrong_type = dict(self.payload, schema_version="1"); cases.append(wrong_type)
        cases.append(dict(self.payload, metrics_path=None))
        cases.append(dict(self.payload, task_root=str(other_root)))
        cases.append(dict(self.payload, metrics_path=str(self.root / "other.jsonl")))
        cases.append(dict(self.payload, result_path=str(self.root / "other.json")))
        cases.append(dict(self.payload, log_path=str(self.root / "other.log")))
        cases.append(dict(self.payload, config_path=str(other_config)))
        cases.append(dict(self.payload, config_sha256="A" * 64))
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ChildSpecError):
                    ChildTaskSpec.load(self._write(payload))

    def test_training_adapter_and_resume_paths_are_exact_task_local_snapshots(self):
        cpt_adapter = self.root / "inputs" / "cpt-adapter"
        resume = self.root / "inputs" / "resume-checkpoint"
        cpt_adapter.mkdir(parents=True)
        resume.mkdir()
        sft_payload = dict(
            self.payload,
            task_type="sft",
            cpt_adapter_path=str(cpt_adapter),
            resume_checkpoint=str(resume),
        )
        spec = ChildTaskSpec.load(self._write(sft_payload))
        self.assertEqual(Path(spec.cpt_adapter_path), cpt_adapter)
        self.assertEqual(Path(spec.resume_checkpoint), resume)

        other_task_adapter = self.output / "task-2" / "adapter"
        other_task_adapter.mkdir(parents=True)
        invalid = (
            dict(self.payload, cpt_adapter_path=str(cpt_adapter)),
            dict(sft_payload, cpt_adapter_path=str(other_task_adapter)),
            dict(sft_payload, resume_checkpoint=str(other_task_adapter)),
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ChildSpecError):
                    ChildTaskSpec.load(self._write(payload))

    def test_evaluation_requires_exact_sft_only_fields_and_local_inputs(self):
        test_file = self.root / "inputs" / "test.jsonl"
        test_file.parent.mkdir()
        test_file.write_text("{}\n", encoding="utf-8")
        sft_adapter = self.root / "inputs" / "sft-adapter"
        sft_adapter.mkdir()
        evaluation = dict(
            self.payload,
            task_type="sft",
            job_kind="evaluation",
            config_path=None,
            config_sha256=None,
            test_file=str(test_file),
            sft_adapter_path=str(sft_adapter),
            evaluation_output_dir=str(self.root / "evaluation"),
            evaluation_checkpoint="sft",
        )
        ChildTaskSpec.load(self._write(evaluation))

        invalid = (
            dict(evaluation, task_type="cpt"),
            dict(evaluation, evaluation_checkpoint="base"),
            dict(evaluation, config_path=str(self.config), config_sha256=self._config_digest()),
            dict(evaluation, cpt_adapter_path=str(sft_adapter)),
            dict(evaluation, resume_checkpoint=str(sft_adapter)),
            dict(evaluation, test_file=str(self.root / "data" / "train.jsonl")),
            dict(evaluation, evaluation_output_dir=str(self.root / "other-evaluation")),
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ChildSpecError):
                    ChildTaskSpec.load(self._write(payload))

    def test_evaluation_child_uses_local_sft_adapter_and_current_device_profile(self):
        test_file = self.root / "inputs" / "test.jsonl"
        test_file.parent.mkdir()
        test_file.write_text("{}\n", encoding="utf-8")
        sft_adapter = self.root / "inputs" / "sft-adapter"
        sft_adapter.mkdir()
        evaluation = dict(
            self.payload,
            task_type="sft",
            job_kind="evaluation",
            config_path=None,
            config_sha256=None,
            test_file=str(test_file),
            sft_adapter_path=str(sft_adapter),
            evaluation_output_dir=str(self.root / "evaluation"),
            evaluation_checkpoint="sft",
            required_device_profile="cuda_qlora_bf16",
        )
        spec = ChildTaskSpec.load(self._write(evaluation))
        captured = {}
        module = ModuleType("embedded_fault_diag.eval_open_set")

        def load_samples(path, *, limit, task_types):
            captured["load"] = (path, limit, task_types)
            return [{"sample": 1}]

        def run_generation(
            args,
            samples,
            *,
            requested_device,
            required_profile,
            on_profile_selected,
        ):
            captured["args"] = args
            captured["samples"] = samples
            captured["profile"] = (requested_device, required_profile)
            on_profile_selected(
                SimpleNamespace(
                    device_type="cuda",
                    name="cuda_qlora_bf16",
                    quantization="nf4_4bit",
                    compute_dtype="bf16",
                )
            )
            return {"sft": {"metrics": {key: 0.5 for key in EVALUATION_SCORE_KEYS}}}

        module.load_samples = load_samples
        module.run_generation = run_generation
        with patch.dict(sys.modules, {"embedded_fault_diag.eval_open_set": module}):
            result = run_spec(spec)

        self.assertEqual(captured["args"].cpt_adapter, None)
        self.assertEqual(captured["args"].sft_adapter, sft_adapter)
        self.assertEqual(captured["args"].checkpoint, ["sft"])
        self.assertEqual(captured["profile"], ("cuda", "cuda_qlora_bf16"))
        self.assertEqual(captured["load"], (test_file, 0, set()))
        self.assertEqual(result["profile_name"], "cuda_qlora_bf16")
        self.assertEqual(
            Path(result["evaluation_summary"]),
            self.root / "evaluation" / "summary.json",
        )

    def test_rejects_symlink_spec_sources_and_sinks(self):
        real_spec = self._write(self.payload)
        linked_spec = self.root / "linked-spec.json"
        linked_spec.symlink_to(real_spec)
        with self.assertRaises(ChildSpecError):
            ChildTaskSpec.load(linked_spec)

        real_config = self.root / "real-training.yaml"
        self.config.rename(real_config)
        self.config.symlink_to(real_config)
        with self.assertRaises(ChildSpecError):
            ChildTaskSpec.load(self._write(self.payload))
        self.config.unlink()
        real_config.rename(self.config)

        result_target = self.root / "result-target.json"
        result_target.write_text("{}", encoding="utf-8")
        (self.root / "result.json").symlink_to(result_target)
        with self.assertRaises(ChildSpecError):
            ChildTaskSpec.load(self._write(self.payload))

    def test_digest_and_yaml_paths_are_verified_before_training_import(self):
        spec = ChildTaskSpec.load(self._write(self.payload))
        trainer = Mock(return_value=self.root / "run" / "final_adapter")
        module = ModuleType("embedded_fault_diag.train_cpt")
        module.run_training = trainer

        self.config.write_text("not: the original config\n", encoding="utf-8")
        with patch.dict(sys.modules, {"embedded_fault_diag.train_cpt": module}):
            with self.assertRaises(ChildSpecError):
                run_spec(spec)
        trainer.assert_not_called()

        invalid_configs = (
            self.training_config | {"extra": {}},
            self.training_config | {"model": {**self.training_config["model"], "cache_dir": "/tmp"}},
            self.training_config | {"data": {**self.training_config["data"], "test_file": "/tmp/test"}},
            self.training_config | {"training": {"output_dir": str(self.root / "other-run")}},
            self.training_config | {"model": {"model_name_or_path": str(self.root)}},
            self.training_config | {"data": {**self.training_config["data"], "train_file": str(self.root / "data" / "validation.jsonl")}},
            self.training_config | {"adapter": {"cpt_adapter_path": str(self.root)}},
        )
        for config in invalid_configs:
            with self.subTest(config=config):
                self._write_config(config)
                payload = dict(self.payload, config_sha256=self._config_digest())
                invalid_spec = ChildTaskSpec.load(self._write(payload))
                trainer.reset_mock()
                with patch.dict(sys.modules, {"embedded_fault_diag.train_cpt": module}):
                    with self.assertRaises(ChildSpecError):
                        run_spec(invalid_spec)
                trainer.assert_not_called()

    def test_duplicate_yaml_mapping_keys_are_rejected_before_training_import(self):
        duplicate = yaml.safe_dump(self.training_config, sort_keys=False)
        duplicate += "model:\n  model_name_or_path: " + str(self.model.resolve()) + "\n"
        self.config.write_text(duplicate, encoding="utf-8")
        payload = dict(self.payload, config_sha256=self._config_digest())
        spec = ChildTaskSpec.load(self._write(payload))
        trainer = Mock(return_value=self.root / "run" / "final_adapter")
        module = ModuleType("embedded_fault_diag.train_cpt")
        module.run_training = trainer

        with patch.dict(sys.modules, {"embedded_fault_diag.train_cpt": module}):
            with self.assertRaises(ChildSpecError):
                run_spec(spec)
        trainer.assert_not_called()

    def test_training_result_must_remain_in_canonical_run_root(self):
        spec = ChildTaskSpec.load(self._write(self.payload))
        module = ModuleType("embedded_fault_diag.train_cpt")

        def run_training(*_args, on_profile_selected, **_kwargs):
            on_profile_selected(SimpleNamespace(
                device_type="cuda",
                name="cuda_qlora_bf16",
                quantization="nf4_4bit",
                compute_dtype="bf16",
            ))
            return trainer.return_value

        trainer = Mock(side_effect=run_training)
        trainer.return_value = self.root / "outside-adapter"
        module.run_training = trainer
        with patch.dict(sys.modules, {"embedded_fault_diag.train_cpt": module}):
            with self.assertRaises(ChildSpecError):
                run_spec(spec)

        expected = self.root / "run" / "final_adapter"
        expected.mkdir()
        trainer.return_value = expected
        with patch.dict(sys.modules, {"embedded_fault_diag.train_cpt": module}):
            self.assertEqual(run_spec(spec)["final_adapter"], str(expected.resolve()))


if __name__ == "__main__":
    unittest.main()
