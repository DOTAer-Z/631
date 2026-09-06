from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.fault_type import FaultTypeResponse

SuggestionStatus = Literal["pending", "accepted", "rejected"]


class FaultTypeSuggestionResponse(BaseModel):
    id: int
    slice_window_id: int
    suggested_name: str
    suggested_description: str
    reason: str | None
    model: str | None
    status: SuggestionStatus
    accepted_fault_type_id: int | None
    created_at: datetime
    updated_at: datetime


class FaultTypeSuggestionListResponse(BaseModel):
    items: list[FaultTypeSuggestionResponse]
    total: int
    page: int
    page_size: int


class FaultTypeSuggestionAcceptRequest(BaseModel):
    """Override-able fields when accepting a suggestion. If both are omitted the
    LLM-proposed name/description are used verbatim."""

    name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None)

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("description")
    @classmethod
    def _normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class FaultTypeSuggestionAcceptResponse(BaseModel):
    suggestion: FaultTypeSuggestionResponse
    fault_type: FaultTypeResponse
