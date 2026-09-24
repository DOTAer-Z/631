from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Sequence

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.training_task import TrainingArtifact, TrainingMetric, TrainingTask
from app.services.training_artifact_service import register_artifact, resolve_artifact_path
from app.services.training_public_artifact_service import path_metadata, write_sanitized_log


_TERMINAL_STATES = {"cancelled", "succeeded", "failed", "interrupted"}
_TRAIN_LOSS_PATTERN = re.compile(
    r"(?:['\"]train_loss['\"]|train_loss)\s*:\s*['\"]?"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)['\"]?"
)


def backfill_training_observability(
    db: Session,
    *,
    output_root: str | Path,
    base_model_path: str | Path,
    evaluation_task_ids: Sequence[str] = (),
    training_task_ids: Sequence[str] = (),
) -> dict[str, int]:
    root = Path(output_root).resolve(strict=True)
    services = SimpleNamespace(output_root=root, base_model_path=Path(base_model_path))
    logs_created = 0
    metrics_updated = 0

    for task_id in dict.fromkeys(evaluation_task_ids):
        task = db.get(TrainingTask, task_id)
        if task is None or task.job_kind != "evaluation" or task.state not in _TERMINAL_STATES:
            continue
        existing_log = (
            db.query(TrainingArtifact.id)
            .filter(
                TrainingArtifact.task_id == task.id,
                TrainingArtifact.artifact_type == "log",
                TrainingArtifact.deleted_at.is_(None),
                TrainingArtifact.deletion_state.is_(None),
            )
            .first()
        )
        if existing_log is not None:
            continue
        task_root = _existing_task_root(root, task.id)
        if task_root is None:
            continue
        source = task_root / "child.log"
        if not source.is_file() or source.is_symlink():
            continue
        public = task_root / "public"
        if public.exists() and (not public.is_dir() or public.is_symlink()):
            continue
        public.mkdir(exist_ok=True)
        destination = public / "evaluation.log"
        if destination.is_symlink():
            continue
        write_sanitized_log(source, destination, services, task_root)
        size, digest = path_metadata(destination)
        register_artifact(
            db,
            root,
            task_id=task.id,
            artifact_type="log",
            relative_path=destination.relative_to(root).as_posix(),
            size_bytes=size,
            sha256=digest,
            metadata_json={},
        )
        logs_created += 1

    for task_id in dict.fromkeys(training_task_ids):
        task = db.get(TrainingTask, task_id)
        if task is None or task.job_kind != "training" or task.state not in _TERMINAL_STATES:
            continue
        metrics = (
            db.query(TrainingMetric)
            .filter(TrainingMetric.task_id == task.id)
            .order_by(TrainingMetric.id)
            .all()
        )
        if len(metrics) != 1 or metrics[0].loss is not None:
            continue
        log_artifacts = (
            db.query(TrainingArtifact)
            .filter(
                TrainingArtifact.task_id == task.id,
                TrainingArtifact.artifact_type == "log",
                TrainingArtifact.deleted_at.is_(None),
                TrainingArtifact.deletion_state.is_(None),
            )
            .all()
        )
        public_logs = [
            artifact
            for artifact in log_artifacts
            if artifact.relative_path.endswith("/public/training.log")
        ]
        if len(public_logs) != 1:
            continue
        log_path = resolve_artifact_path(db, public_logs[0].id, root)
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        matches = _TRAIN_LOSS_PATTERN.findall(text)
        if len(matches) != 1:
            continue
        value = float(matches[0])
        if not math.isfinite(value):
            continue
        metrics[0].loss = value
        metrics_updated += 1

    return {"logs_created": logs_created, "metrics_updated": metrics_updated}


def _existing_task_root(root: Path, task_id: str) -> Path | None:
    lexical = root / task_id
    if lexical.is_symlink():
        return None
    try:
        resolved = lexical.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_dir() or resolved.parent != root:
        return None
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill public evaluation logs and proven final training Loss values."
    )
    parser.add_argument("--evaluation-task-id", action="append", default=[])
    parser.add_argument("--training-task-id", action="append", default=[])
    args = parser.parse_args()
    with SessionLocal() as db:
        try:
            result = backfill_training_observability(
                db,
                output_root=settings.TRAINING_OUTPUT_ROOT,
                base_model_path=settings.TRAINING_BASE_MODEL_PATH,
                evaluation_task_ids=args.evaluation_task_id,
                training_task_ids=args.training_task_id,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
