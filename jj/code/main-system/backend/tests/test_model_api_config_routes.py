import unittest
from datetime import datetime
from types import SimpleNamespace

from httpx import ASGITransport, AsyncClient

from app.api.v1.model_api_config import (
    get_model_api_config_service,
    get_model_api_connection_tester,
)
from app.main import app
from app.schemas.model_api_config import (
    ModelApiConfigDeleteOut,
    ModelApiConfigListOut,
    ModelApiConfigOut,
    ModelApiTestResult,
)
from app.services.api_key_cipher import ApiKeyCipherUnavailable
from app.services.model_api_config_service import (
    ActiveModelApiConfigDeleteForbidden,
    ModelApiConfigConflict,
    ModelApiConfigNotFound,
    ModelApiConfigStorageError,
)


class FakeService:
    def create_config(self, payload):
        return self._row(
            name=payload.name,
            provider=payload.provider,
            base_url=payload.base_url,
            model=payload.model,
            timeout_seconds=payload.timeout_seconds,
            max_output_tokens=payload.max_output_tokens,
        )

    def list_configs(self):
        return [self._row()]

    def get_config(self, config_id):
        if config_id == 404:
            raise ModelApiConfigNotFound("model API configuration not found")
        return self._row(id=config_id)

    def get_connection_test_snapshot(self, config_id):
        return self.get_config(config_id)

    def update_config(self, config_id, payload):
        return self._row(id=config_id, model=payload.model or "qwen")

    def delete_config(self, config_id):
        self.get_config(config_id)

    def activate_config(self, config_id):
        return self._row(id=config_id, is_active=True)

    def to_out(self, row):
        return ModelApiConfigOut(
            id=row.id,
            name=row.name,
            provider=row.provider,
            base_url=row.base_url,
            model=row.model,
            timeout_seconds=row.timeout_seconds,
            max_output_tokens=row.max_output_tokens,
            api_key_configured=True,
            is_active=row.is_active,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _row(
        id=7,
        name="Cluster vLLM",
        provider="vllm",
        base_url="http://vllm:8000/v1",
        model="qwen",
        timeout_seconds=60,
        max_output_tokens=1024,
        is_active=False,
    ):
        return SimpleNamespace(
            id=id,
            name=name,
            provider=provider,
            base_url=base_url,
            model=model,
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
            api_key_ciphertext="encrypted-only",
            is_active=is_active,
            created_at=datetime(2026, 7, 14, 8, 0, 0),
            updated_at=datetime(2026, 7, 14, 8, 0, 0),
        )


class FakeTester:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def test(self, row):
        if self.error:
            raise self.error
        return self.result


class ModelApiConfigRouteTests(unittest.IsolatedAsyncioTestCase):
    def _route_for(self, path: str, method: str):
        for route in app.routes:
            if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
                return route
        self.fail(f"route not found: {method} {path}")

    async def asyncSetUp(self):
        app.dependency_overrides[get_model_api_config_service] = FakeService
        app.dependency_overrides[get_model_api_connection_tester] = lambda: FakeTester(
            ModelApiTestResult(
                success=True,
                latency_ms=1,
                model="qwen",
                message="OK",
            )
        )
        self.client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        )

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        await self.client.aclose()

    def test_routes_and_response_models_match_frontend_contract(self):
        expected = [
            ("GET", "/api/v1/model-api/configs", ModelApiConfigListOut),
            ("POST", "/api/v1/model-api/configs", ModelApiConfigOut),
            ("PATCH", "/api/v1/model-api/configs/{config_id}", ModelApiConfigOut),
            ("DELETE", "/api/v1/model-api/configs/{config_id}", ModelApiConfigDeleteOut),
            ("POST", "/api/v1/model-api/configs/{config_id}/activate", ModelApiConfigOut),
            ("POST", "/api/v1/model-api/configs/{config_id}/test", ModelApiTestResult),
        ]
        for method, path, response_model in expected:
            self.assertIs(self._route_for(path, method).response_model, response_model)

    async def test_create_response_never_serializes_secret_fields(self):
        response = await self.client.post(
            "/api/v1/model-api/configs",
            json={
                "name": "Cluster vLLM",
                "provider": "vllm",
                "base_url": "http://vllm:8000/v1",
                "api_key": "sk-secret",
                "model": "qwen",
                "timeout_seconds": 60,
                "max_output_tokens": 1024,
            },
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["api_key_configured"])
        self.assertNotIn("api_key", body)
        self.assertNotIn("api_key_ciphertext", body)
        self.assertNotIn("sk-secret", response.text)

    async def test_create_validation_error_redacts_nested_api_key_input(self):
        secret = "post-malformed-api-key-secret"

        response = await self.client.post(
            "/api/v1/model-api/configs",
            json={
                "name": "Cluster vLLM",
                "provider": "vllm",
                "base_url": "http://vllm:8000/v1",
                "api_key": {"nested": {"value": secret}},
                "model": "qwen",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertNotIn(secret, response.text)
        api_key_errors = [
            error
            for error in response.json()["detail"]
            if "api_key" in error["loc"]
        ]
        self.assertEqual(len(api_key_errors), 1)
        self.assertNotIn("input", api_key_errors[0])

    async def test_update_validation_error_redacts_nested_api_key_input(self):
        secret = "patch-malformed-api-key-secret"

        response = await self.client.patch(
            "/api/v1/model-api/configs/7",
            json={"api_key": {"nested": {"value": secret}}},
        )

        self.assertEqual(response.status_code, 422)
        self.assertNotIn(secret, response.text)
        api_key_errors = [
            error
            for error in response.json()["detail"]
            if "api_key" in error["loc"]
        ]
        self.assertEqual(len(api_key_errors), 1)
        self.assertNotIn("input", api_key_errors[0])

    async def test_update_rejects_explicit_null_for_every_field(self):
        fields = (
            "name",
            "provider",
            "base_url",
            "api_key",
            "model",
            "timeout_seconds",
            "max_output_tokens",
        )

        for field in fields:
            with self.subTest(field=field):
                response = await self.client.patch(
                    "/api/v1/model-api/configs/7",
                    json={field: None},
                )

                self.assertEqual(response.status_code, 422)

    async def test_update_allows_omission_but_rejects_empty_object(self):
        partial_response = await self.client.patch(
            "/api/v1/model-api/configs/7",
            json={"model": "qwen-plus"},
        )
        empty_response = await self.client.patch(
            "/api/v1/model-api/configs/7",
            json={},
        )

        self.assertEqual(partial_response.status_code, 200)
        self.assertEqual(partial_response.json()["model"], "qwen-plus")
        self.assertEqual(empty_response.status_code, 422)

    async def test_create_conflict_returns_http_409(self):
        class ConflictService(FakeService):
            def create_config(self, payload):
                raise ModelApiConfigConflict("model API configuration already exists")

        app.dependency_overrides[get_model_api_config_service] = ConflictService

        response = await self.client.post(
            "/api/v1/model-api/configs",
            json={
                "name": "Cluster vLLM",
                "provider": "vllm",
                "base_url": "http://vllm:8000/v1",
                "api_key": "sk-secret",
                "model": "qwen",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"], "model API configuration already exists"
        )

    async def test_storage_failure_returns_fixed_http_503(self):
        class StorageFailureService(FakeService):
            def create_config(self, payload):
                raise ModelApiConfigStorageError()

        app.dependency_overrides[get_model_api_config_service] = StorageFailureService

        response = await self.client.post(
            "/api/v1/model-api/configs",
            json={
                "name": "Cluster vLLM",
                "provider": "vllm",
                "base_url": "http://vllm:8000/v1",
                "api_key": "sk-secret",
                "model": "qwen",
            },
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "model API configuration storage is unavailable",
        )

    async def test_delete_active_config_returns_http_409(self):
        class ActiveDeleteService(FakeService):
            def delete_config(self, config_id):
                raise ActiveModelApiConfigDeleteForbidden(
                    "active model API configuration cannot be deleted"
                )

        app.dependency_overrides[get_model_api_config_service] = ActiveDeleteService

        response = await self.client.delete("/api/v1/model-api/configs/7")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"], "active model API configuration cannot be deleted"
        )

    async def test_connection_provider_failure_returns_result_with_http_200(self):
        app.dependency_overrides[get_model_api_connection_tester] = lambda: FakeTester(
            ModelApiTestResult(
                success=False,
                latency_ms=4,
                model="qwen",
                error="provider unavailable",
            )
        )

        response = await self.client.post("/api/v1/model-api/configs/7/test")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["success"])
        self.assertEqual(response.json()["error"], "provider unavailable")

    async def test_connection_route_obtains_snapshot_before_network_test(self):
        events = []
        result = ModelApiTestResult(
            success=True,
            latency_ms=1,
            model="qwen",
            message="OK",
        )

        class SnapshotService(FakeService):
            def get_config(self, config_id):
                events.append("orm-row")
                return super().get_config(config_id)

            def get_connection_test_snapshot(self, config_id):
                events.append("snapshot")
                return self._row(id=config_id)

        class OrderingTester(FakeTester):
            def test(self, snapshot):
                events.append("network")
                return result

        app.dependency_overrides[get_model_api_config_service] = SnapshotService
        app.dependency_overrides[get_model_api_connection_tester] = OrderingTester

        response = await self.client.post("/api/v1/model-api/configs/7/test")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), result.model_dump())
        self.assertEqual(events, ["snapshot", "network"])

    async def test_missing_config_returns_http_404(self):
        response = await self.client.post("/api/v1/model-api/configs/404/test")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "model API configuration not found")

    async def test_cipher_failure_returns_http_503(self):
        app.dependency_overrides[get_model_api_connection_tester] = lambda: FakeTester(
            error=ApiKeyCipherUnavailable("stored API key cannot be decrypted")
        )

        response = await self.client.post("/api/v1/model-api/configs/7/test")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "stored API key cannot be decrypted")


if __name__ == "__main__":
    unittest.main()
