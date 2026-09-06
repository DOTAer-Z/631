import unittest
from unittest import mock

from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app
from app.schemas.internal_llm_gateway import InternalLLMChatResponse


class FakeGatewayService:
    def __init__(self):
        self.requests = []

    def chat(self, request):
        self.requests.append(request)
        return InternalLLMChatResponse(content='{"label":"normal"}', model="model-a")


class InternalLLMGatewayRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.api.v1.internal_llm_gateway import get_internal_llm_gateway_service

        self.service = FakeGatewayService()
        self.service_factory_calls = 0
        self.old_token = settings.INTERNAL_LLM_GATEWAY_TOKEN
        settings.INTERNAL_LLM_GATEWAY_TOKEN = "service-token"
        app.dependency_overrides[get_internal_llm_gateway_service] = self._get_service
        self.run_sync_patch = mock.patch(
            "anyio.to_thread.run_sync", new=self._run_sync_inline
        )
        self.run_sync_patch.start()
        self.client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.run_sync_patch.stop()
        app.dependency_overrides.clear()
        settings.INTERNAL_LLM_GATEWAY_TOKEN = self.old_token

    async def _run_sync_inline(self, func, *args, **kwargs):
        kwargs.pop("abandon_on_cancel", None)
        kwargs.pop("cancellable", None)
        kwargs.pop("limiter", None)
        return func(*args, **kwargs)

    def _get_service(self):
        self.service_factory_calls += 1
        return self.service

    async def test_valid_token_calls_gateway(self):
        response = await self.client.post(
            "/api/v1/internal/llm/chat",
            headers={"Authorization": "Bearer service-token"},
            json={
                "purpose": "annotation_recommendation",
                "messages": [{"role": "user", "content": "classify"}],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["model"], "model-a")
        self.assertEqual(len(self.service.requests), 1)

    async def test_missing_and_wrong_tokens_are_rejected_before_service(self):
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            with self.subTest(headers=headers):
                response = await self.client.post(
                    "/api/v1/internal/llm/chat",
                    headers=headers,
                    json={
                        "purpose": "annotation_recommendation",
                        "messages": [{"role": "user", "content": "classify"}],
                    },
                )
                self.assertEqual(response.status_code, 401)
                self.assertEqual(
                    response.json()["detail"],
                    {
                        "code": "invalid_internal_token",
                        "message": "内部服务认证失败",
                    },
                )
        self.assertEqual(self.service.requests, [])

    async def test_unset_server_token_returns_503(self):
        settings.INTERNAL_LLM_GATEWAY_TOKEN = ""
        response = await self.client.post(
            "/api/v1/internal/llm/chat",
            headers={"Authorization": "Bearer anything"},
            json={
                "purpose": "annotation_recommendation",
                "messages": [{"role": "user", "content": "classify"}],
            },
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "llm_gateway_not_configured",
                "message": "内部大模型网关未配置",
            },
        )
        self.assertEqual(self.service.requests, [])

    async def test_malformed_json_is_authenticated_before_body_decode(self):
        malformed_body = '{"prompt":"malformed-secret"'
        cases = (
            (None, 401, "invalid_internal_token"),
            ("Bearer wrong", 401, "invalid_internal_token"),
        )
        for authorization, status_code, code in cases:
            with self.subTest(authorization=authorization):
                headers = {"Content-Type": "application/json"}
                if authorization is not None:
                    headers["Authorization"] = authorization
                response = await self.client.post(
                    "/api/v1/internal/llm/chat",
                    headers=headers,
                    content=malformed_body,
                )
                self.assertEqual(response.status_code, status_code)
                self.assertEqual(response.json()["detail"]["code"], code)

        self.assertEqual(self.service_factory_calls, 0)
        self.assertEqual(self.service.requests, [])

    async def test_malformed_json_checks_unset_server_token_before_body_decode(self):
        settings.INTERNAL_LLM_GATEWAY_TOKEN = ""
        response = await self.client.post(
            "/api/v1/internal/llm/chat",
            headers={
                "Authorization": "Bearer anything",
                "Content-Type": "application/json",
            },
            content='{"prompt":"malformed-secret"',
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"]["code"], "llm_gateway_not_configured"
        )
        self.assertEqual(self.service_factory_calls, 0)
        self.assertEqual(self.service.requests, [])

    async def test_valid_token_with_malformed_json_uses_sanitized_422(self):
        secret_prompt = "malformed-secret-that-must-not-be-returned"
        response = await self.client.post(
            "/api/v1/internal/llm/chat",
            headers={
                "Authorization": "Bearer service-token",
                "Content-Type": "application/json",
            },
            content=f'{{"prompt":"{secret_prompt}"',
        )

        self.assertEqual(response.status_code, 422)
        self.assertNotIn(secret_prompt, response.text)
        self.assertEqual(self.service_factory_calls, 0)
        self.assertEqual(self.service.requests, [])

    async def test_validation_errors_do_not_echo_prompt_content(self):
        secret_prompt = "raw-log-secret-that-must-not-be-returned"
        response = await self.client.post(
            "/api/v1/internal/llm/chat",
            headers={"Authorization": "Bearer service-token"},
            json={
                "purpose": "annotation_recommendation",
                "messages": [{"role": "tool", "content": secret_prompt}],
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(secret_prompt, response.text)
        self.assertEqual(self.service.requests, [])

    async def test_service_errors_have_stable_http_contracts(self):
        from app.api.v1.internal_llm_gateway import get_internal_llm_gateway_service
        from app.services.internal_llm_gateway import (
            ModelApiGatewayTimedOut,
            ModelApiGatewayUnavailable,
            ModelApiNotConfigured,
        )

        class ErrorService:
            def __init__(self, error):
                self.error = error

            def chat(self, request):
                raise self.error

        cases = (
            (
                ModelApiNotConfigured("hidden"),
                503,
                "model_api_not_configured",
                "大模型 API 未配置",
            ),
            (
                ModelApiGatewayTimedOut("hidden"),
                504,
                "model_api_timeout",
                "大模型 API 请求超时",
            ),
            (
                ModelApiGatewayUnavailable("hidden"),
                502,
                "model_api_unavailable",
                "大模型 API 请求失败",
            ),
        )
        for error, status_code, code, message in cases:
            with self.subTest(code=code):
                app.dependency_overrides[get_internal_llm_gateway_service] = (
                    lambda error=error: ErrorService(error)
                )
                response = await self.client.post(
                    "/api/v1/internal/llm/chat",
                    headers={"Authorization": "Bearer service-token"},
                    json={
                        "purpose": "annotation_recommendation",
                        "messages": [{"role": "user", "content": "classify"}],
                    },
                )
                self.assertEqual(response.status_code, status_code)
                self.assertEqual(
                    response.json()["detail"], {"code": code, "message": message}
                )
                self.assertNotIn("hidden", response.text)


if __name__ == "__main__":
    unittest.main()
