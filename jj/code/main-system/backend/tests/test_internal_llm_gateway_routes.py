import unittest
from unittest import mock

from httpx import ASGITransport, AsyncClient

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

    async def _run_sync_inline(self, func, *args, **kwargs):
        kwargs.pop("abandon_on_cancel", None)
        kwargs.pop("cancellable", None)
        kwargs.pop("limiter", None)
        return func(*args, **kwargs)

    def _get_service(self):
        self.service_factory_calls += 1
        return self.service

    async def test_request_reaches_gateway_without_any_auth_header(self):
        # 零-yaml：网关去掉共享 token 鉴权（外部由 nginx 封 /api/v1/internal/ 404），
        # 集群内标注后端直接以服务名 backend:8000 调用，无 Authorization 头。
        response = await self.client.post(
            "/api/v1/internal/llm/chat",
            json={
                "purpose": "annotation_recommendation",
                "messages": [{"role": "user", "content": "classify"}],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["model"], "model-a")
        self.assertEqual(len(self.service.requests), 1)

    async def test_malformed_json_uses_sanitized_422_without_auth(self):
        secret_prompt = "malformed-secret-that-must-not-be-returned"
        response = await self.client.post(
            "/api/v1/internal/llm/chat",
            headers={"Content-Type": "application/json"},
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
