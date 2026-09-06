from __future__ import annotations

import hashlib
import errno
import json
import math
import os
import re
import shutil
import signal
import struct
import stat
import subprocess
import sys
import tarfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.training_task import (
    TrainingArtifact,
    TrainingEvaluation,
    TrainingMetric,
    TrainingTask,
    TrainingWorker,
)
from app.services.training_artifact_service import (
    acquire_artifact_registry_lock,
    register_artifact,
    resolve_artifact_path,
)
from app.services.training_public_artifact_service import (
    path_metadata as _path_metadata,
    write_sanitized_log as _write_sanitized_log,
)
from app.services.training_task_service import (
    TrainingTaskConflict,
    TrainingTaskNotFound,
    classify_cpt_adapter,
    classify_uploaded_sft_adapter,
)
from app.training_contracts import EVALUATION_SCORE_KEYS
from app.training_worker.materializer import (
    MaterializedTask,
    materialize_task,
    write_executable_training_config,
)


class ExecutionError(RuntimeError):
    pass


class TerminalPersistenceError(ExecutionError):
    pass


class UnownedTaskError(ExecutionError):
    pass


@dataclass(frozen=True)
class _VirtualMemory:
    available: int


def _system_virtual_memory() -> _VirtualMemory:
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        available_pages = os.sysconf("SC_AVPHYS_PAGES")
    except (OSError, ValueError) as exc:
        raise ExecutionError("available system memory could not be determined") from exc
    if type(page_size) is not int or type(available_pages) is not int:
        raise ExecutionError("available system memory could not be determined")
    return _VirtualMemory(available=page_size * available_pages)


@dataclass(frozen=True)
class ExecutionResult:
    task_id: str
    state: str
    error_message: str | None = None


class ProcessCancellationController:
    def __init__(self, *, killpg: Callable[[int, int], None] = os.killpg):
        self._killpg = killpg
        self._active: dict[str, int] = {}
        self._requested: set[str] = set()

    def register(self, task_id: str, pid: int) -> None:
        self._active[task_id] = pid

    def unregister(self, task_id: str) -> None:
        self._active.pop(task_id, None)
        self._requested.discard(task_id)

    def request_cancel(self, task_id: str) -> None:
        self._requested.add(task_id)
        pid = self._active.get(task_id)
        if pid is not None:
            self._signal_group(pid, signal.SIGTERM)

    def terminate(self, task_id: str) -> None:
        pid = self._active.get(task_id)
        if pid is not None:
            self._signal_group(pid, signal.SIGTERM)

    def kill(self, task_id: str) -> None:
        pid = self._active.get(task_id)
        if pid is not None:
            self._signal_group(pid, signal.SIGKILL)

    def _signal_group(self, pid: int, sent_signal: int) -> None:
        try:
            self._killpg(pid, sent_signal)
        except ProcessLookupError:
            return
        except OSError as exc:
            if exc.errno != errno.ESRCH:
                raise

    def externally_requested(self, task_id: str) -> bool:
        return task_id in self._requested


def nvidia_smi_snapshot() -> dict[str, Any]:
    command = [
        "nvidia-smi",
        "--query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,driver_version",
        "--format=csv,noheader,nounits",
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, check=True, timeout=10
    )
    rows = [line for line in completed.stdout.splitlines() if line.strip()]
    if len(rows) != 1:
        raise ExecutionError("worker requires exactly one visible GPU")
    values = [value.strip() for value in rows[0].split(",")]
    if len(values) != 8:
        raise ExecutionError("nvidia-smi returned an invalid snapshot")
    return {
        "index": int(values[0]),
        "name": values[1],
        "memory_total_mb": float(values[2]),
        "memory_used_mb": float(values[3]),
        "memory_free_mb": float(values[4]),
        "utilization_percent": float(values[5]),
        "temperature_c": float(values[6]),
        "driver_version": values[7],
    }


@dataclass
class WorkerServices:
    session_factory: Callable[[], Session] = SessionLocal
    output_root: str | Path = settings.TRAINING_OUTPUT_ROOT
    base_model_path: str | Path = settings.TRAINING_BASE_MODEL_PATH
    worker_id: str = settings.TRAINING_WORKER_ID
    materialize: Callable[[Session, TrainingTask, Path], MaterializedTask] = materialize_task
    process_factory: Callable[..., Any] = subprocess.Popen
    gpu_snapshot_provider: Callable[[], dict[str, Any]] = nvidia_smi_snapshot
    disk_usage: Callable[[str | Path], Any] = shutil.disk_usage
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = datetime.utcnow
    heartbeat_seconds: float = settings.TRAINING_HEARTBEAT_SECONDS
    cancel_timeout_seconds: float = 30.0
    min_free_disk_gb: float = settings.TRAINING_MIN_FREE_DISK_GB
    min_free_gpu_mb: float = settings.TRAINING_MIN_FREE_GPU_MB
    requested_device: str = settings.TRAINING_DEVICE
    cpu_profile: str = settings.TRAINING_CPU_PROFILE
    min_free_cpu_gb: float = settings.TRAINING_MIN_FREE_CPU_GB
    virtual_memory: Callable[[], Any] = _system_virtual_memory
    cancellation_controller: ProcessCancellationController = field(default_factory=ProcessCancellationController)
    group_exists: Callable[[int], bool] = field(default=lambda pid: _process_group_exists(pid))
    environ: dict[str, str] = field(default_factory=lambda: dict(os.environ))

    def __post_init__(self) -> None:
        if self.requested_device not in {"auto", "cuda", "cpu"}:
            raise ValueError("requested training device is invalid")
        if (
            isinstance(self.min_free_cpu_gb, bool)
            or not isinstance(self.min_free_cpu_gb, (int, float))
            or not math.isfinite(self.min_free_cpu_gb)
            or self.min_free_cpu_gb <= 0
        ):
            raise ValueError("minimum free CPU memory must be positive")


_ENV_ALLOWLIST = {
    "PATH", "PYTHONPATH", "HOME", "LANG", "LC_ALL", "LD_LIBRARY_PATH",
    "CUDA_HOME", "CUDA_PATH", "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES",
    "HF_HOME", "TRANSFORMERS_CACHE", "TORCH_HOME", "TMPDIR",
}
_ADAPTER_REQUIRED = {"adapter_config.json", "adapter_model.safetensors"}
_CHECKPOINT_REQUIRED = _ADAPTER_REQUIRED | {"trainer_state.json"}
_GPU_NAME_PATTERN = re.compile(r"NVIDIA [A-Za-z0-9][A-Za-z0-9 .()/_+\-]{0,120}")
_GPU_VERSION_PATTERN = re.compile(r"\d+(?:\.\d+){1,3}")
_PROFILE_FIELDS = {
    "cuda_qlora_bf16": ("cuda", "nf4_4bit", "bf16"),
    "cpu_qlora_4bit_bf16": ("cpu", "nf4_4bit", "bf16"),
    "cpu_qlora_4bit_fp32": ("cpu", "nf4_4bit", "fp32"),
    "cpu_lora_bf16": ("cpu", "none", "bf16"),
    "cpu_lora_fp32": ("cpu", "none", "fp32"),
}
_CPU_PROFILE_NAMES = tuple(
    name for name, fields in _PROFILE_FIELDS.items() if fields[0] == "cpu"
)
_SAFE_PROFILE_KEYS = {"device_type", "profile_name", "quantization", "compute_dtype"}
_GIB = 1024**3
_MAX_METRIC_RECORDS = 100
_MAX_METRIC_LINE_BYTES = 64 * 1024
_HASH_CHUNK_BYTES = 1024 * 1024
_MAX_SAFETENSORS_HEADER_BYTES = 100 * 1024 * 1024
_MAX_ADAPTER_ARCHIVE_MEMBERS = 128
_MAX_ADAPTER_MEMBER_BYTES = 2 * _GIB
_MAX_ADAPTER_EXPANDED_BYTES = 2 * _GIB
_UTC_TIMESTAMP_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)"
)


@dataclass(frozen=True)
class _ArtifactDescriptor:
    artifact_type: str
    path: Path
    relative_path: str
    size_bytes: int
    sha256: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class _TerminalArtifacts:
    descriptors: tuple[_ArtifactDescriptor, ...]
    cleanup_paths: tuple[Path, ...] = ()


def execute_task(task_id: str, services: WorkerServices) -> ExecutionResult:
    prepared = None
    try:
        cancel_requested = _verify_active_ownership(task_id, services)
    except UnownedTaskError as exc:
        return ExecutionResult(task_id, "failed", _safe_error(exc))
    except TerminalPersistenceError:
        raise
    except Exception as exc:
        raise TerminalPersistenceError("ownership verification failed") from exc
    if cancel_requested:
        return _terminal(task_id, services, "cancelled")
    try:
        task_root = _trusted_task_root(services.output_root, task_id)
    except Exception as exc:
        return _terminal(task_id, services, "failed", _safe_error(exc))
    try:
        prepared = _prepare(task_id, task_root, services)
        process = _mark_running_and_spawn(prepared, task_id, services)
        if process is None:
            terminal = _finish_incomplete(
                task_root, state="cancelled", services=services, prepared=prepared
            )
            return _terminal(task_id, services, "cancelled", artifacts=terminal)
        state = _monitor(
            process,
            task_id,
            prepared.metrics_path,
            services,
            job_kind=prepared.job_kind,
        )
        if state in {"cancelled", "interrupted"}:
            terminal = _finish_incomplete(
                task_root, state=state, services=services, prepared=prepared
            )
            return _terminal(task_id, services, state, artifacts=terminal)
        if process.returncode != 0:
            child_error = _failed_child_error(prepared.result_path, task_root)
            raise ExecutionError(child_error or f"training child exited with status {process.returncode}")
        result, effective_profile = _load_result(
            prepared.result_path, task_root, prepared.job_kind, services
        )
        if result.get("status") != "succeeded":
            raise ExecutionError(str(result.get("error") or "training child failed"))
        if prepared.job_kind == "evaluation":
            summary, terminal = _finish_evaluation(task_root, result, services)
            return _terminal(
                task_id,
                services,
                "succeeded",
                evaluation_summary=summary,
                artifacts=terminal,
            )
        else:
            terminal = _finish_training(
                task_root, prepared, result, effective_profile, services
            )
            return _terminal(task_id, services, "succeeded", artifacts=terminal)
    except TerminalPersistenceError:
        raise
    except Exception as exc:
        message = _safe_error(exc)
        try:
            terminal = _finish_incomplete(
                task_root, state="failed", services=services, prepared=prepared
            )
            return _terminal(
                task_id, services, "failed", message, artifacts=terminal
            )
        except TerminalPersistenceError:
            raise
        except Exception as finalize_exc:
            fallback_error = _safe_error(finalize_exc)
            return _terminal(
                task_id,
                services,
                "failed",
                f"{message}; artifact preparation failed: {fallback_error}",
                artifacts=_TerminalArtifacts(()),
            )


@dataclass(frozen=True)
class _PreparedExecution:
    job_kind: str
    spec_path: Path
    result_path: Path
    metrics_path: Path
    config_path: Path | None
    materialized: MaterializedTask | None
    public_config: dict[str, Any]


@dataclass(frozen=True)
class _ValidatedProfile:
    device_type: str
    profile_name: str
    quantization: str
    compute_dtype: str

    def metadata(self) -> dict[str, str]:
        return {
            "device_type": self.device_type,
            "profile_name": self.profile_name,
            "quantization": self.quantization,
            "compute_dtype": self.compute_dtype,
        }


def _prepare(task_id: str, task_root: Path, services: WorkerServices) -> _PreparedExecution:
    model = _validate_base_model(services.base_model_path)
    disk = services.disk_usage(task_root)
    if disk.free < services.min_free_disk_gb * 1024**3:
        raise ExecutionError("insufficient free disk for training")
    if services.requested_device != "cpu":
        gpu = _typed_gpu_snapshot(services.gpu_snapshot_provider())
        if gpu["memory_free_mb"] < services.min_free_gpu_mb:
            raise ExecutionError("insufficient free GPU memory for training")

    with services.session_factory() as db:
        with db.begin():
            task = _lock_execution_graph(db, task_id, services.worker_id)
            job_kind = task.job_kind
            public_config = _sanitize_public_value({
                "schema_version": 1,
                "task_type": task.task_type,
                "job_kind": task.job_kind,
                "model_id": task.model_id,
                "config": task.config_snapshot,
            }, services, task_root)
            if job_kind == "training":
                resume_path, required_profile = _validated_resume_checkpoint(
                    db, task, services
                )
                if services.requested_device == "cpu":
                    _cpu_memory_preflight(model, services, required_profile)
                materialized = services.materialize(db, task, task_root)
                cpt_path = _validated_cpt_adapter(db, task, services)
                config_path = write_executable_training_config(
                    task, materialized, task_root, base_model_path=model,
                    cpt_adapter_path=cpt_path,
                )
                config_sha256 = _sha256_regular_file(config_path, "training config")
                spec = _training_spec(task, task_root, model, materialized, config_path,
                                      config_sha256, cpt_path, resume_path,
                                      services, required_profile)
                _ensure_task_child_directory(task_root, "run")
            else:
                if services.requested_device == "cpu":
                    _cpu_memory_preflight(model, services, None)
                materialized = None
                config_path = None
                spec = _evaluation_spec(db, task, task_root, model, services)

    spec_path = task_root / "task-spec.json"
    _atomic_json(spec_path, spec)
    return _PreparedExecution(
        job_kind,
        spec_path,
        task_root / "result.json",
        task_root / "metrics.jsonl",
        config_path,
        materialized,
        public_config,
    )


def _verify_active_ownership(task_id: str, services: WorkerServices) -> bool:
    with services.session_factory() as db:
        with db.begin():
            acquire_artifact_registry_lock(db)
            current = (
                db.query(TrainingTask)
                .filter_by(id=task_id, deleted_at=None)
                .one_or_none()
            )
            if (current is None or current.worker_id != services.worker_id
                    or current.state not in {
                        "preparing_data", "training", "evaluating", "cancelling"
                    }):
                raise UnownedTaskError(
                    "task is not actively claimed by this worker"
                )
            try:
                task = _lock_execution_graph(db, task_id, services.worker_id)
            except ExecutionError as exc:
                raise TerminalPersistenceError(
                    "claimed task ownership graph is inconsistent"
                ) from exc
            return task.state == "cancelling" or task.cancel_requested_at is not None


def _training_spec(
    task, root, model, materialized, config, config_sha256, cpt, resume,
    services, required_profile,
):
    return _spec_base(task, root, model, services, required_profile) | {
        "config_path": str(config), "config_sha256": config_sha256, "test_file": None,
        "cpt_adapter_path": str(cpt) if cpt else None, "sft_adapter_path": None,
        "resume_checkpoint": str(resume) if resume else None,
        "evaluation_output_dir": None, "evaluation_checkpoint": None,
    }


def _evaluation_spec(db, task, root, model, services):
    evaluation = db.query(TrainingEvaluation).filter_by(task_id=task.id).one_or_none()
    if evaluation is None:
        raise ExecutionError("evaluation graph is invalid")
    task_source = evaluation.source_sft_task_id
    artifact_source = evaluation.source_sft_artifact_id
    if (task_source is None) == (artifact_source is None):
        raise ExecutionError("evaluation graph is invalid")
    if not _valid_evaluation_parent(db, task, evaluation):
        raise ExecutionError("evaluation graph is invalid")

    if task_source is not None:
        source = db.query(TrainingTask).filter_by(id=task_source).one_or_none()
        if (
            source is None
            or source.task_type != "sft"
            or source.job_kind != "training"
            or source.state != "succeeded"
        ):
            raise ExecutionError("evaluation source is not a succeeded SFT task")
        adapter = (db.query(TrainingArtifact)
            .filter_by(task_id=source.id, artifact_type="final_adapter", deleted_at=None, deletion_state=None)
            .one_or_none())
        if adapter is None:
            raise ExecutionError("source SFT final adapter is unavailable")
        adapter_path = _materialize_source_artifact(
            db,
            adapter,
            services,
            root / "inputs" / "sft-adapter",
            _ADAPTER_REQUIRED,
        )
        source_root = _inside_root(
            Path(services.output_root), Path(services.output_root) / source.id
        )
        source_materialized = services.materialize(db, source, source_root)
        source_test_file = _inside_root(
            Path(services.output_root), source_materialized.test_file
        )
        test_label = "source SFT frozen test dataset"
    else:
        adapter = _active_artifact(db, artifact_source)
        try:
            classify_uploaded_sft_adapter(adapter, task.model_id)
        except (TrainingTaskConflict, TrainingTaskNotFound):
            raise ExecutionError("SFT adapter artifact is not trusted") from None
        adapter_path = _materialize_source_artifact(
            db,
            adapter,
            services,
            root / "inputs" / "sft-adapter",
            _ADAPTER_REQUIRED,
            expected_size=adapter.size_bytes,
            expected_sha256=adapter.sha256,
        )
        task_materialized = services.materialize(db, task, root)
        source_test_file = _inside_root(
            Path(services.output_root), task_materialized.test_file
        )
        test_label = "evaluation frozen test dataset"
    if not source_test_file.is_file() or source_test_file.is_symlink():
        raise ExecutionError(f"{test_label} is unavailable")
    test_file = _snapshot_regular_file(
        source_test_file,
        root / "inputs" / "test.jsonl",
        test_label,
    )
    return _spec_base(task, root, model, services, None) | {
        "config_path": None, "config_sha256": None, "test_file": str(test_file),
        "cpt_adapter_path": None,
        "sft_adapter_path": str(adapter_path), "resume_checkpoint": None,
        "evaluation_output_dir": str(root / "evaluation"), "evaluation_checkpoint": "sft",
    }


def _valid_evaluation_parent(db, task, evaluation):
    if task.parent_task_id is None:
        return evaluation.source_sft_artifact_id is not None
    if task.parent_task_id == evaluation.source_sft_task_id:
        return True
    parent = (
        db.query(TrainingTask)
        .filter_by(id=task.parent_task_id, job_kind="evaluation")
        .one_or_none()
    )
    if parent is None:
        return False
    parent_evaluation = (
        db.query(TrainingEvaluation)
        .filter_by(task_id=parent.id)
        .one_or_none()
    )
    return (
        parent_evaluation is not None
        and parent_evaluation.source_sft_task_id == evaluation.source_sft_task_id
        and parent_evaluation.source_sft_artifact_id
        == evaluation.source_sft_artifact_id
    )


def _spec_base(task, root, model, services, required_profile):
    return {
        "schema_version": 1, "task_id": task.id, "task_type": task.task_type,
        "job_kind": task.job_kind, "task_root": str(root),
        "output_root": str(root.parent),
        "config_sha256": None,
        "base_model_path": str(model), "metrics_path": str(root / "metrics.jsonl"),
        "result_path": str(root / "result.json"), "log_path": str(root / "child.log"),
        "requested_device": services.requested_device,
        "required_device_profile": required_profile,
        "profile_path": str(root / "effective-device-profile.json"),
    }


def _spawn_child(spec_path: Path, task_id: str, services: WorkerServices):
    log_path = spec_path.parent / "child.log"
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    log_handle = os.fdopen(os.open(log_path, flags, 0o600), "ab")
    try:
        process = services.process_factory(
            [sys.executable, "-m", "app.training_worker.child", "--task-spec", str(spec_path)],
            env=_child_environment(services.environ, services.requested_device),
            stdin=subprocess.DEVNULL, stdout=log_handle, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception:
        log_handle.close()
        raise
    process._training_log_handle = log_handle
    services.cancellation_controller.register(task_id, process.pid)
    return process


def _monitor(
    process,
    task_id: str,
    metrics_path: Path,
    services: WorkerServices,
    *,
    job_kind: str,
) -> str:
    offset = 0
    published_source_size = None
    cancellation_sent = False
    release_ownership = False
    try:
        while True:
            if services.cancellation_controller.externally_requested(task_id):
                _stop_process(process, task_id, services, send_term=False)
                release_ownership = True
                return "interrupted"
            if process.poll() is not None:
                break
            offset = _persist_metric_records(task_id, metrics_path, offset, services)
            _, published_source_size = _publish_live_log(
                task_id,
                job_kind,
                services,
                include_partial=False,
                skip_source_size=published_source_size,
            )
            cancel = _heartbeat_and_cancel_requested(task_id, services)
            if cancel and not cancellation_sent:
                _stop_process(process, task_id, services, send_term=True)
                cancellation_sent = True
                release_ownership = True
                return "cancelled"
            services.sleep(max(0, services.heartbeat_seconds))
        _publish_live_log(task_id, job_kind, services, include_partial=True)
        if _heartbeat_and_cancel_requested(task_id, services):
            _stop_process(process, task_id, services, send_term=True)
            release_ownership = True
            return "cancelled"
        while True:
            next_offset = _persist_metric_records(
                task_id, metrics_path, offset, services
            )
            if next_offset == offset:
                break
            offset = next_offset
        release_ownership = True
        return "finished"
    except TerminalPersistenceError:
        raise
    except Exception as exc:
        try:
            _stop_process(process, task_id, services, send_term=True)
        except Exception as stop_exc:
            raise TerminalPersistenceError(
                "active child process ownership could not be recovered"
            ) from stop_exc
        release_ownership = True
        raise exc
    finally:
        if release_ownership:
            _release_process_ownership(process, task_id, services)


def _publish_live_log(
    task_id: str,
    job_kind: str,
    services: WorkerServices,
    *,
    include_partial: bool = True,
    skip_source_size: int | None = None,
) -> tuple[Path, int]:
    if job_kind not in {"training", "evaluation"}:
        raise ExecutionError("training job kind is invalid")
    root = _trusted_task_root(services.output_root, task_id)
    _ensure_task_child_directory(root, "public")
    filename = "evaluation.log" if job_kind == "evaluation" else "training.log"
    public_log = root / "public" / filename
    source_size = _write_sanitized_log(
        root / "child.log",
        public_log,
        services,
        root,
        include_partial=include_partial,
        skip_source_size=skip_source_size,
    )
    return public_log, source_size


def _stop_process(process, task_id, services, *, send_term):
    if send_term:
        services.cancellation_controller.terminate(task_id)
    try:
        process.wait(timeout=services.cancel_timeout_seconds)
    except (subprocess.TimeoutExpired, TimeoutError):
        services.cancellation_controller.kill(task_id)
        process.wait()
        return
    if services.group_exists(process.pid):
        services.cancellation_controller.kill(task_id)
    process.wait()


def _process_group_exists(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _persist_metric_records(task_id, path, offset, services):
    if not path.exists():
        return offset

    with services.session_factory() as db:
        with db.begin():
            task = _lock_owned_worker_and_task(db, task_id, services.worker_id)
            persisted_offset = (
                db.query(func.max(TrainingMetric.stream_offset))
                .filter(
                    TrainingMetric.task_id == task.id,
                    TrainingMetric.stream_offset >= 0,
                )
                .scalar()
            )

    start_offset = max(0, offset)
    with path.open("rb") as handle:
        if persisted_offset is not None:
            handle.seek(persisted_offset)
            persisted_line = handle.readline(_MAX_METRIC_LINE_BYTES + 2)
            if not persisted_line.endswith(b"\n"):
                raise ExecutionError("persisted child metric record is unavailable")
            start_offset = max(start_offset, handle.tell())
        handle.seek(start_offset)
        records = []
        for _ in range(_MAX_METRIC_RECORDS):
            line_offset = handle.tell()
            line = handle.readline(_MAX_METRIC_LINE_BYTES + 2)
            if not line:
                break
            if len(line) > _MAX_METRIC_LINE_BYTES:
                raise ExecutionError("child metric line exceeds 64 KiB")
            if not line.endswith(b"\n"):
                handle.seek(line_offset)
                break
            records.append((line_offset, _metric_record(line)))
        new_offset = handle.tell()
    if not records:
        return new_offset

    with services.session_factory() as db:
        with db.begin():
            task = _lock_owned_worker_and_task(db, task_id, services.worker_id)
            current_offset = (
                db.query(func.max(TrainingMetric.stream_offset))
                .filter(
                    TrainingMetric.task_id == task.id,
                    TrainingMetric.stream_offset >= 0,
                )
                .scalar()
            )
            fresh_records = [
                (stream_offset, record)
                for stream_offset, record in records
                if current_offset is None or stream_offset > current_offset
            ]
            for stream_offset, record in fresh_records:
                db.add(
                    TrainingMetric(
                        task_id=task_id,
                        stream_offset=stream_offset,
                        **record,
                    )
                )
            if not fresh_records:
                return new_offset
            epochs = task.config_snapshot.get("training", {}).get("num_train_epochs")
            epoch = fresh_records[-1][1]["epoch"]
            if (isinstance(epochs, (int, float)) and not isinstance(epochs, bool)
                    and epochs > 0 and epoch is not None):
                task.progress = max(task.progress or 0.0, min(0.99, epoch / epochs))
            elif fresh_records[-1][1]["step"] is not None:
                task.progress = max(task.progress or 0.0, 0.01)
    return new_offset


def _metric_record(line):
    try:
        value = json.loads(line, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ExecutionError("child metric stream contains invalid JSON") from exc
    if not isinstance(value, dict) or set(value) != {"step", "epoch", "loss", "eval_loss", "learning_rate", "timestamp"}:
        raise ExecutionError("child metric record does not match schema")
    step = value["step"]
    if step is not None and (type(step) is not int or step < 0):
        raise ExecutionError("child metric step is invalid")
    timestamp_text = value["timestamp"]
    if (
        not isinstance(timestamp_text, str)
        or _UTC_TIMESTAMP_PATTERN.fullmatch(timestamp_text) is None
    ):
        raise ExecutionError("child metric timestamp must be a UTC ISO timestamp")
    try:
        timestamp = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExecutionError("child metric timestamp is invalid") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != UTC.utcoffset(timestamp):
        raise ExecutionError("child metric timestamp must be UTC")
    return {
        "step": step,
        "created_at": timestamp.astimezone(UTC).replace(tzinfo=None),
        **{
            key: _nullable_metric_finite(value[key], key)
            for key in ("epoch", "loss", "eval_loss", "learning_rate")
        },
    }


def _lock_owned_worker_and_task(db, task_id, worker_id):
    worker = (
        db.query(TrainingWorker)
        .filter_by(id=worker_id)
        .with_for_update()
        .one_or_none()
    )
    task = _owned_task(db, task_id, worker_id)
    if (
        worker is None
        or worker.status != "busy"
        or worker.current_task_id != task_id
    ):
        raise ExecutionError("worker does not own the active task")
    return task


def _heartbeat_and_cancel_requested(task_id, services):
    with services.session_factory() as db:
        with db.begin():
            task = _lock_execution_graph(db, task_id, services.worker_id)
            task.heartbeat_at = services.now()
            worker = db.get(TrainingWorker, services.worker_id)
            worker.last_heartbeat_at = services.now()
            task_root = _trusted_task_root(services.output_root, task_id)
            worker.gpu_snapshot = _worker_device_snapshot(task_root, services)
            return task.state == "cancelling" or task.cancel_requested_at is not None


def _mark_running_and_spawn(prepared, task_id, services):
    process = None
    try:
        with services.session_factory() as db:
            with db.begin():
                task = _lock_execution_graph(db, task_id, services.worker_id)
                if task.state == "cancelling" or task.cancel_requested_at is not None:
                    return None
                task.state = "training" if task.job_kind == "training" else "evaluating"
                if task.job_kind == "evaluation":
                    evaluation = db.query(TrainingEvaluation).filter_by(task_id=task.id).one()
                    evaluation.status = "evaluating"
                    evaluation.started_at = evaluation.started_at or services.now()
                process = _spawn_child(prepared.spec_path, task_id, services)
        return process
    except Exception:
        if process is not None:
            try:
                _discard_spawned_process(process, task_id, services)
            except Exception as discard_exc:
                raise TerminalPersistenceError(
                    "spawned child ownership could not be recovered"
                ) from discard_exc
        raise


def _discard_spawned_process(process, task_id, services):
    _stop_process(process, task_id, services, send_term=True)
    _release_process_ownership(process, task_id, services)


def _release_process_ownership(process, task_id, services):
    services.cancellation_controller.unregister(task_id)
    handle = getattr(process, "_training_log_handle", None)
    if handle is not None:
        handle.close()


def _terminal(
    task_id,
    services,
    state,
    error=None,
    evaluation_summary=None,
    artifacts=None,
):
    terminal = artifacts or _TerminalArtifacts(())
    try:
        cleanup_paths = _cleanup_journal_paths(
            services.output_root, task_id, terminal.cleanup_paths
        )
        with services.session_factory() as db:
            with db.begin():
                task = _lock_execution_graph(db, task_id, services.worker_id)
                worker = db.get(TrainingWorker, services.worker_id)
                task.state = state
                task.completed_at = services.now()
                task.error_message = error
                if state == "succeeded":
                    task.progress = 1.0
                if task.job_kind == "evaluation":
                    row = db.query(TrainingEvaluation).filter_by(task_id=task_id).one()
                    row.status = state
                    row.completed_at = task.completed_at
                    row.error_message = error
                    if evaluation_summary is not None:
                        row.summary = evaluation_summary
                _register_descriptors(db, task_id, terminal.descriptors, services)
                if cleanup_paths:
                    task.cleanup_state = "pending"
                    task.cleanup_paths = list(cleanup_paths)
                    task.cleanup_updated_at = services.now()
                worker.current_task_id = None
                worker.status = "idle"
                worker.last_heartbeat_at = services.now()
    except Exception as exc:
        raise TerminalPersistenceError("terminal persistence failed") from exc
    if cleanup_paths:
        _retry_task_cleanup_safely(task_id, services)
    return ExecutionResult(task_id, state, error)


def _finish_training(root, prepared, result, effective_profile, services):
    profile = effective_profile.metadata()
    adapter = _result_path(result, "final_adapter", root / "run")
    if not _valid_directory(adapter, _ADAPTER_REQUIRED):
        raise ExecutionError("child did not produce a complete final adapter")
    tokenizer_bundle = _publish_tokenizer_bundle(adapter, root)
    if tokenizer_bundle is None:
        raise ExecutionError("child did not produce tokenizer files")
    _ensure_task_child_directory(root, "public")
    public = root / "public"
    config_path = public / "config.json"
    _atomic_json(config_path, prepared.public_config)
    log_path, _ = _publish_live_log(root.name, "training", services)
    artifact_root = root / "artifacts"
    artifact_root.mkdir(exist_ok=True)
    adapter_archive = artifact_root / "final_adapter.tar"
    tokenizer_archive = artifact_root / "tokenizer.tar"
    _create_deterministic_tar(adapter, adapter_archive)
    _create_deterministic_tar(tokenizer_bundle, tokenizer_archive)
    paths = [
        ("final_adapter", adapter_archive, profile), ("config", config_path, {}),
        ("split", prepared.materialized.split_path, {}),
        ("dataset", prepared.materialized.train_file, {"split": "train"}),
        ("dataset", prepared.materialized.validation_file, {"split": "validation"}),
        ("dataset", prepared.materialized.test_file, {"split": "test"}),
        ("log", log_path, {}),
    ]
    if (root / "metrics.jsonl").is_file():
        paths.append(("log", root / "metrics.jsonl", {"format": "jsonl"}))
    paths.append(("tokenizer", tokenizer_archive, {}))
    cleanup = tuple(
        path
        for path in (
            root / "run" / "final_adapter",
            root / "run" / "best_adapter",
            root / "run" / "checkpoints",
            root / "tokenizer",
        )
        if path.exists()
    )
    return _terminal_artifacts(paths, cleanup, services)


def _publish_tokenizer_bundle(adapter: Path, root: Path) -> Path | None:
    names = ("tokenizer.json", "tokenizer_config.json", "chat_template.jinja")
    sources = [adapter / name for name in names if (adapter / name).is_file()]
    if not sources:
        return None
    destination = root / "tokenizer"
    destination.mkdir(exist_ok=True)
    for source in sources:
        shutil.copy2(source, destination / source.name)
    return destination


def _finish_evaluation(root, result, services):
    summary_path = _result_path(result, "evaluation_summary", root / "evaluation")
    try: payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise ExecutionError("evaluation summary is invalid") from exc
    metrics = payload.get("sft", {}).get("metrics") if isinstance(payload, dict) else None
    if not isinstance(metrics, dict): raise ExecutionError("evaluation summary has no SFT metrics")
    if set(metrics) != set(EVALUATION_SCORE_KEYS):
        raise ExecutionError("evaluation summary score keys are invalid")
    summary = {}
    has_applicable_score = False
    for key in EVALUATION_SCORE_KEYS:
        value = metrics[key]
        if value is None:
            summary[key] = None
            continue
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value)):
            raise ExecutionError("evaluation summary score value is invalid")
        summary[key] = float(value)
        has_applicable_score = True
    if not has_applicable_score:
        raise ExecutionError("evaluation summary has no applicable scores")
    report_path = root / "public" / "summary.json"
    report_path.parent.mkdir(exist_ok=True)
    _atomic_json(report_path, {"metrics": summary})
    public_log, _ = _publish_live_log(root.name, "evaluation", services)
    return summary, _terminal_artifacts(
        [
            ("evaluation_report", report_path, {}),
            ("log", public_log, {}),
        ],
        (),
        services,
    )


def _finish_incomplete(root, *, state, services, prepared=None):
    checkpoints = root / "run" / "checkpoints"
    valid = [(int(path.name.removeprefix("checkpoint-")), path) for path in checkpoints.glob("checkpoint-*")
             if path.name.removeprefix("checkpoint-").isdigit() and _valid_directory(path, _CHECKPOINT_REQUIRED)] if checkpoints.is_dir() else []
    paths = []
    if valid:
        step, latest = max(valid)
        profile = _effective_profile_for_worker(root, services)
        artifact_root = root / "artifacts"
        artifact_root.mkdir(exist_ok=True)
        archive = artifact_root / f"checkpoint-{step}.tar"
        _create_deterministic_tar(latest, archive)
        paths.append(("checkpoint", archive, {"step": step, **profile}))
    job_kind = prepared.job_kind if prepared is not None else "training"
    public_log, _ = _publish_live_log(root.name, job_kind, services)
    if public_log.is_file():
        paths.append(("log", public_log, {}))
    cleanup = tuple(
        path
        for path in (
            root / "run" / "final_adapter",
            root / "run" / "best_adapter",
            checkpoints,
        )
        if path.exists()
    )
    return _terminal_artifacts(paths, cleanup, services)


def _register_descriptors(db, task_id, descriptors, services):
    for descriptor in descriptors:
        register_artifact(
            db,
            services.output_root,
            task_id=task_id,
            artifact_type=descriptor.artifact_type,
            relative_path=descriptor.relative_path,
            size_bytes=descriptor.size_bytes,
            sha256=descriptor.sha256,
            metadata_json=descriptor.metadata,
        )


def _validated_cpt_adapter(db, task, services):
    if task.cpt_adapter_artifact_id is None:
        return None
    artifact = _active_artifact(db, task.cpt_adapter_artifact_id)
    try:
        source_kind = classify_cpt_adapter(db, artifact, task.model_id)
    except (TrainingTaskConflict, TrainingTaskNotFound):
        raise ExecutionError("CPT adapter artifact is not trusted") from None
    path = _materialize_source_artifact(
        db,
        artifact,
        services,
        Path(services.output_root) / task.id / "inputs" / "cpt-adapter",
        _ADAPTER_REQUIRED,
        expected_size=artifact.size_bytes if source_kind == "uploaded_cpt" else None,
        expected_sha256=artifact.sha256 if source_kind == "uploaded_cpt" else None,
    )
    return path


def _validated_resume_checkpoint(db, task, services):
    if task.resume_checkpoint_artifact_id is None:
        return None, None
    artifact = _active_artifact(db, task.resume_checkpoint_artifact_id)
    source = (db.query(TrainingTask)
        .filter_by(id=task.parent_task_id, deleted_at=None)
        .one_or_none()) if task.parent_task_id is not None else None
    if (artifact.artifact_type != "checkpoint" or task.parent_task_id is None
            or artifact.task_id != task.parent_task_id or source is None
            or source.task_type != task.task_type or source.job_kind != "training"
            or source.state not in {"failed", "cancelled", "interrupted"}):
        raise ExecutionError("resume checkpoint artifact is not trusted")
    profile = _profile_from_metadata(artifact.metadata_json)
    if not _worker_supports_profile(services, profile["profile_name"]):
        raise ExecutionError("resume checkpoint device profile is unavailable")
    path = _materialize_source_artifact(
        db,
        artifact,
        services,
        Path(services.output_root) / task.id / "inputs" / "resume-checkpoint",
        _CHECKPOINT_REQUIRED,
    )
    return path, profile["profile_name"]


def _worker_supports_profile(services: WorkerServices, profile_name: str) -> bool:
    device_type = _PROFILE_FIELDS.get(profile_name, (None,))[0]
    worker_device_type = "cpu" if services.requested_device == "cpu" else "cuda"
    if device_type != worker_device_type:
        return False
    if device_type == "cpu" and services.cpu_profile != "auto_low_memory":
        return services.cpu_profile == profile_name
    return True


def _active_artifact(db, artifact_id):
    artifact = (db.query(TrainingArtifact)
        .filter_by(id=artifact_id, deleted_at=None, deletion_state=None)
        .with_for_update()
        .one_or_none())
    if artifact is None:
        raise ExecutionError("referenced training artifact is unavailable")
    return artifact


def _owned_task(db, task_id, worker_id):
    task = db.query(TrainingTask).filter_by(id=task_id, deleted_at=None).with_for_update().one_or_none()
    if task is None or task.worker_id != worker_id or task.state not in {"preparing_data", "training", "evaluating", "cancelling"}:
        raise ExecutionError("task is not actively claimed by this worker")
    return task


def _lock_execution_graph(db, task_id, worker_id):
    acquire_artifact_registry_lock(db)
    worker = (
        db.query(TrainingWorker)
        .filter_by(id=worker_id)
        .with_for_update()
        .one_or_none()
    )
    current = (
        db.query(TrainingTask)
        .filter_by(id=task_id, deleted_at=None)
        .one_or_none()
    )
    if current is None:
        raise ExecutionError("training task is unavailable")
    source_ids = {
        value
        for value in (current.parent_task_id,)
        if isinstance(value, str) and value
    }
    artifact_ids = {
        value
        for value in (
            current.cpt_adapter_artifact_id,
            current.resume_checkpoint_artifact_id,
        )
        if isinstance(value, str) and value
    }
    if current.job_kind == "evaluation":
        evaluation = (
            db.query(TrainingEvaluation)
            .filter_by(task_id=task_id)
            .one_or_none()
        )
        if evaluation is not None:
            if evaluation.source_sft_task_id is not None:
                source_ids.add(evaluation.source_sft_task_id)
                source_adapter = (
                    db.query(TrainingArtifact)
                    .filter_by(
                        task_id=evaluation.source_sft_task_id,
                        artifact_type="final_adapter",
                        deleted_at=None,
                        deletion_state=None,
                    )
                    .one_or_none()
                )
                if source_adapter is not None:
                    artifact_ids.add(source_adapter.id)
            if evaluation.source_sft_artifact_id is not None:
                artifact_ids.add(evaluation.source_sft_artifact_id)
    if artifact_ids:
        artifact_owners = (
            db.query(TrainingArtifact)
            .filter(TrainingArtifact.id.in_(sorted(artifact_ids)))
            .all()
        )
        source_ids.update(
            artifact.task_id
            for artifact in artifact_owners
            if artifact.task_id is not None
        )
    task_ids = sorted({task_id, *source_ids})
    tasks = (
        db.query(TrainingTask)
        .filter(TrainingTask.id.in_(task_ids))
        .order_by(TrainingTask.id)
        .with_for_update()
        .all()
    )
    tasks_by_id = {task.id: task for task in tasks}
    task = tasks_by_id.get(task_id)
    if (
        worker is None
        or task is None
        or task.worker_id != worker_id
        or task.state
        not in {"preparing_data", "training", "evaluating", "cancelling"}
        or worker.status != "busy"
        or worker.current_task_id != task_id
    ):
        raise ExecutionError("worker does not own the active task")
    evaluation_task_ids = sorted(
        row.id for row in tasks if row.job_kind == "evaluation"
    )
    if evaluation_task_ids:
        (
            db.query(TrainingEvaluation)
            .filter(TrainingEvaluation.task_id.in_(evaluation_task_ids))
            .order_by(TrainingEvaluation.task_id)
            .with_for_update()
            .all()
        )
    artifact_ids = sorted(artifact_ids)
    if artifact_ids:
        (
            db.query(TrainingArtifact)
            .filter(TrainingArtifact.id.in_(artifact_ids))
            .order_by(TrainingArtifact.id)
            .with_for_update()
            .all()
        )
    return task


def _trusted_task_root(output_root, task_id):
    _validate_task_id(task_id)
    root = Path(output_root).resolve(strict=True)
    task_root = root / task_id
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            os.mkdir(task_id, mode=0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        task_fd = os.open(task_id, flags, dir_fd=root_fd)
        try:
            if not stat.S_ISDIR(os.fstat(task_fd).st_mode):
                raise ExecutionError("task root is not a directory")
        finally:
            os.close(task_fd)
    except OSError as exc:
        raise ExecutionError("task root is not safe") from exc
    finally:
        os.close(root_fd)
    return task_root


def _ensure_task_child_directory(task_root, name):
    task_fd = os.open(
        task_root,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        try:
            os.mkdir(name, mode=0o700, dir_fd=task_fd)
        except FileExistsError:
            pass
        child_fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=task_fd,
        )
        try:
            if not stat.S_ISDIR(os.fstat(child_fd).st_mode):
                raise ExecutionError("task output is not a directory")
        finally:
            os.close(child_fd)
    except OSError as exc:
        raise ExecutionError("task output is not safe") from exc
    finally:
        os.close(task_fd)


def _validate_task_id(task_id):
    if (not isinstance(task_id, str) or not task_id or "/" in task_id
            or "\\" in task_id or task_id in {".", ".."}):
        raise ExecutionError("task ID is not safe")


def _validate_base_model(value):
    path = Path(value).resolve(strict=True)
    marker = path / "config.json"
    if (not path.is_dir() or not os.access(path, os.R_OK)
            or not marker.is_file() or not os.access(marker, os.R_OK)):
        raise ExecutionError("local base model is missing its readable config.json marker")
    return path


def _configured_cpu_profile_name(services: WorkerServices) -> str:
    if services.cpu_profile == "auto_low_memory":
        return _CPU_PROFILE_NAMES[0]
    if services.cpu_profile in _CPU_PROFILE_NAMES:
        return services.cpu_profile
    raise ExecutionError("configured CPU training profile is invalid")


def _cpu_memory_preflight(
    model: Path,
    services: WorkerServices,
    required_profile: str | None,
) -> None:
    available = getattr(services.virtual_memory(), "available", None)
    minimum = _minimum_cpu_memory_bytes(model, services, required_profile)
    if (
        isinstance(available, bool)
        or not isinstance(available, (int, float))
        or not math.isfinite(available)
        or available < minimum
    ):
        raise ExecutionError("insufficient free system memory for training")


def _minimum_cpu_memory_bytes(
    model: Path,
    services: WorkerServices,
    required_profile: str | None = None,
) -> int:
    profile_name = required_profile or _configured_cpu_profile_name(services)
    model_bytes = _regular_tree_size(model)
    if profile_name.startswith("cpu_qlora_4bit_"):
        estimate = model_bytes // 2 + 8 * _GIB
    elif profile_name == "cpu_lora_bf16":
        estimate = model_bytes + 8 * _GIB
    else:
        estimate = model_bytes * 2 + 8 * _GIB
    configured_floor = math.ceil(services.min_free_cpu_gb * _GIB)
    return max(24 * _GIB, configured_floor, estimate)


def _regular_tree_size(root: Path) -> int:
    total = 0
    pending = [Path(root)]
    try:
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in sorted(entries, key=lambda value: value.name):
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
    except OSError as exc:
        raise ExecutionError("base model size could not be determined") from exc
    return total


def _typed_gpu_snapshot(value):
    if not isinstance(value, dict) or type(value.get("index")) is not int:
        raise ExecutionError("GPU snapshot is invalid")
    result = {"index": value["index"]}
    for key in ("memory_total_mb", "memory_used_mb", "memory_free_mb", "utilization_percent", "temperature_c"):
        if key in value: result[key] = _finite(value[key], key)
    if "memory_free_mb" not in result: raise ExecutionError("GPU snapshot has no free-memory value")
    name = value.get("name")
    if name is not None:
        if not isinstance(name, str) or _GPU_NAME_PATTERN.fullmatch(name) is None:
            raise ExecutionError("GPU name is invalid")
        result["name"] = name
    for key in ("driver_version", "cuda_version"):
        version = value.get(key)
        if version is not None:
            if not isinstance(version, str) or _GPU_VERSION_PATTERN.fullmatch(version) is None:
                raise ExecutionError(f"GPU {key} is invalid")
            result[key] = version
    return result


def _safe_profile_payload(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != _SAFE_PROFILE_KEYS:
        raise ExecutionError("effective device profile is invalid")
    if any(not isinstance(value.get(key), str) for key in _SAFE_PROFILE_KEYS):
        raise ExecutionError("effective device profile is invalid")
    profile_name = value["profile_name"]
    if _PROFILE_FIELDS.get(profile_name) != (
        value["device_type"],
        value["quantization"],
        value["compute_dtype"],
    ):
        raise ExecutionError("effective device profile is invalid")
    return {key: value[key] for key in (
        "device_type", "profile_name", "quantization", "compute_dtype"
    )}


def _profile_from_metadata(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ExecutionError("effective device profile is invalid")
    try:
        payload = {key: value[key] for key in _SAFE_PROFILE_KEYS}
    except KeyError as exc:
        raise ExecutionError("effective device profile is invalid") from exc
    return _safe_profile_payload(payload)


def _read_effective_profile(root: Path) -> dict[str, str]:
    path = Path(root) / "effective-device-profile.json"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ExecutionError("effective device profile is missing or invalid") from exc
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode) or stat.S_IMODE(file_stat.st_mode) != 0o600:
            raise ExecutionError("effective device profile is missing or invalid")
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            payload = json.load(handle, object_pairs_hook=_unique_json_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ExecutionError("effective device profile is missing or invalid") from exc
    finally:
        if fd >= 0:
            os.close(fd)
    return _safe_profile_payload(payload)


def _effective_profile_for_worker(
    root: Path, services: WorkerServices
) -> dict[str, str]:
    profile = _read_effective_profile(root)
    if not _worker_supports_profile(services, profile["profile_name"]):
        raise ExecutionError("effective device profile violates the worker contract")
    return profile


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _worker_device_snapshot(root: Path, services: WorkerServices) -> dict[str, Any]:
    profile_path = Path(root) / "effective-device-profile.json"
    if profile_path.exists() or profile_path.is_symlink():
        profile = _effective_profile_for_worker(root, services)
    elif services.requested_device == "cpu":
        profile_name = _configured_cpu_profile_name(services)
        device_type, quantization, compute_dtype = _PROFILE_FIELDS[profile_name]
        profile = {
            "device_type": device_type,
            "profile_name": profile_name,
            "quantization": quantization,
            "compute_dtype": compute_dtype,
        }
    else:
        profile = {
            "device_type": "cuda",
            "profile_name": "cuda_qlora_bf16",
            "quantization": "nf4_4bit",
            "compute_dtype": "bf16",
        }
    if profile["device_type"] == "cpu":
        return {
            "device_type": "cpu",
            "profile_name": profile["profile_name"],
            "device": "CPU",
        }
    gpu = _typed_gpu_snapshot(services.gpu_snapshot_provider())
    snapshot = {
        **gpu,
        "device_type": "cuda",
        "profile_name": profile["profile_name"],
    }
    if "name" in gpu:
        snapshot["device"] = gpu["name"]
    return snapshot


def _child_environment(environ, requested_device):
    result = {key: value for key, value in environ.items() if key in _ENV_ALLOWLIST and isinstance(value, str)}
    if requested_device == "cpu":
        for key in tuple(result):
            if key.startswith("CUDA") or key.startswith("NVIDIA"):
                result.pop(key)
    else:
        result["CUDA_VISIBLE_DEVICES"] = "0"
    result["PYTHONUNBUFFERED"] = "1"
    return result


def _load_result(path, root, job_kind, services):
    try: payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise ExecutionError("child result is missing or invalid") from exc
    result_path_key = "final_adapter" if job_kind == "training" else "evaluation_summary"
    expected = {"status", result_path_key} | _SAFE_PROFILE_KEYS
    if (not isinstance(payload, dict) or set(payload) != expected
            or payload.get("status") != "succeeded"
            or not isinstance(payload[result_path_key], str)):
        raise ExecutionError("child result does not match schema")
    profile = _effective_profile_for_worker(root, services)
    try:
        result_profile = _safe_profile_payload(
            {key: payload[key] for key in _SAFE_PROFILE_KEYS}
        )
    except ExecutionError as exc:
        raise ExecutionError("child result does not match schema") from exc
    if result_profile != profile:
        raise ExecutionError("child result device profile does not match publication")
    return payload, _ValidatedProfile(**profile)


def _failed_child_error(path: Path, root: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (isinstance(payload, dict) and set(payload) == {"status", "error"}
            and payload.get("status") == "failed" and isinstance(payload.get("error"), str)):
        return payload["error"]
    return None


def _result_path(result, key, root):
    value = result.get(key)
    if not isinstance(value, str): raise ExecutionError(f"child result has no {key}")
    return _inside_root(root, Path(value))


def _inside_root(root, path):
    root = Path(root).resolve(strict=True); resolved = Path(path).resolve()
    try: resolved.relative_to(root)
    except ValueError as exc: raise ExecutionError("path escapes trusted output root") from exc
    return resolved


def _valid_directory(path, required):
    path = Path(path)
    if not path.is_dir() or path.is_symlink():
        return False
    if not all(
        (path / name).is_file() and not (path / name).is_symlink()
        for name in required
    ):
        return False
    try:
        _json_object(path / "adapter_config.json", "adapter config")
        _validate_safetensors(path / "adapter_model.safetensors")
        if "trainer_state.json" in required:
            _json_object(path / "trainer_state.json", "trainer state")
    except ExecutionError:
        return False
    return True


def _terminal_artifacts(paths, cleanup, services):
    root = Path(services.output_root).resolve(strict=True)
    descriptors = []
    for artifact_type, path, metadata in paths:
        if path is None or not Path(path).exists():
            continue
        resolved = _inside_root(root, Path(path))
        size, digest = _path_metadata(resolved)
        descriptors.append(
            _ArtifactDescriptor(
                artifact_type=artifact_type,
                path=resolved,
                relative_path=resolved.relative_to(root).as_posix(),
                size_bytes=size,
                sha256=digest,
                metadata=dict(metadata),
            )
        )
    return _TerminalArtifacts(tuple(descriptors), tuple(Path(path) for path in cleanup))


def _json_object(path, label):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExecutionError(f"{label} is invalid") from exc
    if not isinstance(payload, dict):
        raise ExecutionError(f"{label} must be a JSON object")
    return payload


def _validate_safetensors(path):
    try:
        size = Path(path).stat().st_size
        with Path(path).open("rb") as handle:
            prefix = handle.read(8)
            if len(prefix) != 8:
                raise ExecutionError("safetensors header is missing")
            header_size = struct.unpack("<Q", prefix)[0]
            if (
                header_size <= 0
                or header_size > size - 8
                or header_size > _MAX_SAFETENSORS_HEADER_BYTES
            ):
                raise ExecutionError("safetensors header size is invalid")
            header = json.loads(
                handle.read(header_size), object_pairs_hook=_unique_json_object
            )
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        struct.error,
        ValueError,
    ) as exc:
        raise ExecutionError("safetensors header is invalid") from exc
    if not isinstance(header, dict) or not header:
        raise ExecutionError("safetensors header must be a nonempty object")
    metadata = header.get("__metadata__")
    if metadata is not None and (
        not isinstance(metadata, dict)
        or any(not isinstance(key, str) or not isinstance(value, str)
               for key, value in metadata.items())
    ):
        raise ExecutionError("safetensors metadata is invalid")
    tensors = {key: value for key, value in header.items() if key != "__metadata__"}
    if not tensors:
        raise ExecutionError("safetensors contains no tensors")
    data_size = size - 8 - header_size
    dtype_sizes = {
        "BOOL": 1, "U8": 1, "I8": 1, "F8_E4M3": 1, "F8_E5M2": 1,
        "I16": 2, "U16": 2, "F16": 2, "BF16": 2,
        "I32": 4, "U32": 4, "F32": 4,
        "I64": 8, "U64": 8, "F64": 8,
    }
    intervals = []
    for name, descriptor in tensors.items():
        if not isinstance(name, str) or not name or not isinstance(descriptor, dict):
            raise ExecutionError("safetensors tensor descriptor is invalid")
        if set(descriptor) != {"dtype", "shape", "data_offsets"}:
            raise ExecutionError("safetensors tensor descriptor is invalid")
        offsets = descriptor["data_offsets"]
        shape = descriptor["shape"]
        if (
            descriptor["dtype"] not in dtype_sizes
            or not isinstance(shape, list)
            or any(type(value) is not int or value < 0 for value in shape)
            or not isinstance(offsets, list)
            or len(offsets) != 2
            or any(type(value) is not int or value < 0 for value in offsets)
            or offsets[0] > offsets[1]
            or offsets[1] > data_size
        ):
            raise ExecutionError("safetensors tensor descriptor is invalid")
        elements = math.prod(shape)
        if offsets[1] - offsets[0] != elements * dtype_sizes[descriptor["dtype"]]:
            raise ExecutionError("safetensors tensor byte range is invalid")
        intervals.append((offsets[0], offsets[1]))
    cursor = 0
    for start, end in sorted(intervals):
        if start != cursor:
            raise ExecutionError("safetensors tensor ranges are not contiguous")
        cursor = end
    if cursor != data_size:
        raise ExecutionError("safetensors data length is invalid")


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _create_deterministic_tar(source, destination):
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    entries = sorted(source.rglob("*"))
    if any(path.is_symlink() for path in entries):
        raise ExecutionError("artifact contains a symlink")
    if any(not (path.is_dir() or path.is_file()) for path in entries):
        raise ExecutionError("artifact contains a non-regular entry")
    files = [path for path in entries if path.is_file()]
    if not files:
        raise ExecutionError("artifact directory is empty")
    with tarfile.open(temporary, "w", format=tarfile.PAX_FORMAT) as archive:
        for path in files:
            relative = path.relative_to(source).as_posix()
            info = archive.gettarinfo(str(path), arcname=relative)
            if not info.isreg():
                raise ExecutionError("artifact contains a non-regular file")
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            info.mode = 0o644
            with path.open("rb") as handle:
                archive.addfile(info, handle)
    os.replace(temporary, destination)


def _extract_adapter_archive(
    archive_path,
    destination,
    required=_ADAPTER_REQUIRED,
    *,
    expected_size=None,
    expected_sha256=None,
):
    archive_path = Path(archive_path)
    destination = Path(destination)
    if destination.exists():
        _remove_path(destination)
    destination.mkdir(parents=True, mode=0o700)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(archive_path, flags)
        with os.fdopen(descriptor, "rb") as archive_file:
            file_stat = os.fstat(archive_file.fileno())
            if not stat.S_ISREG(file_stat.st_mode):
                raise ExecutionError("artifact archive is not a regular file")
            if (expected_size is None) != (expected_sha256 is None):
                raise ExecutionError("artifact archive integrity metadata is invalid")
            if expected_size is not None:
                if file_stat.st_size != expected_size:
                    raise ExecutionError("artifact archive integrity check failed")
                digest = hashlib.sha256()
                while chunk := archive_file.read(_HASH_CHUNK_BYTES):
                    digest.update(chunk)
                if digest.hexdigest() != expected_sha256:
                    raise ExecutionError("artifact archive integrity check failed")
                archive_file.seek(0)
            with tarfile.open(fileobj=archive_file, mode="r:") as archive:
                members = []
                for member in archive:
                    members.append(member)
                    if len(members) > _MAX_ADAPTER_ARCHIVE_MEMBERS:
                        raise ExecutionError("artifact archive contains too many entries")
                if not members:
                    raise ExecutionError("artifact archive is empty")
                expanded_size = 0
                for member in members:
                    member_path = Path(member.name)
                    if (
                        member_path.is_absolute()
                        or ".." in member_path.parts
                        or member.issym()
                        or member.islnk()
                        or not (member.isdir() or member.isreg())
                    ):
                        raise ExecutionError("artifact archive contains an unsafe entry")
                    if member.isreg():
                        if member.size < 0 or member.size > _MAX_ADAPTER_MEMBER_BYTES:
                            raise ExecutionError("artifact archive entry is too large")
                        expanded_size += member.size
                        if expanded_size > _MAX_ADAPTER_EXPANDED_BYTES:
                            raise ExecutionError("artifact archive expands beyond its limit")
                for member in members:
                    member_path = Path(member.name)
                    target = destination.joinpath(*member_path.parts)
                    _inside_root(destination, target)
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        source = archive.extractfile(member)
                        if source is None:
                            raise ExecutionError("artifact archive entry is unreadable")
                        with target.open("xb") as output:
                            shutil.copyfileobj(source, output, _HASH_CHUNK_BYTES)
        if not _valid_directory(destination, required):
            raise ExecutionError("extracted artifact is structurally invalid")
    except ExecutionError:
        _remove_path(destination)
        raise
    except (OSError, tarfile.TarError) as exc:
        _remove_path(destination)
        raise ExecutionError("artifact archive is invalid") from exc
    return destination


def _materialize_source_artifact(
    db,
    artifact,
    services,
    destination,
    required,
    *,
    expected_size=None,
    expected_sha256=None,
):
    destination = _prepare_snapshot_destination(destination, "task-local artifact input")
    path = resolve_artifact_path(db, artifact.id, services.output_root)
    integrity_required = expected_size is not None or expected_sha256 is not None
    if integrity_required and (not path.is_file() or path.is_symlink()):
        raise ExecutionError("uploaded adapter archive is unavailable")
    if path.is_file() and not path.is_symlink():
        return _extract_adapter_archive(
            path,
            destination,
            required,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )
    if not _legacy_source_directory(path, required):
        raise ExecutionError("source training artifact is incomplete")
    return _snapshot_regular_tree(path, destination, required)


def _snapshot_regular_tree(source, destination, required):
    source = Path(source)
    destination = Path(destination)
    entries = sorted(source.rglob("*"))
    if not entries or any(path.is_symlink() for path in entries):
        raise ExecutionError("source training artifact contains a symlink")
    if any(not (path.is_dir() or path.is_file()) for path in entries):
        raise ExecutionError("source training artifact contains a non-regular entry")
    if destination.exists():
        _remove_path(destination)
    destination.mkdir(parents=True, mode=0o700)
    try:
        for path in entries:
            target = destination / path.relative_to(source)
            if path.is_dir():
                target.mkdir(mode=0o700, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                _copy_regular_file(path, target, "source training artifact")
    except Exception:
        _remove_path(destination)
        raise
    if not _valid_directory(destination, required):
        _remove_path(destination)
        raise ExecutionError("task-local source artifact is structurally invalid")
    return destination.resolve(strict=True)


def _snapshot_regular_file(source, destination, label):
    source = Path(source)
    destination = _prepare_snapshot_destination(destination, label)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        _copy_regular_file(source, temporary, label)
        if destination.is_symlink():
            raise ExecutionError(f"{label} destination is a symlink")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination.resolve(strict=True)


def _prepare_snapshot_destination(destination, label):
    destination = Path(destination)
    if not destination.is_absolute():
        raise ExecutionError(f"{label} destination must be absolute")
    _reject_symlink_components(destination, label)
    try:
        destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise ExecutionError(f"{label} destination is unavailable") from exc
    _reject_symlink_components(destination, label)
    return destination


def _reject_symlink_components(path, label):
    path = Path(path)
    for component in reversed((path, *path.parents)):
        try:
            if component.is_symlink():
                raise ExecutionError(f"{label} destination contains a symlink")
        except OSError as exc:
            raise ExecutionError(f"{label} destination is unavailable") from exc


def _copy_regular_file(source, destination, label):
    source_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    destination_flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        source_fd = os.open(source, source_flags)
        try:
            destination_fd = os.open(destination, destination_flags, 0o600)
            with os.fdopen(source_fd, "rb") as input_handle, os.fdopen(destination_fd, "wb") as output_handle:
                source_fd = -1
                shutil.copyfileobj(input_handle, output_handle, _HASH_CHUNK_BYTES)
                output_handle.flush()
                os.fsync(output_handle.fileno())
        finally:
            if source_fd >= 0:
                os.close(source_fd)
    except OSError as exc:
        raise ExecutionError(f"{label} is not a regular file") from exc


def _sha256_regular_file(path, label):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    digest = hashlib.sha256()
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            while chunk := handle.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
    except OSError as exc:
        raise ExecutionError(f"{label} is not a regular file") from exc
    return digest.hexdigest()


def _legacy_source_directory(path, required):
    path = Path(path)
    if not path.is_dir() or path.is_symlink():
        return False
    if not all(
        (path / name).is_file() and not (path / name).is_symlink()
        for name in required
    ):
        return False
    try:
        _json_object(path / "adapter_config.json", "adapter config")
        if "trainer_state.json" in required:
            _json_object(path / "trainer_state.json", "trainer state")
    except ExecutionError:
        return False
    return True


def _sanitize_public_value(value, services, task_root, key=None):
    if isinstance(key, str) and re.search(
        r"(?i)(api[_-]?key|token|secret|password|credential)", key
    ):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(child_key): _sanitize_public_value(
                child_value, services, task_root, str(child_key)
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [
            _sanitize_public_value(item, services, task_root, key)
            for item in value
        ]
    if isinstance(value, str):
        roots = {
            str(Path(services.output_root).resolve()),
            str(Path(services.base_model_path).resolve()),
            str(Path(task_root).resolve()),
        }
        text = value
        for root in sorted(roots, key=len, reverse=True):
            text = text.replace(root, "[REDACTED_PATH]")
        if text.startswith(("/", "../", "./")):
            return "[REDACTED_PATH]"
        text = re.sub(
            r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@",
            r"\1[REDACTED]@",
            text,
        )
        text = re.sub(r"(?i)\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED_KEY]", text)
        return text
    return value


def _cleanup_journal_paths(output_root, task_id, paths):
    if not paths:
        return ()
    _validate_task_id(task_id)
    task_root = Path(output_root).resolve(strict=True) / task_id
    relative_paths = []
    for value in paths:
        try:
            relative = Path(value).relative_to(task_root)
        except (TypeError, ValueError) as exc:
            raise ExecutionError("cleanup path is outside the task root") from exc
        if (not relative.parts or any(part in {"", ".", ".."} for part in relative.parts)):
            raise ExecutionError("cleanup path is invalid")
        relative_paths.append(relative.as_posix())
    return tuple(sorted(set(relative_paths)))


def _retry_task_cleanup_safely(task_id, services):
    try:
        with services.session_factory() as db:
            with db.begin():
                reconcile_task_cleanups(db, services.output_root, task_id=task_id)
    except Exception:
        # The terminal transaction already durably recorded the pending journal.
        return


def reconcile_task_cleanups(db, output_root, *, task_id=None):
    query = db.query(TrainingTask).filter(TrainingTask.cleanup_state == "pending")
    if task_id is not None:
        query = query.filter(TrainingTask.id == task_id)
    tasks = query.order_by(TrainingTask.id).with_for_update().all()
    for task in tasks:
        task.cleanup_updated_at = datetime.utcnow()
        try:
            task_root = _trusted_cleanup_task_root(output_root, task.id)
            cleanup_paths = _validated_cleanup_paths(task.cleanup_paths)
            if task_root is not None:
                for relative_path in cleanup_paths:
                    _remove_cleanup_entry(task_root, relative_path)
        except (ExecutionError, OSError):
            continue
        task.cleanup_state = "cleaned"


def _trusted_cleanup_task_root(output_root, task_id):
    _validate_task_id(task_id)
    root = Path(output_root).resolve(strict=True)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        try:
            task_fd = os.open(task_id, flags, dir_fd=root_fd)
        except FileNotFoundError:
            return None
        try:
            if not stat.S_ISDIR(os.fstat(task_fd).st_mode):
                raise ExecutionError("cleanup task root is not a directory")
        finally:
            os.close(task_fd)
    except OSError as exc:
        raise ExecutionError("cleanup task root is not safe") from exc
    finally:
        os.close(root_fd)
    return root / task_id


def _validated_cleanup_paths(value):
    if not isinstance(value, list) or not value:
        raise ExecutionError("cleanup journal paths are invalid")
    paths = []
    for item in value:
        if not isinstance(item, str):
            raise ExecutionError("cleanup journal path is invalid")
        path = Path(item)
        if path.is_absolute() or not path.parts or any(
            part in {"", ".", ".."} for part in path.parts
        ):
            raise ExecutionError("cleanup journal path is invalid")
        paths.append(path)
    return paths


def _remove_cleanup_entry(task_root, relative_path):
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    root_fd = os.open(task_root, flags)
    parent_fd = os.dup(root_fd)
    os.close(root_fd)
    try:
        for component in relative_path.parts[:-1]:
            next_fd = os.open(component, flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
        try:
            _remove_entry_at(parent_fd, relative_path.parts[-1])
        except FileNotFoundError:
            return
    finally:
        os.close(parent_fd)


def _remove_entry_at(parent_fd, name):
    item_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(item_stat.st_mode):
        os.unlink(name, dir_fd=parent_fd)
        return
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(name, flags, dir_fd=parent_fd)
    try:
        for child_name in os.listdir(directory_fd):
            _remove_entry_at(directory_fd, child_name)
    finally:
        os.close(directory_fd)
    os.rmdir(name, dir_fd=parent_fd)


def _remove_path(path):
    path = Path(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _atomic_json(path, payload):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def _finite(value, key):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ExecutionError(f"GPU {key} is invalid")
    return float(value)


def _nullable_finite(value, key):
    return None if value is None else _finite(value, key)


def _nullable_metric_finite(value, key):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ExecutionError(f"child metric {key} is invalid")
    return float(value)


def _safe_error(exc):
    text = str(exc).strip() or exc.__class__.__name__
    return text[:2000]
