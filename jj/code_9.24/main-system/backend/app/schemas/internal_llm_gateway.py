from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


InternalLLMPurpose = Literal[
    "annotation_recommendation",
    "annotation_window_analysis",
    "annotation_timestamp_inference",
]


class InternalLLMMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=100_000)

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message content must not be blank")
        return value


class InternalLLMChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: InternalLLMPurpose
    messages: list[InternalLLMMessage] = Field(min_length=1, max_length=32)
    json_mode: bool = True
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)

    @model_validator(mode="after")
    def cap_total_message_content(self):
        if sum(len(message.content) for message in self.messages) > 200_000:
            raise ValueError("total message content exceeds 200000 characters")
        return self


class InternalLLMChatResponse(BaseModel):
    content: str
    model: str
