from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.training_task import TrainingEvaluation, TrainingTask, TrainingWorker


_ACTIVE_STATES = {"preparing_data", "training", "evaluating", "cancelling"}
_HEARTBEAT_STATES_BY_JOB_KIND = {
    "training": {"preparing_data", "training", "cancelling"},
    "evaluation": {"preparing_data", "evaluating", "cancelling"},
}
_GPU_NUMERIC_FIELDS = {
    "memory_total_mb",
    "memory_used_mb",
    "memory_free_mb",
    "utilization_percent",
    "temperature_c",
}
_GPU_NAME_PATTERN = re.compile(r"NVIDIA [A-Za-z0-9][A-Za-z0-9 .()/_+\-]{0,120}")
_GPU_VERSION_PATTERN = re.compile(r"\d+(?:\.\d+){1,3}")
_PROFILE_DEVICE_TYPES = {
    "cuda_qlora_bf16": "cuda",
    "cpu_qlora_4bit_bf16": "cpu",
    "cpu_qlora_4bit_fp32": "cpu",
    "cpu_lora_bf16": "cpu",
    "cpu_lora_fp32": "cpu",
}


class TrainingWorkerOwnershipError(RuntimeError):
    pass


class TrainingWorkerStateError(RuntimeError):
    pass


def next_queued_task_statement():
    """Build the PostgreSQL queue-locking statement used by ``claim_next_task``."""
    return (
        select(TrainingTask)
        .where(
            TrainingTask.state == "queued",
            TrainingTask.deleted_at.is_(None),
        )
        .order_by(TrainingTask.queued_at, TrainingTask.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def active_owned_task_statement(worker_id: str):
    """Lock all active work owned by one worker in a deterministic order."""
    return (
        select(TrainingTask)
        .where(
            TrainingTask.worker_id == worker_id,
            TrainingTask.state.in_(_ACTIVE_STATES),
            TrainingTask.deleted_at.is_(None),
        )
        .order_by(TrainingTask.queued_at, TrainingTask.id)
        .with_for_update()
    )


def recover_worker_ownership(
    db: Session, worker_id: str, stale_before: datetime
) -> tuple[TrainingWorker, TrainingTask | None]:
    """Lock worker, then every active task, before any evaluation row is locked."""
    worker = _locked_or_create_worker(db, worker_id)
    tasks = _locked_active_tasks(db, worker)
    stale_tasks, fresh_tasks = _partition_owned_tasks(tasks, stale_before)
    _interrupt_locked_stale_tasks(db, worker, stale_tasks)
    fresh_task = fresh_tasks[0] if fresh_tasks else None
    return worker, fresh_task


def register_worker(
    db: Session,
    worker_id: str,
    *,
    status: str,
    current_task_id: str | None = None,
    model_status: str | None = None,
    gpu_snapshot: dict[str, Any] | None = None,
) -> TrainingWorker:
    """Create or refresh the configured singleton worker without committing."""
    now = datetime.utcnow()
    worker = _locked_or_create_worker(db, worker_id)
    update_locked_worker(
        worker,
        status=status,
        current_task_id=current_task_id,
        model_status=model_status,
        gpu_snapshot=gpu_snapshot,
        now=now,
    )
    db.flush()
    return worker


def update_locked_worker(
    worker: TrainingWorker,
    *,
    status: str,
    current_task_id: str | None,
    model_status: str | None,
    gpu_snapshot: dict[str, Any] | None,
    now: datetime,
) -> None:
    """Update a Worker row already locked by the caller without another query."""
    worker.status = status
    worker.current_task_id = current_task_id
    if model_status is not None:
        worker.model_status = model_status
    if gpu_snapshot is not None:
        worker.gpu_snapshot = _safe_gpu_snapshot(gpu_snapshot)
    worker.last_heartbeat_at = now
    worker.updated_at = now


def claim_next_task(db: Session, worker_id: str) -> TrainingTask | None:
    """Lock and claim the oldest queued training or evaluation task.

    The caller owns the transaction.  This function deliberately flushes ORM
    changes but never commits or rolls back the session.
    """
    worker = register_worker(db, worker_id, status="idle")
    task = db.execute(next_queued_task_statement()).scalar_one_or_none()
    if task is None:
        return None

    now = datetime.utcnow()
    task.state = "preparing_data"
    task.worker_id = worker_id
    task.started_at = task.started_at or now
    task.heartbeat_at = now
    worker.status = "busy"
    worker.current_task_id = task.id
    worker.last_heartbeat_at = now
    worker.updated_at = now
    if task.job_kind == "evaluation":
        _synchronize_evaluation(db, task, state="preparing_data")
    db.flush()
    return task


def heartbeat(
    db: Session,
    worker_id: str,
    task_id: str,
    gpu_snapshot: dict[str, Any],
) -> TrainingTask:
    """Refresh task and worker liveness after validating exclusive ownership."""
    worker = _locked_worker(db, worker_id)
    task = _locked_task(db, task_id)
    if (
        worker is None
        or task is None
        or task.worker_id != worker_id
        or worker.current_task_id != task_id
        or worker.status != "busy"
    ):
        raise TrainingWorkerOwnershipError("busy worker/task ownership does not permit heartbeat")
    if task.state not in _HEARTBEAT_STATES_BY_JOB_KIND.get(task.job_kind, set()):
        raise TrainingWorkerStateError(f"task in state {task.state} cannot be heartbeated")

    now = datetime.utcnow()
    task.started_at = task.started_at or now
    task.heartbeat_at = now
    worker.last_heartbeat_at = now
    worker.updated_at = now
    worker.gpu_snapshot = _safe_gpu_snapshot(gpu_snapshot)
    if task.job_kind == "evaluation":
        _synchronize_evaluation(db, task, state=task.state)
    db.flush()
    return task


def mark_stale_tasks_interrupted(
    db: Session,
    worker_id: str,
    stale_before: datetime,
) -> list[TrainingTask]:
    """Interrupt stale active work assigned to this worker; never requeue it."""
    worker = _locked_or_create_worker(db, worker_id)
    tasks = _locked_active_tasks(db, worker)
    stale_tasks, _fresh_tasks = _partition_owned_tasks(tasks, stale_before)
    _interrupt_locked_stale_tasks(db, worker, stale_tasks)
    return stale_tasks


def _interrupt_locked_stale_tasks(
    db: Session, worker: TrainingWorker, stale_tasks: list[TrainingTask]
) -> None:
    now = datetime.utcnow()
    for task in stale_tasks:
        task.state = "interrupted"
        task.completed_at = now
        task.error_message = f"worker {worker.id} interrupted after stale heartbeat"

    # Task locks are held for the complete active set before evaluation rows lock.
    for task in stale_tasks:
        if task.job_kind == "evaluation":
            _synchronize_evaluation(
                db,
                task,
                state="interrupted",
                error_message=task.error_message,
            )

    if worker.current_task_id in {task.id for task in stale_tasks}:
        worker.status = "idle"
        worker.current_task_id = None
        worker.last_heartbeat_at = now
        worker.updated_at = now
    db.flush()


def set_worker_offline(db: Session, worker_id: str) -> TrainingWorker:
    return register_worker(db, worker_id, status="offline")


def _locked_worker(db: Session, worker_id: str) -> TrainingWorker | None:
    return db.execute(
        select(TrainingWorker).where(TrainingWorker.id == worker_id).with_for_update()
    ).scalar_one_or_none()


def _locked_or_create_worker(db: Session, worker_id: str) -> TrainingWorker:
    worker = _locked_worker(db, worker_id)
    if worker is None:
        worker = TrainingWorker(id=worker_id)
        db.add(worker)
        db.flush()
    return worker


def _locked_active_tasks(db: Session, worker: TrainingWorker) -> list[TrainingTask]:
    return db.execute(active_owned_task_statement(worker.id)).scalars().all()


def _partition_owned_tasks(
    tasks: list[TrainingTask], stale_before: datetime
) -> tuple[list[TrainingTask], list[TrainingTask]]:
    stale_tasks = [
        task
        for task in tasks
        if task.heartbeat_at is None or task.heartbeat_at < stale_before
    ]
    fresh_tasks = [
        task
        for task in tasks
        if task.heartbeat_at is not None and task.heartbeat_at >= stale_before
    ]
    return stale_tasks, fresh_tasks


def _locked_task(db: Session, task_id: str) -> TrainingTask | None:
    return db.execute(
        select(TrainingTask)
        .where(TrainingTask.id == task_id, TrainingTask.deleted_at.is_(None))
        .with_for_update()
    ).scalar_one_or_none()


def _synchronize_evaluation(
    db: Session,
    task: TrainingTask,
    *,
    state: str,
    error_message: str | None = None,
) -> None:
    evaluation = db.execute(
        select(TrainingEvaluation)
        .where(TrainingEvaluation.task_id == task.id)
        .with_for_update()
    ).scalar_one_or_none()
    if evaluation is None:
        raise TrainingWorkerStateError("evaluation task has no evaluation record")
    evaluation.status = state
    if state in {"preparing_data", "evaluating"} and evaluation.started_at is None:
        evaluation.started_at = task.started_at or datetime.utcnow()
    if state in {"cancelled", "succeeded", "failed", "interrupted"}:
        evaluation.completed_at = task.completed_at or datetime.utcnow()
    if error_message is not None:
        evaluation.error_message = error_message


def _safe_gpu_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    has_device_fields = any(
        key in snapshot for key in ("device_type", "profile_name", "device")
    )
    device_type = snapshot.get("device_type")
    profile_name = snapshot.get("profile_name")
    if has_device_fields:
        if (
            device_type not in {"cpu", "cuda"}
            or not isinstance(profile_name, str)
            or _PROFILE_DEVICE_TYPES.get(profile_name) != device_type
        ):
            return {}
        if device_type == "cpu":
            if snapshot.get("device") != "CPU":
                return {}
            return {
                "device_type": "cpu",
                "profile_name": profile_name,
                "device": "CPU",
            }

    safe: dict[str, Any] = {}
    index = snapshot.get("index")
    if type(index) is int and index >= 0:
        safe["index"] = index
    name = snapshot.get("name")
    if isinstance(name, str) and _GPU_NAME_PATTERN.fullmatch(name):
        safe["name"] = name
    for field in _GPU_NUMERIC_FIELDS:
        value = snapshot.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            safe[field] = value
    for field in ("driver_version", "cuda_version"):
        value = snapshot.get(field)
        if isinstance(value, str) and _GPU_VERSION_PATTERN.fullmatch(value):
            safe[field] = value
    if has_device_fields:
        device = snapshot.get("device")
        if not isinstance(device, str) or _GPU_NAME_PATTERN.fullmatch(device) is None:
            return {}
        safe.update({
            "device_type": "cuda",
            "profile_name": profile_name,
            "device": device,
        })
    return safe
