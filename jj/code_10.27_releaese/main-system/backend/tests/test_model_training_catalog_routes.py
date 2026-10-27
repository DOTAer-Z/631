import unittest
from datetime import datetime

from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.main import app
from app.models.training_data import TrainingTest, TrainingTestLog, TrainingTestVersion
from app.schemas.model_training import TrainingTestCatalogOut, TrainingSplitPreviewOut


class ModelTrainingCatalogRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        with self.engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE dataset_imports (import_id VARCHAR(64) PRIMARY KEY)")
            )
        for table in (TrainingTest.__table__, TrainingTestVersion.__table__, TrainingTestLog.__table__):
            table.create(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self._seed_catalog()

        def get_test_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = get_test_db
        self.client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        )

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        await self.client.aclose()
        self.db.close()
        self.engine.dispose()

    def _add_version(
        self,
        *,
        platform: str,
        test_name: str,
        version_number: int,
        import_id: str,
        sample_class: str | None = "fault",
        domain: str | None = "kernel",
        fault_type: str | None = "watchdog",
        completeness: str = "complete",
        missing_files: list[str] | None = None,
    ) -> TrainingTestVersion:
        test = (
            self.db.query(TrainingTest)
            .filter_by(platform=platform, test_name=test_name)
            .one_or_none()
        )
        if test is None:
            test = TrainingTest(platform=platform, test_name=test_name)
            self.db.add(test)
            self.db.flush()
        version = TrainingTestVersion(
            test_id=test.id,
            version_number=version_number,
            content_sha256=f"{test_name}-{version_number}".ljust(64, "0")[:64],
            ground_truth={
                "sample_class": sample_class,
                "domain": domain,
                "fault_type": fault_type,
                "server_path": "/not-for-api",
                "nested": {"full_ground_truth": "not-for-api"},
            },
            fip_info={"FAULT_TYPE": fault_type or "unknown", "detail": "not-for-api"},
            completeness=completeness,
            missing_files=missing_files,
            import_id=import_id,
            sample_class=sample_class,
            domain=domain,
            fault_type=fault_type,
            round_1_parse_status="parsed",
            round_2_parse_status="parsed",
            created_at=datetime(2026, 7, 15, 8, 0, 0),
        )
        self.db.add(version)
        self.db.flush()
        test.latest_version_id = version.id
        self.db.flush()
        return version

    def _seed_catalog(self):
        self.old_alpha = self._add_version(
            platform="NuttX", test_name="Test_alpha", version_number=1, import_id="import-old"
        )
        self.alpha = self._add_version(
            platform="NuttX", test_name="Test_alpha", version_number=2, import_id="import-new"
        )
        self.beta = self._add_version(
            platform="Zephyr", test_name="Test_beta", version_number=1, import_id="import-beta",
            sample_class="normal", domain=None, fault_type=None,
        )
        self.incomplete = self._add_version(
            platform="NuttX", test_name="Test_incomplete", version_number=1,
            import_id="import-incomplete", completeness="incomplete",
            missing_files=["logs/round_2/monitor/system_metrics.log"],
        )
        self.stale = self._add_version(
            platform="NuttX", test_name="Test_stale", version_number=1, import_id="import-stale"
        )
        self.latest_stale = self._add_version(
            platform="NuttX", test_name="Test_stale", version_number=2, import_id="import-stale-new"
        )
        self.db.add_all(
            [
                TrainingTestLog(test_version_id=self.alpha.id, round_no=1, log_type="qemu_console", content="raw", byte_count=4),
                TrainingTestLog(test_version_id=self.alpha.id, round_no=2, log_type="fault_events", content="raw logs", byte_count=8),
                TrainingTestLog(test_version_id=self.beta.id, round_no=1, log_type="qemu_console", content="x", byte_count=1),
            ]
        )
        self.db.commit()

    def _route_for(self, path: str, method: str):
        for route in app.routes:
            if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
                return route
        self.fail(f"route not found: {method} {path}")

    def test_routes_are_registered_with_response_models(self):
        self.assertIs(
            self._route_for("/api/v1/model-training/tests", "GET").response_model,
            TrainingTestCatalogOut,
        )
        self.assertIs(
            self._route_for("/api/v1/model-training/splits/preview", "POST").response_model,
            TrainingSplitPreviewOut,
        )

    async def test_catalog_returns_latest_versions_only_and_redacts_training_content(self):
        response = await self.client.get("/api/v1/model-training/tests")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        returned_ids = {item["version_id"] for item in body["items"]}
        self.assertNotIn(self.old_alpha.id, returned_ids)
        self.assertNotIn(self.stale.id, returned_ids)
        alpha = next(item for item in body["items"] if item["version_id"] == self.alpha.id)
        self.assertEqual(alpha["total_training_log_bytes"], 12)
        self.assertEqual(alpha["label_summary"], {
            "sample_class": "fault", "domain": "kernel", "fault_type": "watchdog"
        })
        self.assertNotIn("ground_truth", alpha)
        self.assertNotIn("fip_info", alpha)
        self.assertNotIn("content", alpha)
        self.assertNotIn("/not-for-api", response.text)
        self.assertNotIn("not-for-api", response.text)

    async def test_catalog_supports_filters_and_pagination(self):
        cases = (
            ({"search": "zeph"}, {self.beta.id}),
            ({"sample_class": "normal"}, {self.beta.id}),
            ({"fault_type": "watchdog"}, {self.alpha.id, self.incomplete.id, self.latest_stale.id}),
            ({"import_id": "import-new"}, {self.alpha.id}),
            ({"completeness": "incomplete"}, {self.incomplete.id}),
        )
        for params, expected_ids in cases:
            with self.subTest(params=params):
                response = await self.client.get("/api/v1/model-training/tests", params=params)
                self.assertEqual(response.status_code, 200)
                self.assertEqual({item["version_id"] for item in response.json()["items"]}, expected_ids)

        page = await self.client.get(
            "/api/v1/model-training/tests", params={"page": 2, "page_size": 2}
        )
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.json()["total"], 4)
        self.assertEqual(len(page.json()["items"]), 2)
        self.assertEqual(page.json()["page"], 2)
        self.assertEqual(page.json()["page_size"], 2)

    async def test_preview_returns_disjoint_ids_counts_and_strata(self):
        response = await self.client.post(
            "/api/v1/model-training/splits/preview",
            json={
                "test_version_ids": [self.latest_stale.id, self.alpha.id, self.beta.id],
                "train_ratio": 0.8,
                "validation_ratio": 0.1,
                "test_ratio": 0.1,
                "seed": 631,
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        all_ids = set(body["train_ids"]) | set(body["validation_ids"]) | set(body["test_ids"])
        self.assertEqual(all_ids, {self.latest_stale.id, self.alpha.id, self.beta.id})
        self.assertTrue(set(body["train_ids"]).isdisjoint(body["validation_ids"]))
        self.assertTrue(set(body["train_ids"]).isdisjoint(body["test_ids"]))
        self.assertEqual(body["counts"]["train"] + body["counts"]["validation"] + body["counts"]["test"], 3)
        self.assertEqual(len(body["strata"]), 2)

    async def test_preview_rejects_empty_duplicate_unknown_incomplete_and_nonlatest_versions(self):
        cases = (
            [],
            [self.alpha.id, self.alpha.id],
            [999999],
            [self.incomplete.id],
            [self.stale.id],
        )
        for version_ids in cases:
            with self.subTest(version_ids=version_ids):
                response = await self.client.post(
                    "/api/v1/model-training/splits/preview",
                    json={
                        "test_version_ids": version_ids,
                        "train_ratio": 0.8,
                        "validation_ratio": 0.1,
                        "test_ratio": 0.1,
                        "seed": 631,
                    },
                )
                self.assertEqual(response.status_code, 422)

    async def test_preview_rejects_invalid_ratios(self):
        response = await self.client.post(
            "/api/v1/model-training/splits/preview",
            json={
                "test_version_ids": [self.alpha.id],
                "train_ratio": 0.8,
                "validation_ratio": 0.1,
                "test_ratio": 0.2,
                "seed": 631,
            },
        )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
