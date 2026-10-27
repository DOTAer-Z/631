from types import SimpleNamespace
import time
import unittest

from app.services.llm_runtime_config import LLMRuntimeHandle
from app.services.llm_service import LLMService
from app.services.log_parse_errors import LogParseCancelled, LogParseTimedOut


class FakeStream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def __iter__(self):
        return iter(self.chunks)

    def close(self):
        self.closed = True


def chunk(*, content=None, reasoning=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=content,
                    reasoning_content=reasoning,
                )
            )
        ]
    )


class FakeCompletions:
    def __init__(self, stream):
        self.stream = stream
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.stream


class FakeClient:
    def __init__(self, stream):
        self.completions = FakeCompletions(stream)
        self.chat = SimpleNamespace(completions=self.completions)
        self.options_calls = []

    def with_options(self, **kwargs):
        self.options_calls.append(kwargs)
        return self


class Provider:
    def __init__(self, handle):
        self.handle = handle

    def resolve(self):
        return self.handle


class LLMLogParseStreamingTests(unittest.TestCase):
    def service(self, chunks):
        stream = FakeStream(chunks)
        client = FakeClient(stream)
        runtime = LLMRuntimeHandle(client, "qwen", 1024, "database")
        return LLMService(runtime_provider=Provider(runtime)), client, stream

    def test_streams_json_excludes_reasoning_and_reports_progress(self):
        service, client, stream = self.service(
            [
                chunk(reasoning="thinking"),
                chunk(content='{"summary":"ok","severity":"low",'),
                chunk(content='"parsed_events":[],"fault_signals":[]}'),
            ]
        )
        progress = []

        result = service.parse_log(
            "INFO ready",
            on_progress=lambda received, budget: progress.append((received, budget)),
            deadline=time.monotonic() + 60,
        )

        self.assertEqual(result["summary"], "ok")
        self.assertNotIn("thinking", str(result))
        self.assertTrue(progress)
        self.assertEqual(client.options_calls[0]["max_retries"], 0)
        self.assertLessEqual(client.options_calls[0]["timeout"], 60)
        self.assertTrue(client.completions.calls[0]["stream"])
        self.assertTrue(stream.closed)

    def test_cancel_before_next_chunk_closes_stream(self):
        service, _, stream = self.service([chunk(content="{")])

        with self.assertRaises(LogParseCancelled):
            service.parse_log("ERROR", should_cancel=lambda: True)

        self.assertTrue(stream.closed)

    def test_expired_deadline_closes_stream_and_raises_timeout(self):
        service, _, stream = self.service([chunk(content="{")])

        with self.assertRaises(LogParseTimedOut):
            service.parse_log("ERROR", deadline=time.monotonic() - 1)

        self.assertTrue(stream.closed)

    def test_event_ids_are_preserved_for_streamed_result(self):
        service, _, _ = self.service(
            [
                chunk(
                    content=(
                        '{"summary":"fault","severity":"high","parsed_events":'
                        '[{"message":"panic"}],"fault_signals":["panic"]}'
                    )
                )
            ]
        )

        result = service.parse_log("ERROR panic")

        self.assertEqual(result["parsed_events"][0]["id"], 1)


if __name__ == "__main__":
    unittest.main()
