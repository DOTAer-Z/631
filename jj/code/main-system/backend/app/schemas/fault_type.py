from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class FaultTypeCreate(BaseModel):
    name: str
    description: Optional[str] = None
    color_tag: str = "red"


class FaultTypeUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color_tag: Optional[str] = None


class FaultTypeOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    color_tag: str
    created_at: datetime

    class Config:
        from_attributes = True
