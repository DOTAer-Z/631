import unittest
from datetime import datetime
from types import SimpleNamespace

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.api.v1.data_import import _to_item
from app.schemas.data_import import (
    DataImportDeleteOut,
    DataImportItem,
    DataImportListOut,
    DataImportPreviewOut,
)


class DataImportRouteTests(unittest.IsolatedAsyncioTestCase):
    def _route_for(self, path: str, method: str):
        for route in app.routes:
            if (
                getattr(route, "path", None) == path
                and method.upper() in getattr(route, "methods", set())
            ):
                return route
        self.fail(f"route not found: {method.upper()} {path}")

    async def asyncSetUp(self):
        self.transport = ASGITransport(app=app)
        self.client = AsyncClient(transport=self.transport, base_url="http://testserver")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_data_import_list_route_exists(self):
        response = await self.client.options("/api/v1/data-imports")
        self.assertNotEqual(response.status_code, 404)

    def test_router_registers_all_data_import_routes(self):
        self._route_for("/api/v1/data-imports", "POST")
        self._route_for("/api/v1/data-imports", "GET")
        self._route_for("/api/v1/data-imports/{import_id}", "GET")
        self._route_for("/api/v1/data-imports/{import_id}", "PATCH")
        self._route_for("/api/v1/data-imports/{import_id}", "DELETE")
        self._route_for("/api/v1/data-imports/{import_id}/download", "GET")
        self._route_for("/api/v1/data-imports/{import_id}/preview", "GET")

    def test_response_models_match_contract(self):
        self.assertIs(
            self._route_for("/api/v1/data-imports", "POST").response_model,
            DataImportItem,
        )
        self.assertIs(
            self._route_for("/api/v1/data-imports", "GET").response_model,
            DataImportListOut,
        )
        self.assertIs(
            self._route_for("/api/v1/data-imports/{import_id}", "GET").response_model,
            DataImportItem,
        )
        self.assertIs(
            self._route_for("/api/v1/data-imports/{import_id}", "PATCH").response_model,
            DataImportItem,
        )
        self.assertIs(
            self._route_for("/api/v1/data-imports/{import_id}", "DELETE").response_model,
            DataImportDeleteOut,
        )
        self.assertIs(
            self._route_for("/api/v1/data-imports/{import_id}/preview", "GET").response_model,
            DataImportPreviewOut,
        )

    def test_list_and_preview_dtos_expose_training_counters_without_training_content(self):
        expected_counters = {
            "training_complete_count",
            "training_incomplete_count",
            "training_duplicate_count",
            "training_failed_count",
            "training_parse_failed_count",
        }
        self.assertTrue(expected_counters.issubset(DataImportItem.model_fields))
        self.assertTrue(expected_counters.issubset(DataImportPreviewOut.model_fields))
        self.assertNotIn("ground_truth", DataImportItem.model_fields)
        self.assertNotIn("raw_training_logs", DataImportPreviewOut.model_fields)

        record = SimpleNamespace(
            import_id="import-1", original_filename="dataset.zip", file_ext="zip",
            size_bytes=12, status="uploaded", display_name=None, description=None,
            tags_json=[], created_by=None, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
            ingest_status="partial_success", ingest_error=None, ingested_at=None,
            ingested_run_count=0, ingested_entry_count=0, ingested_case_ids=[],
            ingested_new_case_count=0, ingested_updated_case_count=0,
            ingested_new_run_count=0, ingested_updated_run_count=0,
            training_complete_count=1, training_incomplete_count=2,
            training_duplicate_count=3, training_failed_count=4,
            training_parse_failed_count=5,
            format=None,
        )

        item = _to_item(record)
        self.assertEqual(item.ingest_status, "partial_success")
        self.assertEqual(item.training_parse_failed_count, 5)


if __name__ == "__main__":
    unittest.main()
