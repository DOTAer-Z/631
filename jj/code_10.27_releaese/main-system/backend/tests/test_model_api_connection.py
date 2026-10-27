import unittest
from dataclasses import FrozenInstanceError, fields, is_dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock

from cryptography.fernet import Fernet

from app.models.model_api_config import ModelApiConfig
from app.services.api_key_cipher import ApiKeyCipher
from app.services.model_api_config_service import (
    ModelApiConfigService,
    ModelApiConnectionTestSnapshot,
)
from app.services.model_api_connection import ModelApiConnectionTester


class FakeCompletions:
    def __init__(self, error=None):
        self.error = error
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
        )


class FakeClient:
    def __init__(self, completions):
        self.chat = SimpleNamespace(completions=completions)
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


class ReplacementTrackingString(str):
    def __new__(cls, value):
        instance = super().__new__(cls, value)
        instance.replacements = []
        return instance

    def replace(self, old, new, count=-1):
        self.replacements.append(old)
        return self


class ModelApiConnectionTesterTests(unittest.TestCase):
    def setUp(self):
        self.cipher = ApiKeyCipher(Fernet.generate_key().decode("ascii"))
        self.row = ModelApiConfig(
            id=1,
            name="Test",
            provider="vllm",
            base_url="http://vllm:8000/v1",
            model="qwen",
            api_key_ciphertext=self.cipher.encrypt("sk-secret"),
            timeout_seconds=60,
            max_output_tokens=1024,
            is_active=False,
        )
        self.snapshot = ModelApiConnectionTestSnapshot(
            base_url=self.row.base_url,
            model=self.row.model,
            api_key_ciphertext=self.row.api_key_ciphertext,
            timeout_seconds=self.row.timeout_seconds,
        )

    def test_provider_failure_redacts_plaintext_and_ciphertext_and_closes_client(self):
        ciphertext = self.snapshot.api_key_ciphertext
        completions = FakeCompletions(
            RuntimeError(
                f"authorization failed for sk-secret using stored value {ciphertext}"
            )
        )
        client = FakeClient(completions)
        tester = ModelApiConnectionTester(
            self.cipher, client_factory=lambda **kwargs: client
        )

        result = tester.test(self.snapshot)

        self.assertFalse(result.success)
        self.assertNotIn("sk-secret", result.error)
        self.assertNotIn(ciphertext, result.error)
        self.assertEqual(
            result.error, "authorization failed for *** using stored value ***"
        )
        self.assertEqual(result.model, "qwen")
        self.assertLessEqual(len(result.error), 500)
        self.assertEqual(client.close_calls, 1)

    def test_success_redacts_plaintext_and_ciphertext_and_closes_client(self):
        ciphertext = self.snapshot.api_key_ciphertext
        completions = FakeCompletions()
        completions.create = lambda **kwargs: SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=f"OK sk-secret stored {ciphertext}"
                    )
                )
            ]
        )
        client = FakeClient(completions)
        tester = ModelApiConnectionTester(
            self.cipher, client_factory=lambda **kwargs: client
        )

        result = tester.test(self.snapshot)

        self.assertTrue(result.success)
        self.assertNotIn("sk-secret", result.message)
        self.assertNotIn(ciphertext, result.message)
        self.assertEqual(result.message, "OK *** stored ***")
        self.assertEqual(client.close_calls, 1)
        self.assertIsNone(result.error)

    def test_success_uses_minimal_deterministic_request(self):
        completions = FakeCompletions()
        tester = ModelApiConnectionTester(
            self.cipher, client_factory=lambda **kwargs: FakeClient(completions)
        )

        tester.test(self.snapshot)

        self.assertEqual(completions.kwargs["temperature"], 0)
        self.assertEqual(completions.kwargs["max_tokens"], 8)
        self.assertEqual(
            completions.kwargs["messages"],
            [{"role": "user", "content": "Reply with OK."}],
        )

    def test_client_creation_failure_returns_bounded_secret_free_result(self):
        def fail_client_factory(**kwargs):
            raise RuntimeError("invalid key sk-secret" + "x" * 600)

        result = ModelApiConnectionTester(
            self.cipher, client_factory=fail_client_factory
        ).test(self.snapshot)

        self.assertFalse(result.success)
        self.assertTrue(result.error.startswith("invalid key ***"))
        self.assertNotIn("sk-secret", result.error)
        self.assertLessEqual(len(result.error), 500)

    def test_redaction_sorts_and_deduplicates_secrets_longest_first(self):
        ciphertext = self.snapshot.api_key_ciphertext
        plaintext_prefix = ciphertext[:6]
        value = ReplacementTrackingString("provider output")

        ModelApiConnectionTester._redact(
            value,
            plaintext_prefix,
            ciphertext,
            plaintext_prefix,
            "",
        )

        self.assertEqual(value.replacements, [ciphertext, plaintext_prefix])

    def test_success_redacts_overlapping_plaintext_and_fernet_prefix(self):
        plaintext_prefix = "gAAAAA"
        ciphertext = self.cipher.encrypt(plaintext_prefix)
        snapshot = ModelApiConnectionTestSnapshot(
            base_url=self.snapshot.base_url,
            model=self.snapshot.model,
            api_key_ciphertext=ciphertext,
            timeout_seconds=self.snapshot.timeout_seconds,
        )
        completions = FakeCompletions()
        completions.create = lambda **kwargs: SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=f"OK {ciphertext}")
                )
            ]
        )
        tester = ModelApiConnectionTester(
            self.cipher,
            client_factory=lambda **kwargs: FakeClient(completions),
        )

        result = tester.test(snapshot)

        self.assertTrue(result.success)
        self.assertEqual(result.message, "OK ***")
        self.assertNotIn(ciphertext, result.message)

    def test_error_redacts_overlapping_plaintext_and_fernet_prefix(self):
        plaintext_prefix = "gAAAAA"
        ciphertext = self.cipher.encrypt(plaintext_prefix)
        snapshot = ModelApiConnectionTestSnapshot(
            base_url=self.snapshot.base_url,
            model=self.snapshot.model,
            api_key_ciphertext=ciphertext,
            timeout_seconds=self.snapshot.timeout_seconds,
        )
        completions = FakeCompletions(
            RuntimeError(f"authorization failed for {ciphertext}")
        )
        tester = ModelApiConnectionTester(
            self.cipher,
            client_factory=lambda **kwargs: FakeClient(completions),
        )

        result = tester.test(snapshot)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "authorization failed for ***")
        self.assertNotIn(ciphertext, result.error)

    def test_snapshot_is_minimal_immutable_and_rollback_precedes_network(self):
        events = []
        query = MagicMock()
        query.filter.return_value.first.side_effect = lambda: (
            events.append("query") or self.row
        )
        db = MagicMock()
        db.query.return_value = query
        db.rollback.side_effect = lambda: events.append("rollback")
        service = ModelApiConfigService(db, self.cipher)

        snapshot = service.get_connection_test_snapshot(self.row.id)

        self.assertTrue(is_dataclass(snapshot))
        self.assertEqual(
            {field.name for field in fields(snapshot)},
            {"base_url", "model", "api_key_ciphertext", "timeout_seconds"},
        )
        with self.assertRaises(FrozenInstanceError):
            snapshot.model = "mutated"

        completions = FakeCompletions()

        def create(**kwargs):
            events.append("network")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
            )

        completions.create = create

        def client_factory(**kwargs):
            events.append("client_factory")
            return FakeClient(completions)

        result = ModelApiConnectionTester(
            self.cipher,
            client_factory=client_factory,
        ).test(snapshot)

        self.assertTrue(result.success)
        self.assertEqual(
            events,
            ["query", "rollback", "client_factory", "network"],
        )


if __name__ == "__main__":
    unittest.main()
