from types import SimpleNamespace
import unittest
from unittest import mock

from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.main import app
from app.models.log_parse_task import LogParseTask
from app.services.log_parse_errors import LogParseTimedOut
from app.services.log_parse_task_service import (
    LogParseTaskRunner,
    create_task,
    get_task,
    request_cancel,
)


class LogParseTaskRunnerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        LogParseTask.__table__.create(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.runner = LogParseTaskRunner(
            session_factory=self.Session,
            max_workers=1,
        )

    def tearDown(self):
        self.runner.shutdown(wait=True)
        LogParseTask.__table__.drop(bind=self.engine)
        self.engine.dispose()

    def create(self, run_ids):
        with self.Session() as db:
            return create_task(db, run_ids)

    def current(self, task_id):
        with self.Session() as db:
            return get_task(db, task_id)

    def test_batch_partial_success_has_results_and_failure_counts(self):
        task = self.create(["run-1", "run-2"])

        def execute(run_id, control):
            control.checkpoint("preprocessing", 20)
            if run_id == "run-2":
                raise RuntimeError("model failed")
            control.begin_persisting(95)
            return SimpleNamespace(model_dump=lambda: {"run_id": run_id})

        self.runner.run_now(task.id, execute)

        final = self.current(task.id)
        self.assertEqual(final.state, "succeeded")
        self.assertEqual((final.success_count, final.fail_count), (1, 1))
        self.assertIn("run-1", final.results)
        self.assertEqual(final.fail_details[0]["run_id"], "run-2")

    def test_cancel_during_current_item_stops_remaining_items(self):
        task = self.create(["run-1", "run-2"])
        calls = []

        def execute(run_id, control):
            calls.append(run_id)
            with self.Session() as db:
                request_cancel(db, task.id)
            control.checkpoint("llm", 40)

        self.runner.run_now(task.id, execute)

        final = self.current(task.id)
        self.assertEqual(final.state, "cancelled")
        self.assertEqual(calls, ["run-1"])
        self.assertLess(final.progress, 100)

    def test_timeout_is_recorded_without_retrying(self):
        task = self.create(["run-1"])
        calls = []

        def execute(run_id, control):
            calls.append(run_id)
            raise LogParseTimedOut("日志解析超过 60 秒")

        self.runner.run_now(task.id, execute)

        final = self.current(task.id)
        self.assertEqual(final.state, "failed")
        self.assertEqual(final.fail_details[0]["code"], "timeout")
        self.assertEqual(calls, ["run-1"])


class LogParseTaskRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        LogParseTask.__table__.create(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.previous_overrides = dict(app.dependency_overrides)
        app.dependency_overrides[deps.get_db] = lambda: self.db
        self.run_sync_patch = mock.patch(
            "anyio.to_thread.run_sync",
            new=self._run_sync_inline,
        )
        self.run_sync_patch.start()
        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self):
        self.run_sync_patch.stop()
        await self.client.aclose()
        app.dependency_overrides = self.previous_overrides
        self.db.close()
        LogParseTask.__table__.drop(bind=self.engine)
        self.engine.dispose()

    async def _run_sync_inline(self, func, *args, **kwargs):
        kwargs.pop("abandon_on_cancel", None)
        kwargs.pop("cancellable", None)
        kwargs.pop("limiter", None)
        return func(*args, **kwargs)

    async def test_create_get_and_cancel_task_contract(self):
        with mock.patch(
            "app.api.v1.log_analysis.log_parse_task_runner.submit"
        ) as submit:
            created = await self.client.post(
                "/api/v1/log-analysis/parse-tasks",
                json={"run_ids": ["run-1"]},
            )

        self.assertEqual(created.status_code, 202)
        task_id = created.json()["id"]
        submit.assert_called_once_with(task_id, mock.ANY)

        detail = await self.client.get(
            f"/api/v1/log-analysis/parse-tasks/{task_id}"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["state"], "queued")

        cancelled = await self.client.post(
            f"/api/v1/log-analysis/parse-tasks/{task_id}/cancel"
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["state"], "cancelled")

    async def test_missing_and_overlapping_tasks_have_stable_status_codes(self):
        missing = await self.client.get(
            "/api/v1/log-analysis/parse-tasks/missing"
        )
        self.assertEqual(missing.status_code, 404)

        with mock.patch("app.api.v1.log_analysis.log_parse_task_runner.submit"):
            first = await self.client.post(
                "/api/v1/log-analysis/parse-tasks",
                json={"run_ids": ["run-1"]},
            )
            overlap = await self.client.post(
                "/api/v1/log-analysis/parse-tasks",
                json={"run_ids": ["run-1"]},
            )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(overlap.status_code, 409)


if __name__ == "__main__":
    unittest.main()
