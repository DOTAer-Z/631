from __future__ import annotations

import argparse
import json
import os
import signal
import stat
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Protocol

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal
from app.models.training_task import TrainingTask
from app.services.training_artifact_service import reconcile_artifact_deletions
from app.training_worker.executor import (
    ExecutionResult,
    ProcessCancellationController,
    WorkerServices,
    execute_task,
    nvidia_smi_snapshot,
    reconcile_task_cleanups,
)
from app.training_worker.repository import (
    claim_next_task,
    register_worker,
    recover_worker_ownership,
    set_worker_offline,
    update_locked_worker,
)


class CancellationController(Protocol):
    def request_cancel(self, task_id: str) -> None:
        ...


class NoopCancellationController:
    def request_cancel(self, task_id: str) -> None:
        return None


TaskExecutor = Callable[[str], ExecutionResult | None]
Reconciler = Callable[[Session, str | Path], None]
GpuSnapshotProvider = Callable[[], dict[str, Any]]
DeviceProbe = Callable[[], dict[str, Any]]
ClaimTask = Callable[[Session, str], TrainingTask | None]

_PROFILE_DEVICE_TYPES = {
    "cuda_qlora_bf16": "cuda",
    "cpu_qlora_4bit_bf16": "cpu",
    "cpu_qlora_4bit_fp32": "cpu",
    "cpu_lora_bf16": "cpu",
    "cpu_lora_fp32": "cpu",
}


def _empty_gpu_snapshot() -> dict[str, Any]:
    return {}


def base_model_status(base_model_path: str | Path) -> str:
    """Check only local model availability; downloading is intentionally out of scope."""
    path = Path(base_model_path)
    return "ready" if path.exists() and os.access(path, os.R_OK) else "unavailable"


def _database_probe() -> None:
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))


def _output_root_writable(output_root: str | Path) -> bool:
    path = Path(output_root)
    if not path.is_dir() or not os.access(path, os.W_OK | os.X_OK):
        return False
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return bool(mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _base_model_marker_ready(base_model_path: str | Path) -> bool:
    marker = Path(base_model_path) / "config.json"
    return marker.is_file() and os.access(marker, os.R_OK)


def _gpu_ready(environ: dict[str, str], gpu_probe: GpuSnapshotProvider) -> bool:
    if environ.get("NVIDIA_VISIBLE_DEVICES") != "1":
        return False
    if environ.get("CUDA_VISIBLE_DEVICES") != "0":
        return False
    try:
        snapshot = gpu_probe()
    except Exception:
        return False
    return isinstance(snapshot, dict) and isinstance(snapshot.get("index"), int)


def _default_device_probe(
    requested_device: str,
    environ: dict[str, str],
) -> dict[str, str]:
    if requested_device not in {"auto", "cuda", "cpu"}:
        raise ValueError("requested training device is invalid")
    if requested_device == "cpu":
        device_type = "cpu"
    elif requested_device == "cuda":
        device_type = "cuda"
    else:
        device_type = (
            "cuda"
            if environ.get("NVIDIA_VISIBLE_DEVICES") == "1"
            and environ.get("CUDA_VISIBLE_DEVICES") == "0"
            else "cpu"
        )
    return {
        "device_type": device_type,
        "profile_name": (
            "cuda_qlora_bf16"
            if device_type == "cuda"
            else "cpu_qlora_4bit_bf16"
        ),
    }


def _typed_device_result(
    requested_device: str,
    device_probe: DeviceProbe,
) -> dict[str, str] | None:
    try:
        snapshot = device_probe()
    except Exception:
        return None
    if not isinstance(snapshot, dict) or set(snapshot) != {"device_type", "profile_name"}:
        return None
    device_type = snapshot.get("device_type")
    profile_name = snapshot.get("profile_name")
    if (
        not isinstance(device_type, str)
        or not isinstance(profile_name, str)
        or _PROFILE_DEVICE_TYPES.get(profile_name) != device_type
        or (requested_device == "cpu" and device_type != "cpu")
        or (requested_device == "cuda" and device_type != "cuda")
        or requested_device not in {"auto", "cuda", "cpu"}
    ):
        return None
    return {"device_type": device_type, "profile_name": profile_name}


def run_startup_check(
    *,
    database_probe: Callable[[], None] = _database_probe,
    output_root: str | Path = settings.TRAINING_OUTPUT_ROOT,
    base_model_path: str | Path = settings.TRAINING_BASE_MODEL_PATH,
    requested_device: str = settings.TRAINING_DEVICE,
    device_probe: DeviceProbe | None = None,
    environ: dict[str, str] | None = None,
    gpu_probe: GpuSnapshotProvider | None = None,
) -> dict[str, str | bool]:
    """Inspect Worker prerequisites without registering or claiming database work."""
    try:
        database_probe()
        database_status = "ready"
    except Exception:
        database_status = "unavailable"
    active_environ = environ if environ is not None else dict(os.environ)
    selected = _typed_device_result(
        requested_device,
        device_probe
        or (lambda: _default_device_probe(requested_device, active_environ)),
    )
    result: dict[str, str | bool] = {
        "database": database_status,
        "output_root": "writable" if _output_root_writable(output_root) else "unwritable",
        "base_model": "ready" if _base_model_marker_ready(base_model_path) else "unavailable",
        "device": "ready" if selected is not None else "unavailable",
    }
    if selected is not None:
        result.update(selected)
    if (
        selected is not None and selected["device_type"] == "cuda"
    ) or (selected is None and requested_device == "cuda"):
        result["gpu"] = (
            "ready"
            if _gpu_ready(active_environ, gpu_probe or nvidia_smi_snapshot)
            else "unavailable"
        )
    result["ok"] = all(
        result[name] == expected
        for name, expected in (
            ("database", "ready"),
            ("output_root", "writable"),
            ("base_model", "ready"),
            ("device", "ready"),
        )
    ) and (result.get("device_type") != "cuda" or result.get("gpu") == "ready")
    return result


class WorkerRuntime:
    """Serial worker orchestration with database transactions owned at this layer."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        worker_id: str = settings.TRAINING_WORKER_ID,
        output_root: str | Path = settings.TRAINING_OUTPUT_ROOT,
        base_model_path: str | Path = settings.TRAINING_BASE_MODEL_PATH,
        poll_seconds: int = settings.TRAINING_POLL_SECONDS,
        stale_seconds: int = settings.TRAINING_STALE_SECONDS,
        executor: TaskExecutor | None = None,
        cancellation_controller: CancellationController | None = None,
        reconciler: Reconciler = reconcile_artifact_deletions,
        cleanup_reconciler: Reconciler = reconcile_task_cleanups,
        gpu_snapshot_provider: GpuSnapshotProvider | None = None,
        claim_task: ClaimTask = claim_next_task,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = datetime.utcnow,
    ) -> None:
        self.session_factory = session_factory
        self.worker_id = worker_id
        self.output_root = output_root
        self.base_model_path = base_model_path
        self.poll_seconds = max(1, poll_seconds)
        self.stale_seconds = stale_seconds
        if executor is None:
            controller = ProcessCancellationController()
            service_kwargs: dict[str, Any] = {
                "session_factory": session_factory,
                "output_root": output_root,
                "base_model_path": base_model_path,
                "worker_id": worker_id,
                "cancellation_controller": controller,
            }
            if gpu_snapshot_provider is not None:
                service_kwargs["gpu_snapshot_provider"] = gpu_snapshot_provider
            services = WorkerServices(
                **service_kwargs,
            )
            self.executor = lambda task_id: execute_task(task_id, services)
            self.cancellation_controller = controller
        else:
            self.executor = executor
            self.cancellation_controller = cancellation_controller or NoopCancellationController()
        self.reconciler = reconciler
        self.cleanup_reconciler = cleanup_reconciler
        self.gpu_snapshot_provider = gpu_snapshot_provider or _empty_gpu_snapshot
        self.claim_task = claim_task
        self.sleep = sleep
        self.now = now
        self.current_task_id: str | None = None
        self.stop_requested = False
        self._shutdown_persisted = False

    def startup_claim(self) -> str | None:
        """Reconcile, recover this worker, then atomically claim only if it is idle."""
        self._reconcile_startup()
        if self._recover_worker_state():
            return None
        if self.stop_requested:
            return None
        return self._claim_one()

    def claim_next(self) -> str | None:
        if self.stop_requested or self._prepare_claim_cycle():
            return None
        return self._claim_one()

    def _claim_one(self) -> str | None:
        with self.session_factory() as db:
            with db.begin():
                task = self.claim_task(db, self.worker_id)
                task_id = task.id if task is not None else None
        self.current_task_id = task_id
        return task_id

    def _reconcile_startup(self) -> None:
        with self.session_factory() as db:
            with db.begin():
                self.reconciler(db, self.output_root)
                self.cleanup_reconciler(db, self.output_root)

    def _recover_worker_state(self) -> bool:
        """Return whether fresh owned work exists after stale-task recovery."""
        model_status = base_model_status(self.base_model_path)
        cycle_now = self.now()
        stale_before = cycle_now - timedelta(seconds=self.stale_seconds)
        with self.session_factory() as db:
            with db.begin():
                worker, fresh_task = recover_worker_ownership(
                    db, self.worker_id, stale_before
                )
                self.current_task_id = fresh_task.id if fresh_task is not None else None
                update_locked_worker(
                    worker,
                    status="error" if model_status != "ready" else ("busy" if fresh_task else "idle"),
                    current_task_id=self.current_task_id,
                    model_status=model_status,
                    gpu_snapshot=self.gpu_snapshot_provider(),
                    now=cycle_now,
                )
        return self.current_task_id is not None or model_status != "ready"

    def _prepare_claim_cycle(self) -> bool:
        """Return whether an unavailable model or fresh current work defers claiming."""
        model_status = base_model_status(self.base_model_path)
        cycle_now = self.now()
        stale_before = cycle_now - timedelta(seconds=self.stale_seconds)
        with self.session_factory() as db:
            with db.begin():
                worker, fresh_task = recover_worker_ownership(
                    db, self.worker_id, stale_before
                )
                self.current_task_id = fresh_task.id if fresh_task is not None else None
                update_locked_worker(
                    worker,
                    status="error" if model_status != "ready" else ("busy" if fresh_task else "idle"),
                    current_task_id=self.current_task_id,
                    model_status=model_status,
                    gpu_snapshot=self.gpu_snapshot_provider(),
                    now=cycle_now,
                )
        return model_status != "ready" or self.current_task_id is not None

    def execute_claimed_task(self, task_id: str) -> None:
        if self.stop_requested:
            return
        result = self.executor(task_id)
        if not isinstance(result, ExecutionResult):
            return
        if result.task_id != task_id:
            raise RuntimeError("executor did not return a durable execution result")
        self.current_task_id = None

    def run_forever(self, *, max_cycles: int | None = None) -> None:
        cycles = 0
        try:
            task_id = self.startup_claim()
            while not self.stop_requested:
                if task_id is not None:
                    self.execute_claimed_task(task_id)
                if max_cycles is not None:
                    cycles += 1
                    if cycles >= max_cycles:
                        break
                task_id = self.claim_next()
                if task_id is None and not self.stop_requested:
                    self.sleep(self.poll_seconds)
        finally:
            self.shutdown()

    def request_stop(self) -> None:
        if self.stop_requested:
            return
        self.stop_requested = True
        if self.current_task_id is not None:
            self.cancellation_controller.request_cancel(self.current_task_id)

    def shutdown(self) -> None:
        if self._shutdown_persisted:
            return
        with self.session_factory() as db:
            with db.begin():
                set_worker_offline(db, self.worker_id)
        self._shutdown_persisted = True
        self.current_task_id = None

    def install_signal_handlers(self) -> None:
        def _handle_signal(_signum: int, _frame: object) -> None:
            self.request_stop()

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)


def _unconfigured_executor(task_id: str) -> None:
    raise RuntimeError(f"training execution is not configured for task {task_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the serial GPU training Worker")
    parser.add_argument(
        "--check",
        action="store_true",
        help="check prerequisites without registering or claiming work",
    )
    args = parser.parse_args(argv)
    if args.check:
        result = run_startup_check()
        print(json.dumps(result, sort_keys=True))
        return 0 if result["ok"] else 1
    runtime = WorkerRuntime()
    runtime.install_signal_handlers()
    runtime.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
