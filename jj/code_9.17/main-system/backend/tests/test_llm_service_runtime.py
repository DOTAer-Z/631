import json
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.api_key_cipher import ApiKeyCipherUnavailable
from app.services.fault_location_service import FaultLocationService
from app.services.llm_runtime_config import LLMRuntimeHandle
from app.services.llm_service import LLMService


class RecordingCompletions:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            chunk = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=self.content,
                            reasoning_content=None,
                        )
                    )
                ]
            )
            return RecordingStream([chunk])
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.content)
                )
            ]
        )


class RecordingStream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def __iter__(self):
        return iter(self.chunks)

    def close(self):
        self.closed = True


class RecordingClient:
    def __init__(self, completions):
        self.chat = SimpleNamespace(completions=completions)
        self.options_calls = []

    def with_options(self, **kwargs):
        self.options_calls.append(kwargs)
        return self


class FailingCompletions:
    def __init__(self, error):
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        raise self.error


class SequenceProvider:
    def __init__(self, handles):
        self.handles = iter(handles)
        self.resolve_calls = 0

    def resolve(self):
        self.resolve_calls += 1
        return next(self.handles)


def runtime(model, max_output_tokens, content, secrets=()):
    completions = RecordingCompletions(content)
    client = RecordingClient(completions)
    handle_kwargs = {}
    if secrets:
        handle_kwargs["_redaction_secrets"] = secrets
    return (
        LLMRuntimeHandle(
            client,
            model,
            max_output_tokens,
            "database",
            **handle_kwargs,
        ),
        completions,
    )


class LLMServiceRuntimeTests(unittest.TestCase):
    def test_each_call_uses_a_fresh_runtime_handle(self):
        first, first_calls = runtime(
            "model-a",
            512,
            '{"is_fault": false, "fault_type": null, "confidence": 0.9, "reasoning": "ok"}',
        )
        second, second_calls = runtime(
            "model-b",
            1024,
            '{"is_fault": false, "fault_type": null, "confidence": 0.9, "reasoning": "ok"}',
        )
        provider = SequenceProvider([first, second])
        service = LLMService(runtime_provider=provider)

        service.diagnose("log-a", "context-a")
        service.diagnose("log-b", "context-b")

        self.assertEqual(provider.resolve_calls, 2)
        self.assertEqual(first_calls.calls[0]["model"], "model-a")
        self.assertEqual(first_calls.calls[0]["max_tokens"], 512)
        self.assertEqual(second_calls.calls[0]["model"], "model-b")
        self.assertEqual(second_calls.calls[0]["max_tokens"], 1024)

    def test_all_five_public_methods_resolve_once_and_use_local_runtime(self):
        specs = [
            (
                "diagnose-model",
                501,
                '{"is_fault": false, "fault_type": null, "confidence": 0.9, "reasoning": "ok"}',
            ),
            (
                "predict-model",
                502,
                '{"health_status": "green", "risk_summary": "ok", "risk_details": []}',
            ),
            (
                "locate-model",
                503,
                '{"reasoning": "ok", "recovery_hint": null}',
            ),
            (
                "preprocess-model",
                504,
                '{"cleaned_text": "log", "summary": "ok", "extracted_keywords": [], "suggested_fault_type": null}',
            ),
            (
                "parse-model",
                505,
                '{"summary": "ok", "severity": "low", "parsed_events": [], "fault_signals": [], "suggested_fault_type": null, "confidence": 0.0, "root_cause_analysis": "", "recovery_hint": null}',
            ),
        ]
        handles_and_calls = [runtime(*spec) for spec in specs]
        provider = SequenceProvider([item[0] for item in handles_and_calls])
        service = LLMService(runtime_provider=provider)

        service.diagnose("log", "context")
        service.predict("log", {})
        service.locate("log", "context")
        service.preprocess("log")
        service.parse_log("log")

        self.assertEqual(provider.resolve_calls, 5)
        calls = [item[1].calls[0] for item in handles_and_calls]
        self.assertEqual([call["model"] for call in calls], [spec[0] for spec in specs])
        self.assertEqual(calls[0]["temperature"], 0.1)
        self.assertEqual(calls[0]["max_tokens"], 501)
        self.assertEqual(calls[1]["temperature"], 0.2)
        self.assertNotIn("max_tokens", calls[1])
        self.assertEqual(calls[2]["temperature"], 0.1)
        self.assertNotIn("max_tokens", calls[2])
        self.assertEqual(calls[3]["temperature"], 0.1)
        self.assertNotIn("max_tokens", calls[3])
        self.assertEqual(calls[4]["temperature"], 0.1)
        self.assertEqual(calls[4]["max_tokens"], 1500)

        for call in calls:
            self.assertEqual(
                [message["role"] for message in call["messages"]],
                ["system", "user"],
            )

        prompt_expectations = [
            (
                "必须以JSON格式返回",
                ["## 待诊断日志\nlog", "## 参考知识库案例\ncontext"],
            ),
            (
                "前瞻性风险预测与预警",
                ["## 系统日志\nlog", "## 系统指标", "- CPU使用率: N/A%"],
            ),
            (
                "故障类型已由检索系统确认",
                ["## 检索确认结果\ncontext", "## 日志片段（异常行优先）\nlog"],
            ),
            (
                "清洗文本",
                ["## 原始日志\nlog", "请处理并返回JSON。"],
            ),
            (
                "严格只返回 JSON",
                ["## 原始日志\nlog", "请按要求返回 JSON。"],
            ),
        ]
        for call, (system_text, user_texts) in zip(calls, prompt_expectations):
            self.assertIn(system_text, call["messages"][0]["content"])
            for user_text in user_texts:
                self.assertIn(user_text, call["messages"][1]["content"])

    def test_parse_log_uses_larger_runtime_token_limit(self):
        handle, completions = runtime(
            "parse-model",
            2048,
            '{"summary": "ok", "severity": "low", "parsed_events": []}',
        )
        service = LLMService(runtime_provider=SequenceProvider([handle]))

        service.parse_log("log")

        self.assertEqual(completions.calls[0]["max_tokens"], 2048)

    def test_service_does_not_keep_mutable_client_or_model_fields(self):
        service = LLMService(runtime_provider=SequenceProvider([]))
        source = Path("app/services/llm_service.py").read_text(encoding="utf-8")

        self.assertFalse(hasattr(service, "client"))
        self.assertFalse(hasattr(service, "model"))
        self.assertFalse(hasattr(service, "max_tokens"))
        self.assertNotIn("self.client", source)
        self.assertNotIn("self.model", source)
        self.assertNotIn("self.max_tokens", source)
        self.assertEqual(source.count("runtime = self._runtime()"), 5)
        self.assertEqual(source.count("self._chat_completion_content("), 4)
        self.assertEqual(source.count("self._stream_chat_completion_content("), 1)
        self.assertEqual(source.count(".chat.completions.create("), 2)

    def test_unavailable_runtime_raises_existing_public_call_error_shape(self):
        service = LLMService(runtime_provider=SequenceProvider([None]))

        with self.assertRaisesRegex(RuntimeError, "大模型未配置或已关闭"):
            service.predict("log", {})

    def test_is_available_resolves_current_runtime_and_handles_errors(self):
        available, _ = runtime("model", 1024, "{}")
        failing_provider = MagicMock()
        failing_provider.resolve.side_effect = ApiKeyCipherUnavailable(
            "stored API key cannot be decrypted"
        )

        self.assertTrue(
            LLMService(runtime_provider=SequenceProvider([available])).is_available()
        )
        self.assertFalse(
            LLMService(runtime_provider=SequenceProvider([None])).is_available()
        )
        self.assertFalse(
            LLMService(runtime_provider=failing_provider).is_available()
        )

    def test_all_public_calls_sanitize_runtime_resolution_failures(self):
        plaintext = "resolver-plaintext-key"
        ciphertext = "resolver-stored-ciphertext"
        original_text = "resolver failure detail"
        method_calls = [
            ("diagnose", ("log", "context")),
            ("predict", ("log", {})),
            ("locate", ("log", "context")),
            ("preprocess", ("log",)),
            ("parse_log", ("log",)),
        ]

        for method_name, args in method_calls:
            with self.subTest(method=method_name):
                provider = MagicMock()
                provider.resolve.side_effect = ApiKeyCipherUnavailable(
                    f"{original_text}: {plaintext} {ciphertext}"
                )
                service = LLMService(runtime_provider=provider)

                with self.assertRaises(RuntimeError) as caught:
                    getattr(service, method_name)(*args)

                error = caught.exception
                self.assertEqual(
                    str(error),
                    "LLM runtime configuration could not be resolved",
                )
                self.assertNotIn(plaintext, str(error))
                self.assertNotIn(ciphertext, str(error))
                self.assertNotIn(original_text, str(error))
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)
                provider.resolve.assert_called_once_with()

    def test_is_available_logs_fixed_warning_for_runtime_resolution_failure(self):
        plaintext = "availability-plaintext-key"
        ciphertext = "availability-stored-ciphertext"
        provider = MagicMock()
        provider.resolve.side_effect = ApiKeyCipherUnavailable(
            f"availability failure: {plaintext} {ciphertext}"
        )

        with self.assertLogs("app.services.llm_service", level="WARNING") as logs:
            available = LLMService(runtime_provider=provider).is_available()

        self.assertFalse(available)
        self.assertEqual(
            logs.output,
            ["WARNING:app.services.llm_service:[LLMService] runtime configuration is unavailable"],
        )
        provider.resolve.assert_called_once_with()

    def test_all_five_chat_completion_exceptions_are_sanitized_without_chaining(self):
        plaintext = "provider-plaintext-key"
        ciphertext = "provider-stored-ciphertext"
        method_calls = [
            ("diagnose", ("log", "context")),
            ("predict", ("log", {})),
            ("locate", ("log", "context")),
            ("preprocess", ("log",)),
            ("parse_log", ("log",)),
        ]

        for method_name, args in method_calls:
            with self.subTest(method=method_name):
                completions = FailingCompletions(
                    ValueError(f"provider rejected {plaintext} and {ciphertext}")
                )
                client = RecordingClient(completions)
                handle = LLMRuntimeHandle(
                    client,
                    "model",
                    1024,
                    "database",
                    _redaction_secrets=(plaintext, ciphertext),
                )
                provider = SequenceProvider([handle])
                service = LLMService(runtime_provider=provider)

                with self.assertRaises(RuntimeError) as caught:
                    getattr(service, method_name)(*args)

                error = caught.exception
                self.assertEqual(provider.resolve_calls, 1)
                self.assertNotIn(plaintext, str(error))
                self.assertNotIn(ciphertext, str(error))
                self.assertNotIn(plaintext, repr(error))
                self.assertNotIn(ciphertext, repr(error))
                self.assertIn("***", str(error))
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)

    def test_all_five_methods_redact_provider_response_content_before_return(self):
        plaintext = "echoed-plaintext-key"
        ciphertext = "echoed-stored-ciphertext"
        secrets = (plaintext, ciphertext)
        specs = [
            (
                "diagnose",
                ("log", "context"),
                {"is_fault": True, "fault_type": "x", "confidence": 0.8,
                 "reasoning": f"{plaintext} {ciphertext}"},
            ),
            (
                "predict",
                ("log", {}),
                {"health_status": "yellow", "risk_summary": f"{plaintext} {ciphertext}",
                 "risk_details": []},
            ),
            (
                "locate",
                ("log", "context"),
                {"reasoning": f"{plaintext} {ciphertext}", "recovery_hint": None},
            ),
            (
                "preprocess",
                ("log",),
                {"cleaned_text": "log", "summary": f"{plaintext} {ciphertext}",
                 "extracted_keywords": [], "suggested_fault_type": None},
            ),
            (
                "parse_log",
                ("log",),
                {"summary": f"{plaintext} {ciphertext}", "severity": "low",
                 "parsed_events": [], "fault_signals": [],
                 "suggested_fault_type": None, "confidence": 0.0,
                 "root_cause_analysis": "", "recovery_hint": None},
            ),
        ]

        for method_name, args, response_body in specs:
            with self.subTest(method=method_name):
                handle, _ = runtime(
                    "model",
                    1024,
                    json.dumps(response_body),
                    secrets=secrets,
                )
                result = getattr(
                    LLMService(runtime_provider=SequenceProvider([handle])),
                    method_name,
                )(*args)
                serialized = json.dumps(result, ensure_ascii=False)

                self.assertNotIn(plaintext, serialized)
                self.assertNotIn(ciphertext, serialized)
                self.assertIn("***", serialized)

    def test_malformed_response_fallback_shapes_are_preserved_and_redacted(self):
        plaintext = "malformed-plaintext-key"
        ciphertext = "malformed-stored-ciphertext"
        raw_content = f"not-json {plaintext} {ciphertext}"
        handles = [
            runtime("predict-model", 1024, raw_content, (plaintext, ciphertext))[0],
            runtime("locate-model", 1024, raw_content, (plaintext, ciphertext))[0],
            runtime("parse-model", 1024, raw_content, (plaintext, ciphertext))[0],
        ]
        service = LLMService(runtime_provider=SequenceProvider(handles))

        with self.assertLogs("app.services.llm_service", level="INFO") as logs:
            prediction = service.predict("log", {})
            location = service.locate("log", "context")
            parsed = service.parse_log("log")

        self.assertIsNone(prediction)
        self.assertEqual(
            location,
            {"reasoning": "not-json *** ***", "recovery_hint": None},
        )
        self.assertEqual(
            parsed,
            {
                "summary": "not-json *** ***…",
                "severity": "low",
                "parsed_events": [],
                "fault_signals": [],
                "suggested_fault_type": None,
                "confidence": 0.0,
                "root_cause_analysis": "",
                "recovery_hint": None,
                "_truncated": True,
            },
        )
        observable = "\n".join(logs.output) + repr(location) + repr(parsed)
        self.assertNotIn(plaintext, observable)
        self.assertNotIn(ciphertext, observable)

    def test_fault_location_availability_delegates_to_llm_service(self):
        service = FaultLocationService.__new__(FaultLocationService)
        service._llm = MagicMock()
        service._llm.is_available.return_value = True

        self.assertTrue(service._llm_available())
        service._llm.is_available.assert_called_once_with()

    def test_fault_location_uses_success_snapshot_for_authoritative_llm_result(self):
        service = FaultLocationService.__new__(FaultLocationService)
        service._llm_available = MagicMock(return_value=False)
        db = MagicMock()

        def assign_database_values(record):
            record.id = 7
            record.created_at = datetime(2026, 7, 15, 12, 0, 0)

        db.add.side_effect = assign_database_values
        top_candidate = {
            "fault_type": "retrieval-type",
            "kg_root_causes": [
                {
                    "root_cause": "retrieval-root-cause",
                    "root_cause_type": "retrieval",
                    "recovery_hint": None,
                }
            ],
            "all_cases": [],
        }
        reasoning_path = []

        with patch(
            "app.services.fault_location_service.kg_service_v2.get_affected_components",
            return_value=[],
        ):
            output = service._build_output(
                db=db,
                log_text="log",
                run_id="",
                channel="fast",
                top_candidate=top_candidate,
                candidate_list=[top_candidate],
                final_confidence=0.6,
                top_sim=0.8,
                llm_result={
                    "fault_type": "llm-type",
                    "confidence": 0.91,
                    "root_cause": "llm-root-cause",
                    "reasoning": "llm-reasoning",
                    "recovery_hint": "llm-recovery",
                },
                llm_ok=True,
                reasoning_path=reasoning_path,
                n_windows=1,
            )

        self.assertEqual(output.fault_type_name, "llm-type")
        self.assertEqual(output.confidence, 0.91)
        self.assertEqual(output.root_cause, "llm-root-cause")
        self.assertIn(
            "[LLM] fault_type / confidence determined by LLM (authoritative)",
            reasoning_path,
        )
        service._llm_available.assert_not_called()


if __name__ == "__main__":
    unittest.main()
