from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.slice_window import SliceWindow


class WindowAnalysis(Base):
    """Persisted result of the on-demand multi-fault AI analysis for a window.

    Pure cache so users can re-open a window and read the last analysis without
    re-invoking the LLM. One row per window (upsert). The per-file suggestions
    are stored as a JSON blob in ``files_json``.
    """

    __tablename__ = "ann_window_analyses"
    __table_args__ = (
        UniqueConstraint("slice_window_id", name="uq_window_analyses_window_id"),
        CheckConstraint(
            "status IN ('success', 'failed')",
            name="ck_window_analyses_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slice_window_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ann_slice_windows.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    multiple_faults: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suggest_subdivide: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suggested_window_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    files_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    slice_window: Mapped["SliceWindow"] = relationship()
