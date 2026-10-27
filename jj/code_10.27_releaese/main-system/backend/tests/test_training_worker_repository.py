import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.training_task import TrainingEvaluation, TrainingTask, TrainingWorker
from app.training_worker import repository


class TrainingWorkerRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        for table in (TrainingTask.__table__, TrainingEvaluation.__table__, TrainingWorker.__table__):
            table.create(bind=self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _task(self, *, name, job_kind="training", state="queued", worker_id=None, heartbeat_at=None):
        task = TrainingTask(
            name=name,
            task_type="sft",
            job_kind=job_kind,
            state=state,
            model_id="qwen-qwen3.5-9b",
            config_snapshot={"training": {}},
            worker_id=worker_id,
            heartbeat_at=heartbeat_at,
        )
        self.db.add(task)
        self.db.flush()
        if job_kind == "evaluation":
            self.db.add(
                TrainingEvaluation(
                    task_id=task.id,
                    source_sft_task_id=task.id,
                    status=state,
                )
            )
            self.db.flush()
        return task

    def test_claim_statement_uses_postgresql_skip_locked_and_global_queue_order(self):
        sql = str(
            repository.next_queued_task_statement().compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )

        self.assertIn("FOR UPDATE SKIP LOCKED", sql)
        self.assertIn("training_tasks.deleted_at IS NULL", sql)
        self.assertIn("ORDER BY training_tasks.queued_at, training_tasks.id", sql)

    def test_active_owned_task_statement_compiles_with_postgresql_row_lock_and_order(self):
        sql = str(
            repository.active_owned_task_statement("gpu-worker-1").compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )

        self.assertIn("FOR UPDATE", sql)
        self.assertIn("training_tasks.deleted_at IS NULL", sql)
        self.assertIn("ORDER BY training_tasks.queued_at, training_tasks.id", sql)

    def test_recovery_locks_all_tasks_before_the_first_evaluation_lock(self):
        cutoff = datetime.utcnow() - timedelta(seconds=60)
        stale = self._task(
            name="stale-evaluation",
            job_kind="evaluation",
            state="cancelling",
            worker_id="gpu-worker-1",
            heartbeat_at=cutoff - timedelta(seconds=1),
        )
        fresh = self._task(
            name="fresh-training",
            state="training",
            worker_id="gpu-worker-1",
            heartbeat_at=cutoff + timedelta(seconds=1),
        )
        self.db.add(TrainingWorker(id="gpu-worker-1", status="busy", current_task_id=stale.id))
        self.db.flush()

        executed_tables = []
        original_execute = self.db.execute

        def record_execute(statement, *args, **kwargs):
            statement_sql = str(statement)
            if "training_workers" in statement_sql:
                executed_tables.append("worker")
            elif "training_evaluations" in statement_sql:
                executed_tables.append("evaluation")
            elif "training_tasks" in statement_sql:
                executed_tables.append("task")
            return original_execute(statement, *args, **kwargs)

        with patch.object(self.db, "execute", side_effect=record_execute):
            worker, fresh_task = repository.recover_worker_ownership(
                self.db, "gpu-worker-1", cutoff
            )

        self.assertEqual(worker.id, "gpu-worker-1")
        self.assertEqual(fresh_task.id, fresh.id)
        self.assertEqual(stale.state, "interrupted")
        self.assertEqual(executed_tables[:2], ["worker", "task"])
        evaluation_index = executed_tables.index("evaluation")
        self.assertEqual(executed_tables[evaluation_index + 1 :], [])

    def test_claim_selects_oldest_training_or_evaluation_task_without_committing(self):
        training = self._task(name="training")
        evaluation = self._task(name="evaluation", job_kind="evaluation")
        training.queued_at = datetime(2026, 7, 16, 9, 0, 0)
        evaluation.queued_at = datetime(2026, 7, 16, 8, 0, 0)

        with patch.object(self.db, "commit") as commit:
            claimed = repository.claim_next_task(self.db, "gpu-worker-1")

        self.assertEqual(claimed.id, evaluation.id)
        self.assertEqual(claimed.state, "preparing_data")
        self.assertEqual(claimed.worker_id, "gpu-worker-1")
        self.assertIsNotNone(claimed.started_at)
        self.assertIsNotNone(claimed.heartbeat_at)
        mirrored = self.db.query(TrainingEvaluation).filter_by(task_id=evaluation.id).one()
        self.assertEqual((mirrored.status, mirrored.started_at), ("preparing_data", claimed.started_at))
        worker = self.db.get(TrainingWorker, "gpu-worker-1")
        self.assertEqual((worker.status, worker.current_task_id), ("busy", claimed.id))
        self.assertEqual(training.state, "queued")
        commit.assert_not_called()

    def test_heartbeat_requires_busy_current_worker_and_keeps_only_typed_gpu_fields(self):
        task = self._task(name="active", state="training", worker_id="gpu-worker-1")
        worker = TrainingWorker(id="gpu-worker-1", status="busy", current_task_id=task.id)
        self.db.add(worker)
        self.db.flush()

        repository.heartbeat(
            self.db,
            "gpu-worker-1",
            task.id,
            {
                "index": 1,
                "name": "NVIDIA GeForce RTX 3090",
                "memory_total_mb": 24576,
                "memory_used_mb": 5576.5,
                "memory_free_mb": 19000,
                "utilization_percent": 40,
                "temperature_c": 63.5,
                "driver_version": "550.54.15",
                "cuda_version": "12.4",
                "api_key": "do-not-store",
                "unknown": "drop",
                "nested": {"token": "drop"},
                "memory_reserved_mb": 1,
            },
        )

        self.assertIsNotNone(task.heartbeat_at)
        self.assertEqual(
            worker.gpu_snapshot,
            {
                "index": 1,
                "name": "NVIDIA GeForce RTX 3090",
                "memory_total_mb": 24576,
                "memory_used_mb": 5576.5,
                "memory_free_mb": 19000,
                "utilization_percent": 40,
                "temperature_c": 63.5,
                "driver_version": "550.54.15",
                "cuda_version": "12.4",
            },
        )
        with self.assertRaisesRegex(repository.TrainingWorkerOwnershipError, "ownership"):
            repository.heartbeat(self.db, "other-worker", task.id, {})

        worker.status = "idle"
        with self.assertRaisesRegex(repository.TrainingWorkerOwnershipError, "busy"):
            repository.heartbeat(self.db, "gpu-worker-1", task.id, {})

    def test_heartbeat_synchronizes_evaluation_row(self):
        task = self._task(
            name="evaluation",
            job_kind="evaluation",
            state="evaluating",
            worker_id="gpu-worker-1",
        )
        self.db.add(TrainingWorker(id="gpu-worker-1", status="busy", current_task_id=task.id))
        self.db.flush()

        repository.heartbeat(self.db, "gpu-worker-1", task.id, {"utilization_percent": 40})

        evaluation = self.db.query(TrainingEvaluation).filter_by(task_id=task.id).one()
        self.assertEqual(evaluation.status, "evaluating")
        self.assertEqual(evaluation.started_at, task.started_at)

    def test_heartbeat_rejects_states_that_do_not_match_the_task_job_kind(self):
        cases = (
            ("training", "evaluating"),
            ("evaluation", "training"),
        )
        for job_kind, state in cases:
            with self.subTest(job_kind=job_kind, state=state):
                task = self._task(
                    name=f"{job_kind}-{state}",
                    job_kind=job_kind,
                    state=state,
                    worker_id="gpu-worker-1",
                )
                self.db.add(TrainingWorker(id=f"gpu-worker-1-{job_kind}", status="busy", current_task_id=task.id))
                self.db.flush()
                task.worker_id = f"gpu-worker-1-{job_kind}"

                with self.assertRaisesRegex(repository.TrainingWorkerStateError, "cannot be heartbeated"):
                    repository.heartbeat(self.db, f"gpu-worker-1-{job_kind}", task.id, {})

    def test_stale_recovery_interrupts_only_this_workers_active_tasks_and_never_commits(self):
        stale_before = datetime.utcnow() - timedelta(seconds=60)
        stale = self._task(
            name="stale",
            job_kind="evaluation",
            state="cancelling",
            worker_id="gpu-worker-1",
            heartbeat_at=stale_before - timedelta(seconds=1),
        )
        fresh = self._task(
            name="fresh",
            state="training",
            worker_id="gpu-worker-1",
            heartbeat_at=stale_before + timedelta(seconds=1),
        )
        other = self._task(
            name="other",
            state="training",
            worker_id="gpu-worker-2",
            heartbeat_at=stale_before - timedelta(seconds=1),
        )

        with patch.object(self.db, "commit") as commit:
            interrupted = repository.mark_stale_tasks_interrupted(
                self.db, "gpu-worker-1", stale_before
            )

        self.assertEqual([task.id for task in interrupted], [stale.id])
        self.assertEqual(stale.state, "interrupted")
        self.assertIsNotNone(stale.completed_at)
        self.assertIn("stale heartbeat", stale.error_message)
        mirrored = self.db.query(TrainingEvaluation).filter_by(task_id=stale.id).one()
        self.assertEqual((mirrored.status, mirrored.error_message), ("interrupted", stale.error_message))
        self.assertEqual((fresh.state, other.state), ("training", "training"))
        commit.assert_not_called()


@pytest.mark.postgresql
@pytest.mark.skipif(
    not os.environ.get("TRAINING_TEST_DATABASE_URL"),
    reason="requires TRAINING_TEST_DATABASE_URL PostgreSQL test database",
)
def test_two_postgresql_sessions_skip_locked_claims_different_tasks():
    schema = f"task9_{uuid4().hex}"
    engine = create_engine(os.environ["TRAINING_TEST_DATABASE_URL"])
    first = second = None
    schema_created = False
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        schema_created = True
        isolated = engine.execution_options(schema_translate_map={None: schema})
        Base.metadata.create_all(bind=isolated)
        sessions = sessionmaker(bind=isolated)
        first = sessions()
        second = sessions()
        first_id = str(uuid4())
        second_id = str(uuid4())
        first.add_all(
            [
                TrainingTask(
                    id=first_id,
                    name=f"task9-first-{first_id}",
                    task_type="cpt",
                    job_kind="training",
                    state="queued",
                    model_id="qwen-qwen3.5-9b",
                    config_snapshot={},
                    queued_at=datetime(2026, 7, 16, 8, 0, 0),
                ),
                TrainingTask(
                    id=second_id,
                    name=f"task9-second-{second_id}",
                    task_type="cpt",
                    job_kind="training",
                    state="queued",
                    model_id="qwen-qwen3.5-9b",
                    config_snapshot={},
                    queued_at=datetime(2026, 7, 16, 8, 1, 0),
                ),
            ]
        )
        first.commit()
        first.begin()
        claimed_first = repository.claim_next_task(first, "gpu-worker-1")
        second.begin()
        claimed_second = repository.claim_next_task(second, "gpu-worker-2")

        assert claimed_first.id != claimed_second.id
        assert claimed_first.id == first_id
        assert claimed_second.id == second_id
    finally:
        if first is not None:
            first.rollback()
            first.close()
        if second is not None:
            second.rollback()
            second.close()
        if schema_created:
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
