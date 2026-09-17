import hashlib
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.training_task import TrainingArtifact, TrainingTask, TrainingWorker
from app.training_worker import child as training_child
from app.training_worker import main
from app.training_worker.child import ChildSpecError, ChildTaskSpec, run_spec


class TrainingWorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temp_dir.name) / "outputs"
        self.output_root.mkdir()
        self.base_model_root = Path(self.temp_dir.name) / "model"
        self.base_model_root.mkdir()
        self.engine = create_engine("sqlite://")
        for table in (TrainingTask.__table__, TrainingArtifact.__table__, TrainingWorker.__table__):
            table.create(bind=self.engine)
        self.sessions = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _task(self, *, state="queued", worker_id=None, heartbeat_at=None):
        db = self.sessions()
        try:
            task = TrainingTask(
                name="task",
                task_type="cpt",
                job_kind="training",
                state=state,
                model_id="qwen-qwen3.5-9b",
                config_snapshot={},
                worker_id=worker_id,
                heartbeat_at=heartbeat_at,
            )
            db.add(task)
            db.commit()
            return task.id
        finally:
            db.close()

    def _runtime(self, **changes):
        values = {
            "session_factory": self.sessions,
            "worker_id": "gpu-worker-1",
            "output_root": self.output_root,
            "base_model_path": self.base_model_root,
            "poll_seconds": 1,
            "stale_seconds": 60,
            "executor": Mock(),
        }
        values.update(changes)
        return main.WorkerRuntime(**values)

    def _child_paths(self):
        root = self.output_root / "child-task"
        root.mkdir(exist_ok=True)
        (root / "run").mkdir(exist_ok=True)
        model = Path(self.temp_dir.name) / "child-model"
        model.mkdir(exist_ok=True)
        config = root / "training.yaml"
        config.write_text("{}\n", encoding="utf-8")
        return root, model, config

    def _child_payload(self):
        root, model, config = self._child_paths()
        return {
            "schema_version": 1,
            "task_id": "child-task",
            "task_type": "cpt",
            "job_kind": "training",
            "output_root": str(self.output_root),
            "task_root": str(root),
            "config_path": str(config),
            "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
            "test_file": None,
            "base_model_path": str(model),
            "cpt_adapter_path": None,
            "sft_adapter_path": None,
            "resume_checkpoint": None,
            "metrics_path": str(root / "metrics.jsonl"),
            "result_path": str(root / "result.json"),
            "log_path": str(root / "child.log"),
            "evaluation_output_dir": None,
            "evaluation_checkpoint": None,
            "requested_device": "cpu",
            "required_device_profile": "cpu_qlora_4bit_bf16",
            "profile_path": str(root / "effective-device-profile.json"),
        }

    def _load_child_spec(self, payload=None):
        payload = payload or self._child_payload()
        path = Path(payload["task_root"]) / "task-spec.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return ChildTaskSpec.load(path)

    def test_child_spec_requires_exact_device_contract_and_profile_sink(self):
        payload = self._child_payload()
        spec = self._load_child_spec(payload)
        self.assertEqual(spec.requested_device, "cpu")
        self.assertEqual(spec.required_device_profile, "cpu_qlora_4bit_bf16")

        invalid = (
            dict(payload, requested_device="tpu"),
            dict(payload, required_device_profile="auto_low_memory"),
            dict(payload, profile_path=str(Path(payload["task_root"]) / "other.json")),
        )
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ChildSpecError):
                    self._load_child_spec(candidate)

        profile_path = Path(payload["profile_path"])
        target = profile_path.with_name("profile-target.json")
        target.write_text("{}", encoding="utf-8")
        profile_path.symlink_to(target)
        with self.assertRaises(ChildSpecError):
            self._load_child_spec(payload)

    def test_training_publishes_only_safe_profile_and_passes_device_contract(self):
        for task_type in ("cpt", "sft"):
            with self.subTest(task_type=task_type):
                payload = dict(self._child_payload(), task_type=task_type)
                spec = self._load_child_spec(payload)
                adapter = Path(spec.task_root) / "run" / "final_adapter"
                adapter.mkdir()
                profile = SimpleNamespace(
                    name="cpu_qlora_4bit_bf16",
                    device_type="cpu",
                    quantization="nf4_4bit",
                    compute_dtype="bf16",
                    device_map={"": "cpu"},
                    token="must-not-persist",
                )

                def train(_config, **kwargs):
                    self.assertEqual(kwargs["requested_device"], "cpu")
                    self.assertEqual(
                        kwargs["required_profile"], "cpu_qlora_4bit_bf16"
                    )
                    kwargs["on_profile_selected"](profile)
                    kwargs["on_profile_selected"](profile)
                    return adapter

                module = ModuleType(f"embedded_fault_diag.train_{task_type}")
                module.run_training = Mock(side_effect=train)
                with (
                    patch.object(
                        sys.modules["app.training_worker.child"],
                        "_load_training_config",
                        return_value={},
                    ),
                    patch.dict(sys.modules, {module.__name__: module}),
                ):
                    result = run_spec(spec)

                safe_profile = {
                    "device_type": "cpu",
                    "profile_name": "cpu_qlora_4bit_bf16",
                    "quantization": "nf4_4bit",
                    "compute_dtype": "bf16",
                }
                self.assertEqual(
                    result,
                    {"status": "succeeded", "final_adapter": str(adapter.resolve())}
                    | safe_profile,
                )
                profile_path = Path(spec.profile_path)
                self.assertEqual(json.loads(profile_path.read_text(encoding="utf-8")), safe_profile)
                self.assertEqual(stat.S_IMODE(profile_path.stat().st_mode), 0o600)
                serialized = profile_path.read_text(encoding="utf-8")
                self.assertNotIn("device_map", serialized)
                self.assertNotIn("token", serialized)

                adapter.rmdir()
                profile_path.unlink()

    def test_profile_callback_enforces_requested_and_required_profile_contract(self):
        cases = (
            (
                "cpu",
                None,
                SimpleNamespace(
                    name="cuda_qlora_bf16",
                    device_type="cuda",
                    quantization="nf4_4bit",
                    compute_dtype="bf16",
                ),
            ),
            (
                "cuda",
                None,
                SimpleNamespace(
                    name="cpu_lora_fp32",
                    device_type="cpu",
                    quantization="none",
                    compute_dtype="fp32",
                ),
            ),
            (
                "auto",
                "cpu_qlora_4bit_bf16",
                SimpleNamespace(
                    name="cpu_qlora_4bit_fp32",
                    device_type="cpu",
                    quantization="nf4_4bit",
                    compute_dtype="fp32",
                ),
            ),
        )

        for requested_device, required_profile, selected in cases:
            with self.subTest(
                requested_device=requested_device,
                required_profile=required_profile,
            ):
                payload = dict(
                    self._child_payload(),
                    requested_device=requested_device,
                    required_device_profile=required_profile,
                )
                spec = self._load_child_spec(payload)
                adapter = Path(spec.task_root) / "run" / "final_adapter"
                adapter.mkdir(exist_ok=True)

                def train(_config, **kwargs):
                    kwargs["on_profile_selected"](selected)
                    return adapter

                module = ModuleType("embedded_fault_diag.train_cpt")
                module.run_training = Mock(side_effect=train)
                with (
                    patch.object(training_child, "_load_training_config", return_value={}),
                    patch.dict(sys.modules, {module.__name__: module}),
                ):
                    with self.assertRaises(ChildSpecError):
                        run_spec(spec)

    def test_auto_device_accepts_either_valid_profile_device(self):
        profiles = (
            SimpleNamespace(
                name="cuda_qlora_bf16",
                device_type="cuda",
                quantization="nf4_4bit",
                compute_dtype="bf16",
            ),
            SimpleNamespace(
                name="cpu_lora_fp32",
                device_type="cpu",
                quantization="none",
                compute_dtype="fp32",
            ),
        )

        for selected in profiles:
            with self.subTest(profile=selected.name):
                payload = dict(
                    self._child_payload(),
                    requested_device="auto",
                    required_device_profile=None,
                )
                spec = self._load_child_spec(payload)
                adapter = Path(spec.task_root) / "run" / "final_adapter"
                adapter.mkdir(exist_ok=True)

                def train(_config, **kwargs):
                    kwargs["on_profile_selected"](selected)
                    return adapter

                module = ModuleType("embedded_fault_diag.train_cpt")
                module.run_training = Mock(side_effect=train)
                with (
                    patch.object(training_child, "_load_training_config", return_value={}),
                    patch.dict(sys.modules, {module.__name__: module}),
                ):
                    result = run_spec(spec)

                self.assertEqual(result["device_type"], selected.device_type)
                Path(spec.profile_path).unlink()
                adapter.rmdir()

    def test_atomic_json_uses_owned_unique_temps_under_concurrent_writes(self):
        root, _, _ = self._child_paths()
        path = root / "concurrent-profile.json"
        foreign_temp = path.with_suffix(path.suffix + ".tmp")
        foreign_temp.write_text("foreign-owned", encoding="utf-8")
        barrier = threading.Barrier(3)
        errors = []

        def publish(writer):
            try:
                barrier.wait()
                training_child._atomic_json(path, {"writer": writer})
            except BaseException as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=publish, args=(writer,))
            for writer in ("first", "second")
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5)

        self.assertEqual(errors, [])
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertIn(json.loads(path.read_text(encoding="utf-8"))["writer"], {"first", "second"})
        self.assertEqual(foreign_temp.read_text(encoding="utf-8"), "foreign-owned")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_atomic_json_keeps_inflight_parent_swap_on_opened_directory(self):
        base = Path(self.temp_dir.name) / "atomic-swap"
        parent = base / "owned"
        attacker = base / "attacker"
        displaced = base / "displaced"
        parent.mkdir(parents=True)
        attacker.mkdir()
        path = parent / "profile.json"
        real_open = os.open
        swapped = False

        def swapping_open(open_path, flags, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            if not swapped and flags & os.O_CREAT:
                parent.rename(displaced)
                parent.symlink_to(attacker, target_is_directory=True)
                swapped = True
            if dir_fd is None:
                return real_open(open_path, flags, mode)
            return real_open(open_path, flags, mode, dir_fd=dir_fd)

        with patch.object(training_child.os, "open", side_effect=swapping_open):
            training_child._atomic_json(path, {"owned": True})

        self.assertTrue(swapped)
        self.assertFalse((attacker / path.name).exists())
        self.assertEqual(
            json.loads((displaced / path.name).read_text(encoding="utf-8")),
            {"owned": True},
        )

    def test_profile_publication_failure_result_is_stable_and_sanitized(self):
        spec = self._load_child_spec()
        secret_path = Path(self.temp_dir.name) / "private" / "profile.json"
        module = ModuleType("embedded_fault_diag.train_cpt")

        def train(_config, **kwargs):
            kwargs["on_profile_selected"](
                SimpleNamespace(
                    name="cpu_qlora_4bit_bf16",
                    device_type="cpu",
                    quantization="nf4_4bit",
                    compute_dtype="bf16",
                )
            )

        module.run_training = Mock(side_effect=train)
        real_atomic_json = training_child._atomic_json

        def fail_profile_publication(path, payload):
            if Path(path) == Path(spec.profile_path):
                raise OSError(f"write failed at {secret_path}")
            return real_atomic_json(Path(path), payload)

        with (
            patch.object(ChildTaskSpec, "load", return_value=spec),
            patch.object(training_child, "_load_training_config", return_value={}),
            patch.object(training_child, "_atomic_json", side_effect=fail_profile_publication),
            patch.dict(sys.modules, {module.__name__: module}),
            patch.object(sys, "argv", ["child", "--task-spec", str(Path(spec.task_root) / "task-spec.json")]),
        ):
            with self.assertRaises(Exception):
                training_child.main()

        result = json.loads(Path(spec.result_path).read_text(encoding="utf-8"))
        self.assertEqual(
            result,
            {
                "status": "failed",
                "error": "effective device profile publication failed",
            },
        )
        self.assertNotIn(str(secret_path), json.dumps(result))

    def test_profile_publication_replaces_a_late_symlink_without_following_it(self):
        spec = self._load_child_spec()
        profile_path = Path(spec.profile_path)
        target = profile_path.with_name("outside-profile.json")
        target.write_text('{"preserved": true}', encoding="utf-8")
        profile_path.symlink_to(target)
        adapter = Path(spec.task_root) / "run" / "final_adapter"
        adapter.mkdir()
        profile = SimpleNamespace(
            name="cpu_qlora_4bit_bf16",
            device_type="cpu",
            quantization="nf4_4bit",
            compute_dtype="bf16",
        )

        def train(_config, **kwargs):
            kwargs["on_profile_selected"](profile)
            return adapter

        module = ModuleType("embedded_fault_diag.train_cpt")
        module.run_training = Mock(side_effect=train)
        with (
            patch.object(
                sys.modules["app.training_worker.child"],
                "_load_training_config",
                return_value={},
            ),
            patch.dict(sys.modules, {module.__name__: module}),
        ):
            result = run_spec(spec)

        self.assertEqual(result["profile_name"], "cpu_qlora_4bit_bf16")
        self.assertEqual(target.read_text(encoding="utf-8"), '{"preserved": true}')
        self.assertFalse(profile_path.is_symlink())
        self.assertEqual(stat.S_IMODE(profile_path.stat().st_mode), 0o600)

    def test_evaluation_uses_same_profile_callback_and_fails_closed_without_it(self):
        payload = self._child_payload()
        root = Path(payload["task_root"])
        inputs = root / "inputs"
        inputs.mkdir()
        test_file = inputs / "test.jsonl"
        test_file.write_text("{}\n", encoding="utf-8")
        adapter = inputs / "sft-adapter"
        adapter.mkdir()
        evaluation = dict(
            payload,
            task_type="sft",
            job_kind="evaluation",
            config_path=None,
            config_sha256=None,
            test_file=str(test_file),
            sft_adapter_path=str(adapter),
            evaluation_output_dir=str(root / "evaluation"),
            evaluation_checkpoint="sft",
            required_device_profile=None,
        )
        spec = self._load_child_spec(evaluation)
        profile = SimpleNamespace(
            name="cpu_lora_fp32",
            device_type="cpu",
            quantization="none",
            compute_dtype="fp32",
        )

        module = ModuleType("embedded_fault_diag.eval_open_set")
        module.load_samples = Mock(return_value=[])

        def generate(_args, _samples, **kwargs):
            self.assertEqual(kwargs["requested_device"], "cpu")
            self.assertIsNone(kwargs["required_profile"])
            kwargs["on_profile_selected"](profile)
            return {"sft": {"count": 0}}

        module.run_generation = Mock(side_effect=generate)
        with patch.dict(sys.modules, {module.__name__: module}):
            result = run_spec(spec)

        self.assertEqual(result["profile_name"], "cpu_lora_fp32")
        self.assertEqual(result["quantization"], "none")

        Path(spec.profile_path).unlink()
        module.run_generation = Mock(return_value={"sft": {"count": 0}})
        with patch.dict(sys.modules, {module.__name__: module}):
            with self.assertRaisesRegex(ChildSpecError, "profile"):
                run_spec(spec)

    def test_base_model_check_only_reports_readable_local_path_and_task_nine_settings(self):
        self.assertEqual(main.base_model_status(self.base_model_root), "ready")
        self.assertEqual(main.base_model_status(self.base_model_root / "missing"), "unavailable")
        self.assertFalse(hasattr(main, "snapshot_download"))
        self.assertEqual(settings.TRAINING_OUTPUT_ROOT, "/training/outputs")

    def test_startup_recovers_stale_work_then_claims_in_separate_transactions(self):
        queued_id = self._task()
        stale_id = self._task(
            state="training",
            worker_id="gpu-worker-1",
            heartbeat_at=datetime.utcnow() - timedelta(seconds=61),
        )
        reconciler = Mock()
        runtime = self._runtime(reconciler=reconciler)

        claimed = runtime.startup_claim()

        self.assertEqual(claimed, queued_id)
        reconciler.assert_called_once()
        db = self.sessions()
        try:
            stale = db.get(TrainingTask, stale_id)
            worker = db.get(TrainingWorker, "gpu-worker-1")
            self.assertEqual(stale.state, "interrupted")
            self.assertEqual((worker.status, worker.current_task_id), ("busy", queued_id))
            self.assertEqual(worker.model_status, "ready")
        finally:
            db.close()

    def test_startup_uses_one_cutoff_and_interrupts_work_that_is_stale_at_that_cutoff(self):
        now = datetime(2026, 7, 16, 8, 0, 0)
        stale_id = self._task(
            state="training",
            worker_id="gpu-worker-1",
            heartbeat_at=now - timedelta(seconds=61),
        )
        queued_id = self._task()
        clock = Mock(return_value=now)
        runtime = self._runtime(now=clock, reconciler=Mock())

        claimed = runtime.startup_claim()

        self.assertEqual(claimed, queued_id)
        self.assertEqual(clock.call_count, 1)
        db = self.sessions()
        try:
            self.assertEqual(db.get(TrainingTask, stale_id).state, "interrupted")
        finally:
            db.close()

    def test_startup_registration_records_a_safe_injected_gpu_snapshot(self):
        runtime = self._runtime(
            gpu_snapshot_provider=lambda: {
                "index": 1,
                "memory_free_mb": 19000,
                "token": "must-not-persist",
            }
        )

        runtime.startup_claim()

        db = self.sessions()
        try:
            worker = db.get(TrainingWorker, "gpu-worker-1")
            self.assertEqual(worker.gpu_snapshot, {"index": 1, "memory_free_mb": 19000})
        finally:
            db.close()

    def test_startup_preserves_fresh_owned_work_and_defers_queue_claim(self):
        fresh_id = self._task(
            state="training",
            worker_id="gpu-worker-1",
            heartbeat_at=datetime.utcnow(),
        )
        queued_id = self._task()
        runtime = self._runtime(reconciler=Mock())

        claimed = runtime.startup_claim()

        self.assertIsNone(claimed)
        self.assertEqual(runtime.current_task_id, fresh_id)
        db = self.sessions()
        try:
            fresh = db.get(TrainingTask, fresh_id)
            queued = db.get(TrainingTask, queued_id)
            worker = db.get(TrainingWorker, "gpu-worker-1")
            self.assertEqual(fresh.state, "training")
            self.assertEqual(queued.state, "queued")
            self.assertEqual((worker.status, worker.current_task_id), ("busy", fresh_id))
        finally:
            db.close()

    def test_reconciliation_commit_survives_a_later_atomic_claim_failure(self):
        def reconciler(db, _output_root):
            db.add(TrainingArtifact(artifact_type="log", relative_path="reconciled.log"))

        def fail_claim(_db, _worker_id):
            raise RuntimeError("claim failed")

        runtime = self._runtime(reconciler=reconciler, claim_task=fail_claim)

        with self.assertRaisesRegex(RuntimeError, "claim failed"):
            runtime.startup_claim()

        db = self.sessions()
        try:
            self.assertEqual(db.query(TrainingArtifact).filter_by(relative_path="reconciled.log").count(), 1)
        finally:
            db.close()

    def test_startup_retries_pending_task_output_cleanup(self):
        task_id = self._task(state="succeeded")
        raw_checkpoint = self.output_root / task_id / "run" / "checkpoints" / "checkpoint-10"
        raw_checkpoint.mkdir(parents=True)
        (raw_checkpoint / "trainer_state.json").write_text("{}", encoding="utf-8")
        with self.sessions.begin() as db:
            task = db.get(TrainingTask, task_id)
            task.cleanup_state = "pending"
            task.cleanup_paths = ["run/checkpoints"]
            task.cleanup_updated_at = datetime.utcnow()

        runtime = self._runtime(reconciler=Mock())

        runtime.startup_claim()

        self.assertFalse(raw_checkpoint.exists())
        with self.sessions() as db:
            task = db.get(TrainingTask, task_id)
            self.assertEqual(task.cleanup_state, "cleaned")

    def test_every_claim_cycle_gates_on_local_base_model_availability(self):
        queued_id = self._task()
        self.base_model_root.rmdir()
        runtime = self._runtime()

        claimed = runtime.claim_next()

        self.assertIsNone(claimed)
        db = self.sessions()
        try:
            worker = db.get(TrainingWorker, "gpu-worker-1")
            queued = db.get(TrainingTask, queued_id)
            self.assertEqual((worker.status, worker.model_status), ("error", "unavailable"))
            self.assertEqual(queued.state, "queued")
        finally:
            db.close()

    def test_serial_poll_executes_at_most_one_claimed_task(self):
        first_id = self._task()
        self._task()
        executor = Mock()
        runtime = self._runtime(executor=executor)

        claimed = runtime.startup_claim()
        runtime.execute_claimed_task(claimed)

        executor.assert_called_once_with(first_id)
        self.assertEqual(claimed, first_id)

    def test_signal_only_sets_stop_intent_and_run_finally_persists_offline_once(self):
        controller = Mock()
        runtime = self._runtime(cancellation_controller=controller)
        runtime.current_task_id = "active-task"
        runtime.startup_claim = Mock(return_value=None)
        previous_handler = main.signal.getsignal(main.signal.SIGTERM)
        runtime.install_signal_handlers()
        handler = main.signal.getsignal(main.signal.SIGTERM)

        def stop_from_claim():
            handler(main.signal.SIGTERM, None)
            return None

        runtime.claim_next = Mock(side_effect=stop_from_claim)
        try:
            with patch("app.training_worker.main.set_worker_offline", wraps=main.set_worker_offline) as offline:
                handler(main.signal.SIGTERM, None)
                self.assertTrue(runtime.stop_requested)
                controller.request_cancel.assert_called_once_with("active-task")
                offline.assert_not_called()

                runtime.stop_requested = False
                runtime.current_task_id = "active-task"
                controller.reset_mock()
                runtime.run_forever(max_cycles=2)

                controller.request_cancel.assert_called_once_with("active-task")
                offline.assert_called_once()
        finally:
            main.signal.signal(main.signal.SIGTERM, previous_handler)

    def test_signal_handler_delegates_to_stop_request(self):
        runtime = self._runtime()
        with patch.object(runtime, "request_stop") as request_stop:
            runtime.install_signal_handlers()
            handler = main.signal.getsignal(main.signal.SIGTERM)
            handler(main.signal.SIGTERM, None)

        request_stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
