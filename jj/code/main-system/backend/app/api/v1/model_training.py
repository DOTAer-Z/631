from __future__ import annotations

from collections import defaultdict
import math
import os
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from sqlalchemy import func, or_, select, true
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import settings
from app.training_contracts import EVALUATION_SCORE_KEYS
from app.models.training_data import TrainingTest, TrainingTestLog, TrainingTestVersion
from app.models.training_task import (
    TrainingArtifact,
    TrainingEvaluation,
    TrainingMetric,
    TrainingTask,
    TrainingTaskTest,
    TrainingWorker,
)
from app.schemas.model_training import (
    Completeness,
    TrainingArtifactListOut,
    TrainingArtifactOut,
    TrainingDeleteOut,
    TrainingEvaluationOut,
    TrainingLabelSummary,
    TrainingLogsOut,
    TrainingMetricOut,
    TrainingTaskCreate,
    TrainingTaskDetailOut,
    TrainingTaskListOut,
    TrainingTaskOut,
    TrainingTaskRetryRequest,
    TrainingTimelineEvent,
    TrainingWorkerOut,
    TrainingSplitCounts,
    TrainingSplitPreviewOut,
    TrainingSplitPreviewRequest,
    TrainingSplitStratum,
    TrainingTestCatalogItem,
    TrainingTestCatalogOut,
)
from app.services.training_artifact_service import (
    StagedArtifactDeletion,
    TrainingArtifactConflict,
    TrainingArtifactNotFound,
    mark_artifact_cleaned,
    mark_artifact_cleanup_pending,
    mark_artifact_recovery_required,
    open_artifact_file,
    open_relative_regular_file,
    soft_delete_task,
    stage_artifact_deletion,
)
from app.services.training_split_service import (
    SplitRatios,
    SplitSample,
    build_stratified_split,
    sample_group_key,
)
from app.services.training_task_service import (
    TrainingTaskConflict,
    TrainingTaskNotFound,
    TrainingTaskValidationError,
    cancel_task,
    create_evaluation,
    create_task,
    retry_task,
)


router = APIRouter(prefix="/model-training")
_TERMINAL_TASK_STATES = {"cancelled", "succeeded", "failed", "interrupted"}


@router.get("/tests", response_model=TrainingTestCatalogOut)
def list_training_tests(
    search: str | None = Query(default=None, max_length=256),
    sample_class: str | None = Query(default=None, max_length=64),
    fault_type: str | None = Query(default=None, max_length=256),
    import_id: str | None = Query(default=None, max_length=64),
    completeness: Completeness | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> TrainingTestCatalogOut:
    total_log_bytes = (
        select(func.coalesce(func.sum(TrainingTestLog.byte_count), 0))
        .where(TrainingTestLog.test_version_id == TrainingTestVersion.id)
        .correlate(TrainingTestVersion)
        .scalar_subquery()
    )
    query = (
        db.query(TrainingTest, TrainingTestVersion, total_log_bytes.label("total_log_bytes"))
        .join(TrainingTestVersion, TrainingTest.latest_version_id == TrainingTestVersion.id)
    )
    if search and search.strip():
        pattern = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(TrainingTest.test_name).like(pattern),
                func.lower(TrainingTest.platform).like(pattern),
            )
        )
    if sample_class is not None:
        query = query.filter(TrainingTestVersion.sample_class == sample_class)
    if fault_type is not None:
        query = query.filter(TrainingTestVersion.fault_type == fault_type)
    if import_id is not None:
        query = query.filter(TrainingTestVersion.import_id == import_id)
    if completeness is not None:
        query = query.filter(TrainingTestVersion.completeness == completeness)

    total = query.order_by(None).count()
    rows = (
        query.order_by(TrainingTest.test_name, TrainingTest.platform, TrainingTestVersion.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return TrainingTestCatalogOut(
        items=[_catalog_item(test, version, total_bytes) for test, version, total_bytes in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/splits/preview", response_model=TrainingSplitPreviewOut)
def preview_training_split(
    payload: TrainingSplitPreviewRequest,
    db: Session = Depends(get_db),
) -> TrainingSplitPreviewOut:
    versions = (
        db.query(TrainingTestVersion, TrainingTest)
        .join(TrainingTest, TrainingTestVersion.test_id == TrainingTest.id)
        .filter(TrainingTestVersion.id.in_(payload.test_version_ids))
        .all()
    )
    versions_by_id = {version.id: (version, test) for version, test in versions}
    unknown_ids = sorted(set(payload.test_version_ids) - set(versions_by_id))
    if unknown_ids:
        raise HTTPException(status_code=422, detail=f"unknown test version IDs: {unknown_ids}")

    samples: list[SplitSample] = []
    for version_id in payload.test_version_ids:
        version, test = versions_by_id[version_id]
        if version.completeness != "complete":
            raise HTTPException(status_code=422, detail=f"test version {version_id} is incomplete")
        if test.latest_version_id != version_id:
            raise HTTPException(status_code=422, detail=f"test version {version_id} is not latest")
        samples.append(
            SplitSample(
                version_id=version.id,
                test_name=test.test_name,
                platform=test.platform,
                sample_class=version.sample_class,
                domain=version.domain,
                fault_type=version.fault_type,
            )
        )

    split = build_stratified_split(
        samples,
        SplitRatios(payload.train_ratio, payload.validation_ratio, payload.test_ratio),
        payload.seed,
    )
    return TrainingSplitPreviewOut(
        train_ids=split["train"],
        validation_ids=split["validation"],
        test_ids=split["test"],
        counts=TrainingSplitCounts(
            train=len(split["train"]),
            validation=len(split["validation"]),
            test=len(split["test"]),
        ),
        strata=_strata(samples, split),
    )


def _catalog_item(
    test: TrainingTest, version: TrainingTestVersion, total_log_bytes: int
) -> TrainingTestCatalogItem:
    return TrainingTestCatalogItem(
        test_id=test.id,
        test_name=test.test_name,
        platform=test.platform,
        version_id=version.id,
        version_number=version.version_number,
        label_summary=TrainingLabelSummary(
            sample_class=version.sample_class,
            domain=version.domain,
            fault_type=version.fault_type,
        ),
        round_1_parse_status=version.round_1_parse_status,
        round_2_parse_status=version.round_2_parse_status,
        total_training_log_bytes=int(total_log_bytes or 0),
        completeness=version.completeness,
        missing_files=list(version.missing_files or []),
        import_id=version.import_id,
        created_at=version.created_at,
    )


def _strata(samples: list[SplitSample], split: dict[str, list[int]]) -> list[TrainingSplitStratum]:
    split_by_version_id = {
        version_id: split_name
        for split_name, version_ids in split.items()
        for version_id in version_ids
    }
    counts_by_group: dict[tuple[str, str, str], dict[str, int]] = defaultdict(
        lambda: {"train": 0, "validation": 0, "test": 0}
    )
    for sample in samples:
        counts_by_group[sample_group_key(sample)][split_by_version_id[sample.version_id]] += 1
    return [
        TrainingSplitStratum(
            sample_class=key[0],
            domain=key[1],
            fault_type=key[2],
            counts=TrainingSplitCounts(**counts_by_group[key]),
        )
        for key in sorted(counts_by_group)
    ]


_PUBLIC_QLORA_KEYS = {
    "load_in_4bit",
    "bnb_4bit_quant_type",
    "bnb_4bit_compute_dtype",
    "bnb_4bit_use_double_quant",
    "lora_r",
    "lora_alpha",
    "lora_dropout",
}
_PUBLIC_TRAINING_KEYS = {
    "max_seq_length",
    "learning_rate",
    "num_train_epochs",
    "gradient_accumulation_steps",
    "per_device_train_batch_size",
    "per_device_eval_batch_size",
    "warmup_ratio",
    "lr_scheduler_type",
    "eval_strategy",
    "logging_steps",
    "save_steps",
    "eval_steps",
    "save_total_limit",
    "bf16",
    "report_to",
}
_PUBLIC_EVALUATION_KEYS = {
    "json_valid",
    "has_error_accuracy",
    "fault_type_status_accuracy",
    "known_fault_type_accuracy",
    "new_candidate_detection",
    "evidence_recall",
    "affected_component_match",
    "affected_function_match",
    "repair_suggestion_presence",
    "root_cause_keyword_overlap",
    "accuracy",
    "precision",
    "recall",
    "f1",
    "macro_f1",
    "weighted_f1",
    "loss",
    "support",
    "total",
    "correct",
    "incorrect",
    "open_set_accuracy",
    "unknown_recall",
    "per_class",
    "confusion_matrix",
}
_PUBLIC_QLORA_BOOL_KEYS = {"load_in_4bit", "bnb_4bit_use_double_quant"}
_PUBLIC_QLORA_NUMERIC_KEYS = {"lora_r", "lora_alpha", "lora_dropout"}
_PUBLIC_QLORA_ENUMS = {
    "bnb_4bit_quant_type": {"nf4"},
    "bnb_4bit_compute_dtype": {"bfloat16"},
}
_PUBLIC_TRAINING_BOOL_KEYS = {"bf16"}
_PUBLIC_TRAINING_NUMERIC_KEYS = {
    "max_seq_length",
    "learning_rate",
    "num_train_epochs",
    "gradient_accumulation_steps",
    "per_device_train_batch_size",
    "per_device_eval_batch_size",
    "warmup_ratio",
    "logging_steps",
    "save_steps",
    "eval_steps",
    "save_total_limit",
}
_PUBLIC_TRAINING_ENUMS = {
    "lr_scheduler_type": {"cosine"},
    "eval_strategy": {"no", "steps"},
    "report_to": {"none"},
}
_PUBLIC_ARTIFACT_FORMATS = {"safetensors", "pytorch_bin", "json", "jsonl", "text"}
_PUBLIC_ARTIFACT_FRAMEWORKS = {"peft", "transformers", "pytorch"}
_PUBLIC_ARTIFACT_BASE_MODELS = {"qwen-qwen3.5-9b", "Qwen/Qwen3.5-9B"}
_PUBLIC_GPU_KEYS = {
    "index",
    "name",
    "memory_total_mb",
    "memory_used_mb",
    "utilization_percent",
    "temperature_c",
}
_PUBLIC_WORKER_STATUSES = {"offline", "idle", "busy", "starting", "error"}
_PUBLIC_MODEL_STATUSES = {"ready", "loading", "unavailable", "error"}
_PROFILE_DEVICE_TYPES = {
    "cuda_qlora_bf16": "cuda",
    "cpu_qlora_4bit_bf16": "cpu",
    "cpu_qlora_4bit_fp32": "cpu",
    "cpu_lora_bf16": "cpu",
    "cpu_lora_fp32": "cpu",
}
_PUBLIC_PROFILE_FIELDS = {
    "cuda_qlora_bf16": ("cuda", "nf4_4bit", "bf16"),
    "cpu_qlora_4bit_bf16": ("cpu", "nf4_4bit", "bf16"),
    "cpu_qlora_4bit_fp32": ("cpu", "nf4_4bit", "fp32"),
    "cpu_lora_bf16": ("cpu", "none", "bf16"),
    "cpu_lora_fp32": ("cpu", "none", "fp32"),
}
_ARTIFACT_DISPLAY_NAMES = {
    "final_adapter": "Final adapter",
    "checkpoint": "Checkpoint",
    "config": "Configuration",
    "dataset": "Dataset",
    "log": "Training log",
    "split": "Dataset split",
    "tokenizer": "Tokenizer",
}
_NVIDIA_GPU_NAME_RE = re.compile(
    r"NVIDIA [A-Za-z0-9][A-Za-z0-9 .()/_+\-]{0,120}"
)


@router.post("/tasks", response_model=TrainingTaskOut)
def create_training_task(payload: TrainingTaskCreate, db: Session = Depends(get_db)) -> TrainingTaskOut:
    try:
        task = create_task(db, payload)
        db.commit()
        return _task_header_out(db, task.id)
    except TrainingTaskNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TrainingTaskConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (TrainingTaskValidationError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="unable to create training task") from exc


@router.get("/tasks", response_model=TrainingTaskListOut)
def list_training_tasks(
    task_type: str | None = Query(default=None, pattern="^(cpt|sft)$"),
    state: str | None = Query(default=None, max_length=32),
    job_kind: str | None = Query(default=None, pattern="^(training|evaluation)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> TrainingTaskListOut:
    rows = db.execute(
        _task_page_statement(
            task_type=task_type,
            state=state,
            job_kind=job_kind,
            page=page,
            page_size=page_size,
        )
    ).all()
    return TrainingTaskListOut(
        items=[
            _task_out_values(task, metric, queue_position)
            for task, _, queue_position, metric in rows
            if task is not None
        ],
        total=int(rows[0].total) if rows else 0,
        page=page,
        page_size=page_size,
    )


@router.get("/tasks/{task_id}", response_model=TrainingTaskDetailOut)
def get_training_task(task_id: str, db: Session = Depends(get_db)) -> TrainingTaskDetailOut:
    task, latest_metric, queue_position = _task_header(db, task_id)
    metrics = (
        db.query(TrainingMetric)
        .filter(TrainingMetric.task_id == task.id)
        .order_by(TrainingMetric.created_at, TrainingMetric.id)
        .all()
    )
    split_ids = {"train": [], "validation": [], "test": []}
    for row in (
        db.query(TrainingTaskTest)
        .filter(TrainingTaskTest.task_id == task.id)
        .order_by(TrainingTaskTest.split_name, TrainingTaskTest.test_version_id)
        .all()
    ):
        split_ids[row.split_name].append(row.test_version_id)
    evaluation = db.query(TrainingEvaluation).filter(TrainingEvaluation.task_id == task.id).first()
    return TrainingTaskDetailOut(
        **_task_out_values(task, latest_metric, queue_position).model_dump(),
        config=_public_config(task.config_snapshot),
        split_ids=split_ids,
        split_counts=TrainingSplitCounts(**{name: len(ids) for name, ids in split_ids.items()}),
        timeline=_timeline(task),
        metrics=[_metric_out(metric) for metric in metrics],
        artifacts=[_artifact_out(artifact) for artifact in _task_artifacts(db, task.id)],
        evaluation=(
            TrainingEvaluationOut(
                status=evaluation.status,
                summary=_public_evaluation_summary(evaluation.summary),
                started_at=evaluation.started_at,
                completed_at=evaluation.completed_at,
            )
            if evaluation is not None
            else None
        ),
    )


@router.post("/tasks/{task_id}/cancel", response_model=TrainingTaskOut)
def cancel_training_task(task_id: str, db: Session = Depends(get_db)) -> TrainingTaskOut:
    try:
        task = cancel_task(db, task_id)
        db.commit()
        return _task_header_out(db, task.id)
    except TrainingTaskNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TrainingTaskConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="unable to cancel training task") from exc


@router.post("/tasks/{task_id}/retry", response_model=TrainingTaskOut)
def retry_training_task(
    task_id: str,
    payload: TrainingTaskRetryRequest | None = None,
    db: Session = Depends(get_db),
) -> TrainingTaskOut:
    try:
        task = retry_task(
            db,
            task_id,
            resume_checkpoint_artifact_id=(payload.resume_checkpoint_artifact_id if payload else None),
        )
        db.commit()
        return _task_header_out(db, task.id)
    except TrainingTaskNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TrainingTaskConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="unable to retry training task") from exc


@router.post("/tasks/{task_id}/evaluate", response_model=TrainingTaskOut)
def evaluate_training_task(task_id: str, db: Session = Depends(get_db)) -> TrainingTaskOut:
    try:
        task = create_evaluation(db, task_id)
        db.commit()
        return _task_header_out(db, task.id)
    except TrainingTaskNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TrainingTaskConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="unable to create training evaluation") from exc


@router.get("/tasks/{task_id}/logs", response_model=TrainingLogsOut)
def get_training_task_logs(
    task_id: str,
    after_line: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> TrainingLogsOut:
    task = _task_or_404(db, task_id)
    public_log_name = "evaluation.log" if task.job_kind == "evaluation" else "training.log"
    log_artifact = (
        db.query(TrainingArtifact)
        .filter(
            TrainingArtifact.task_id == task_id,
            TrainingArtifact.artifact_type == "log",
            TrainingArtifact.relative_path == f"{task_id}/public/{public_log_name}",
            TrainingArtifact.deleted_at.is_(None),
            TrainingArtifact.deletion_state.is_(None),
        )
        .order_by(TrainingArtifact.created_at, TrainingArtifact.id)
        .first()
    )
    if log_artifact is None:
        if task.state in _TERMINAL_TASK_STATES:
            return TrainingLogsOut(
                lines=[],
                next_after_line=after_line,
                has_more=False,
            )
        try:
            opened = open_relative_regular_file(
                settings.TRAINING_OUTPUT_ROOT,
                f"{task.id}/public/{public_log_name}",
            )
        except TrainingArtifactNotFound:
            return TrainingLogsOut(
                lines=[],
                next_after_line=after_line,
                has_more=False,
            )
        except TrainingArtifactConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    else:
        try:
            opened = open_artifact_file(
                db, log_artifact.id, settings.TRAINING_OUTPUT_ROOT
            )
        except TrainingArtifactNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TrainingArtifactConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        log_file = os.fdopen(opened.fd, "r", encoding="utf-8", errors="replace")
        opened.fd = -1
    except OSError as exc:
        opened.close()
        raise HTTPException(status_code=409, detail="training task log is not available") from exc
    with log_file:
        for _ in range(after_line):
            if not log_file.readline():
                break
        batch = []
        for _ in range(2000):
            line = log_file.readline()
            if not line:
                break
            batch.append(line.rstrip("\r\n"))
        has_more = bool(log_file.readline())
    next_after_line = after_line + len(batch)
    return TrainingLogsOut(
        lines=batch,
        next_after_line=next_after_line,
        has_more=has_more,
    )


@router.delete("/tasks/{task_id}", response_model=TrainingDeleteOut)
def delete_training_task(task_id: str, db: Session = Depends(get_db)) -> TrainingDeleteOut:
    try:
        soft_delete_task(db, task_id)
        db.commit()
        return TrainingDeleteOut(deleted=True)
    except TrainingArtifactNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TrainingArtifactConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="unable to delete training task") from exc


@router.get("/artifacts", response_model=TrainingArtifactListOut)
def list_training_artifacts(
    task_id: str | None = Query(default=None, max_length=36),
    artifact_type: str | None = Query(default=None, max_length=32),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> TrainingArtifactListOut:
    query = db.query(TrainingArtifact).filter(
        TrainingArtifact.deleted_at.is_(None),
        TrainingArtifact.deletion_state.is_(None),
    )
    if task_id is not None:
        query = query.filter(TrainingArtifact.task_id == task_id)
    if artifact_type is not None:
        query = query.filter(TrainingArtifact.artifact_type == artifact_type)
    total = query.order_by(None).count()
    artifacts = (
        query.order_by(TrainingArtifact.created_at, TrainingArtifact.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return TrainingArtifactListOut(
        items=[_artifact_out(artifact) for artifact in artifacts],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/artifacts/{artifact_id}/download")
def download_training_artifact(artifact_id: str, db: Session = Depends(get_db)) -> StreamingResponse:
    try:
        opened = open_artifact_file(db, artifact_id, settings.TRAINING_OUTPUT_ROOT)
    except TrainingArtifactNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TrainingArtifactConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return StreamingResponse(
        _stream_open_file(opened),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{artifact_id}"'},
        background=BackgroundTask(opened.close),
    )


@router.delete("/artifacts/{artifact_id}", response_model=TrainingDeleteOut)
def delete_training_artifact(artifact_id: str, db: Session = Depends(get_db)) -> TrainingDeleteOut:
    staged: StagedArtifactDeletion | None = None
    try:
        staged = stage_artifact_deletion(db, artifact_id, settings.TRAINING_OUTPUT_ROOT)
        db.commit()
    except TrainingArtifactNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TrainingArtifactConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        if staged is not None:
            try:
                staged.restore()
            except Exception:
                try:
                    mark_artifact_recovery_required(
                        db,
                        staged.artifact_id,
                        staged.quarantine_name,
                    )
                    db.commit()
                except Exception:
                    db.rollback()
        raise HTTPException(status_code=500, detail="unable to delete training artifact") from exc
    try:
        staged.cleanup()
    except Exception:
        try:
            mark_artifact_cleanup_pending(db, staged.artifact_id)
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=500, detail="artifact cleanup is pending")
    try:
        mark_artifact_cleaned(db, staged.artifact_id)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="unable to finalize training artifact deletion") from exc
    return TrainingDeleteOut(deleted=True)


@router.get("/worker", response_model=TrainingWorkerOut)
def get_training_worker(db: Session = Depends(get_db)) -> TrainingWorkerOut:
    worker = db.query(TrainingWorker).order_by(TrainingWorker.updated_at.desc(), TrainingWorker.id).first()
    queue_length = (
        db.query(TrainingTask)
        .filter(TrainingTask.state == "queued", TrainingTask.deleted_at.is_(None))
        .count()
    )
    if worker is None:
        return TrainingWorkerOut(status="offline", queue_length=queue_length)
    status = _public_worker_status(worker.status)
    public_snapshot = (
        None if status in {"offline", "unknown"} else worker.gpu_snapshot
    )
    device_type, profile_name, device = _public_compute_device(public_snapshot)
    legacy_snapshot = isinstance(public_snapshot, dict) and not any(
        key in public_snapshot for key in ("device_type", "profile_name", "device")
    )
    return TrainingWorkerOut(
        id=worker.id,
        status=status,
        current_task_id=worker.current_task_id,
        model_status=_public_model_status(worker.model_status),
        last_heartbeat_at=worker.last_heartbeat_at,
        device_type=device_type,
        profile_name=profile_name,
        device=device,
        gpu=(
            _public_gpu(public_snapshot)
            if device_type == "cuda" or legacy_snapshot
            else None
        ),
        queue_length=queue_length,
    )


def _task_or_404(db: Session, task_id: str) -> TrainingTask:
    task = (
        db.query(TrainingTask)
        .filter(TrainingTask.id == task_id, TrainingTask.deleted_at.is_(None))
        .first()
    )
    if task is None:
        raise HTTPException(status_code=404, detail="training task not found")
    return task


def _task_header(db: Session, task_id: str) -> tuple[TrainingTask, TrainingMetric | None, int | None]:
    row = db.execute(_task_header_statement(task_id)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="training task not found")
    task, latest_metric, queue_position = row
    return task, latest_metric, queue_position


def _task_header_out(db: Session, task_id: str) -> TrainingTaskOut:
    task, latest_metric, queue_position = _task_header(db, task_id)
    return _task_out_values(task, latest_metric, queue_position)


def _task_out(db: Session, task: TrainingTask, queue_position: int | None = None) -> TrainingTaskOut:
    latest_metric = (
        db.query(TrainingMetric)
        .filter(TrainingMetric.task_id == task.id)
        .order_by(TrainingMetric.created_at.desc(), TrainingMetric.id.desc())
        .first()
    )
    return _task_out_values(task, latest_metric, queue_position)


def _task_out_values(
    task: TrainingTask, latest_metric: TrainingMetric | None, queue_position: int | None
) -> TrainingTaskOut:
    return TrainingTaskOut(
        id=task.id,
        name=task.name,
        task_type=task.task_type,
        job_kind=task.job_kind,
        state=task.state,
        model_id=task.model_id,
        parent_task_id=task.parent_task_id,
        cpt_adapter_artifact_id=task.cpt_adapter_artifact_id,
        queued_at=task.queued_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        heartbeat_at=task.heartbeat_at,
        progress=latest_metric.epoch if latest_metric is not None and latest_metric.epoch is not None else task.progress,
        queue_position=queue_position if task.state == "queued" else None,
        latest_metric=_metric_out(latest_metric) if latest_metric is not None else None,
    )


def _metric_out(metric: TrainingMetric) -> TrainingMetricOut:
    return TrainingMetricOut(
        step=metric.step,
        epoch=metric.epoch,
        loss=metric.loss,
        eval_loss=metric.eval_loss,
        learning_rate=metric.learning_rate,
        created_at=metric.created_at,
    )


def _artifact_out(artifact: TrainingArtifact) -> TrainingArtifactOut:
    return TrainingArtifactOut(
        id=artifact.id,
        task_id=artifact.task_id,
        artifact_type=artifact.artifact_type,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        metadata=_public_artifact_metadata(artifact),
        created_at=artifact.created_at,
    )


def _task_artifacts(db: Session, task_id: str) -> list[TrainingArtifact]:
    return (
        db.query(TrainingArtifact)
        .filter(
            TrainingArtifact.task_id == task_id,
            TrainingArtifact.deleted_at.is_(None),
            TrainingArtifact.deletion_state.is_(None),
        )
        .order_by(TrainingArtifact.created_at, TrainingArtifact.id)
        .all()
    )


def _task_page_statement(
    *,
    task_type: str | None,
    state: str | None,
    job_kind: str | None,
    page: int,
    page_size: int,
):
    conditions = [TrainingTask.deleted_at.is_(None)]
    if task_type is not None:
        conditions.append(TrainingTask.task_type == task_type)
    if state is not None:
        conditions.append(TrainingTask.state == state)
    if job_kind is not None:
        conditions.append(TrainingTask.job_kind == job_kind)
    filtered_tasks = (
        select(TrainingTask.id.label("task_id"))
        .where(*conditions)
        .cte("filtered_tasks")
    )
    task_total = (
        select(func.count().label("total"))
        .select_from(filtered_tasks)
        .cte("task_total")
    )
    page_tasks = (
        select(filtered_tasks.c.task_id)
        .join(TrainingTask, TrainingTask.id == filtered_tasks.c.task_id)
        .order_by(TrainingTask.queued_at, TrainingTask.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .cte("page_tasks")
    )
    queued, latest_metric_id = _task_header_components()
    return (
        select(
            TrainingTask,
            task_total.c.total,
            queued.c.queue_position,
            TrainingMetric,
        )
        .select_from(task_total)
        .outerjoin(page_tasks, true())
        .outerjoin(TrainingTask, TrainingTask.id == page_tasks.c.task_id)
        .outerjoin(queued, queued.c.task_id == TrainingTask.id)
        .outerjoin(TrainingMetric, TrainingMetric.id == latest_metric_id)
        .order_by(TrainingTask.queued_at, TrainingTask.id)
    )


def _task_header_statement(task_id: str):
    queued, latest_metric_id = _task_header_components()
    return (
        select(TrainingTask, TrainingMetric, queued.c.queue_position)
        .select_from(TrainingTask)
        .outerjoin(queued, queued.c.task_id == TrainingTask.id)
        .outerjoin(TrainingMetric, TrainingMetric.id == latest_metric_id)
        .where(TrainingTask.id == task_id, TrainingTask.deleted_at.is_(None))
    )


def _task_header_components():
    queued = (
        select(
            TrainingTask.id.label("task_id"),
            func.row_number().over(order_by=(TrainingTask.queued_at, TrainingTask.id)).label("queue_position"),
        )
        .where(TrainingTask.deleted_at.is_(None), TrainingTask.state == "queued")
        .cte("queued_tasks")
    )
    latest_metric_id = (
        select(TrainingMetric.id)
        .where(TrainingMetric.task_id == TrainingTask.id)
        .order_by(TrainingMetric.created_at.desc(), TrainingMetric.id.desc())
        .limit(1)
        .correlate(TrainingTask)
        .scalar_subquery()
    )
    return queued, latest_metric_id


def _timeline(task: TrainingTask) -> list[TrainingTimelineEvent]:
    events = [("queued", task.queued_at), ("started", task.started_at), ("heartbeat", task.heartbeat_at)]
    if task.cancel_requested_at is not None:
        events.append(("cancel_requested", task.cancel_requested_at))
    events.append((task.state, task.completed_at))
    return [TrainingTimelineEvent(name=name, at=at) for name, at in events if at is not None]


def _public_config(snapshot) -> dict:
    if not isinstance(snapshot, dict):
        return {}
    result = {}
    preset = snapshot.get("preset")
    if preset in {"quick", "formal"}:
        result["preset"] = preset
    qlora = _public_config_section(
        snapshot.get("qlora"),
        numeric_keys=_PUBLIC_QLORA_NUMERIC_KEYS,
        bool_keys=_PUBLIC_QLORA_BOOL_KEYS,
        trusted_enums=_PUBLIC_QLORA_ENUMS,
    )
    training = _public_config_section(
        snapshot.get("training"),
        numeric_keys=_PUBLIC_TRAINING_NUMERIC_KEYS,
        bool_keys=_PUBLIC_TRAINING_BOOL_KEYS,
        trusted_enums=_PUBLIC_TRAINING_ENUMS,
    )
    if qlora:
        result["qlora"] = qlora
    if training:
        result["training"] = training
    return result


def _public_evaluation_summary(summary) -> dict | None:
    if not isinstance(summary, dict):
        return None
    result = {}
    for key in _PUBLIC_EVALUATION_KEYS:
        if key in EVALUATION_SCORE_KEYS and key in summary and summary[key] is None:
            result[key] = None
            continue
        value = _numeric_tree(summary.get(key))
        if value is not None:
            result[key] = value
    return result or None


def _public_artifact_metadata(artifact: TrainingArtifact) -> dict | None:
    metadata = artifact.metadata_json
    if not isinstance(metadata, dict):
        metadata = {}
    result = {"display_name": _ARTIFACT_DISPLAY_NAMES.get(artifact.artifact_type, "Training artifact")}
    if metadata.get("format") in _PUBLIC_ARTIFACT_FORMATS:
        result["format"] = metadata["format"]
    if metadata.get("framework") in _PUBLIC_ARTIFACT_FRAMEWORKS:
        result["framework"] = metadata["framework"]
    if metadata.get("base_model") in _PUBLIC_ARTIFACT_BASE_MODELS:
        result["base_model"] = metadata["base_model"]
    for key in ("step", "epoch"):
        if _finite_number(metadata.get(key)):
            result[key] = metadata[key]
    profile_name = metadata.get("profile_name")
    if isinstance(profile_name, str) and _PUBLIC_PROFILE_FIELDS.get(profile_name) == (
        metadata.get("device_type"),
        metadata.get("quantization"),
        metadata.get("compute_dtype"),
    ):
        result.update({
            "device_type": metadata["device_type"],
            "profile_name": profile_name,
            "quantization": metadata["quantization"],
            "compute_dtype": metadata["compute_dtype"],
        })
    return result


def _public_gpu(snapshot) -> dict | None:
    if not isinstance(snapshot, dict):
        return None
    result = {
        key: snapshot[key]
        for key in _PUBLIC_GPU_KEYS - {"name"}
        if _finite_number(snapshot.get(key))
    }
    gpu_name = snapshot.get("name")
    if isinstance(gpu_name, str) and _NVIDIA_GPU_NAME_RE.fullmatch(gpu_name):
        result["name"] = gpu_name
    return result or None


def _public_compute_device(snapshot) -> tuple[str | None, str | None, str | None]:
    if not isinstance(snapshot, dict):
        return None, None, None
    device_type = snapshot.get("device_type")
    profile_name = snapshot.get("profile_name")
    device = snapshot.get("device")
    if (
        device_type not in {"cpu", "cuda"}
        or not isinstance(profile_name, str)
        or _PROFILE_DEVICE_TYPES.get(profile_name) != device_type
    ):
        return None, None, None
    if device_type == "cpu":
        if device != "CPU":
            return None, None, None
    elif not isinstance(device, str) or _NVIDIA_GPU_NAME_RE.fullmatch(device) is None:
        return None, None, None
    return device_type, profile_name, device


def _public_config_section(
    source,
    *,
    numeric_keys: set[str],
    bool_keys: set[str],
    trusted_enums: dict[str, set[str]],
) -> dict:
    if not isinstance(source, dict):
        return {}
    result = {
        key: source[key]
        for key in numeric_keys
        if _finite_number(source.get(key))
    }
    result.update(
        {
            key: source[key]
            for key in bool_keys
            if isinstance(source.get(key), bool)
        }
    )
    result.update(
        {
            key: source[key]
            for key, allowed_values in trusted_enums.items()
            if source.get(key) in allowed_values
        }
    )
    return result


def _finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _numeric_tree(value):
    if isinstance(value, bool):
        return value
    if _finite_number(value):
        return value
    if isinstance(value, list):
        values = [_numeric_tree(item) for item in value]
        return values if all(item is not None for item in values) else None
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            return None
        values = {key: _numeric_tree(item) for key, item in value.items()}
        return values if all(item is not None for item in values.values()) else None
    return None


def _public_worker_status(value: str | None) -> str:
    return value if value in _PUBLIC_WORKER_STATUSES else "unknown"


def _public_model_status(value: str | None) -> str | None:
    if value is None:
        return None
    return value if value in _PUBLIC_MODEL_STATUSES else "unknown"


def _stream_open_file(opened):
    try:
        while chunk := os.read(opened.fd, 64 * 1024):
            yield chunk
    finally:
        opened.close()
