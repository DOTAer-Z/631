import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import RLock
from typing import Callable

from openai import OpenAI

from app.config import settings
from app.database import SessionLocal
from app.models.model_api_config import ModelApiConfig
from app.services.api_key_cipher import ApiKeyCipher


@dataclass(frozen=True)
class LLMRuntimeHandle:
    client: object = field(repr=False)
    model: str
    max_output_tokens: int
    source: str
    _redaction_secrets: tuple[str, ...] = field(
        default=(),
        repr=False,
        compare=False,
    )

    def redact(self, value: str | None) -> str | None:
        if value is None:
            return value
        for secret in sorted(
            (secret for secret in self._redaction_secrets if secret),
            key=len,
            reverse=True,
        ):
            value = value.replace(secret, "***")
        return value


class OpenAIClientCache:
    def __init__(
        self,
        client_factory: Callable[..., object] = OpenAI,
        max_size: int = 16,
    ) -> None:
        self.client_factory = client_factory
        self.max_size = max_size
        self._clients: OrderedDict[tuple[str, int, str], object] = OrderedDict()
        self._lock = RLock()

    def get(self, base_url: str, api_key: str, timeout_seconds: int) -> object:
        fingerprint = (
            base_url,
            timeout_seconds,
            hashlib.sha256(api_key.encode("utf-8")).hexdigest(),
        )
        with self._lock:
            client = self._clients.get(fingerprint)
            if client is None:
                client = self.client_factory(
                    api_key=api_key,
                    base_url=base_url,
                    timeout=timeout_seconds,
                )
                self._clients[fingerprint] = client
                if len(self._clients) > self.max_size:
                    self._clients.popitem(last=False)
            else:
                self._clients.move_to_end(fingerprint)

        return client


class LLMRuntimeConfigProvider:
    def __init__(
        self,
        session_factory: Callable = SessionLocal,
        cipher_factory: Callable[[], ApiKeyCipher] = ApiKeyCipher.from_settings,
        client_cache: OpenAIClientCache | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.cipher_factory = cipher_factory
        self.client_cache = client_cache or OpenAIClientCache()

    def resolve(
        self,
        *,
        allow_environment_fallback: bool = True,
    ) -> LLMRuntimeHandle | None:
        database_config = None
        with self.session_factory() as db:
            row = (
                db.query(ModelApiConfig)
                .filter(ModelApiConfig.is_active.is_(True))
                .one_or_none()
            )
            if row is not None:
                database_config = (
                    row.base_url,
                    row.model,
                    row.api_key_ciphertext,
                    row.timeout_seconds,
                    row.max_output_tokens,
                )

        if database_config is not None:
            base_url, model, ciphertext, timeout_seconds, max_output_tokens = (
                database_config
            )
            api_key = self.cipher_factory().decrypt(ciphertext)
            redaction_secrets = (api_key, ciphertext)
            client = self._get_client(
                base_url,
                api_key,
                timeout_seconds,
                redaction_secrets,
            )
            return LLMRuntimeHandle(
                client=client,
                model=model,
                max_output_tokens=max_output_tokens,
                source="database",
                _redaction_secrets=redaction_secrets,
            )

        if not allow_environment_fallback:
            return None

        api_key = settings.LLM_API_KEY or settings.DASHSCOPE_API_KEY
        if not settings.ENABLE_LLM or not (
            settings.LLM_BASE_URL and settings.LLM_MODEL and api_key
        ):
            return None
        return LLMRuntimeHandle(
            client=self._get_client(
                settings.LLM_BASE_URL,
                api_key,
                settings.LLM_TIMEOUT,
                (api_key,),
            ),
            model=settings.LLM_MODEL,
            max_output_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
            source="environment",
            _redaction_secrets=(api_key,),
        )

    def _get_client(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: int,
        redaction_secrets: tuple[str, ...],
    ) -> object:
        error_message = None
        try:
            client = self.client_cache.get(base_url, api_key, timeout_seconds)
        except Exception as exc:
            detail = str(exc)
            for secret in sorted(
                (secret for secret in redaction_secrets if secret),
                key=len,
                reverse=True,
            ):
                detail = detail.replace(secret, "***")
            error_message = (
                f"LLM client initialization failed: {detail}"
                if detail
                else "LLM client initialization failed"
            )
        if error_message is not None:
            raise RuntimeError(error_message) from None
        return client


llm_runtime_config_provider = LLMRuntimeConfigProvider()
