import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import create_tables


MIGRATION_0005_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0005_add_model_training.py"
)
MIGRATION_0006_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0006_add_training_artifact_deletion_journal.py"
)
MIGRATION_0007_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0007_add_training_metric_stream_offset.py"
)
MIGRATION_0008_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0008_add_training_task_cleanup_journal.py"
)


def _load_migration_module(path=MIGRATION_0005_PATH):
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TrainingMigrationTests(unittest.TestCase):
    def test_migration_revision_and_required_schema_operations(self):
        migration = _load_migration_module()

        self.assertEqual(migration.revision, "0005")
        self.assertEqual(migration.down_revision, "0004")

        with patch.object(migration.op, "create_table") as create_table, patch.object(
            migration.op, "add_column"
        ) as add_column, patch.object(migration.op, "create_index") as create_index, patch.object(
            migration.op, "create_foreign_key"
        ) as create_foreign_key:
            migration.upgrade()

        created_tables = [call.args[0] for call in create_table.call_args_list]
        self.assertEqual(
            created_tables,
            [
                "training_tests",
                "training_test_versions",
                "training_test_logs",
                "training_import_items",
                "training_tasks",
                "training_task_tests",
                "training_evaluations",
                "training_metrics",
                "training_artifacts",
                "training_workers",
            ],
        )
        added_columns = {(call.args[0], call.args[1].name) for call in add_column.call_args_list}
        self.assertTrue(
            {
                ("training_tests", "latest_version_id"),
                ("cases", "test_version_id"),
                ("runs", "test_version_id"),
                ("dataset_imports", "training_complete_count"),
                ("dataset_imports", "training_incomplete_count"),
                ("dataset_imports", "training_duplicate_count"),
                ("dataset_imports", "training_failed_count"),
                ("dataset_imports", "training_parse_failed_count"),
            }.issubset(added_columns)
        )
        self.assertGreaterEqual(create_index.call_count, 1)
        self.assertTrue(any(call.args[0] == "fk_training_tests_latest_version" for call in create_foreign_key.call_args_list))

    def test_downgrade_removes_only_training_schema_additions(self):
        migration = _load_migration_module()

        with patch.object(migration.op, "drop_table") as drop_table, patch.object(
            migration.op, "drop_column"
        ) as drop_column, patch.object(migration.op, "drop_constraint") as drop_constraint:
            migration.downgrade()

        self.assertEqual(
            [call.args[0] for call in drop_table.call_args_list],
            [
                "training_workers",
                "training_artifacts",
                "training_metrics",
                "training_evaluations",
                "training_task_tests",
                "training_tasks",
                "training_import_items",
                "training_test_logs",
                "training_test_versions",
                "training_tests",
            ],
        )
        self.assertTrue(
            {
                ("cases", "test_version_id"),
                ("runs", "test_version_id"),
                ("dataset_imports", "training_complete_count"),
                ("dataset_imports", "training_incomplete_count"),
                ("dataset_imports", "training_duplicate_count"),
                ("dataset_imports", "training_failed_count"),
                ("dataset_imports", "training_parse_failed_count"),
                ("training_tests", "latest_version_id"),
            }.issubset({call.args for call in drop_column.call_args_list})
        )
        self.assertTrue(any(call.args[0] == "fk_training_tests_latest_version" for call in drop_constraint.call_args_list))

    def test_schema_bootstrap_does_not_patch_alembic_owned_training_columns(self):
        patch_columns = {(table, column) for table, column, _ddl in create_tables._PATCH_COLUMNS}
        self.assertFalse(
            {
                ("cases", "test_version_id"),
                ("runs", "test_version_id"),
                ("dataset_imports", "training_complete_count"),
                ("dataset_imports", "training_incomplete_count"),
                ("dataset_imports", "training_duplicate_count"),
                ("dataset_imports", "training_failed_count"),
                ("dataset_imports", "training_parse_failed_count"),
            }
            & patch_columns
        )

    def test_artifact_deletion_journal_migration_has_exact_operations(self):
        migration = _load_migration_module(MIGRATION_0006_PATH)
        self.assertEqual(migration.revision, "0006")
        self.assertEqual(migration.down_revision, "0005")

        with patch.object(migration.op, "add_column") as add_column, patch.object(
            migration.op, "create_check_constraint"
        ) as create_check_constraint:
            migration.upgrade()

        columns = {
            call.args[1].name: call.args[1]
            for call in add_column.call_args_list
            if call.args[0] == "training_artifacts"
        }
        self.assertEqual(set(columns), {"deletion_state", "quarantine_name", "deletion_updated_at"})
        self.assertEqual(columns["deletion_state"].type.length, 32)
        self.assertEqual(columns["quarantine_name"].type.length, 128)
        self.assertTrue(all(column.nullable for column in columns.values()))

        checks = {call.args[0]: call.args[2] for call in create_check_constraint.call_args_list}
        self.assertEqual(
            checks,
            {
                "ck_training_artifacts_deletion_state":
                    "deletion_state IS NULL OR deletion_state IN ('staged', 'pending_cleanup', 'recovery_required', 'cleaned')",
                "ck_training_artifacts_deletion_journal":
                    "(deletion_state IS NULL AND quarantine_name IS NULL AND deletion_updated_at IS NULL) OR "
                    "(deletion_state = 'recovery_required' AND quarantine_name IS NOT NULL AND deletion_updated_at IS NOT NULL) OR "
                    "(deletion_state IN ('staged', 'pending_cleanup') AND deleted_at IS NOT NULL AND quarantine_name IS NOT NULL AND deletion_updated_at IS NOT NULL) OR "
                    "(deletion_state = 'cleaned' AND deleted_at IS NOT NULL AND quarantine_name IS NULL AND deletion_updated_at IS NOT NULL)",
                "ck_training_artifacts_quarantine_name":
                    "quarantine_name IS NULL OR (quarantine_name NOT LIKE '%/%' AND quarantine_name NOT IN ('', '.', '..'))",
            },
        )

    def test_artifact_deletion_journal_downgrade_reverses_exact_operations(self):
        migration = _load_migration_module(MIGRATION_0006_PATH)
        with patch.object(migration.op, "execute") as execute, patch.object(
            migration.op, "drop_constraint"
        ) as drop_constraint, patch.object(
            migration.op, "drop_column"
        ) as drop_column:
            migration.downgrade()

        guard = execute.call_args.args[0]
        self.assertIn("RAISE EXCEPTION", guard)
        self.assertIn("'staged'", guard)
        self.assertIn("'pending_cleanup'", guard)
        self.assertIn("'recovery_required'", guard)
        self.assertNotIn("'cleaned'", guard)

        self.assertEqual(
            [call.args for call in drop_constraint.call_args_list],
            [
                ("ck_training_artifacts_quarantine_name", "training_artifacts"),
                ("ck_training_artifacts_deletion_journal", "training_artifacts"),
                ("ck_training_artifacts_deletion_state", "training_artifacts"),
            ],
        )
        self.assertTrue(all(call.kwargs == {"type_": "check"} for call in drop_constraint.call_args_list))
        self.assertEqual(
            [call.args for call in drop_column.call_args_list],
            [
                ("training_artifacts", "deletion_updated_at"),
                ("training_artifacts", "quarantine_name"),
                ("training_artifacts", "deletion_state"),
            ],
        )

    def test_artifact_deletion_journal_renders_postgresql_offline_sql(self):
        backend_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["DATABASE_URL"] = "postgresql+psycopg2://user:password@localhost/training"
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0005:0006", "--sql"],
            cwd=backend_root,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

        sql = result.stdout
        self.assertIn("ALTER TABLE training_artifacts ADD COLUMN deletion_state VARCHAR(32)", sql)
        self.assertIn("ALTER TABLE training_artifacts ADD COLUMN quarantine_name VARCHAR(128)", sql)
        self.assertIn("ALTER TABLE training_artifacts ADD COLUMN deletion_updated_at TIMESTAMP WITHOUT TIME ZONE", sql)
        self.assertIn("CONSTRAINT ck_training_artifacts_deletion_state CHECK", sql)
        self.assertIn("CONSTRAINT ck_training_artifacts_deletion_journal CHECK", sql)
        self.assertIn("CONSTRAINT ck_training_artifacts_quarantine_name CHECK", sql)

    def test_artifact_deletion_journal_downgrade_renders_guard_before_column_drops(self):
        backend_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["DATABASE_URL"] = "postgresql+psycopg2://user:password@localhost/training"
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "0006:0005", "--sql"],
            cwd=backend_root,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

        sql = result.stdout
        guard_index = sql.index("RAISE EXCEPTION")
        drop_index = sql.index("ALTER TABLE training_artifacts DROP COLUMN deletion_updated_at")
        self.assertLess(guard_index, drop_index)
        self.assertIn("'staged'", sql)
        self.assertIn("'pending_cleanup'", sql)
        self.assertIn("'recovery_required'", sql)

    def test_metric_stream_offset_migration_has_exact_operations(self):
        migration = _load_migration_module(MIGRATION_0007_PATH)
        self.assertEqual(migration.revision, "0007")
        self.assertEqual(migration.down_revision, "0006")

        with patch.object(migration.op, "add_column") as add_column, patch.object(
            migration.op, "execute"
        ) as execute, patch.object(
            migration.op, "alter_column"
        ) as alter_column, patch.object(
            migration.op, "create_unique_constraint"
        ) as create_unique_constraint:
            migration.upgrade()

        column = add_column.call_args.args[1]
        self.assertEqual(add_column.call_args.args[0], "training_metrics")
        self.assertEqual(column.name, "stream_offset")
        self.assertEqual(column.type.__class__.__name__, "BigInteger")
        self.assertTrue(column.nullable)
        self.assertIsNone(column.server_default)
        self.assertIn("-ROW_NUMBER() OVER", execute.call_args.args[0])
        alter_column.assert_called_once()
        self.assertEqual(
            alter_column.call_args.args,
            ("training_metrics", "stream_offset"),
        )
        self.assertFalse(alter_column.call_args.kwargs["nullable"])
        create_unique_constraint.assert_called_once_with(
            "uq_training_metrics_task_stream_offset",
            "training_metrics",
            ["task_id", "stream_offset"],
        )

    def test_metric_stream_offset_downgrade_reverses_exact_operations(self):
        migration = _load_migration_module(MIGRATION_0007_PATH)
        with patch.object(migration.op, "drop_constraint") as drop_constraint, patch.object(
            migration.op, "drop_column"
        ) as drop_column:
            migration.downgrade()

        drop_constraint.assert_called_once_with(
            "uq_training_metrics_task_stream_offset",
            "training_metrics",
            type_="unique",
        )
        drop_column.assert_called_once_with("training_metrics", "stream_offset")

    def test_metric_stream_offset_renders_postgresql_offline_sql(self):
        backend_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["DATABASE_URL"] = "postgresql+psycopg2://user:password@localhost/training"
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0006:0007", "--sql"],
            cwd=backend_root,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn(
            "ALTER TABLE training_metrics ADD COLUMN stream_offset BIGINT",
            result.stdout,
        )
        self.assertIn(
            "ALTER TABLE training_metrics ALTER COLUMN stream_offset SET NOT NULL",
            result.stdout,
        )
        self.assertIn(
            "CONSTRAINT uq_training_metrics_task_stream_offset UNIQUE (task_id, stream_offset)",
            result.stdout,
        )

    def test_task_cleanup_journal_migration_has_exact_reversible_operations(self):
        migration = _load_migration_module(MIGRATION_0008_PATH)
        self.assertEqual(migration.revision, "0008")
        self.assertEqual(migration.down_revision, "0007")

        with patch.object(migration.op, "add_column") as add_column, patch.object(
            migration.op, "create_check_constraint"
        ) as create_check_constraint:
            migration.upgrade()

        columns = {
            call.args[1].name: call.args[1]
            for call in add_column.call_args_list
            if call.args[0] == "training_tasks"
        }
        self.assertEqual(
            set(columns),
            {"cleanup_state", "cleanup_paths", "cleanup_updated_at"},
        )
        self.assertTrue(all(column.nullable for column in columns.values()))
        checks = {
            call.args[0]: call.args[2]
            for call in create_check_constraint.call_args_list
        }
        self.assertEqual(
            checks,
            {
                "ck_training_tasks_cleanup_state":
                    "cleanup_state IS NULL OR cleanup_state IN ('pending', 'cleaned')",
                "ck_training_tasks_cleanup_journal":
                    "(cleanup_state IS NULL AND cleanup_paths IS NULL AND cleanup_updated_at IS NULL) OR "
                    "(cleanup_state IN ('pending', 'cleaned') AND cleanup_paths IS NOT NULL AND cleanup_updated_at IS NOT NULL)",
            },
        )

        with patch.object(migration.op, "execute") as execute, patch.object(
            migration.op, "drop_constraint"
        ) as drop_constraint, patch.object(
            migration.op, "drop_column"
        ) as drop_column:
            migration.downgrade()

        self.assertIn("cleanup_state = 'pending'", execute.call_args.args[0])
        self.assertEqual(
            [call.args for call in drop_constraint.call_args_list],
            [
                ("ck_training_tasks_cleanup_journal", "training_tasks"),
                ("ck_training_tasks_cleanup_state", "training_tasks"),
            ],
        )
        self.assertTrue(
            all(call.kwargs == {"type_": "check"} for call in drop_constraint.call_args_list)
        )
        self.assertEqual(
            [call.args for call in drop_column.call_args_list],
            [
                ("training_tasks", "cleanup_updated_at"),
                ("training_tasks", "cleanup_paths"),
                ("training_tasks", "cleanup_state"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
