import time
from typing import Callable

from openai import OpenAI

from app.schemas.model_api_config import ModelApiTestResult
from app.services.api_key_cipher import ApiKeyCipher
from app.services.model_api_config_service import ModelApiConnectionTestSnapshot


class ModelApiConnectionTester:
    def __init__(self, cipher: ApiKeyCipher, client_factory: Callable = OpenAI):
        self.cipher = cipher
        self.client_factory = client_factory

    def test(self, snapshot: ModelApiConnectionTestSnapshot) -> ModelApiTestResult:
        api_key = self.cipher.decrypt(snapshot.api_key_ciphertext)
        started = time.perf_counter()
        client = None
        try:
            client = self.client_factory(
                api_key=api_key,
                base_url=snapshot.base_url,
                timeout=snapshot.timeout_seconds,
            )
            response = client.chat.completions.create(
                model=snapshot.model,
                messages=[{"role": "user", "content": "Reply with OK."}],
                temperature=0,
                max_tokens=8,
            )
            message = self._redact(
                (response.choices[0].message.content or "OK").strip(),
                api_key,
                snapshot.api_key_ciphertext,
            )[:200]
            return ModelApiTestResult(
                success=True,
                latency_ms=self._latency_ms(started),
                model=snapshot.model,
                message=message or "OK",
                error=None,
            )
        except Exception as exc:
            error = self._redact(
                str(exc), api_key, snapshot.api_key_ciphertext
            )[:500]
            return ModelApiTestResult(
                success=False,
                latency_ms=self._latency_ms(started),
                model=snapshot.model,
                message=None,
                error=error or "connection test failed",
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    @staticmethod
    def _latency_ms(started: float) -> int:
        return round((time.perf_counter() - started) * 1000)

    @staticmethod
    def _redact(value: str, *secrets: str) -> str:
        ordered_secrets = sorted(
            {secret for secret in secrets if secret}, key=len, reverse=True
        )
        for secret in ordered_secrets:
            value = value.replace(secret, "***")
        return value
