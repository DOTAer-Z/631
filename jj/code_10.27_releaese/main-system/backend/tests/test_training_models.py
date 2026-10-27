import importlib.util
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from sqlalchemy import BigInteger, CheckConstraint, create_engine
from sqlalchemy.exc import IntegrityError

from app.models.case import Case
from app.models.dataset_import import DatasetImport
from app.models.run import Run
from app.models.training_data import (
    IMPORT_ITEM_STATUS,
    TEST_COMPLETENESS,
    TrainingImportItem,
    TrainingTest,
    TrainingTestLog,
    TrainingTestVersion,
)
from app.models.training_task import (
    ARTIFACT_DELETION_STATES,
    ARTIFACT_TYPES,
    JOB_KINDS,
    SPLIT_NAMES,
    TASK_STATES,
    TASK_TYPES,
    TrainingArtifact,
    TrainingEvaluation,
    TrainingMetric,
    TrainingTask,
    TrainingTaskTest,
    TrainingWorker,
)
from app.models import training_task as training_task_models
from app.schemas.model_training import TrainingEvaluationOut


MIGRATION_0010_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0010_add_external_sft_evaluation_source.py"
)


def _load_migration_0010():
    spec = importlib.util.spec_from_file_location("migration_0010", MIGRATION_0010_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _constraint_names(table):
    return {constraint.name for constraint in table.constraints}


def _check_sql(table):
    return {str(constraint.sqltext) for constraint in table.constraints if isinstance(constraint, CheckConstraint)}


def _foreign_key_ondelete(table, column_name):
    foreign_keys = table.columns[column_name].foreign_keys
    assert len(foreign_keys) == 1
    return next(iter(foreign_keys)).ondelete


class TrainingDataModelTests(unittest.TestCase):
    def test_training_test_tables_define_immutable_version_contract(self):
        self.assertEqual(TrainingTest.__tablename__, "training_tests")
        self.assertEqual(TrainingTestVersion.__tablename__, "training_test_versions")
        self.assertEqual(TrainingTestLog.__tablename__, "training_test_logs")
        self.assertEqual(TrainingImportItem.__tablename__, "training_import_items")

        constraints = _constraint_names(TrainingTestVersion.__table__)
        self.assertIn("uq_training_test_version_number", constraints)
        self.assertIn("uq_training_test_content", constraints)
        self.assertFalse(TrainingTestVersion.__table__.columns["ground_truth"].nullable)
        self.assertFalse(TrainingTestVersion.__table__.columns["fip_info"].nullable)
        self.assertEqual(_foreign_key_ondelete(TrainingTestVersion.__table__, "test_id"), "CASCADE")
        self.assertEqual(_foreign_key_ondelete(TrainingTest.__table__, "latest_version_id"), "SET NULL")

    def test_training_data_uses_required_string_check_constraints(self):
        self.assertIn(
            "completeness IN ('complete', 'incomplete')",
            _check_sql(TrainingTestVersion.__table__),
        )
        self.assertIn(
            "status IN ('imported', 'duplicate', 'incomplete', 'failed')",
            _check_sql(TrainingImportItem.__table__),
        )
        self.assertEqual(TEST_COMPLETENESS, ("complete", "incomplete"))
        self.assertEqual(IMPORT_ITEM_STATUS, ("imported", "duplicate", "incomplete", "failed"))

    def test_training_logs_are_versioned_and_limited_to_training_inputs(self):
        constraints = _constraint_names(TrainingTestLog.__table__)
        self.assertIn("uq_training_test_log_round_type", constraints)
        checks = _check_sql(TrainingTestLog.__table__)
        self.assertIn("round_no IN (1, 2)", checks)
        self.assertIn(
            "log_type IN ('qemu_console', 'fault_events', 'system_metrics')",
            checks,
        )
        self.assertNotIn("foreground_wl", " ".join(checks))
        self.assertEqual(_foreign_key_ondelete(TrainingTestLog.__table__, "test_version_id"), "CASCADE")

    def test_training_data_indexes_match_migration_for_schema_bootstrap(self):
        self.assertIn(
            "idx_training_test_versions_test_id",
            {index.name for index in TrainingTestVersion.__table__.indexes},
        )
        self.assertIn(
            "idx_training_import_items_import_id",
            {index.name for index in TrainingImportItem.__table__.indexes},
        )

    def test_existing_ingestion_rows_can_reference_a_training_version(self):
        for table in (Case.__table__, Run.__table__):
            column = table.columns["test_version_id"]
            self.assertTrue(column.nullable)
            self.assertEqual(_foreign_key_ondelete(table, "test_version_id"), "SET NULL")

        for name in [
            "training_complete_count",
            "training_incomplete_count",
            "training_duplicate_count",
            "training_failed_count",
            "training_parse_failed_count",
        ]:
            self.assertIn(name, DatasetImport.__table__.columns)


class TrainingTaskModelTests(unittest.TestCase):
    def test_training_task_tables_have_required_id_and_frozen_split_contracts(self):
        self.assertEqual(TrainingTask.__tablename__, "training_tasks")
        self.assertEqual(TrainingTaskTest.__tablename__, "training_task_tests")
        self.assertEqual(TrainingEvaluation.__tablename__, "training_evaluations")
        self.assertEqual(TrainingMetric.__tablename__, "training_metrics")
        self.assertEqual(TrainingArtifact.__tablename__, "training_artifacts")
        self.assertEqual(TrainingWorker.__tablename__, "training_workers")
        self.assertEqual(TrainingTask.__table__.columns["id"].type.length, 36)
        self.assertEqual(TrainingArtifact.__table__.columns["id"].type.length, 36)

        constraints = _constraint_names(TrainingTaskTest.__table__)
        self.assertIn("uq_training_task_test_version", constraints)
        self.assertIn("split_name IN ('train', 'validation', 'test')", _check_sql(TrainingTaskTest.__table__))
        self.assertEqual(_foreign_key_ondelete(TrainingTaskTest.__table__, "task_id"), "CASCADE")
        self.assertEqual(_foreign_key_ondelete(TrainingTaskTest.__table__, "test_version_id"), "RESTRICT")

    def test_training_task_uses_required_string_check_constraints_and_optional_lineage(self):
        checks = _check_sql(TrainingTask.__table__)
        self.assertIn("task_type IN ('cpt', 'sft')", checks)
        self.assertIn("job_kind IN ('training', 'evaluation')", checks)
        self.assertIn(
            "state IN ('queued', 'preparing_data', 'training', 'evaluating', 'cancelling', 'cancelled', 'succeeded', 'failed', 'interrupted')",
            checks,
        )
        self.assertEqual(TASK_TYPES, ("cpt", "sft"))
        self.assertEqual(JOB_KINDS, ("training", "evaluation"))
        self.assertEqual(
            TASK_STATES,
            (
                "queued", "preparing_data", "training", "evaluating", "cancelling",
                "cancelled", "succeeded", "failed", "interrupted",
            ),
        )
        self.assertEqual(SPLIT_NAMES, ("train", "validation", "test"))

        for name in [
            "cpt_adapter_artifact_id",
            "parent_task_id",
            "deleted_at",
            "cancel_requested_at",
            "resume_checkpoint_artifact_id",
        ]:
            self.assertTrue(TrainingTask.__table__.columns[name].nullable)

    def test_evaluation_has_exactly_one_task_or_artifact_source(self):
        self.assertTrue(TrainingEvaluation.__table__.columns["task_id"].unique)
        columns = TrainingEvaluation.__table__.columns
        self.assertTrue(columns["source_sft_task_id"].nullable)
        self.assertTrue(columns["source_sft_artifact_id"].nullable)
        self.assertEqual(
            _foreign_key_ondelete(TrainingEvaluation.__table__, "source_sft_task_id"),
            "RESTRICT",
        )
        self.assertEqual(
            _foreign_key_ondelete(TrainingEvaluation.__table__, "source_sft_artifact_id"),
            "RESTRICT",
        )
        self.assertIn(
            "idx_training_evaluations_source_sft_artifact_id",
            {index.name for index in TrainingEvaluation.__table__.indexes},
        )
        self.assertIn(
            "ck_training_evaluations_exactly_one_source",
            _constraint_names(TrainingEvaluation.__table__),
        )
        self.assertIn(
            "(source_sft_task_id IS NOT NULL AND source_sft_artifact_id IS NULL) OR "
            "(source_sft_task_id IS NULL AND source_sft_artifact_id IS NOT NULL)",
            _check_sql(TrainingEvaluation.__table__),
        )

    def test_evaluation_xor_constraint_rejects_zero_or_two_sources(self):
        engine = create_engine("sqlite:///:memory:")
        created_at = datetime(2026, 7, 26)
        TrainingTask.__table__.create(engine)
        TrainingArtifact.__table__.create(engine)
        TrainingEvaluation.__table__.create(engine)
        with engine.begin() as connection:
            for number in range(1, 5):
                connection.execute(
                    TrainingTask.__table__.insert().values(
                        id=f"task-{number}",
                        name=f"task {number}",
                        task_type="sft",
                        job_kind="evaluation" if number > 1 else "training",
                        state="queued" if number > 1 else "succeeded",
                        model_id="Qwen/Qwen3.5-9B",
                        config_snapshot={},
                        queued_at=created_at,
                    )
                )
            connection.execute(
                TrainingArtifact.__table__.insert().values(
                    id="artifact-1",
                    task_id=None,
                    artifact_type="final_adapter",
                    relative_path="imports/artifact-1/final_adapter.tar",
                    created_at=created_at,
                )
            )

        invalid_sources = (
            {"source_sft_task_id": None, "source_sft_artifact_id": None},
            {"source_sft_task_id": "task-1", "source_sft_artifact_id": "artifact-1"},
        )
        for offset, sources in enumerate(invalid_sources, start=2):
            with engine.begin() as connection, self.assertRaises(IntegrityError):
                connection.execute(
                    TrainingEvaluation.__table__.insert().values(
                        task_id=f"task-{offset}",
                        status="queued",
                        created_at=created_at,
                        **sources,
                    )
                )

        with engine.begin() as connection:
            connection.execute(
                TrainingEvaluation.__table__.insert().values(
                    task_id="task-2",
                    source_sft_task_id="task-1",
                    source_sft_artifact_id=None,
                    status="queued",
                    created_at=created_at,
                )
            )
            connection.execute(
                TrainingEvaluation.__table__.insert().values(
                    task_id="task-3",
                    source_sft_task_id=None,
                    source_sft_artifact_id="artifact-1",
                    status="queued",
                    created_at=created_at,
                )
            )

    def test_evaluation_response_schema_has_optional_safe_source_ids(self):
        fields = TrainingEvaluationOut.model_fields
        self.assertIsNone(fields["source_sft_task_id"].default)
        self.assertIsNone(fields["source_sft_artifact_id"].default)
        payload = TrainingEvaluationOut(status="queued")
        self.assertIsNone(payload.source_sft_task_id)
        self.assertIsNone(payload.source_sft_artifact_id)

    def test_artifact_paths_are_relative_and_task_reference_can_be_cleared(self):
        checks = _check_sql(TrainingArtifact.__table__)
        self.assertIn("relative_path NOT LIKE '/%'", checks)
        self.assertIn("relative_path NOT LIKE '../%'", checks)
        self.assertEqual(_foreign_key_ondelete(TrainingArtifact.__table__, "task_id"), "SET NULL")
        self.assertIn(
            "artifact_type IN ('final_adapter', 'checkpoint', 'tokenizer', 'config', 'split', 'dataset', 'log', 'evaluation_report')",
            checks,
        )
        self.assertEqual(
            ARTIFACT_TYPES,
            (
                "final_adapter", "checkpoint", "tokenizer", "config", "split",
                "dataset", "log", "evaluation_report",
            ),
        )

    def test_artifact_deletion_journal_has_exact_columns_and_constraints(self):
        columns = TrainingArtifact.__table__.columns
        self.assertEqual(columns["deletion_state"].type.length, 32)
        self.assertTrue(columns["deletion_state"].nullable)
        self.assertEqual(columns["quarantine_name"].type.length, 128)
        self.assertTrue(columns["quarantine_name"].nullable)
        self.assertTrue(columns["deletion_updated_at"].nullable)
        self.assertEqual(
            ARTIFACT_DELETION_STATES,
            ("staged", "pending_cleanup", "recovery_required", "cleaned"),
        )

        checks = _check_sql(TrainingArtifact.__table__)
        self.assertIn(
            "deletion_state IS NULL OR deletion_state IN ('staged', 'pending_cleanup', 'recovery_required', 'cleaned')",
            checks,
        )
        self.assertIn(
            "(deletion_state IS NULL AND quarantine_name IS NULL AND deletion_updated_at IS NULL) OR "
            "(deletion_state = 'recovery_required' AND quarantine_name IS NOT NULL AND deletion_updated_at IS NOT NULL) OR "
            "(deletion_state IN ('staged', 'pending_cleanup') AND deleted_at IS NOT NULL AND quarantine_name IS NOT NULL AND deletion_updated_at IS NOT NULL) OR "
            "(deletion_state = 'cleaned' AND deleted_at IS NOT NULL AND quarantine_name IS NULL AND deletion_updated_at IS NOT NULL)",
            checks,
        )
        self.assertIn(
            "quarantine_name IS NULL OR (quarantine_name NOT LIKE '%/%' AND quarantine_name NOT IN ('', '.', '..'))",
            checks,
        )

    def test_training_task_indexes_match_migration_for_schema_bootstrap(self):
        expected = {
            TrainingTask: "idx_training_tasks_state_queued_at",
            TrainingTaskTest: "idx_training_task_tests_test_version_id",
            TrainingMetric: "idx_training_metrics_task_id",
            TrainingArtifact: "idx_training_artifacts_task_id",
        }
        for model, index_name in expected.items():
            self.assertIn(index_name, {index.name for index in model.__table__.indexes})

    def test_training_metrics_have_durable_unique_stream_offsets(self):
        columns = TrainingMetric.__table__.columns
        self.assertIsInstance(columns["stream_offset"].type, BigInteger)
        self.assertFalse(columns["stream_offset"].nullable)
        self.assertIn(
            "uq_training_metrics_task_stream_offset",
            _constraint_names(TrainingMetric.__table__),
        )

    def test_training_tasks_have_a_durable_output_cleanup_journal(self):
        columns = TrainingTask.__table__.columns
        self.assertEqual(columns["cleanup_state"].type.length, 32)
        self.assertTrue(columns["cleanup_state"].nullable)
        self.assertTrue(columns["cleanup_paths"].nullable)
        self.assertTrue(columns["cleanup_updated_at"].nullable)
        self.assertEqual(
            training_task_models.TASK_CLEANUP_STATES,
            ("pending", "cleaned"),
        )
        checks = _check_sql(TrainingTask.__table__)
        self.assertIn(
            "cleanup_state IS NULL OR cleanup_state IN ('pending', 'cleaned')",
            checks,
        )
        self.assertIn(
            "(cleanup_state IS NULL AND cleanup_paths IS NULL AND cleanup_updated_at IS NULL) OR "
            "(cleanup_state IN ('pending', 'cleaned') AND cleanup_paths IS NOT NULL AND cleanup_updated_at IS NOT NULL)",
            checks,
        )


class ExternalEvaluationSourceMigrationTests(unittest.TestCase):
    def test_upgrade_has_exact_source_operations_and_preserves_existing_values(self):
        migration = _load_migration_0010()
        self.assertEqual(migration.revision, "0010")
        self.assertEqual(migration.down_revision, "0009")

        with patch.object(migration.op, "alter_column") as alter_column, patch.object(
            migration.op, "add_column"
        ) as add_column, patch.object(
            migration.op, "create_foreign_key"
        ) as create_foreign_key, patch.object(
            migration.op, "create_index"
        ) as create_index, patch.object(
            migration.op, "create_check_constraint"
        ) as create_check_constraint, patch.object(
            migration.op, "execute"
        ) as execute:
            migration.upgrade()

        alter_column.assert_called_once()
        self.assertEqual(
            alter_column.call_args.args,
            ("training_evaluations", "source_sft_task_id"),
        )
        self.assertTrue(alter_column.call_args.kwargs["nullable"])
        column = add_column.call_args.args[1]
        self.assertEqual(add_column.call_args.args[0], "training_evaluations")
        self.assertEqual(column.name, "source_sft_artifact_id")
        self.assertEqual(column.type.length, 36)
        self.assertTrue(column.nullable)
        create_foreign_key.assert_called_once_with(
            "fk_training_evaluations_source_sft_artifact",
            "training_evaluations",
            "training_artifacts",
            ["source_sft_artifact_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        create_index.assert_called_once_with(
            "idx_training_evaluations_source_sft_artifact_id",
            "training_evaluations",
            ["source_sft_artifact_id"],
        )
        create_check_constraint.assert_called_once_with(
            "ck_training_evaluations_exactly_one_source",
            "training_evaluations",
            "(source_sft_task_id IS NOT NULL AND source_sft_artifact_id IS NULL) OR "
            "(source_sft_task_id IS NULL AND source_sft_artifact_id IS NOT NULL)",
        )
        execute.assert_not_called()

    def test_downgrade_refuses_external_rows_before_schema_changes(self):
        migration = _load_migration_0010()
        result = unittest.mock.MagicMock()
        result.scalar_one.return_value = 1
        connection = unittest.mock.MagicMock()
        connection.execute.return_value = result
        with patch.object(migration.context, "is_offline_mode", return_value=False), patch.object(
            migration.op, "get_bind", return_value=connection
        ), patch.object(migration.op, "drop_constraint") as drop_constraint, self.assertRaisesRegex(
            RuntimeError,
            "^cannot downgrade while external SFT evaluations exist$",
        ):
            migration.downgrade()

        query = str(connection.execute.call_args.args[0])
        self.assertEqual(
            query,
            "SELECT count(*) FROM training_evaluations "
            "WHERE source_sft_artifact_id IS NOT NULL",
        )
        drop_constraint.assert_not_called()

    def test_downgrade_without_external_rows_restores_old_task_source(self):
        migration = _load_migration_0010()
        result = unittest.mock.MagicMock()
        result.scalar_one.return_value = 0
        connection = unittest.mock.MagicMock()
        connection.execute.return_value = result
        with patch.object(migration.context, "is_offline_mode", return_value=False), patch.object(
            migration.op, "get_bind", return_value=connection
        ), patch.object(migration.op, "drop_constraint") as drop_constraint, patch.object(
            migration.op, "drop_index"
        ) as drop_index, patch.object(
            migration.op, "drop_column"
        ) as drop_column, patch.object(
            migration.op, "alter_column"
        ) as alter_column:
            migration.downgrade()

        self.assertEqual(
            [call.args for call in drop_constraint.call_args_list],
            [
                (
                    "ck_training_evaluations_exactly_one_source",
                    "training_evaluations",
                ),
                (
                    "fk_training_evaluations_source_sft_artifact",
                    "training_evaluations",
                ),
            ],
        )
        self.assertEqual(drop_constraint.call_args_list[0].kwargs, {"type_": "check"})
        self.assertEqual(drop_constraint.call_args_list[1].kwargs, {"type_": "foreignkey"})
        drop_index.assert_called_once_with(
            "idx_training_evaluations_source_sft_artifact_id",
            table_name="training_evaluations",
        )
        drop_column.assert_called_once_with(
            "training_evaluations",
            "source_sft_artifact_id",
        )
        alter_column.assert_called_once()
        self.assertEqual(
            alter_column.call_args.args,
            ("training_evaluations", "source_sft_task_id"),
        )
        self.assertFalse(alter_column.call_args.kwargs["nullable"])

    def test_revision_renders_postgresql_upgrade_and_guarded_downgrade_sql(self):
        backend_root = Path(__file__).resolve().parents[1]
        environment = dict(os.environ)
        environment["DATABASE_URL"] = (
            "postgresql+psycopg2://offline:offline@localhost/offline"
        )
        upgrade = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0009:0010", "--sql"],
            cwd=backend_root,
            env=environment,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn(
            "ALTER TABLE training_evaluations ALTER COLUMN source_sft_task_id DROP NOT NULL",
            upgrade.stdout,
        )
        self.assertIn(
            "FOREIGN KEY(source_sft_artifact_id) REFERENCES training_artifacts (id) ON DELETE RESTRICT",
            upgrade.stdout,
        )
        self.assertIn(
            "CONSTRAINT ck_training_evaluations_exactly_one_source CHECK",
            upgrade.stdout,
        )

        downgrade = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "0010:0009", "--sql"],
            cwd=backend_root,
            env=environment,
            text=True,
            capture_output=True,
            check=True,
        )
        guard_index = downgrade.stdout.index(
            "cannot downgrade while external SFT evaluations exist"
        )
        drop_index = downgrade.stdout.index(
            "DROP CONSTRAINT ck_training_evaluations_exactly_one_source"
        )
        self.assertLess(guard_index, drop_index)


if __name__ == "__main__":
    unittest.main()
