from __future__ import annotations

import copy
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.training_data import TrainingTest, TrainingTestVersion
from app.models.training_task import TrainingArtifact, TrainingEvaluation, TrainingTask, TrainingTaskTest
from app.schemas.model_training import TrainingTaskCreate
from app.services.training_artifact_service import acquire_artifact_registry_lock
from app.services.training_config_service import normalized_config_snapshot
from app.services.training_split_service import SplitRatios, SplitSample, build_stratified_split


MODEL_ID = "qwen-qwen3.5-9b"
_UPLOADED_ADAPTER_METADATA_FIELDS = {
    "source",
    "adapter_stage",
    "base_model_id",
    "display_name",
    "description",
    "uploaded_archive_sha256",
    "peft_type",
    "rank",
    "target_modules",
    "original_archive_format",
}
_QWEN_TARGET_MODULES = {
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
}
_ACTIVE_STATES = {"preparing_data", "training", "evaluating"}
_RETRYABLE_STATES = {"failed", "cancelled", "interrupted"}
_TERMINAL_STATES = {"cancelled", "succeeded", "failed", "interrupted"}
_EVALUATION_TRANSITIONS = {
    "queued": {"preparing_data"},
    "preparing_data": {"evaluating", "failed", "interrupted"},
    "evaluating": {"succeeded", "failed", "interrupted"},
    "cancelling": {"cancelled", "failed", "interrupted"},
}


class TrainingTaskNotFound(LookupError):
    pass


class TrainingTaskConflict(RuntimeError):
    pass


class TrainingTaskValidationError(ValueError):
    pass


def create_task(db: Session, request: TrainingTaskCreate) -> TrainingTask:
    version_ids = list(request.test_version_ids)
    if len(set(version_ids)) != len(version_ids):
        raise TrainingTaskValidationError("test version IDs must not contain duplicates")
    if request.task_type == "sft" and request.cpt_adapter_artifact_id is not None:
        acquire_artifact_registry_lock(db)
    versions = _lock_versions(db, version_ids)
    versions_by_id = {version.id: version for version in versions}
    unknown_ids = sorted(set(version_ids) - set(versions_by_id))
    if unknown_ids:
        raise TrainingTaskNotFound(f"unknown test version IDs: {unknown_ids}")
    for version_id in version_ids:
        if versions_by_id[version_id].completeness != "complete":
            raise TrainingTaskConflict(f"test version {version_id} is incomplete")

    tests = db.query(TrainingTest).filter(
        TrainingTest.id.in_({version.test_id for version in versions})
    ).all()
    tests_by_id = {test.id: test for test in tests}
    snapshot = normalized_config_snapshot(request.task_type, request.preset, request.overrides)
    adapter_id = None
    if request.task_type == "sft" and request.cpt_adapter_artifact_id is not None:
        adapter_id = _validate_cpt_adapter(db, request.cpt_adapter_artifact_id)
    try:
        split = build_stratified_split(
            [
                SplitSample(
                    version_id=version.id,
                    test_name=tests_by_id[version.test_id].test_name,
                    platform=tests_by_id[version.test_id].platform,
                    sample_class=version.sample_class,
                    domain=version.domain,
                    fault_type=version.fault_type,
                )
                for version in (versions_by_id[version_id] for version_id in version_ids)
            ],
            SplitRatios(request.train_ratio, request.validation_ratio, request.test_ratio),
            request.seed,
        )
    except ValueError as exc:
        raise TrainingTaskValidationError(str(exc)) from None

    task = TrainingTask(
        name=request.name,
        task_type=request.task_type,
        job_kind="training",
        state="queued",
        model_id=MODEL_ID,
        cpt_adapter_artifact_id=adapter_id,
        config_snapshot=snapshot,
        split_seed=request.seed,
        base_model_path=(request.base_model_path.strip() if request.base_model_path else None),
    )
    db.add(task)
    db.flush()
    _add_split_rows(db, task.id, split)
    db.flush()
    return task


def cancel_task(db: Session, task_id: str) -> TrainingTask:
    task = _locked_task(db, task_id)
    now = datetime.utcnow()
    if task.state == "queued":
        task.state = "cancelled"
        task.completed_at = now
    elif task.state in _ACTIVE_STATES:
        task.state = "cancelling"
        task.cancel_requested_at = task.cancel_requested_at or now
    else:
        raise TrainingTaskConflict(f"task in state {task.state} cannot be cancelled")
    if task.job_kind == "evaluation":
        evaluation = _locked_evaluation(db, task.id)
        _synchronize_evaluation_row(evaluation, task, state=task.state)
    db.flush()
    return task


def retry_task(
    db: Session, task_id: str, resume_checkpoint_artifact_id: str | None = None
) -> TrainingTask:
    source = _locked_task(db, task_id)
    if source.state not in _RETRYABLE_STATES:
        raise TrainingTaskConflict(f"task in state {source.state} cannot be retried")
    checkpoint_id = _validate_checkpoint(db, source, resume_checkpoint_artifact_id)
    source_rows = (
        db.query(TrainingTaskTest)
        .filter(TrainingTaskTest.task_id == source.id)
        .with_for_update()
        .all()
    )
    retry = TrainingTask(
        name=f"{source.name} retry",
        task_type=source.task_type,
        job_kind=source.job_kind,
        state="queued",
        model_id=source.model_id,
        cpt_adapter_artifact_id=source.cpt_adapter_artifact_id,
        parent_task_id=source.id,
        resume_checkpoint_artifact_id=checkpoint_id,
        config_snapshot=copy.deepcopy(source.config_snapshot),
        split_seed=source.split_seed,
    )
    db.add(retry)
    db.flush()
    for row in source_rows:
        db.add(TrainingTaskTest(task_id=retry.id, test_version_id=row.test_version_id, split_name=row.split_name))
    if retry.job_kind == "evaluation":
        source_evaluation = (
            db.query(TrainingEvaluation)
            .filter(TrainingEvaluation.task_id == source.id)
            .with_for_update()
            .first()
        )
        if source_evaluation is None:
            raise TrainingTaskConflict("evaluation task has no evaluation record")
        db.add(
            TrainingEvaluation(
                task_id=retry.id,
                source_sft_task_id=source_evaluation.source_sft_task_id,
                source_sft_artifact_id=source_evaluation.source_sft_artifact_id,
                status="queued",
            )
        )
    db.flush()
    return retry


def create_evaluation(db: Session, source_sft_task_id: str) -> TrainingTask:
    source = _locked_task(db, source_sft_task_id)
    if source.task_type != "sft" or source.job_kind != "training" or source.state != "succeeded":
        raise TrainingTaskConflict("evaluation requires a succeeded SFT training task")
    source_rows = (
        db.query(TrainingTaskTest)
        .filter(TrainingTaskTest.task_id == source.id, TrainingTaskTest.split_name == "test")
        .with_for_update()
        .all()
    )
    if not source_rows:
        raise TrainingTaskConflict("source SFT task has no frozen test split")
    evaluation = TrainingTask(
        name=f"{source.name} evaluation",
        task_type="sft",
        job_kind="evaluation",
        state="queued",
        model_id=source.model_id,
        cpt_adapter_artifact_id=source.cpt_adapter_artifact_id,
        parent_task_id=source.id,
        config_snapshot=copy.deepcopy(source.config_snapshot),
        split_seed=source.split_seed,
    )
    db.add(evaluation)
    db.flush()
    for row in source_rows:
        db.add(TrainingTaskTest(task_id=evaluation.id, test_version_id=row.test_version_id, split_name="test"))
    db.add(
        TrainingEvaluation(
            task_id=evaluation.id,
            source_sft_task_id=source.id,
            status="queued",
        )
    )
    db.flush()
    return evaluation


def create_external_adapter_evaluation(
    db: Session,
    artifact_id: str,
    name: str,
    test_version_ids: list[int],
) -> TrainingTask:
    trimmed_name = name.strip() if isinstance(name, str) else ""
    if not trimmed_name or len(trimmed_name) > 255:
        raise TrainingTaskValidationError("evaluation name is invalid")
    if (
        not isinstance(test_version_ids, list)
        or not test_version_ids
        or any(type(version_id) is not int or version_id <= 0 for version_id in test_version_ids)
        or len(set(test_version_ids)) != len(test_version_ids)
    ):
        raise TrainingTaskValidationError(
            "test version IDs must be a nonempty unique list"
        )

    acquire_artifact_registry_lock(db)
    artifact = (
        db.query(TrainingArtifact)
        .filter(TrainingArtifact.id == artifact_id)
        .with_for_update()
        .first()
    )
    if artifact is None or artifact.deleted_at is not None or artifact.deletion_state is not None:
        raise TrainingTaskNotFound("SFT adapter is not available")
    classify_uploaded_sft_adapter(artifact, MODEL_ID)

    versions = _lock_versions(db, test_version_ids)
    versions_by_id = {version.id: version for version in versions}
    unknown_ids = sorted(set(test_version_ids) - set(versions_by_id))
    if unknown_ids:
        raise TrainingTaskNotFound(f"unknown test version IDs: {unknown_ids}")
    for version_id in test_version_ids:
        if versions_by_id[version_id].completeness != "complete":
            raise TrainingTaskConflict(f"test version {version_id} is incomplete")

    task = TrainingTask(
        name=trimmed_name,
        task_type="sft",
        job_kind="evaluation",
        state="queued",
        model_id=MODEL_ID,
        parent_task_id=None,
        cpt_adapter_artifact_id=None,
        config_snapshot={},
    )
    db.add(task)
    db.flush()
    for version_id in test_version_ids:
        db.add(
            TrainingTaskTest(
                task_id=task.id,
                test_version_id=version_id,
                split_name="test",
            )
        )
    db.add(
        TrainingEvaluation(
            task_id=task.id,
            source_sft_task_id=None,
            source_sft_artifact_id=artifact.id,
            status="queued",
        )
    )
    db.flush()
    return task


def synchronize_evaluation(
    db: Session,
    task_id: str,
    *,
    state: str,
    summary: dict | None = None,
    error_message: str | None = None,
) -> TrainingTask:
    if state not in {"queued", "preparing_data", "evaluating", "cancelling", *_TERMINAL_STATES}:
        raise TrainingTaskConflict(f"invalid evaluation state: {state}")
    task = _locked_task(db, task_id)
    if task.job_kind != "evaluation":
        raise TrainingTaskConflict("task is not an evaluation")
    evaluation = _locked_evaluation(db, task.id)
    _validate_evaluation_transition(task.state, state)
    if task.state == state:
        return task
    task.state = state
    if state in {"preparing_data", "evaluating"} and task.started_at is None:
        task.started_at = datetime.utcnow()
    if state in _TERMINAL_STATES:
        task.completed_at = datetime.utcnow()
    if error_message is not None:
        task.error_message = error_message
    _synchronize_evaluation_row(
        evaluation,
        task,
        state=state,
        summary=summary,
        error_message=error_message,
    )
    db.flush()
    return task


def _lock_versions(db: Session, version_ids: list[int]) -> list[TrainingTestVersion]:
    return (
        db.query(TrainingTestVersion)
        .filter(TrainingTestVersion.id.in_(version_ids))
        .with_for_update()
        .all()
    )


def _locked_task(db: Session, task_id: str) -> TrainingTask:
    task = (
        db.query(TrainingTask)
        .filter(TrainingTask.id == task_id, TrainingTask.deleted_at.is_(None))
        .with_for_update()
        .first()
    )
    if task is None:
        raise TrainingTaskNotFound("training task not found")
    return task


def _validate_cpt_adapter(db: Session, artifact_id: str) -> str:
    artifact = (
        db.query(TrainingArtifact)
        .filter(TrainingArtifact.id == artifact_id)
        .with_for_update()
        .first()
    )
    classify_cpt_adapter(db, artifact, MODEL_ID)
    return artifact.id


def classify_cpt_adapter(
    db: Session,
    artifact: TrainingArtifact | None,
    model_id: str,
) -> str:
    if artifact is None or artifact.deleted_at is not None or artifact.deletion_state is not None:
        raise TrainingTaskNotFound("CPT adapter is not available")
    if artifact.artifact_type != "final_adapter":
        raise TrainingTaskConflict("CPT adapter is not a final adapter")
    if artifact.task_id is None:
        if not _trusted_uploaded_adapter(artifact, model_id, "cpt"):
            raise TrainingTaskConflict("uploaded CPT adapter metadata is not trusted")
        return "uploaded_cpt"

    source = (
        db.query(TrainingTask)
        .filter(TrainingTask.id == artifact.task_id, TrainingTask.deleted_at.is_(None))
        .with_for_update()
        .first()
    )
    if (
        source is None
        or source.task_type != "cpt"
        or source.job_kind != "training"
        or source.state != "succeeded"
        or source.model_id != model_id
        or model_id != MODEL_ID
    ):
        raise TrainingTaskConflict(
            "CPT adapter must belong to a compatible succeeded CPT training task"
        )
    return "trained_cpt"


def classify_uploaded_sft_adapter(
    artifact: TrainingArtifact | None,
    model_id: str,
) -> str:
    if artifact is None or artifact.deleted_at is not None or artifact.deletion_state is not None:
        raise TrainingTaskNotFound("SFT adapter is not available")
    if (
        artifact.task_id is not None
        or artifact.artifact_type != "final_adapter"
        or not _trusted_uploaded_adapter(artifact, model_id, "sft")
    ):
        raise TrainingTaskConflict("uploaded SFT adapter metadata is not trusted")
    return "uploaded_sft"


def _trusted_uploaded_adapter(
    artifact: TrainingArtifact,
    model_id: str,
    adapter_stage: str,
) -> bool:
    metadata = artifact.metadata_json
    if not isinstance(metadata, dict) or set(metadata) != _UPLOADED_ADAPTER_METADATA_FIELDS:
        return False
    name = metadata.get("display_name")
    description = metadata.get("description")
    rank = metadata.get("rank")
    target_modules = metadata.get("target_modules")
    return (
        model_id == MODEL_ID
        and artifact.relative_path == f"imports/{artifact.id}/final_adapter.tar"
        and isinstance(artifact.size_bytes, int)
        and not isinstance(artifact.size_bytes, bool)
        and artifact.size_bytes > 0
        and _is_sha256(artifact.sha256)
        and metadata.get("source") == "uploaded"
        and metadata.get("adapter_stage") == adapter_stage
        and metadata.get("base_model_id") == model_id
        and isinstance(name, str)
        and name == name.strip()
        and 1 <= len(name) <= 255
        and (
            description is None
            or isinstance(description, str) and len(description) <= 1000
        )
        and _is_sha256(metadata.get("uploaded_archive_sha256"))
        and metadata.get("peft_type") == "LORA"
        and isinstance(rank, int)
        and not isinstance(rank, bool)
        and 1 <= rank <= 1024
        and isinstance(target_modules, list)
        and 1 <= len(target_modules) <= 32
        and all(
            isinstance(module, str) and module in _QWEN_TARGET_MODULES
            for module in target_modules
        )
        and target_modules == sorted(set(target_modules))
        and metadata.get("original_archive_format") in {"zip", "tar", "tar.gz"}
    )


def _is_sha256(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_checkpoint(db: Session, source: TrainingTask, artifact_id: str | None) -> str | None:
    if artifact_id is None:
        return None
    artifact = (
        db.query(TrainingArtifact)
        .filter(TrainingArtifact.id == artifact_id)
        .with_for_update()
        .first()
    )
    if artifact is None or artifact.deleted_at is not None or artifact.deletion_state is not None:
        raise TrainingTaskNotFound("checkpoint is not available")
    if artifact.task_id != source.id:
        raise TrainingTaskConflict("checkpoint does not belong to the source task")
    if artifact.artifact_type != "checkpoint":
        raise TrainingTaskConflict("artifact is not a checkpoint")
    return artifact.id


def _add_split_rows(db: Session, task_id: str, split: dict[str, list[int]]) -> None:
    for split_name, version_ids in split.items():
        for version_id in version_ids:
            db.add(TrainingTaskTest(task_id=task_id, test_version_id=version_id, split_name=split_name))


def _locked_evaluation(db: Session, task_id: str) -> TrainingEvaluation:
    evaluation = (
        db.query(TrainingEvaluation)
        .filter(TrainingEvaluation.task_id == task_id)
        .with_for_update()
        .first()
    )
    if evaluation is None:
        raise TrainingTaskConflict("evaluation task has no evaluation record")
    return evaluation


def _validate_evaluation_transition(current_state: str, next_state: str) -> None:
    if current_state == next_state:
        return
    if next_state not in _EVALUATION_TRANSITIONS.get(current_state, set()):
        raise TrainingTaskConflict(
            f"invalid evaluation transition: {current_state} -> {next_state}"
        )


def _synchronize_evaluation_row(
    evaluation: TrainingEvaluation,
    task: TrainingTask,
    *,
    state: str,
    summary: dict | None = None,
    error_message: str | None = None,
) -> None:
    evaluation.status = state
    if state in {"preparing_data", "evaluating"} and evaluation.started_at is None:
        evaluation.started_at = task.started_at or datetime.utcnow()
    if state in _TERMINAL_STATES:
        evaluation.completed_at = task.completed_at or datetime.utcnow()
    if summary is not None:
        evaluation.summary = copy.deepcopy(summary)
    if error_message is not None:
        evaluation.error_message = error_message
