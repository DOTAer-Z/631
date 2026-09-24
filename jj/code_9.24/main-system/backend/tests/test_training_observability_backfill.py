from __future__ import annotations

import tempfile
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.maintenance.backfill_training_observability import backfill_training_observability
from app.models.training_task import TrainingArtifact, TrainingMetric, TrainingTask


def test_maintenance_module_import_does_not_require_model_training_package():
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "Blocker = type('Blocker', (), {'find_spec': lambda self, name, path=None, target=None: "
                "(_ for _ in ()).throw(AssertionError(name)) if name.startswith('embedded_fault_diag') else None}); "
                "sys.meta_path.insert(0, Blocker()); "
                "import app.maintenance.backfill_training_observability"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert probe.returncode == 0, probe.stderr


@pytest.fixture
def graph():
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name) / "outputs"
    model = Path(temporary.name) / "model"
    root.mkdir()
    model.mkdir()
    engine = create_engine("sqlite://")
    for table in (TrainingTask.__table__, TrainingMetric.__table__, TrainingArtifact.__table__):
        table.create(bind=engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    with sessions.begin() as db:
        training = TrainingTask(
            name="source",
            task_type="sft",
            job_kind="training",
            state="succeeded",
            model_id="qwen-qwen3.5-9b",
            config_snapshot={},
        )
        evaluation = TrainingTask(
            name="evaluation",
            task_type="sft",
            job_kind="evaluation",
            state="succeeded",
            model_id="qwen-qwen3.5-9b",
            config_snapshot={},
        )
        db.add_all([training, evaluation])
        db.flush()

        training_log = root / training.id / "public" / "training.log"
        training_log.parent.mkdir(parents=True)
        training_log.write_text(
            "{'train_runtime': '3.12', 'train_loss': '1.407', 'epoch': '0.125'}\n",
            encoding="utf-8",
        )
        db.add(TrainingArtifact(
            task_id=training.id,
            artifact_type="log",
            relative_path=training_log.relative_to(root).as_posix(),
            size_bytes=training_log.stat().st_size,
        ))
        db.add(TrainingMetric(
            task_id=training.id,
            stream_offset=0,
            step=1,
            epoch=0.125,
            loss=None,
        ))

        evaluation_root = root / evaluation.id
        evaluation_root.mkdir(parents=True)
        (evaluation_root / "child.log").write_text(
            f"evaluation at {evaluation_root} model={model} token=private-token-value\n",
            encoding="utf-8",
        )

    yield sessions, root, model, training.id, evaluation.id
    engine.dispose()
    temporary.cleanup()


def test_backfill_creates_sanitized_evaluation_log_and_final_loss_once(graph):
    sessions, root, model, training_id, evaluation_id = graph

    with sessions.begin() as db:
        first = backfill_training_observability(
            db,
            output_root=root,
            base_model_path=model,
            evaluation_task_ids=[evaluation_id],
            training_task_ids=[training_id],
        )

    assert first == {"logs_created": 1, "metrics_updated": 1}
    with sessions() as db:
        log = db.query(TrainingArtifact).filter_by(
            task_id=evaluation_id,
            artifact_type="log",
        ).one()
        public_log = (root / log.relative_path).read_text(encoding="utf-8")
        assert "evaluation at" in public_log
        assert "[REDACTED_PATH]" in public_log
        assert str(root) not in public_log
        assert str(model) not in public_log
        assert "private-token-value" not in public_log
        assert db.query(TrainingMetric).filter_by(task_id=training_id).one().loss == pytest.approx(1.407)

    with sessions.begin() as db:
        second = backfill_training_observability(
            db,
            output_root=root,
            base_model_path=model,
            evaluation_task_ids=[evaluation_id],
            training_task_ids=[training_id],
        )

    assert second == {"logs_created": 0, "metrics_updated": 0}
    with sessions() as db:
        assert db.query(TrainingArtifact).filter_by(
            task_id=evaluation_id,
            artifact_type="log",
        ).count() == 1


def test_backfill_never_overwrites_existing_loss(graph):
    sessions, root, model, training_id, _evaluation_id = graph
    with sessions.begin() as db:
        db.query(TrainingMetric).filter_by(task_id=training_id).one().loss = 0.5

    with sessions.begin() as db:
        result = backfill_training_observability(
            db,
            output_root=root,
            base_model_path=model,
            training_task_ids=[training_id],
        )

    assert result["metrics_updated"] == 0
    with sessions() as db:
        assert db.query(TrainingMetric).filter_by(task_id=training_id).one().loss == 0.5


@pytest.mark.parametrize(
    "content",
    [
        "no final metric here\n",
        "{'train_loss': 'not-a-number'}\n",
        "{'train_loss': '1.0'}\n{'train_loss': '2.0'}\n",
        "{'train_loss': 'NaN'}\n",
    ],
)
def test_backfill_ignores_missing_malformed_or_ambiguous_final_loss(graph, content):
    sessions, root, model, training_id, _evaluation_id = graph
    with sessions() as db:
        artifact = db.query(TrainingArtifact).filter_by(
            task_id=training_id,
            artifact_type="log",
        ).one()
        (root / artifact.relative_path).write_text(content, encoding="utf-8")

    with sessions.begin() as db:
        result = backfill_training_observability(
            db,
            output_root=root,
            base_model_path=model,
            training_task_ids=[training_id],
        )

    assert result["metrics_updated"] == 0
    with sessions() as db:
        assert db.query(TrainingMetric).filter_by(task_id=training_id).one().loss is None


def test_backfill_requires_exactly_one_metric_row(graph):
    sessions, root, model, training_id, _evaluation_id = graph
    with sessions.begin() as db:
        db.add(TrainingMetric(
            task_id=training_id,
            stream_offset=100,
            step=2,
            epoch=0.25,
            loss=None,
        ))

    with sessions.begin() as db:
        result = backfill_training_observability(
            db,
            output_root=root,
            base_model_path=model,
            training_task_ids=[training_id],
        )

    assert result["metrics_updated"] == 0
