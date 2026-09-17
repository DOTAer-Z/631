from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Provider = Literal["openai-compatible", "dashscope", "vllm"]


def _required_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("must not be empty")
    return normalized


def _http_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url must be an http(s) URL")
    return normalized


class ModelApiConfigCreate(BaseModel):
    name: str = Field(max_length=100)
    provider: Provider
    base_url: str = Field(max_length=1024)
    api_key: str
    model: str = Field(max_length=255)
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    # 8192：推理模型(如 deepseek-v4-flash)思考阶段会吃掉输出预算，1024 会导致答案为空
    # (多错误分析尤其明显)。给足思考+答案预算；非推理模型用不满也无副作用。
    max_output_tokens: int = Field(default=8192, ge=1, le=32768)

    _normalize_name = field_validator("name")(_required_text)
    _normalize_model = field_validator("model")(_required_text)
    _normalize_api_key = field_validator("api_key")(_required_text)
    _normalize_base_url = field_validator("base_url")(_http_url)


class ModelApiConfigUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    provider: Provider | None = None
    base_url: str | None = Field(default=None, max_length=1024)
    api_key: str | None = None
    model: str | None = Field(default=None, max_length=255)
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)
    max_output_tokens: int | None = Field(default=None, ge=1, le=32768)

    @field_validator(
        "name",
        "provider",
        "base_url",
        "api_key",
        "model",
        "timeout_seconds",
        "max_output_tokens",
        mode="before",
    )
    @classmethod
    def reject_explicit_null(cls, value):
        if value is None:
            raise ValueError("must not be null")
        return value

    @field_validator("name", "model", "api_key")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return None if value is None else _required_text(value)

    @field_validator("base_url")
    @classmethod
    def normalize_optional_base_url(cls, value: str | None) -> str | None:
        return None if value is None else _http_url(value)

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


class ModelApiConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    provider: Provider
    base_url: str
    model: str
    timeout_seconds: int
    max_output_tokens: int
    api_key_configured: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ModelApiConfigListOut(BaseModel):
    items: list[ModelApiConfigOut]


class ModelApiConfigDeleteOut(BaseModel):
    deleted: bool


class ModelApiTestResult(BaseModel):
    success: bool
    latency_ms: int
    model: str
    message: str | None = None
    error: str | None = None
