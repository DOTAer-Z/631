from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class LogEntryOut(BaseModel):
    id: int
    filename: str
    fault_type_id: Optional[int]
    fault_type_name: Optional[str] = None
    summary: Optional[str]
    is_indexed: bool
    chroma_doc_id: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class LogEntryListOut(BaseModel):
    total: int
    items: list[LogEntryOut]
