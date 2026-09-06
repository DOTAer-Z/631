from __future__ import annotations

import logging
import time
from typing import Literal

import httpx

from app.core.config import get_settings


logger = logging.getLogger(__name__)


GatewayPurpose = Literal[
    "annotation_recommendation",
    "annotation_window_analysis",
    "annotation_timestamp_inference",
]


class LLMError(RuntimeError):
    pass


_ERROR_MESSAGES = {
    "invalid_internal_token": "内部服务认证失败",
    "llm_gateway_not_configured": "内部大模型网关未配置",
    "model_api_not_configured": "大模型 API 未配置",
    "model_api_timeout": "大模型 API 请求超时",
    "model_api_unavailable": "大模型 API 请求失败",
}


class LLMGatewayClient:
    def __init__(
        self,
        *,
        gateway_url: str | None,
        service_token: str | None,
        timeout_seconds: int = 180,
    ) -> None:
        self.gateway_url = (gateway_url or "").rstrip("/")
        self._service_token = service_token or ""
        self.timeout_seconds = timeout_seconds
        self.model = ""

    def is_configured(self) -> bool:
        return bool(self.gateway_url and self._service_token)

    @staticmethod
    def _log_result(
        *,
        purpose: GatewayPurpose,
        code: str,
        started: float,
    ) -> None:
        logger.info(
            "annotation_llm_gateway purpose=%s status=%s code=%s elapsed_ms=%d",
            purpose,
            "succeeded" if code == "success" else "failed",
            code,
            round((time.perf_counter() - started) * 1000),
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        purpose: GatewayPurpose,
        json_mode: bool = True,
        temperature: float = 0.0,
        timeout: float | None = None,
    ) -> str:
        started = time.perf_counter()
        if not self.is_configured():
            self._log_result(
                purpose=purpose,
                code="llm_gateway_not_configured",
                started=started,
            )
            raise LLMError("内部大模型网关未配置")

        transport_error: tuple[str, str] | None = None
        try:
            response = httpx.post(
                self.gateway_url,
                json={
                    "purpose": purpose,
                    "messages": messages,
                    "json_mode": json_mode,
                    "temperature": temperature,
                },
                headers={"Authorization": f"Bearer {self._service_token}"},
                timeout=timeout if timeout is not None else self.timeout_seconds,
            )
        except httpx.TimeoutException:
            transport_error = (
                "gateway_transport_timeout",
                "大模型网关请求超时",
            )
        except (httpx.HTTPError, httpx.InvalidURL):
            transport_error = (
                "gateway_transport_error",
                "大模型网关连接失败",
            )

        if transport_error is not None:
            code, message = transport_error
            self._log_result(
                purpose=purpose,
                code=code,
                started=started,
            )
            raise LLMError(message)

        if not response.is_success:
            code = ""
            try:
                payload = response.json()
                detail = payload.get("detail") if isinstance(payload, dict) else None
                if isinstance(detail, dict):
                    candidate = str(detail.get("code") or "")
                    if candidate in _ERROR_MESSAGES:
                        code = candidate
            except (ValueError, TypeError):
                pass
            self._log_result(
                purpose=purpose,
                code=code or "unknown_gateway_error",
                started=started,
            )
            raise LLMError(_ERROR_MESSAGES.get(code, "大模型网关请求失败"))

        invalid_response = False
        try:
            payload = response.json()
            content = payload["content"]
            model = payload["model"]
        except (KeyError, TypeError, ValueError):
            invalid_response = True

        if (
            invalid_response
            or not isinstance(content, str)
            or not content
            or not isinstance(model, str)
            or not model
        ):
            self._log_result(
                purpose=purpose,
                code="invalid_gateway_response",
                started=started,
            )
            raise LLMError("大模型网关返回无效响应")

        self.model = model
        self._log_result(purpose=purpose, code="success", started=started)
        return content


def get_llm_client() -> LLMGatewayClient:
    settings = get_settings()
    return LLMGatewayClient(
        gateway_url=settings.llm_gateway_url,
        service_token=settings.internal_llm_gateway_token,
        timeout_seconds=settings.llm_gateway_timeout_seconds,
    )
