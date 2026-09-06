import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from app.schemas.internal_llm_gateway import InternalLLMChatRequest
from app.services.internal_llm_gateway import (
    InternalLLMGatewayService,
    ModelApiGatewayTimedOut,
    ModelApiGatewayUnavailable,
    ModelApiNotConfigured,
)
from app.services.llm_runtime_config import LLMRuntimeHandle


class RecordingCompletions:
    def __init__(self, content='{"label":"normal"}', error=None):
        self.content = content
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class RecordingClient:
    def __init__(self, completions):
        self.chat = SimpleNamespace(completions=completions)
        self.options_calls = []

    def with_options(self, **kwargs):
        self.options_calls.append(kwargs)
        return self


class RecordingProvider:
    def __init__(self, handles):
        self.handles = iter(handles)
        self.calls = []

    def resolve(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.handles)


class InternalLLMGatewaySchemaTests(unittest.TestCase):
    def test_request_accepts_only_constrained_chat_fields(self):
        request = InternalLLMChatRequest(
            purpose="annotation_recommendation",
            messages=[{"role": "user", "content": "classify"}],
        )
        self.assertTrue(request.json_mode)
        self.assertEqual(request.temperature, 0.0)

        for payload in (
            {"purpose": "unknown", "messages": [{"role": "user", "content": "x"}]},
            {"purpose": "annotation_recommendation", "messages": []},
            {
                "purpose": "annotation_recommendation",
                "messages": [{"role": "tool", "content": "x"}],
            },
            {
                "purpose": "annotation_recommendation",
                "messages": [{"role": "user", "content": " "}],
            },
            {
                "purpose": "annotation_recommendation",
                "messages": [{"role": "user", "content": "x"}],
                "model": "caller-controlled",
            },
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    InternalLLMChatRequest.model_validate(payload)

    def test_request_caps_each_and_total_message_content(self):
        with self.assertRaises(ValidationError):
            InternalLLMChatRequest(
                purpose="annotation_recommendation",
                messages=[{"role": "user", "content": "x" * 100_001}],
            )

        with self.assertRaises(ValidationError):
            InternalLLMChatRequest(
                purpose="annotation_recommendation",
                messages=[
                    {"role": "user", "content": "x" * 100_000},
                    {"role": "assistant", "content": "y" * 100_000},
                    {"role": "user", "content": "z"},
                ],
            )


class InternalLLMGatewayServiceTests(unittest.TestCase):
    def request(self, purpose="annotation_recommendation"):
        return InternalLLMChatRequest(
            purpose=purpose,
            messages=[{"role": "user", "content": "classify"}],
            json_mode=True,
            temperature=0.2,
        )

    def test_chat_uses_database_only_runtime_and_active_limits(self):
        completions = RecordingCompletions()
        client = RecordingClient(completions)
        provider = RecordingProvider(
            [LLMRuntimeHandle(client, "model-a", 768, "database")]
        )

        result = InternalLLMGatewayService(provider).chat(self.request())

        self.assertEqual(result.content, '{"label":"normal"}')
        self.assertEqual(result.model, "model-a")
        self.assertEqual(provider.calls, [{"allow_environment_fallback": False}])
        self.assertEqual(client.options_calls, [{"max_retries": 0}])
        self.assertEqual(completions.calls[0]["model"], "model-a")
        self.assertEqual(completions.calls[0]["max_tokens"], 768)
        self.assertEqual(completions.calls[0]["temperature"], 0.2)
        self.assertEqual(
            completions.calls[0]["response_format"], {"type": "json_object"}
        )

    def test_next_call_observes_a_new_runtime_model(self):
        first = RecordingClient(RecordingCompletions("first"))
        second = RecordingClient(RecordingCompletions("second"))
        provider = RecordingProvider(
            [
                LLMRuntimeHandle(first, "model-a", 128, "database"),
                LLMRuntimeHandle(second, "model-b", 256, "database"),
            ]
        )
        service = InternalLLMGatewayService(provider)

        self.assertEqual(service.chat(self.request()).model, "model-a")
        self.assertEqual(service.chat(self.request()).model, "model-b")

    def test_missing_runtime_is_not_configured(self):
        with self.assertRaises(ModelApiNotConfigured):
            InternalLLMGatewayService(RecordingProvider([None])).chat(self.request())

    def test_timeout_and_provider_errors_are_stable_and_unchained(self):
        timeout_client = RecordingClient(
            RecordingCompletions(error=TimeoutError("secret raw log"))
        )
        timeout_provider = RecordingProvider(
            [LLMRuntimeHandle(timeout_client, "model-a", 128, "database")]
        )
        with self.assertRaises(ModelApiGatewayTimedOut) as timeout_error:
            InternalLLMGatewayService(timeout_provider).chat(self.request())
        self.assertIsNone(timeout_error.exception.__cause__)
        self.assertIsNone(timeout_error.exception.__context__)
        self.assertNotIn("secret raw log", str(timeout_error.exception))

        failed_client = RecordingClient(
            RecordingCompletions(error=RuntimeError("provider body secret"))
        )
        failed_provider = RecordingProvider(
            [
                LLMRuntimeHandle(
                    failed_client,
                    "model-a",
                    128,
                    "database",
                    _redaction_secrets=("secret",),
                )
            ]
        )
        with self.assertRaises(ModelApiGatewayUnavailable) as provider_error:
            InternalLLMGatewayService(failed_provider).chat(self.request())
        self.assertIsNone(provider_error.exception.__cause__)
        self.assertIsNone(provider_error.exception.__context__)
        self.assertNotIn("provider body", str(provider_error.exception))
        self.assertNotIn("secret", str(provider_error.exception))

    def test_completion_log_contains_only_metadata(self):
        completions = RecordingCompletions("secret-generated-content")
        client = RecordingClient(completions)
        provider = RecordingProvider(
            [
                LLMRuntimeHandle(
                    client,
                    "model-a",
                    128,
                    "database",
                    _redaction_secrets=("provider-api-key",),
                )
            ]
        )
        request = InternalLLMChatRequest(
            purpose="annotation_recommendation",
            messages=[{"role": "user", "content": "secret-raw-log"}],
        )

        with self.assertLogs(
            "app.services.internal_llm_gateway", level="INFO"
        ) as captured:
            InternalLLMGatewayService(provider).chat(request)

        joined = "\n".join(captured.output)
        self.assertIn("purpose=annotation_recommendation", joined)
        self.assertIn("model=model-a", joined)
        self.assertNotIn("secret-raw-log", joined)
        self.assertNotIn("secret-generated-content", joined)
        self.assertNotIn("provider-api-key", joined)

    def test_completion_content_is_redacted_before_returning(self):
        client = RecordingClient(RecordingCompletions("prefix-provider-api-key-suffix"))
        provider = RecordingProvider(
            [
                LLMRuntimeHandle(
                    client,
                    "model-a",
                    128,
                    "database",
                    _redaction_secrets=("provider-api-key",),
                )
            ]
        )

        result = InternalLLMGatewayService(provider).chat(self.request())

        self.assertEqual(result.content, "prefix-***-suffix")
