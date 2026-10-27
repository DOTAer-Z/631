import hashlib
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.models.model_api_config import ModelApiConfig
from app.services.api_key_cipher import ApiKeyCipher, ApiKeyCipherUnavailable
from app.services.llm_runtime_config import (
    LLMRuntimeConfigProvider,
    LLMRuntimeHandle,
    OpenAIClientCache,
)


class FakeClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


class LLMRuntimeConfigProviderTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        ModelApiConfig.__table__.create(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.cipher = ApiKeyCipher(Fernet.generate_key().decode("ascii"))
        self.created_clients = []

        def client_factory(**kwargs):
            client = FakeClient(**kwargs)
            self.created_clients.append(client)
            return client

        self.cache = OpenAIClientCache(client_factory=client_factory)
        self.provider = LLMRuntimeConfigProvider(
            session_factory=self.Session,
            cipher_factory=lambda: self.cipher,
            client_cache=self.cache,
        )

    def tearDown(self):
        ModelApiConfig.__table__.drop(bind=self.engine)
        self.engine.dispose()

    def add_active(self, model="db-model"):
        with self.Session() as db:
            row = ModelApiConfig(
                name="Database",
                provider="vllm",
                base_url="http://db-vllm:8000/v1",
                model=model,
                api_key_ciphertext=self.cipher.encrypt("db-secret"),
                timeout_seconds=30,
                max_output_tokens=2048,
                is_active=True,
            )
            db.add(row)
            db.commit()
            return row.id

    def test_active_database_row_overrides_environment(self):
        self.add_active()
        with patch.multiple(
            settings,
            ENABLE_LLM=True,
            LLM_BASE_URL="http://env:8000/v1",
            LLM_API_KEY="env-secret",
            LLM_MODEL="env-model",
        ):
            handle = self.provider.resolve()

        self.assertEqual(handle.model, "db-model")
        self.assertEqual(handle.max_output_tokens, 2048)
        self.assertEqual(handle.source, "database")
        self.assertEqual(
            handle.client.kwargs,
            {
                "api_key": "db-secret",
                "base_url": "http://db-vllm:8000/v1",
                "timeout": 30,
            },
        )

    def test_no_active_row_uses_existing_environment_fallback(self):
        with patch.multiple(
            settings,
            ENABLE_LLM=True,
            LLM_BASE_URL="http://env:8000/v1",
            LLM_API_KEY="env-secret",
            DASHSCOPE_API_KEY="",
            LLM_MODEL="env-model",
            LLM_TIMEOUT=60,
            LLM_MAX_OUTPUT_TOKENS=1024,
        ):
            handle = self.provider.resolve()

        self.assertEqual(handle.model, "env-model")
        self.assertEqual(handle.max_output_tokens, 1024)
        self.assertEqual(handle.source, "environment")
        self.assertEqual(handle.client.kwargs["api_key"], "env-secret")

    def test_database_only_resolution_rejects_environment_fallback(self):
        with patch.multiple(
            settings,
            ENABLE_LLM=True,
            LLM_BASE_URL="http://env:8000/v1",
            LLM_API_KEY="env-secret",
            DASHSCOPE_API_KEY="",
            LLM_MODEL="env-model",
        ):
            handle = self.provider.resolve(allow_environment_fallback=False)

        self.assertIsNone(handle)
        self.assertEqual(self.created_clients, [])

    def test_database_only_resolution_still_uses_active_database_row(self):
        self.add_active(model="gateway-model")

        handle = self.provider.resolve(allow_environment_fallback=False)

        self.assertEqual(handle.model, "gateway-model")
        self.assertEqual(handle.source, "database")

    def test_no_active_row_and_disabled_environment_returns_none(self):
        with patch.multiple(
            settings,
            ENABLE_LLM=False,
            LLM_BASE_URL="http://env:8000/v1",
            LLM_API_KEY="env-secret",
            LLM_MODEL="env-model",
        ):
            handle = self.provider.resolve()

        self.assertIsNone(handle)
        self.assertEqual(self.created_clients, [])

    def test_database_change_is_observed_on_next_resolve(self):
        row_id = self.add_active()
        first = self.provider.resolve()
        with self.Session() as db:
            row = db.get(ModelApiConfig, row_id)
            row.model = "db-model-v2"
            db.commit()
        second = self.provider.resolve()

        self.assertEqual(first.model, "db-model")
        self.assertEqual(second.model, "db-model-v2")
        self.assertIs(first.client, second.client)

    def test_undecryptable_active_row_does_not_fall_back_to_environment(self):
        self.add_active()
        wrong_cipher = ApiKeyCipher(Fernet.generate_key().decode("ascii"))
        provider = LLMRuntimeConfigProvider(
            session_factory=self.Session,
            cipher_factory=lambda: wrong_cipher,
            client_cache=self.cache,
        )
        with patch.multiple(
            settings,
            ENABLE_LLM=True,
            LLM_BASE_URL="http://env:8000/v1",
            LLM_API_KEY="env-secret",
            LLM_MODEL="env-model",
        ):
            with self.assertRaises(ApiKeyCipherUnavailable):
                provider.resolve()

        self.assertEqual(self.created_clients, [])

    def test_runtime_handle_is_immutable(self):
        handle = LLMRuntimeHandle(object(), "model", 128, "database")

        with self.assertRaises(FrozenInstanceError):
            handle.model = "changed"

    def test_active_row_releases_session_before_decrypt_and_cache_resolution(self):
        self.add_active()
        state = {"session_closed": False}
        session_factory = self.Session

        class TrackedSessionContext:
            def __init__(self):
                self.session = session_factory()

            def __enter__(self):
                return self.session

            def __exit__(self, exc_type, exc, traceback):
                try:
                    return self.session.__exit__(exc_type, exc, traceback)
                finally:
                    state["session_closed"] = True

        class TrackingCipher:
            def decrypt(inner_self, ciphertext):
                self.assertTrue(state["session_closed"])
                return self.cipher.decrypt(ciphertext)

        def client_factory(**kwargs):
            self.assertTrue(state["session_closed"])
            return FakeClient(**kwargs)

        provider = LLMRuntimeConfigProvider(
            session_factory=TrackedSessionContext,
            cipher_factory=TrackingCipher,
            client_cache=OpenAIClientCache(client_factory=client_factory),
        )

        handle = provider.resolve()

        self.assertEqual(handle.model, "db-model")

    def test_runtime_handle_repr_does_not_expose_client_or_redaction_secrets(self):
        plaintext = "repr-plaintext-key"
        ciphertext = "repr-stored-ciphertext"

        class SecretReprClient:
            def __repr__(self):
                return f"SecretReprClient(api_key={plaintext!r})"

        handle = LLMRuntimeHandle(
            SecretReprClient(),
            "model",
            128,
            "database",
            _redaction_secrets=(plaintext, ciphertext),
        )

        representation = repr(handle)
        self.assertNotIn(plaintext, representation)
        self.assertNotIn(ciphertext, representation)

    def test_database_runtime_redacts_plaintext_and_stored_ciphertext(self):
        row_id = self.add_active()
        with self.Session() as db:
            ciphertext = db.get(ModelApiConfig, row_id).api_key_ciphertext

        handle = self.provider.resolve()
        redacted = handle.redact(f"db-secret {ciphertext}")

        self.assertEqual(redacted, "*** ***")
        self.assertNotIn("db-secret", repr(handle))
        self.assertNotIn(ciphertext, repr(handle))
        cache_keys = repr(tuple(self.cache._clients))
        self.assertNotIn("db-secret", cache_keys)
        self.assertNotIn(ciphertext, cache_keys)

    def test_client_initialization_error_redacts_secrets_without_chaining(self):
        row_id = self.add_active()
        with self.Session() as db:
            ciphertext = db.get(ModelApiConfig, row_id).api_key_ciphertext

        def failing_client_factory(**kwargs):
            raise ValueError(f"cannot use {kwargs['api_key']} or {ciphertext}")

        provider = LLMRuntimeConfigProvider(
            session_factory=self.Session,
            cipher_factory=lambda: self.cipher,
            client_cache=OpenAIClientCache(
                client_factory=failing_client_factory,
            ),
        )

        with self.assertRaises(RuntimeError) as caught:
            provider.resolve()

        error = caught.exception
        self.assertNotIn("db-secret", str(error))
        self.assertNotIn(ciphertext, str(error))
        self.assertIn("***", str(error))
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)


class OpenAIClientCacheTests(unittest.TestCase):
    def test_client_cache_key_uses_hash_not_plaintext(self):
        cache = OpenAIClientCache(client_factory=FakeClient)

        cache.get("http://vllm:8000/v1", "db-secret", 30)
        cache_key = next(iter(cache._clients))

        self.assertNotIn("db-secret", repr(cache_key))
        self.assertIn(hashlib.sha256(b"db-secret").hexdigest(), cache_key)

    def test_cache_eviction_does_not_close_potentially_referenced_client(self):
        cache = OpenAIClientCache(client_factory=FakeClient, max_size=2)
        first = cache.get("http://one/v1", "secret-one", 30)
        second = cache.get("http://two/v1", "secret-two", 30)

        cache.get("http://three/v1", "secret-three", 30)

        self.assertEqual(len(cache._clients), 2)
        self.assertNotIn(first, cache._clients.values())
        self.assertEqual(first.close_calls, 0)
        self.assertEqual(second.close_calls, 0)

    def test_concurrent_requests_reuse_one_client(self):
        created_clients = []

        def client_factory(**kwargs):
            time.sleep(0.02)
            client = FakeClient(**kwargs)
            created_clients.append(client)
            return client

        cache = OpenAIClientCache(client_factory=client_factory)
        with ThreadPoolExecutor(max_workers=8) as executor:
            clients = list(
                executor.map(
                    lambda _: cache.get("http://vllm/v1", "secret", 30),
                    range(32),
                )
            )

        self.assertEqual(len(created_clients), 1)
        self.assertTrue(all(client is clients[0] for client in clients))


if __name__ == "__main__":
    unittest.main()
