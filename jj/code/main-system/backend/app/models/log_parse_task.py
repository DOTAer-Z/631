from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base


LOG_PARSE_TASK_STATES = (
    "queued",
    "running",
    "cancelling",
    "cancelled",
    "succeeded",
    "failed",
)


def _uuid_string() -> str:
    return str(uuid4())


_JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


class LogParseTask(Base):
    __tablename__ = "log_parse_tasks"
    __table_args__ = (
        CheckConstraint(
            "state IN ('queued', 'running', 'cancelling', 'cancelled', 'succeeded', 'failed')",
            name="ck_log_parse_tasks_state",
        ),
        CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_log_parse_tasks_progress",
        ),
        Index("idx_log_parse_tasks_state_created_at", "state", "created_at"),
    )

    id = Column(String(36), primary_key=True, default=_uuid_string)
    state = Column(String(16), nullable=False, default="queued")
    progress = Column(Integer, nullable=False, default=0)
    stage = Column(String(32), nullable=False, default="queued")
    run_ids = Column(_JSON_TYPE, nullable=False)
    current_run_id = Column(String(255), nullable=True)
    completed_count = Column(Integer, nullable=False, default=0)
    success_count = Column(Integer, nullable=False, default=0)
    fail_count = Column(Integer, nullable=False, default=0)
    results = Column(_JSON_TYPE, nullable=False, default=dict)
    fail_details = Column(_JSON_TYPE, nullable=False, default=list)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    cancel_requested_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
