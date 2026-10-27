from __future__ import annotations

from app.services.llm import LLMError

"""Shared test doubles for LLM-backed features (tasks 2 and 3)."""


class FakeLLMClient:
    """In-memory stand-in for LLMClient. No network.

    responses: a single string, a list of strings (returned in call order, last
    repeats), or a callable(messages) -> str.
    """

    def __init__(
        self,
        responses=None,
        *,
        configured: bool = True,
        model: str = "fake-model",
    ) -> None:
        self._responses = responses
        self._configured = configured
        self.model = model
        self.calls: list[list[dict]] = []
        self.purposes: list[str] = []

    def is_configured(self) -> bool:
        return self._configured

    def chat(
        self,
        messages,
        *,
        purpose,
        json_mode: bool = True,
        temperature: float = 0.0,
        timeout=None,
    ) -> str:
        self.calls.append(messages)
        self.purposes.append(purpose)
        if not self._configured:
            raise LLMError("fake client not configured")
        responses = self._responses
        if callable(responses):
            return responses(messages)
        if isinstance(responses, list):
            if not responses:
                raise LLMError("fake client has no responses left")
            index = min(len(self.calls) - 1, len(responses) - 1)
            return responses[index]
        if responses is None:
            raise LLMError("fake client has no response configured")
        return responses

    @property
    def call_count(self) -> int:
        return len(self.calls)
