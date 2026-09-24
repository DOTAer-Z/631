from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ImportTaskResponse(BaseModel):
    id: int
    package_id: int
    status: str
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
