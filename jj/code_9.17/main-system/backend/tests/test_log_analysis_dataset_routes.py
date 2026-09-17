import unittest
from unittest import mock

from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.main import app as production_app
from app.schemas.log_analysis import (
    DatasetRunDetailOut,
    DatasetRunEntriesOut,
    DatasetRunListOut,
    DatasetRunWindowsOut,
)


class DatasetLogRouteTests(unittest.IsolatedAsyncioTestCase):
    def _route_for_path(self, path):
        for route in self.app.routes:
            if getattr(route, "path", None) == path:
                return route
        self.fail(f"Route for path {path} not found")

    async def asyncSetUp(self):
        self.db_session = object()
        self.app = production_app
        self.original_dependency_overrides = dict(self.app.dependency_overrides)
        self.app.dependency_overrides[deps.get_db] = lambda: self.db_session
        self.run_sync_patch = mock.patch(
            "anyio.to_thread.run_sync",
            new=self._run_sync_inline,
        )
        self.run_sync_patch.start()
        self.transport = ASGITransport(app=self.app)
        self.client = AsyncClient(transport=self.transport, base_url="http://testserver")

    async def asyncTearDown(self):
        self.run_sync_patch.stop()
        self.app.dependency_overrides = self.original_dependency_overrides
        await self.client.aclose()

    async def _run_sync_inline(self, func, *args, **kwargs):
        kwargs.pop("abandon_on_cancel", None)
        kwargs.pop("cancellable", None)
        kwargs.pop("limiter", None)
        return func(*args, **kwargs)

    async def test_list_logs_forwards_new_filters_and_normalizes_status_alias(self):
        with mock.patch("app.api.v1.log_analysis.log_dataset_service") as mock_service:
            mock_service.list_runs.return_value = {
                "items": [],
                "total": 0,
                "fault_count": 0,
                "normal_count": 0,
            }

            response = await self.client.get(
                "/api/v1/log-analysis/logs",
                params={
                    "page": 2,
                    "page_size": 50,
                    "run_id": "nuttx_NuttX_Test_1030_round_2",
                    "case_id": "nuttx_NuttX_Test_1030",
                    "test_name": "Test_1030",
                    "status": "fault",
                    "system_id": "nuttx",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "items": [],
                "total": 0,
                "fault_count": 0,
                "normal_count": 0,
            },
        )
        mock_service.list_runs.assert_called_once_with(
            self.db_session,
            page=2,
            page_size=50,
            run_id="nuttx_NuttX_Test_1030_round_2",
            case_id="nuttx_NuttX_Test_1030",
            test_name="Test_1030",
            fault_status="fault",
            system_id="nuttx",
        )

    def test_dataset_routes_are_registered_with_expected_response_models(self):
        self.assertIs(
            self._route_for_path("/api/v1/log-analysis/logs").response_model,
            DatasetRunListOut,
        )
        self.assertIs(
            self._route_for_path("/api/v1/log-analysis/logs/{run_id}").response_model,
            DatasetRunDetailOut,
        )
        self.assertIs(
            self._route_for_path("/api/v1/log-analysis/logs/{run_id}/entries").response_model,
            DatasetRunEntriesOut,
        )
        self.assertIs(
            self._route_for_path("/api/v1/log-analysis/logs/{run_id}/windows").response_model,
            DatasetRunWindowsOut,
        )

    async def test_detail_endpoint_returns_payload_and_uses_dependencies(self):
        mongo_db = object()
        with (
            mock.patch("app.api.v1.log_analysis.get_mongo_db", return_value=mongo_db) as mock_get_mongo_db,
            mock.patch("app.api.v1.log_analysis.log_dataset_service") as mock_service,
        ):
            mock_service.get_run_detail.return_value = {
                "run_id": "nuttx_NuttX_Test_1030_round_2",
                "case_id": "nuttx_NuttX_Test_1030",
                "run_name": "nuttx_NuttX_Test_1030_round_2",
                "case_name": "nuttx_NuttX_Test_1030",
                "test_name": "Test_1030",
                "system_id": "nuttx",
                "subsystem": "kernel",
                "round_no": 2,
                "fault_type": "deadlock",
                "fault_status": "fault",
                "start_time": "2026-05-25T00:00:00",
                "end_time": "2026-05-25T00:01:00",
                "parsed_lines": 240,
                "total_lines": 300,
                "error_logs": 12,
                "critical_logs": 3,
                "window_count": 5,
                "entry_count": 240,
                "windows_total": 5,
                "level_distribution": {"ERROR": 12},
                "top_modules": [{"module": "sched", "count": 8}],
                "created_at": "2026-05-25T00:00:00",
            }

            response = await self.client.get(
                "/api/v1/log-analysis/logs/nuttx_NuttX_Test_1030_round_2"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run_id"], "nuttx_NuttX_Test_1030_round_2")
        mock_get_mongo_db.assert_called_once_with()
        mock_service.get_run_detail.assert_called_once_with(
            self.db_session,
            mongo_db,
            run_id="nuttx_NuttX_Test_1030_round_2",
        )

    async def test_entries_endpoint_returns_paginated_payload(self):
        mongo_db = object()
        with (
            mock.patch("app.api.v1.log_analysis.get_mongo_db", return_value=mongo_db) as mock_get_mongo_db,
            mock.patch("app.api.v1.log_analysis.log_dataset_service") as mock_service,
        ):
            mock_service.list_run_entries.return_value = {
                "items": [
                    {
                        "timestamp": "2026-05-25T00:00:10",
                        "level": "ERROR",
                        "module": "sched",
                        "message": "panic",
                        "file_path": "syslog",
                        "line_no": 10,
                    }
                ],
                "total": 1,
                "page": 1,
                "page_size": 20,
            }

            response = await self.client.get(
                "/api/v1/log-analysis/logs/nuttx_NuttX_Test_1030_round_2/entries",
                params={"page": 1, "page_size": 20},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["message"], "panic")
        mock_get_mongo_db.assert_called_once_with()
        mock_service.list_run_entries.assert_called_once_with(
            mongo_db,
            run_id="nuttx_NuttX_Test_1030_round_2",
            page=1,
            page_size=20,
        )

    async def test_windows_endpoint_returns_paginated_payload(self):
        mongo_db = object()
        with (
            mock.patch("app.api.v1.log_analysis.get_mongo_db", return_value=mongo_db) as mock_get_mongo_db,
            mock.patch("app.api.v1.log_analysis.log_dataset_service") as mock_service,
        ):
            mock_service.list_run_windows.return_value = {
                "items": [
                    {
                        "window_id": "nuttx_w_1",
                        "start_time": "2026-05-25T00:00:00",
                        "end_time": "2026-05-25T00:01:00",
                        "strategy": "error",
                        "entry_count": 18,
                        "error_events": 2,
                        "key_events": [{"message": "panic"}],
                        "text_preview": "panic...",
                    }
                ],
                "total": 1,
                "page": 1,
                "page_size": 10,
            }

            response = await self.client.get(
                "/api/v1/log-analysis/logs/nuttx_NuttX_Test_1030_round_2/windows",
                params={"page": 1, "page_size": 10},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["window_id"], "nuttx_w_1")
        mock_get_mongo_db.assert_called_once_with()
        mock_service.list_run_windows.assert_called_once_with(
            mongo_db,
            run_id="nuttx_NuttX_Test_1030_round_2",
            page=1,
            page_size=10,
        )


if __name__ == "__main__":
    unittest.main()
