from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class FaultTypeCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=20000)

    @field_validator("name", "description")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("value cannot be empty")
        return trimmed


class FaultTypeUpdateRequest(FaultTypeCreateRequest):
    pass


class FaultTypeResponse(BaseModel):
    id: int
    name: str
    # Shared with the main system's fault_types, whose description is nullable;
    # main-seeded rows always carry one, but tolerate NULL defensively.
    description: str | None = None
    color_tag: str = "red"
    created_at: datetime
    updated_at: datetime


class FaultTypeListResponse(BaseModel):
    items: list[FaultTypeResponse]
    total: int


class FaultTypeDeleteResponse(BaseModel):
    id: int
    name: str
    referenced_count: int
