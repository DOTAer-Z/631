import copy
import unittest
from datetime import datetime
from unittest.mock import patch

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.training_data import TrainingTest, TrainingTestVersion
from app.models.training_task import (
    TrainingArtifact,
    TrainingEvaluation,
    TrainingTask,
    TrainingTaskTest,
)
from app.schemas.model_training import TrainingTaskCreate
from app.services.training_task_service import (
    TrainingTaskConflict,
    TrainingTaskNotFound,
    TrainingTaskValidationError,
    cancel_task,
    create_evaluation,
    create_task,
    retry_task,
    synchronize_evaluation,
)


class TrainingTaskServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        for table in (
            TrainingTest.__table__,
            TrainingTestVersion.__table__,
            TrainingTask.__table__,
            TrainingArtifact.__table__,
            TrainingTaskTest.__table__,
            TrainingEvaluation.__table__,
        ):
            table.create(bind=self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.complete_ids = [self._version(f"Test_{index}") for index in range(1, 5)]
        self.incomplete_id = self._version("Incomplete", completeness="incomplete")

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _version(self, name, *, completeness="complete", sample_class="fault", domain="kernel", fault_type="watchdog"):
        test = TrainingTest(platform="NuttX", test_name=name)
        self.db.add(test)
        self.db.flush()
        version = TrainingTestVersion(
            test_id=test.id,
            version_number=1,
            content_sha256=f"{test.id:064d}",
            ground_truth={},
            fip_info={},
            completeness=completeness,
            import_id=f"import-{test.id}",
            sample_class=sample_class,
            domain=domain,
            fault_type=fault_type,
        )
        self.db.add(version)
        self.db.flush()
        return version.id

    def _request(self, **changes):
        payload = {
            "name": " CPT verification ",
            "task_type": "cpt",
            "test_version_ids": self.complete_ids,
            "preset": "quick",
            "overrides": {"learning_rate": 0.001},
            "train_ratio": 0.5,
            "validation_ratio": 0.25,
            "test_ratio": 0.25,
            "seed": 631,
        }
        payload.update(changes)
        return TrainingTaskCreate(**payload)

    def _task(self, *, task_type="cpt", state="succeeded", job_kind="training", config_snapshot=None):
        task = TrainingTask(
            name="source",
            task_type=task_type,
            job_kind=job_kind,
            state=state,
            model_id="qwen-qwen3.5-9b",
            config_snapshot=config_snapshot or {"preset": "quick", "training": {}, "qlora": {}},
            split_seed=631,
        )
        self.db.add(task)
        self.db.flush()
        return task

    def _add_task_tests(self, task, rows):
        for version_id, split_name in rows:
            self.db.add(TrainingTaskTest(task_id=task.id, test_version_id=version_id, split_name=split_name))
        self.db.flush()

    def _artifact(self, task, *, artifact_type="final_adapter", deleted_at=None):
        artifact = TrainingArtifact(
            task_id=task.id,
            artifact_type=artifact_type,
            relative_path=f"{task.id}/{artifact_type}",
            deleted_at=deleted_at,
        )
        self.db.add(artifact)
        self.db.flush()
        return artifact

    def test_request_rejects_duplicates_invalid_ratios_and_untrusted_fields(self):
        base = self._request().model_dump()
        cases = (
            ({"test_version_ids": [self.complete_ids[0], self.complete_ids[0]]}, "duplicates"),
            ({"train_ratio": 0.8, "validation_ratio": 0.1, "test_ratio": 0.2}, "sum"),
            ({"train_ratio": float("inf")}, "finite"),
            ({"server_path": "/etc/passwd"}, "extra"),
        )
        for update, message in cases:
            with self.subTest(update=update):
                payload = copy.deepcopy(base)
                payload.update(update)
                with self.assertRaisesRegex(ValidationError, message):
                    TrainingTaskCreate(**payload)

    def test_create_task_locks_complete_versions_and_freezes_path_free_snapshot(self):
        request = self._request()
        with patch.object(self.db, "commit") as commit:
            task = create_task(self.db, request)

        self.assertEqual(task.name, "CPT verification")
        self.assertEqual(task.task_type, "cpt")
        self.assertEqual(task.job_kind, "training")
        self.assertEqual(task.state, "queued")
        self.assertEqual(task.model_id, "qwen-qwen3.5-9b")
        self.assertIsNone(task.cpt_adapter_artifact_id)
        self.assertEqual(task.split_seed, 631)
        self.assertEqual(set(row.test_version_id for row in self.db.query(TrainingTaskTest).filter_by(task_id=task.id)), set(self.complete_ids))
        self.assertEqual(
            {row.split_name for row in self.db.query(TrainingTaskTest).filter_by(task_id=task.id)},
            {"train", "validation", "test"},
        )
        self.assertNotIn("/", repr(task.config_snapshot))
        self.assertNotIn("model_name_or_path", task.config_snapshot)
        self.assertEqual(task.config_snapshot["training"]["learning_rate"], 0.001)
        commit.assert_not_called()

    def test_create_task_uses_precise_not_found_conflict_and_validation_categories(self):
        duplicate_payload = self._request().model_dump()
        duplicate_payload["test_version_ids"] = [self.complete_ids[0], self.complete_ids[0]]
        cases = (
            (self._request(test_version_ids=[99999]), TrainingTaskNotFound, "unknown test version IDs"),
            (self._request(test_version_ids=[self.incomplete_id]), TrainingTaskConflict, "is incomplete"),
            (
                TrainingTaskCreate.model_construct(**duplicate_payload),
                TrainingTaskValidationError,
                "duplicates",
            ),
        )
        for request, error_type, message in cases:
            with self.subTest(request=request):
                with self.assertRaisesRegex(error_type, message):
                    create_task(self.db, request)
                self.assertEqual(self.db.query(TrainingTask).count(), 0)

    def test_create_task_acquires_row_locks_before_revalidation(self):
        request = self._request(test_version_ids=[self.complete_ids[0]])
        with patch("app.services.training_task_service._lock_versions", wraps=__import__(
            "app.services.training_task_service", fromlist=["_lock_versions"]
        )._lock_versions) as lock_versions:
            create_task(self.db, request)

        lock_versions.assert_called_once_with(self.db, [self.complete_ids[0]])

    def test_sft_accepts_base_model_and_rejects_invalid_cpt_adapters(self):
        cpt = self._task(task_type="cpt", state="succeeded")
        final_adapter = self._artifact(cpt)
        wrong_type = self._artifact(cpt, artifact_type="checkpoint")
        deleted = self._artifact(cpt, deleted_at=datetime.utcnow())
        failed_cpt = self._task(task_type="cpt", state="failed")
        failed_adapter = self._artifact(failed_cpt)
        sft = self._task(task_type="sft", state="succeeded")
        sft_adapter = self._artifact(sft)

        base_sft = create_task(self.db, self._request(task_type="sft"))
        self.assertIsNone(base_sft.cpt_adapter_artifact_id)

        cases = (
            (wrong_type.id, TrainingTaskConflict, "not a final adapter"),
            (deleted.id, TrainingTaskNotFound, "not available"),
            (failed_adapter.id, TrainingTaskConflict, "succeeded CPT"),
            (sft_adapter.id, TrainingTaskConflict, "succeeded CPT"),
            ("missing-adapter", TrainingTaskNotFound, "not available"),
        )
        for artifact_id, error_type, message in cases:
            with self.subTest(artifact_id=artifact_id):
                with self.assertRaisesRegex(error_type, message):
                    create_task(self.db, self._request(task_type="sft", cpt_adapter_artifact_id=artifact_id))

        task = create_task(
            self.db, self._request(task_type="sft", cpt_adapter_artifact_id=final_adapter.id)
        )
        self.assertEqual(task.cpt_adapter_artifact_id, final_adapter.id)

    def test_create_task_leaves_no_partial_task_when_caller_rolls_back_after_second_stage_failure(self):
        request = self._request(test_version_ids=[self.complete_ids[0]])

        with patch(
            "app.services.training_task_service._add_split_rows",
            side_effect=RuntimeError("split row insertion failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "split row insertion failed"):
                create_task(self.db, request)

        self.assertEqual(self.db.query(TrainingTask).count(), 1)
        self.db.rollback()
        self.assertEqual(self.db.query(TrainingTask).count(), 0)
        self.assertEqual(self.db.query(TrainingTaskTest).count(), 0)

    def test_create_task_rejects_a_journaled_cpt_adapter_as_not_found(self):
        cpt = self._task(task_type="cpt", state="succeeded")
        adapter = self._artifact(cpt)
        adapter.deletion_state = "recovery_required"
        adapter.quarantine_name = f"{adapter.id}-{'a' * 32}"
        adapter.deletion_updated_at = datetime.utcnow()

        with self.assertRaisesRegex(TrainingTaskNotFound, "CPT adapter"):
            create_task(self.db, self._request(task_type="sft", cpt_adapter_artifact_id=adapter.id))

    def test_cancel_transitions_queued_and_active_tasks_and_synchronizes_evaluation(self):
        queued = self._task(state="queued")
        active = self._task(state="training")
        evaluation = self._task(task_type="sft", job_kind="evaluation", state="evaluating")
        source = self._task(task_type="sft", state="succeeded")
        self.db.add(TrainingEvaluation(task_id=evaluation.id, source_sft_task_id=source.id, status="evaluating"))
        self.db.flush()

        cancel_task(self.db, queued.id)
        cancel_task(self.db, active.id)
        cancel_task(self.db, evaluation.id)

        self.assertEqual(queued.state, "cancelled")
        self.assertIsNotNone(queued.completed_at)
        self.assertEqual(active.state, "cancelling")
        self.assertIsNotNone(active.cancel_requested_at)
        evaluation_row = self.db.query(TrainingEvaluation).filter_by(task_id=evaluation.id).one()
        self.assertEqual(evaluation.state, "cancelling")
        self.assertEqual(evaluation_row.status, "cancelling")
        for task in (queued, active):
            with self.subTest(state=task.state):
                with self.assertRaisesRegex(TrainingTaskConflict, "cannot be cancelled"):
                    cancel_task(self.db, task.id)
        with self.assertRaises(TrainingTaskNotFound):
            cancel_task(self.db, "missing")

    def test_retry_clones_frozen_data_config_and_lineage_with_valid_checkpoint(self):
        original = self._task(state="failed", config_snapshot={"preset": "formal", "training": {"learning_rate": 0.001}, "qlora": {}})
        self._add_task_tests(original, [(self.complete_ids[0], "train"), (self.complete_ids[1], "test")])
        checkpoint = self._artifact(original, artifact_type="checkpoint")

        retry = retry_task(self.db, original.id, resume_checkpoint_artifact_id=checkpoint.id)

        self.assertEqual(retry.state, "queued")
        self.assertEqual(retry.parent_task_id, original.id)
        self.assertEqual(retry.resume_checkpoint_artifact_id, checkpoint.id)
        self.assertEqual(retry.config_snapshot, original.config_snapshot)
        self.assertIsNot(retry.config_snapshot, original.config_snapshot)
        self.assertEqual(
            {(row.test_version_id, row.split_name) for row in self.db.query(TrainingTaskTest).filter_by(task_id=retry.id)},
            {(self.complete_ids[0], "train"), (self.complete_ids[1], "test")},
        )

    def test_retry_rejects_non_terminal_sources_and_invalid_checkpoint(self):
        running = self._task(state="training")
        failed = self._task(state="failed")
        other = self._task(state="failed")
        wrong_checkpoint = self._artifact(other, artifact_type="checkpoint")
        final_adapter = self._artifact(failed)
        deleted_checkpoint = self._artifact(
            failed,
            artifact_type="checkpoint",
            deleted_at=datetime.utcnow(),
        )

        with self.assertRaisesRegex(TrainingTaskConflict, "cannot be retried"):
            retry_task(self.db, running.id)
        with self.assertRaisesRegex(TrainingTaskConflict, "does not belong"):
            retry_task(self.db, failed.id, resume_checkpoint_artifact_id=wrong_checkpoint.id)
        with self.assertRaisesRegex(TrainingTaskConflict, "not a checkpoint"):
            retry_task(self.db, failed.id, resume_checkpoint_artifact_id=final_adapter.id)
        with self.assertRaises(TrainingTaskNotFound):
            retry_task(self.db, failed.id, resume_checkpoint_artifact_id="missing-checkpoint")
        with self.assertRaisesRegex(TrainingTaskNotFound, "not available"):
            retry_task(self.db, failed.id, resume_checkpoint_artifact_id=deleted_checkpoint.id)

    def test_retry_rejects_a_journaled_checkpoint_as_not_found(self):
        failed = self._task(state="failed")
        checkpoint = self._artifact(failed, artifact_type="checkpoint")
        checkpoint.deletion_state = "recovery_required"
        checkpoint.quarantine_name = f"{checkpoint.id}-{'b' * 32}"
        checkpoint.deletion_updated_at = datetime.utcnow()

        with self.assertRaisesRegex(TrainingTaskNotFound, "checkpoint"):
            retry_task(self.db, failed.id, resume_checkpoint_artifact_id=checkpoint.id)

    def test_create_evaluation_only_uses_succeeded_sft_test_split(self):
        source = self._task(task_type="sft", state="succeeded")
        self._add_task_tests(
            source,
            [
                (self.complete_ids[0], "train"),
                (self.complete_ids[1], "validation"),
                (self.complete_ids[2], "test"),
                (self.complete_ids[3], "test"),
            ],
        )

        evaluation = create_evaluation(self.db, source.id)
        evaluation_row = self.db.query(TrainingEvaluation).filter_by(task_id=evaluation.id).one()

        self.assertEqual(evaluation.task_type, "sft")
        self.assertEqual(evaluation.job_kind, "evaluation")
        self.assertEqual(evaluation.parent_task_id, source.id)
        self.assertEqual(evaluation.state, "queued")
        self.assertEqual(evaluation_row.source_sft_task_id, source.id)
        self.assertEqual(evaluation_row.status, "queued")
        self.assertEqual(
            {(row.test_version_id, row.split_name) for row in self.db.query(TrainingTaskTest).filter_by(task_id=evaluation.id)},
            {(self.complete_ids[2], "test"), (self.complete_ids[3], "test")},
        )

    def test_evaluation_rejects_non_succeeded_or_non_sft_tasks_and_stays_synchronized(self):
        failed_sft = self._task(task_type="sft", state="failed")
        cpt = self._task(task_type="cpt", state="succeeded")
        succeeded_sft = self._task(task_type="sft", state="succeeded")
        self._add_task_tests(succeeded_sft, [(self.complete_ids[0], "test")])

        for source in (failed_sft, cpt):
            with self.subTest(source=source.id):
                with self.assertRaisesRegex(TrainingTaskConflict, "succeeded SFT"):
                    create_evaluation(self.db, source.id)

        evaluation = create_evaluation(self.db, succeeded_sft.id)
        synchronize_evaluation(self.db, evaluation.id, state="preparing_data")
        synchronize_evaluation(self.db, evaluation.id, state="evaluating")
        synchronize_evaluation(
            self.db,
            evaluation.id,
            state="succeeded",
            summary={"accuracy": 0.9},
        )
        row = self.db.query(TrainingEvaluation).filter_by(task_id=evaluation.id).one()
        self.assertEqual(evaluation.state, "succeeded")
        self.assertEqual(row.status, "succeeded")
        self.assertEqual(row.summary, {"accuracy": 0.9})
        self.assertIsNotNone(row.completed_at)

    def test_evaluation_accepts_only_the_normal_transition_path(self):
        source = self._task(task_type="sft", state="succeeded")
        self._add_task_tests(source, [(self.complete_ids[0], "test")])
        evaluation = create_evaluation(self.db, source.id)

        with self.assertRaisesRegex(TrainingTaskConflict, "invalid evaluation transition"):
            synchronize_evaluation(self.db, evaluation.id, state="evaluating")

        synchronize_evaluation(self.db, evaluation.id, state="preparing_data")
        synchronize_evaluation(self.db, evaluation.id, state="evaluating")
        synchronize_evaluation(self.db, evaluation.id, state="succeeded", summary={"accuracy": 0.9})
        synchronize_evaluation(self.db, evaluation.id, state="succeeded")
        row = self.db.query(TrainingEvaluation).filter_by(task_id=evaluation.id).one()
        self.assertEqual((evaluation.state, row.status), ("succeeded", "succeeded"))
        self.assertEqual(row.summary, {"accuracy": 0.9})
        with self.assertRaisesRegex(TrainingTaskConflict, "invalid evaluation transition"):
            synchronize_evaluation(self.db, evaluation.id, state="evaluating")

    def test_preparing_evaluation_can_fail_or_be_interrupted_with_synchronized_error(self):
        source = self._task(task_type="sft", state="succeeded")
        self._add_task_tests(source, [(self.complete_ids[0], "test")])

        for state in ("failed", "interrupted"):
            with self.subTest(state=state):
                evaluation = create_evaluation(self.db, source.id)
                synchronize_evaluation(self.db, evaluation.id, state="preparing_data")
                result = synchronize_evaluation(
                    self.db,
                    evaluation.id,
                    state=state,
                    error_message=f"{state} while preparing data",
                )
                row = self.db.query(TrainingEvaluation).filter_by(task_id=evaluation.id).one()
                self.assertIs(result, evaluation)
                self.assertEqual((evaluation.state, row.status), (state, state))
                self.assertIsNotNone(evaluation.completed_at)
                self.assertIsNotNone(row.completed_at)
                self.assertEqual(evaluation.error_message, f"{state} while preparing data")
                self.assertEqual(row.error_message, f"{state} while preparing data")

    def test_stale_cancelled_evaluation_cannot_be_reactivated(self):
        source = self._task(task_type="sft", state="succeeded")
        self._add_task_tests(source, [(self.complete_ids[0], "test")])
        evaluation = create_evaluation(self.db, source.id)

        cancel_task(self.db, evaluation.id)

        for state in ("preparing_data", "evaluating", "succeeded"):
            with self.subTest(state=state):
                with self.assertRaisesRegex(TrainingTaskConflict, "invalid evaluation transition"):
                    synchronize_evaluation(self.db, evaluation.id, state=state)

    def test_stale_cancelling_evaluation_can_only_finish_terminally(self):
        source = self._task(task_type="sft", state="succeeded")
        self._add_task_tests(source, [(self.complete_ids[0], "test")])

        for target in ("cancelled", "failed", "interrupted"):
            with self.subTest(target=target):
                evaluation = create_evaluation(self.db, source.id)
                synchronize_evaluation(self.db, evaluation.id, state="preparing_data")
                synchronize_evaluation(self.db, evaluation.id, state="evaluating")
                cancel_task(self.db, evaluation.id)
                synchronize_evaluation(self.db, evaluation.id, state=target)
                self.assertEqual(evaluation.state, target)

        evaluation = create_evaluation(self.db, source.id)
        synchronize_evaluation(self.db, evaluation.id, state="preparing_data")
        synchronize_evaluation(self.db, evaluation.id, state="evaluating")
        cancel_task(self.db, evaluation.id)
        for state in ("preparing_data", "evaluating", "succeeded"):
            with self.subTest(state=state):
                with self.assertRaisesRegex(TrainingTaskConflict, "invalid evaluation transition"):
                    synchronize_evaluation(self.db, evaluation.id, state=state)

    def test_retry_evaluation_creates_a_new_synchronized_evaluation_record(self):
        source = self._task(task_type="sft", state="succeeded")
        evaluation = self._task(task_type="sft", state="failed", job_kind="evaluation")
        evaluation.parent_task_id = source.id
        self._add_task_tests(evaluation, [(self.complete_ids[0], "test")])
        self.db.add(TrainingEvaluation(task_id=evaluation.id, source_sft_task_id=source.id, status="failed"))
        self.db.flush()

        retry = retry_task(self.db, evaluation.id)
        retry_row = self.db.query(TrainingEvaluation).filter_by(task_id=retry.id).one()

        self.assertEqual(retry.job_kind, "evaluation")
        self.assertEqual(retry.parent_task_id, evaluation.id)
        self.assertEqual(retry_row.source_sft_task_id, source.id)
        self.assertEqual(retry_row.status, "queued")


if __name__ == "__main__":
    unittest.main()
