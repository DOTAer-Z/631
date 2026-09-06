import importlib.util
from datetime import datetime
from pathlib import Path
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from sqlalchemy import CheckConstraint, create_engine

from app.models.log_parse_task import LOG_PARSE_TASK_STATES, LogParseTask
from app.schemas.log_analysis import LogParseTaskCreate, LogParseTaskOut


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0009_add_log_parse_tasks.py"
)


def _check_sql(table):
    return {
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0009", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class LogParseTaskModelTests(unittest.TestCase):
    def test_model_has_durable_task_state_contract(self):
        self.assertEqual(LogParseTask.__tablename__, "log_parse_tasks")
        self.assertEqual(
            LOG_PARSE_TASK_STATES,
            ("queued", "running", "cancelling", "cancelled", "succeeded", "failed"),
        )
        columns = LogParseTask.__table__.columns
        for name in (
            "id",
            "state",
            "progress",
            "stage",
            "run_ids",
            "current_run_id",
            "completed_count",
            "success_count",
            "fail_count",
            "results",
            "fail_details",
            "error_code",
            "error_message",
            "cancel_requested_at",
            "created_at",
            "started_at",
            "finished_at",
        ):
            self.assertIn(name, columns)
        checks = _check_sql(LogParseTask.__table__)
        self.assertIn(
            "state IN ('queued', 'running', 'cancelling', 'cancelled', 'succeeded', 'failed')",
            checks,
        )
        self.assertIn("progress >= 0 AND progress <= 100", checks)

    def test_model_table_can_be_created_on_sqlite_for_unit_tests(self):
        engine = create_engine("sqlite://")
        try:
            LogParseTask.__table__.create(bind=engine)
            self.assertTrue(engine.dialect.has_table(engine.connect(), "log_parse_tasks"))
        finally:
            engine.dispose()

    def test_create_schema_rejects_empty_duplicate_blank_and_oversized_ids(self):
        invalid_values = (
            [],
            ["run-1", "run-1"],
            ["   "],
            [f"run-{index}" for index in range(101)],
        )
        for run_ids in invalid_values:
            with self.subTest(run_ids=run_ids[:3]):
                with self.assertRaises(ValidationError):
                    LogParseTaskCreate(run_ids=run_ids)

    def test_create_schema_strips_ids_and_response_reads_model_attributes(self):
        request = LogParseTaskCreate(run_ids=[" run-1 ", "run-2"])
        self.assertEqual(request.run_ids, ["run-1", "run-2"])

        task = LogParseTask(
            id="task-1",
            state="queued",
            progress=0,
            stage="queued",
            run_ids=["run-1"],
            completed_count=0,
            success_count=0,
            fail_count=0,
            results={},
            fail_details=[],
            created_at=datetime(2026, 7, 23, 8, 0, 0),
        )
        payload = LogParseTaskOut.model_validate(task)
        self.assertEqual(payload.id, "task-1")
        self.assertEqual(payload.results, {})

    def test_migration_is_next_revision_and_creates_task_table(self):
        migration = _load_migration()
        self.assertEqual(migration.revision, "0009")
        self.assertEqual(migration.down_revision, "0008")
        with patch.object(migration.op, "create_table") as create_table, patch.object(
            migration.op, "create_index"
        ) as create_index:
            migration.upgrade()
        self.assertEqual(create_table.call_args.args[0], "log_parse_tasks")
        create_index.assert_called_once_with(
            "idx_log_parse_tasks_state_created_at",
            "log_parse_tasks",
            ["state", "created_at"],
            unique=False,
        )


if __name__ == "__main__":
    unittest.main()
