import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import ValidationError

from app.config import Settings
from app.training_worker import main


class TrainingWorkerCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temp_dir.name) / "outputs"
        self.output_root.mkdir()
        self.base_model_root = Path(self.temp_dir.name) / "model"
        self.base_model_root.mkdir()
        (self.base_model_root / "config.json").write_text("{}", encoding="utf-8")
        self.environment = {
            "NVIDIA_VISIBLE_DEVICES": "1",
            "CUDA_VISIBLE_DEVICES": "0",
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_training_device_settings_are_typed_and_have_safe_defaults(self):
        with patch.dict("os.environ", {}, clear=True):
            defaults = Settings()

        self.assertEqual(defaults.TRAINING_DEVICE, "auto")
        self.assertEqual(defaults.TRAINING_CPU_PROFILE, "auto_low_memory")
        self.assertEqual(defaults.TRAINING_MIN_FREE_CPU_GB, 24)

        configured = Settings(
            TRAINING_DEVICE="auto",
            TRAINING_CPU_PROFILE="auto_low_memory",
            TRAINING_MIN_FREE_CPU_GB=24,
        )

        self.assertEqual(configured.TRAINING_DEVICE, "auto")
        self.assertEqual(configured.TRAINING_CPU_PROFILE, "auto_low_memory")
        self.assertEqual(configured.TRAINING_MIN_FREE_CPU_GB, 24)
        with self.assertRaises(ValidationError):
            Settings(TRAINING_DEVICE="tpu")
        with self.assertRaises(ValidationError):
            Settings(TRAINING_MIN_FREE_CPU_GB=0)

    def test_startup_check_reports_only_typed_status_without_mutating_worker_state(self):
        database_probe = Mock()
        gpu_probe = Mock(return_value={"index": 1, "name": "NVIDIA RTX 4090"})

        with (
            patch.object(main, "register_worker") as register_worker,
            patch.object(main, "recover_worker_ownership") as recover_worker,
            patch.object(main, "claim_next_task") as claim_next_task,
            patch.object(main, "set_worker_offline") as set_worker_offline,
        ):
            result = main.run_startup_check(
                database_probe=database_probe,
                output_root=self.output_root,
                base_model_path=self.base_model_root,
                requested_device="cuda",
                device_probe=Mock(
                    return_value={
                        "device_type": "cuda",
                        "profile_name": "cuda_qlora_bf16",
                    }
                ),
                environ=self.environment,
                gpu_probe=gpu_probe,
            )

        self.assertEqual(
            result,
            {
                "database": "ready",
                "output_root": "writable",
                "base_model": "ready",
                "device": "ready",
                "device_type": "cuda",
                "profile_name": "cuda_qlora_bf16",
                "gpu": "ready",
                "ok": True,
            },
        )
        database_probe.assert_called_once_with()
        gpu_probe.assert_called_once_with()
        register_worker.assert_not_called()
        recover_worker.assert_not_called()
        claim_next_task.assert_not_called()
        set_worker_offline.assert_not_called()
        self.assertEqual(list(self.output_root.iterdir()), [])

    def test_startup_check_returns_failure_for_each_unavailable_prerequisite(self):
        (self.base_model_root / "config.json").unlink()

        result = main.run_startup_check(
            database_probe=Mock(side_effect=RuntimeError("secret connection URL")),
            output_root=self.output_root / "missing",
            base_model_path=self.base_model_root,
            requested_device="cuda",
            device_probe=Mock(
                return_value={
                    "device_type": "cuda",
                    "profile_name": "cuda_qlora_bf16",
                }
            ),
            environ={"NVIDIA_VISIBLE_DEVICES": "all", "CUDA_VISIBLE_DEVICES": "1"},
            gpu_probe=Mock(side_effect=RuntimeError("driver details")),
        )

        self.assertEqual(
            result,
            {
                "database": "unavailable",
                "output_root": "unwritable",
                "base_model": "unavailable",
                "device": "ready",
                "device_type": "cuda",
                "profile_name": "cuda_qlora_bf16",
                "gpu": "unavailable",
                "ok": False,
            },
        )
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("driver", json.dumps(result))

    def test_cpu_startup_never_calls_gpu_probe_or_nvidia_smi(self):
        gpu_probe = Mock(side_effect=AssertionError("GPU probe must not run"))
        with patch.object(
            main,
            "nvidia_smi_snapshot",
            side_effect=AssertionError("nvidia-smi must not run"),
        ) as nvidia_smi:
            result = main.run_startup_check(
                database_probe=Mock(),
                output_root=self.output_root,
                base_model_path=self.base_model_root,
                requested_device="cpu",
                device_probe=Mock(
                    return_value={
                        "device_type": "cpu",
                        "profile_name": "cpu_qlora_4bit_bf16",
                    }
                ),
                environ=self.environment,
                gpu_probe=gpu_probe,
            )

        self.assertEqual(
            result,
            {
                "database": "ready",
                "output_root": "writable",
                "base_model": "ready",
                "device": "ready",
                "device_type": "cpu",
                "profile_name": "cpu_qlora_4bit_bf16",
                "ok": True,
            },
        )
        gpu_probe.assert_not_called()
        nvidia_smi.assert_not_called()

    def test_device_probe_failures_and_malformed_results_are_sanitized(self):
        probes = (
            Mock(side_effect=RuntimeError("secret device details")),
            Mock(return_value={"device_type": "metal", "profile_name": "injected"}),
            Mock(return_value={"device_type": "cpu", "profile_name": "unknown"}),
            Mock(return_value={"device_type": "cpu", "profile_name": "cpu_lora_fp32", "token": "secret"}),
        )

        for device_probe in probes:
            with self.subTest(device_probe=device_probe):
                result = main.run_startup_check(
                    database_probe=Mock(),
                    output_root=self.output_root,
                    base_model_path=self.base_model_root,
                    requested_device="cpu",
                    device_probe=device_probe,
                    gpu_probe=Mock(side_effect=AssertionError("GPU probe must not run")),
                )
                self.assertEqual(result["device"], "unavailable")
                self.assertFalse(result["ok"])
                self.assertNotIn("secret", json.dumps(result))
                self.assertNotIn("token", result)

    def test_cuda_probe_failure_retains_sanitized_legacy_gpu_status(self):
        result = main.run_startup_check(
            database_probe=Mock(),
            output_root=self.output_root,
            base_model_path=self.base_model_root,
            requested_device="cuda",
            device_probe=Mock(side_effect=RuntimeError("secret device details")),
            environ=self.environment,
            gpu_probe=Mock(return_value={"index": 0}),
        )

        self.assertEqual(result["device"], "unavailable")
        self.assertEqual(result["gpu"], "ready")
        self.assertFalse(result["ok"])
        self.assertNotIn("secret", json.dumps(result))

    def test_startup_check_uses_the_real_default_gpu_probe(self):
        completed = Mock(
            stdout="0, NVIDIA RTX 3090, 24576, 0, 24576, 0, 30, 570.00\n"
        )

        with patch(
            "app.training_worker.executor.subprocess.run", return_value=completed
        ) as run:
            result = main.run_startup_check(
                database_probe=Mock(),
                output_root=self.output_root,
                base_model_path=self.base_model_root,
                requested_device="cuda",
                device_probe=Mock(
                    return_value={
                        "device_type": "cuda",
                        "profile_name": "cuda_qlora_bf16",
                    }
                ),
                environ=self.environment,
            )

        self.assertEqual(result["gpu"], "ready")
        run.assert_called_once()

    def test_explicit_empty_environment_never_falls_back_to_process_environment(self):
        with patch.dict(
            "os.environ",
            {"NVIDIA_VISIBLE_DEVICES": "1", "CUDA_VISIBLE_DEVICES": "0"},
            clear=True,
        ):
            result = main.run_startup_check(
                database_probe=Mock(),
                output_root=self.output_root,
                base_model_path=self.base_model_root,
                requested_device="cuda",
                device_probe=Mock(
                    return_value={
                        "device_type": "cuda",
                        "profile_name": "cuda_qlora_bf16",
                    }
                ),
                environ={},
                gpu_probe=Mock(return_value={"index": 0}),
            )

        self.assertEqual(result["gpu"], "unavailable")

    def test_check_cli_uses_only_read_only_probe_and_never_constructs_runtime(self):
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session_factory = Mock(return_value=session)

        forbidden_names = (
            "register_worker",
            "recover_worker_ownership",
            "claim_next_task",
            "reconcile_artifact_deletions",
            "reconcile_task_cleanups",
            "update_locked_worker",
            "set_worker_offline",
            "WorkerRuntime",
        )
        forbidden = [patch.object(main, name) for name in forbidden_names]
        started = [item.start() for item in forbidden]
        try:
            with (
                patch.object(main, "SessionLocal", session_factory),
                patch.object(main, "nvidia_smi_snapshot", return_value={"index": 0}),
                patch.dict(
                    "os.environ",
                    {"NVIDIA_VISIBLE_DEVICES": "1", "CUDA_VISIBLE_DEVICES": "0"},
                    clear=True,
                ),
                patch("builtins.print"),
            ):
                self.assertEqual(main.main(["--check"]), 1)
        finally:
            for item in reversed(forbidden):
                item.stop()

        session_factory.assert_called_once_with()
        session.execute.assert_called_once()
        self.assertEqual(str(session.execute.call_args.args[0]), "SELECT 1")
        session.commit.assert_not_called()
        session.flush.assert_not_called()
        for mocked in started:
            mocked.assert_not_called()

    def test_check_cli_prints_sanitized_json_and_returns_status(self):
        ready = {
            "database": "ready",
            "output_root": "writable",
            "base_model": "ready",
            "gpu": "ready",
            "ok": True,
        }
        unavailable = {**ready, "database": "unavailable", "ok": False}

        with patch.object(main, "run_startup_check", return_value=ready), patch("builtins.print") as output:
            self.assertEqual(main.main(["--check"]), 0)
            self.assertEqual(json.loads(output.call_args.args[0]), ready)
        with patch.object(main, "run_startup_check", return_value=unavailable), patch("builtins.print") as output:
            self.assertEqual(main.main(["--check"]), 1)
            self.assertEqual(json.loads(output.call_args.args[0]), unavailable)


if __name__ == "__main__":
    unittest.main()
