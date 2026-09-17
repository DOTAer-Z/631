import httpx
import pytest

from app.core.config import get_settings
from app.services.llm import LLMError, LLMGatewayClient, get_llm_client


def test_gateway_client_requires_only_gateway_url() -> None:
    # 零-yaml：去掉共享 token 后，可用性只取决于网关地址是否配置。
    assert not LLMGatewayClient(gateway_url=None, service_token="token").is_configured()
    assert not LLMGatewayClient(gateway_url="", service_token="token").is_configured()
    assert LLMGatewayClient(
        gateway_url="http://backend/chat", service_token="token"
    ).is_configured()
    assert LLMGatewayClient(
        gateway_url="http://backend/chat", service_token=None
    ).is_configured()


def test_chat_sends_only_gateway_contract_and_records_model(monkeypatch) -> None:
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return httpx.Response(
            200,
            json={"content": '{"label":"normal"}', "model": "model-a"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = LLMGatewayClient(
        gateway_url="http://backend:8000/api/v1/internal/llm/chat",
        service_token="service-token",
        timeout_seconds=180,
    )

    content = client.chat(
        [{"role": "user", "content": "classify"}],
        purpose="annotation_recommendation",
        json_mode=True,
        temperature=0.1,
    )

    assert content == '{"label":"normal"}'
    assert client.model == "model-a"
    # 网关集群内直连、无共享 token：不再发送 Authorization 头
    assert "headers" not in captured
    assert captured["timeout"] == 180
    assert captured["json"] == {
        "purpose": "annotation_recommendation",
        "messages": [{"role": "user", "content": "classify"}],
        "json_mode": True,
        "temperature": 0.1,
    }
    assert "api_key" not in captured["json"]
    assert "model" not in captured["json"]
    assert "base_url" not in captured["json"]


@pytest.mark.parametrize(
    ("status", "code", "message"),
    [
        (401, "invalid_internal_token", "内部服务认证失败"),
        (503, "model_api_not_configured", "大模型 API 未配置"),
        (504, "model_api_timeout", "大模型 API 请求超时"),
        (502, "model_api_unavailable", "大模型 API 请求失败"),
    ],
)
def test_gateway_errors_are_bounded(monkeypatch, status, code, message) -> None:
    secret = "provider-secret-response"

    def fake_post(url, **kwargs):
        return httpx.Response(
            status,
            json={"detail": {"code": code, "message": secret}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = LLMGatewayClient(gateway_url="http://backend/chat", service_token="token")
    with pytest.raises(LLMError) as caught:
        client.chat(
            [{"role": "user", "content": "raw-log-secret"}],
            purpose="annotation_recommendation",
        )
    assert str(caught.value) == message
    assert secret not in str(caught.value)
    assert "raw-log-secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            httpx.ConnectError("http://secret-host/provider-key"),
            "大模型网关连接失败",
        ),
        (
            httpx.InvalidURL("http://secret-host/provider-key"),
            "大模型网关连接失败",
        ),
        (
            httpx.ReadTimeout("http://secret-host/provider-key"),
            "大模型网关请求超时",
        ),
    ],
)
def test_transport_errors_are_sanitized_without_exception_context(
    monkeypatch, error, message
) -> None:
    client = LLMGatewayClient(gateway_url="http://backend/chat", service_token="token")

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )
    with pytest.raises(LLMError) as caught:
        client.chat(
            [{"role": "user", "content": "x"}],
            purpose="annotation_recommendation",
        )
    assert type(caught.value) is LLMError
    assert str(caught.value) == message
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "secret-host" not in str(caught.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(
            200,
            content=b"raw-response-secret",
            request=httpx.Request("POST", "http://backend/chat"),
        ),
        httpx.Response(
            200,
            json={"content": "ok"},
            request=httpx.Request("POST", "http://backend/chat"),
        ),
    ],
)
def test_invalid_success_response_is_sanitized_without_exception_context(
    monkeypatch, response
) -> None:
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: response)
    client = LLMGatewayClient(gateway_url="http://backend/chat", service_token="token")

    with pytest.raises(LLMError) as caught:
        client.chat(
            [{"role": "user", "content": "raw-log-secret"}],
            purpose="annotation_recommendation",
        )

    assert type(caught.value) is LLMError
    assert str(caught.value) == "大模型网关返回无效响应"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "raw-response-secret" not in str(caught.value)


def test_chat_preserves_explicit_timeout_override(monkeypatch) -> None:
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return httpx.Response(
            200,
            json={"content": "ok", "model": "model-a"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = LLMGatewayClient(
        gateway_url="http://backend/chat",
        service_token="token",
        timeout_seconds=180,
    )

    assert client.chat(
        [{"role": "user", "content": "x"}],
        purpose="annotation_recommendation",
        timeout=0.0,
    ) == "ok"
    assert captured["timeout"] == 0.0


def test_gateway_logs_metadata_without_payload_or_token(monkeypatch, caplog) -> None:
    secret = "response-secret"
    caplog.set_level("INFO")

    def fake_post(url, **kwargs):
        return httpx.Response(
            502,
            json={
                "detail": {
                    "code": "model_api_unavailable",
                    "message": secret,
                }
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = LLMGatewayClient(
        gateway_url="http://backend/chat",
        service_token="service-token",
    )
    with pytest.raises(LLMError):
        client.chat(
            [{"role": "user", "content": "raw-log-secret"}],
            purpose="annotation_recommendation",
        )
    assert "purpose=annotation_recommendation" in caplog.text
    assert "code=model_api_unavailable" in caplog.text
    assert secret not in caplog.text
    assert "raw-log-secret" not in caplog.text
    assert "service-token" not in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        ["response-body-secret"],
        {"detail": {"code": "response-body-secret"}},
    ],
)
def test_unknown_gateway_error_body_is_bounded(monkeypatch, caplog, payload) -> None:
    caplog.set_level("INFO")

    def fake_post(url, **kwargs):
        return httpx.Response(
            500,
            json=payload,
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = LLMGatewayClient(gateway_url="http://backend/chat", service_token="token")

    with pytest.raises(LLMError, match="大模型网关请求失败"):
        client.chat(
            [{"role": "user", "content": "raw-log-secret"}],
            purpose="annotation_recommendation",
        )

    assert "code=unknown_gateway_error" in caplog.text
    assert "response-body-secret" not in caplog.text
    assert "raw-log-secret" not in caplog.text


def test_get_llm_client_reads_only_gateway_settings() -> None:
    settings = get_settings()
    original = (
        settings.llm_gateway_url,
        settings.llm_gateway_timeout_seconds,
    )
    settings.llm_gateway_url = "http://backend:8000/api/v1/internal/llm/chat"
    settings.llm_gateway_timeout_seconds = 180
    try:
        client = get_llm_client()
        assert client.gateway_url.endswith("/api/v1/internal/llm/chat")
        assert client.timeout_seconds == 180
        assert client.is_configured()
        assert not hasattr(client, "api_key")
        assert not hasattr(client, "base_url")
    finally:
        (
            settings.llm_gateway_url,
            settings.llm_gateway_timeout_seconds,
        ) = original
