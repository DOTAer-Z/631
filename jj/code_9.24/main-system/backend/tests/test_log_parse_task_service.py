import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.log_parse_task import LogParseTask
from app.services.log_parse_errors import LogParseCancelled
from app.services.log_parse_task_service import (
    LogParseTaskConflict,
    LogParseTaskNotFound,
    begin_persisting,
    create_task,
    finish_from_counts,
    get_task,
    mark_failed,
    mark_running,
    record_failure,
    record_success,
    recover_interrupted_tasks,
    request_cancel,
    update_progress,
)


class LogParseTaskServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        LogParseTask.__table__.create(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        LogParseTask.__table__.drop(bind=self.engine)
        self.engine.dispose()

    def test_create_rejects_overlapping_active_task_and_allows_terminal_reparse(self):
        first = create_task(self.db, ["run-1", "run-2"])
        with self.assertRaises(LogParseTaskConflict):
            create_task(self.db, ["run-2", "run-3"])

        mark_failed(self.db, first.id, code="timeout", message="解析超过 60 秒")
        second = create_task(self.db, ["run-2"])

        self.assertNotEqual(second.id, first.id)
        self.assertEqual(second.state, "queued")

    def test_get_missing_task_raises_not_found(self):
        with self.assertRaises(LogParseTaskNotFound):
            get_task(self.db, "missing")

    def test_queued_cancel_is_terminal_and_running_cancel_is_idempotent(self):
        queued = create_task(self.db, ["queued-run"])
        self.assertEqual(request_cancel(self.db, queued.id).state, "cancelled")

        running = create_task(self.db, ["running-run"])
        mark_running(self.db, running.id)
        first = request_cancel(self.db, running.id)
        second = request_cancel(self.db, running.id)

        self.assertEqual(first.state, "cancelling")
        self.assertEqual(second.state, "cancelling")
        self.assertEqual(first.cancel_requested_at, second.cancel_requested_at)

    def test_terminal_and_persisting_tasks_cannot_be_cancelled(self):
        finished = create_task(self.db, ["finished-run"])
        mark_failed(self.db, finished.id, code="failed", message="failed")
        with self.assertRaises(LogParseTaskConflict):
            request_cancel(self.db, finished.id)

        persisting = create_task(self.db, ["persisting-run"])
        mark_running(self.db, persisting.id)
        begin_persisting(self.db, persisting.id, progress=95)
        with self.assertRaises(LogParseTaskConflict):
            request_cancel(self.db, persisting.id)

    def test_begin_persisting_honors_prior_cancel_request(self):
        task = create_task(self.db, ["run-1"])
        mark_running(self.db, task.id)
        request_cancel(self.db, task.id)

        with self.assertRaises(LogParseCancelled):
            begin_persisting(self.db, task.id, progress=95)

    def test_progress_is_monotonic_and_capped_before_success(self):
        task = create_task(self.db, ["run-1"])
        mark_running(self.db, task.id)
        update_progress(self.db, task.id, stage="llm", progress=70, current_run_id="run-1")
        update_progress(self.db, task.id, stage="llm", progress=40, current_run_id="run-1")
        update_progress(self.db, task.id, stage="llm", progress=120, current_run_id="run-1")

        current = get_task(self.db, task.id)
        self.assertEqual(current.progress, 99)
        self.assertEqual(current.stage, "llm")
        self.assertEqual(current.current_run_id, "run-1")

    def test_success_and_failure_records_are_copied_and_aggregated(self):
        task = create_task(self.db, ["run-1", "run-2"])
        mark_running(self.db, task.id)
        record_success(self.db, task.id, "run-1", {"summary": "ok"})
        record_failure(
            self.db,
            task.id,
            "run-2",
            code="model_error",
            message="模型请求失败",
        )
        final = finish_from_counts(self.db, task.id)

        self.assertEqual(final.state, "succeeded")
        self.assertEqual(final.progress, 100)
        self.assertEqual(final.success_count, 1)
        self.assertEqual(final.fail_count, 1)
        self.assertEqual(final.completed_count, 2)
        self.assertEqual(final.results["run-1"]["summary"], "ok")
        self.assertEqual(final.fail_details[0]["run_id"], "run-2")

    def test_all_failed_batch_has_failed_terminal_state(self):
        task = create_task(self.db, ["run-1"])
        mark_running(self.db, task.id)
        record_failure(self.db, task.id, "run-1", code="timeout", message="超时")
        final = finish_from_counts(self.db, task.id)
        self.assertEqual(final.state, "failed")
        self.assertEqual(final.error_code, "all_items_failed")
        self.assertLess(final.progress, 100)

    def test_recovery_fails_nonterminal_tasks_without_touching_terminal_tasks(self):
        queued = create_task(self.db, ["run-1"])
        running = create_task(self.db, ["run-2"])
        mark_running(self.db, running.id)
        succeeded = create_task(self.db, ["run-3"])
        mark_running(self.db, succeeded.id)
        record_success(self.db, succeeded.id, "run-3", {"summary": "ok"})
        finish_from_counts(self.db, succeeded.id)

        recovered = recover_interrupted_tasks(self.db)

        self.assertEqual(recovered, 2)
        self.assertEqual(get_task(self.db, queued.id).error_code, "backend_restarted")
        self.assertEqual(get_task(self.db, running.id).state, "failed")
        self.assertEqual(get_task(self.db, succeeded.id).state, "succeeded")


if __name__ == "__main__":
    unittest.main()
